"""Receiver allowlist tests: the restrictive default and the proxy that enforces it.

    python3 -m unittest discover -s tests

The config module resolves its directory at import time, so point it at a
temporary one before importing anything from the package.
"""

import contextlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONFIG_DIR = tempfile.mkdtemp(prefix="omarchy-cast-test-")
os.environ["OMARCHY_CAST_CONFIG_DIR"] = CONFIG_DIR
os.environ["OMARCHY_CAST_STATE_DIR"] = CONFIG_DIR

from omarchy_cast import (  # noqa: E402
    __version__,
    cli,
    config,
    proxy,
    pulse,
    receiver,
    service,
    state,
    util,
)


def setUpModule():
    """Fail loudly rather than advertise a test receiver on the LAN.

    discovery.publish spawns avahi-publish-service, which outlives the test
    that started it. Any test reaching it has not been isolated.
    """
    def refuse(*_args, **_kwargs):
        raise AssertionError(
            "a test tried to advertise on the LAN; pass the receiver "
            "through isolate() first"
        )

    receiver.discovery.publish = refuse


def tearDownModule():
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def recv_until_closed(sock, limit=16):
    """Read until the peer closes; b"" means the connection was cut off."""
    try:
        return sock.recv(limit)
    except OSError:
        return b""


class EchoServer:
    """Stands in for the audio server behind the proxy.

    With ``path`` it listens on a unix socket, the way PipeWire's own socket
    does; without one it listens on loopback TCP, which is what cookie auth
    still puts behind the proxy.
    """

    def __init__(self, path=None):
        self.path = path
        if path:
            self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server.bind(path)
            self.port = None
            self.address = path
        else:
            self._server = socket.socket()
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(("127.0.0.1", 0))
            self.port = self._server.getsockname()[1]
            self.address = ("127.0.0.1", self.port)
        self._server.listen(4)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._echo, args=(conn,), daemon=True).start()

    @staticmethod
    def _echo(conn):
        with conn:
            try:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        return
                    conn.sendall(data)
            except OSError:
                return

    def close(self):
        self._server.close()
        if self.path:
            Path(self.path).unlink(missing_ok=True)


def unix_echo_server(test):
    """An echo server on a unix socket, standing in for PipeWire's own.

    ``pulse.native_socket`` is pointed at it for the duration of the test, so
    the default (anonymous) receiver path is exercised without touching the
    real audio server.
    """
    directory = tempfile.mkdtemp(prefix="omarchy-cast-sock-")
    test.addCleanup(shutil.rmtree, directory, True)
    server = EchoServer(path=os.path.join(directory, "native"))
    test.addCleanup(server.close)
    original = receiver.pulse.native_socket
    receiver.pulse.native_socket = lambda: server.path
    test.addCleanup(setattr, receiver.pulse, "native_socket", original)
    return server


def isolate(test, rcv):
    """Keep a receiver under test off the machine it is running on.

    Everything here reaches outside the process: ``clear_modules`` drives
    pactl against the real audio server, and ``start_publisher`` spawns an
    avahi-publish-service that advertises the test's name and port on the
    LAN and outlives the test -- other hosts then list a receiver that is
    not there and cannot be cast to. A rebuild happens on any listener
    change, so these cannot be stubbed per test and remembered case by case.
    """
    rcv.clear_modules = lambda: None
    rcv.start_publisher = lambda: None
    rcv.stop_publisher = lambda: None


class AllowModeDefaultTests(unittest.TestCase):
    def setUp(self):
        Path(config.CONFIG_PATH).unlink(missing_ok=True)

    tearDown = setUp

    def test_default_is_specific(self):
        self.assertEqual(config.DEFAULTS["allow_mode"], "specific")
        self.assertEqual(config.load()["allow_mode"], "specific")
        self.assertEqual(config.allow_mode(), "specific")

    def test_fresh_install_accepts_nothing(self):
        self.assertTrue(config.accepts_nothing())

    def test_unknown_mode_resolves_to_specific(self):
        for value in (None, "", "lan_only", "ALL", "any", 1, []):
            self.assertEqual(config.normalize_allow_mode(value), "specific")
        self.assertEqual(config.normalize_allow_mode("all"), "all")

    def test_stale_mode_in_config_resolves_to_specific(self):
        config.save({"allow_mode": "lan_only"})
        self.assertEqual(config.load()["allow_mode"], "specific")

    def test_unreadable_config_resolves_to_specific(self):
        Path(config.CONFIG_PATH).write_text("{ not json")
        self.assertEqual(config.load()["allow_mode"], "specific")

    def test_explicit_all_is_kept(self):
        config.set_allow_mode("all")
        self.assertEqual(config.load()["allow_mode"], "all")
        self.assertFalse(config.accepts_nothing())

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            config.set_allow_mode("lan_only")
        with self.assertRaises(ValueError):
            config.set_value("allow_mode", "")
        self.assertEqual(config.load()["allow_mode"], "specific")

    def test_allowed_host_clears_accepts_nothing(self):
        self.assertTrue(config.allow_add("192.168.1.10"))
        self.assertFalse(config.allow_add("192.168.1.10"))
        self.assertFalse(config.accepts_nothing())
        self.assertTrue(config.allow_remove("192.168.1.10"))
        self.assertTrue(config.accepts_nothing())

    def test_saving_is_atomic(self):
        """A reader must never see a half-written config.

        The running receiver re-reads this file once a second. A truncated
        read parses as nothing, falls back to the defaults, and makes the
        receiver rebind on the default port with an empty allowlist, dropping
        the audio it was carrying.
        """
        config.save(dict(config.DEFAULTS, display_name="box", port=9999))
        seen = []

        real_replace = Path.replace

        def replace_spy(self, target):
            # Whatever a reader sees at this instant is the old file, whole.
            seen.append(json.loads(Path(target).read_text()))
            return real_replace(self, target)

        Path.replace = replace_spy
        self.addCleanup(setattr, Path, "replace", real_replace)
        config.set_value("port", 4800)
        self.assertEqual([entry["port"] for entry in seen], [9999])
        self.assertEqual(json.loads(Path(config.CONFIG_PATH).read_text())["port"], 4800)

    def test_saving_drops_keys_we_no_longer_honour(self):
        """A dead key in the file reads like a setting that still does something."""
        Path(config.CONFIG_PATH).write_text(
            json.dumps(dict(config.DEFAULTS, auth="cookie", internal_port=4714))
        )
        self.assertEqual(config.load().get("auth"), "cookie")  # read as-is
        config.allow_add("10.0.0.5")  # any write migrates the file
        saved = json.loads(Path(config.CONFIG_PATH).read_text())
        self.assertNotIn("auth", saved)
        self.assertNotIn("internal_port", saved)
        self.assertEqual(saved["allowed_ips"], ["10.0.0.5"])

    def test_config_round_trip_is_valid_json(self):
        config.allow_add("10.0.0.5")
        saved = json.loads(Path(config.CONFIG_PATH).read_text())
        self.assertEqual(saved["allowed_ips"], ["10.0.0.5"])


class ReceiverBindingTests(unittest.TestCase):
    """The audio server must never be reachable around the proxy.

    With the default anonymous auth it is reached over its unix socket, so
    PipeWire opens no TCP port at all: a loopback port would be open to every
    other local user on the machine, allowlist or not. Only cookie auth puts
    a TCP listener back, and then the audio server checks the cookie itself.
    """

    def setUp(self):
        Path(config.CONFIG_PATH).unlink(missing_ok=True)

    tearDown = setUp

    @staticmethod
    def receiver_with(**patch):
        config.save(dict(config.DEFAULTS, display_name="box", **patch))
        return receiver.Receiver()

    def test_the_backend_is_the_unix_socket(self):
        server = unix_echo_server(self)
        self.assertEqual(self.receiver_with().backend(), server.path)

    def test_all_mode_opens_no_tcp_listener(self):
        """Opting out of the allowlist must not re-expose the audio server."""
        server = unix_echo_server(self)
        self.assertEqual(self.receiver_with(allow_mode="all").backend(), server.path)

    def test_stale_mode_opens_no_tcp_listener(self):
        server = unix_echo_server(self)
        self.assertEqual(
            self.receiver_with(allow_mode="lan_only").backend(), server.path
        )

    def test_starting_unloads_any_tcp_module(self):
        """An older build's loopback listener outlives it; take it down.

        Leaving it loaded would keep a TCP port open beside the proxy, which
        is exactly the way around the allowlist this topology removes.
        """
        unix_echo_server(self)
        rcv = self.receiver_with(port=free_port())
        unloaded = []
        rcv.clear_modules = lambda: unloaded.append("cleared")
        rcv.start_publisher = lambda: None
        rcv.publish_state = lambda: None
        rcv.announce = lambda: None
        rcv.start()
        self.addCleanup(rcv.stop_proxy)
        self.assertEqual(unloaded, ["cleared"])

    def test_a_missing_socket_refuses_to_serve(self):
        """Fail closed: no backend means nothing listens, not an open port."""
        original = receiver.pulse.native_socket
        receiver.pulse.native_socket = lambda: "/nonexistent/pulse/native"
        self.addCleanup(setattr, receiver.pulse, "native_socket", original)
        rcv = self.receiver_with()
        with self.assertRaises(RuntimeError):
            rcv.backend()
        with self.assertRaises(RuntimeError):
            rcv.start_proxy()
        self.assertIsNone(rcv.proxy)

    def test_the_receiver_cannot_load_a_tcp_module(self):
        """There is no code path left that opens a PipeWire TCP port.

        Cookie auth used to be the exception. It is not an exception:
        PipeWire's pulse server admits a client that presents no valid
        cookie, so such a port is open to every local user either way.
        """
        self.assertFalse(hasattr(receiver.Receiver, "load_module"))
        self.assertFalse(hasattr(receiver.Receiver, "_module_args"))
        self.assertNotIn("auth", config.DEFAULTS)

    def test_listen_setting_binds_only_the_proxy(self):
        unix_echo_server(self)
        rcv = self.receiver_with(listen="127.0.0.1", port=free_port())
        rcv.start_proxy()
        self.addCleanup(rcv.stop_proxy)
        self.assertEqual(rcv.proxy.listen_host, "127.0.0.1")

    def test_the_proxy_serves_the_public_port_in_both_modes(self):
        server = unix_echo_server(self)
        for mode, expected in (("specific", set()), ("all", None)):
            with self.subTest(mode=mode):
                port = free_port()
                rcv = self.receiver_with(allow_mode=mode, port=port)
                rcv.start_proxy()
                self.addCleanup(rcv.stop_proxy)
                self.assertIsNotNone(rcv.proxy)
                self.assertEqual(rcv.proxy.listen_port, port)
                self.assertEqual(rcv.proxy.backend, server.path)
                self.assertEqual(rcv.proxy.allowed, expected)


class ProxyEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.backend = EchoServer()
        self.addCleanup(self.backend.close)
        self.proxy = None

    def start_proxy(self, allowed, backend=None):
        port = free_port()
        self.proxy = proxy.TcpProxy(
            "127.0.0.1", port, backend or self.backend.address, allowed=allowed
        )
        self.proxy.start()
        self.addCleanup(self.proxy.stop)
        return port

    def talk(self, port):
        """Send a byte and return what comes back (b"" means refused)."""
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            try:
                sock.sendall(b"ping")
            except OSError:
                return b""
            try:
                return sock.recv(16)
            except OSError:
                return b""

    def test_allowed_source_reaches_the_backend(self):
        port = self.start_proxy(["127.0.0.1"])
        self.assertEqual(self.talk(port), b"ping")

    def test_empty_allowlist_refuses_everyone(self):
        port = self.start_proxy([])
        self.assertEqual(self.talk(port), b"")

    def test_unlisted_source_is_refused(self):
        port = self.start_proxy(["10.1.2.3"])
        self.assertEqual(self.talk(port), b"")

    def test_a_unix_socket_backend_is_forwarded_to(self):
        """The default backend is PipeWire's own socket, not a TCP port."""
        server = unix_echo_server(self)
        port = self.start_proxy(["127.0.0.1"], backend=server.path)
        self.assertEqual(self.talk(port), b"ping")

    def test_a_unix_socket_backend_still_filters(self):
        server = unix_echo_server(self)
        port = self.start_proxy(["10.1.2.3"], backend=server.path)
        self.assertEqual(self.talk(port), b"")

    def test_allowed_none_means_no_filtering(self):
        port = self.start_proxy(None)
        self.assertEqual(self.talk(port), b"ping")

    def test_set_allowed_gates_new_connections(self):
        port = self.start_proxy(["127.0.0.1"])
        self.assertEqual(self.talk(port), b"ping")
        self.assertTrue(self.proxy.set_allowed([]))
        self.assertEqual(self.talk(port), b"")
        self.assertTrue(self.proxy.set_allowed(["127.0.0.1"]))
        self.assertEqual(self.talk(port), b"ping")

    def test_set_allowed_reports_whether_it_changed(self):
        self.start_proxy(["127.0.0.1"])
        self.assertFalse(self.proxy.set_allowed(["127.0.0.1"]))
        self.assertTrue(self.proxy.set_allowed(["127.0.0.2"]))
        self.assertEqual(self.proxy.allowed, {"127.0.0.2"})

    def test_an_idle_connection_is_left_alone(self):
        """A quiet connection is not a dead one.

        Playback pauses and gaps between tracks produce no bytes. A read
        deadline here closed such a connection with nothing in the log.
        """
        port = self.start_proxy(["127.0.0.1"])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(b"ping")
            self.assertEqual(sock.recv(16), b"ping")
            for sides in (self.proxy._live.items(),):
                for client, (_ip, backend) in list(sides):
                    # What the proxy left on the sockets it owns decides
                    # whether a quiet minute kills the connection.
                    self.assertIsNone(client.gettimeout())
                    self.assertIsNone(backend.gettimeout())

    def test_drop_disallowed_cuts_an_open_connection(self):
        port = self.start_proxy(["127.0.0.1"])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(b"ping")
            self.assertEqual(sock.recv(16), b"ping")
            self.proxy.set_allowed([])
            self.assertEqual(self.proxy.drop_disallowed(), ["127.0.0.1"])
            self.assertEqual(recv_until_closed(sock), b"")

    def test_drop_disallowed_keeps_a_still_allowed_connection(self):
        port = self.start_proxy(["127.0.0.1"])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(b"ping")
            self.assertEqual(sock.recv(16), b"ping")
            self.proxy.set_allowed(["127.0.0.1", "10.0.0.9"])
            self.assertEqual(self.proxy.drop_disallowed(), [])
            sock.sendall(b"pong")
            self.assertEqual(sock.recv(16), b"pong")

    def hold_dial(self):
        """Park every new connection in ``dial`` until the returned event is set.

        That is the window the race lives in: the client has been accepted,
        but its handler has not registered it as live yet.
        """
        entered, release = threading.Event(), threading.Event()
        real_dial = proxy.dial

        def held(backend, timeout=5):
            entered.set()
            release.wait(5)
            return real_dial(backend, timeout)

        proxy.dial = held
        self.addCleanup(setattr, proxy, "dial", real_dial)
        self.addCleanup(release.set)
        return entered, release

    def test_a_connection_accepted_before_stop_is_not_forwarded_after_it(self):
        """The old proxy of an all -> specific switch must not leak a session.

        A client accepted by the unfiltered proxy just before the switch used
        to register itself after stop() had taken the connections to shut
        down, and that proxy -- already replaced by the receiver -- went on
        forwarding an unlisted host to the audio server.
        """
        entered, release = self.hold_dial()
        port = self.start_proxy(None)  # "all" mode
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            self.assertTrue(entered.wait(5))
            self.proxy.stop()
            release.set()
            try:
                sock.sendall(b"ping")
            except OSError:
                pass
            self.assertEqual(recv_until_closed(sock), b"")
        self.assertEqual(self.proxy._live, {})

    def test_a_connection_held_past_a_revocation_is_refused(self):
        """Same window, but the allowlist is narrowed instead of the proxy stopped."""
        entered, release = self.hold_dial()
        port = self.start_proxy(["127.0.0.1"])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            self.assertTrue(entered.wait(5))
            self.proxy.set_allowed([])
            self.proxy.drop_disallowed()
            release.set()
            try:
                sock.sendall(b"ping")
            except OSError:
                pass
            self.assertEqual(recv_until_closed(sock), b"")


class ReceiverReloadTests(unittest.TestCase):
    """An allowlist edit has to reach the receiver that is already running.

    Every front end -- the bar panel, the GTK window, and `omarchy-cast allow`
    -- only writes the config file, so if the receiver did not read it back, a
    removed host would stay allowed by the running proxy.
    """

    def setUp(self):
        Path(config.CONFIG_PATH).unlink(missing_ok=True)
        self.backend = unix_echo_server(self)
        # The receiver logs what it applied; keep it out of the test output.
        self.log = io.StringIO()
        stack = contextlib.ExitStack()
        stack.enter_context(contextlib.redirect_stdout(self.log))
        stack.enter_context(contextlib.redirect_stderr(self.log))
        self.addCleanup(stack.close)

    def tearDown(self):
        Path(config.CONFIG_PATH).unlink(missing_ok=True)

    def start_receiver(self, **patch):
        """A receiver with its proxy up, but no PipeWire module behind it."""
        port = free_port()
        cfg = dict(
            config.DEFAULTS,
            display_name="box",
            port=port,
            allowed_ips=["127.0.0.1"],
        )
        cfg.update(patch)
        config.save(cfg)
        rcv = receiver.Receiver()
        isolate(self, rcv)
        rcv.start_proxy()
        self.addCleanup(rcv.stop_proxy)
        return rcv, port

    @staticmethod
    def talk(port):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            try:
                sock.sendall(b"ping")
                return sock.recv(16)
            except OSError:
                return b""

    def test_removed_host_stops_being_allowed(self):
        rcv, port = self.start_receiver()
        self.assertEqual(self.talk(port), b"ping")
        config.allow_remove("127.0.0.1")
        self.assertTrue(rcv.reload())
        self.assertEqual(self.talk(port), b"")

    def test_reload_cuts_off_the_removed_host(self):
        rcv, port = self.start_receiver()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(b"ping")
            self.assertEqual(sock.recv(16), b"ping")
            config.allow_remove("127.0.0.1")
            rcv.reload()
            self.assertEqual(recv_until_closed(sock), b"")
        self.assertIn("cut off 127.0.0.1", self.log.getvalue())

    def test_added_host_is_allowed_without_a_restart(self):
        rcv, port = self.start_receiver(allowed_ips=["10.0.0.9"])
        self.assertEqual(self.talk(port), b"")
        config.allow_add("127.0.0.1")
        self.assertTrue(rcv.reload())
        self.assertEqual(self.talk(port), b"ping")

    def test_reload_without_a_change_does_nothing(self):
        rcv, port = self.start_receiver()
        self.assertFalse(rcv.reload())
        self.assertEqual(self.talk(port), b"ping")

    def test_mode_change_rebuilds_the_listeners(self):
        rcv, port = self.start_receiver()
        rebuilt = []
        rcv.start_publisher = lambda: rebuilt.append("publisher")
        config.set_allow_mode("all")
        self.assertTrue(rcv.reload())
        self.assertEqual(rebuilt, ["publisher"])
        self.assertEqual(rcv.allow_mode, "all")
        # The proxy keeps serving the public port; it just stops filtering.
        self.assertIsNotNone(rcv.proxy)
        self.assertIsNone(rcv.proxy.allowed)
        self.assertEqual(self.talk(port), b"ping")

    def test_a_client_mid_handshake_does_not_survive_turning_the_allowlist_on(self):
        """The reviewer's scenario, end to end through ``reload``.

        An unlisted host is accepted by the unfiltered "all" proxy, and while
        its handler is still dialing the audio server the owner turns the
        allowlist on. The receiver replaces that proxy; the old one must not
        then register the client and forward it anyway.
        """
        entered, release = threading.Event(), threading.Event()
        real_dial = proxy.dial

        def held(backend, timeout=5):
            entered.set()
            release.wait(5)
            return real_dial(backend, timeout)

        proxy.dial = held
        self.addCleanup(setattr, proxy, "dial", real_dial)
        self.addCleanup(release.set)

        rcv, port = self.start_receiver(allow_mode="all", allowed_ips=[])
        old_proxy = rcv.proxy
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            self.assertTrue(entered.wait(5))
            config.set_allow_mode("specific")  # nobody is allowed
            self.assertTrue(rcv.reload())
            self.assertIsNot(rcv.proxy, old_proxy)
            release.set()
            try:
                sock.sendall(b"ping")
            except OSError:
                pass
            self.assertEqual(recv_until_closed(sock), b"")
        self.assertEqual(old_proxy._live, {})

    def test_turning_the_allowlist_back_on_rebinds(self):
        """The mode flip must reach the running proxy, not just the config."""
        rcv, port = self.start_receiver(allow_mode="all")
        self.assertIsNone(rcv.proxy.allowed)
        config.set_allow_mode("specific")
        config.set_value("allowed_ips", [])
        self.assertTrue(rcv.reload())
        self.assertEqual(rcv.proxy.allowed, set())
        self.assertEqual(self.talk(port), b"")

    def test_all_mode_never_starts_filtering(self):
        """Collecting hosts while the restriction is off must not turn it on.

        The panel shows the per-host allow button in "all" mode too, so one
        click writes allowed_ips. Handing that list to the proxy would refuse
        every other host while the switch, the hint and `status` all still say
        any reachable host may cast.
        """
        rcv, port = self.start_receiver(allow_mode="all", allowed_ips=[])
        self.assertIsNone(rcv.proxy.allowed)
        config.allow_add("10.0.0.99")
        self.assertFalse(rcv.reload())
        self.assertIsNone(rcv.proxy.allowed)
        self.assertEqual(self.talk(port), b"ping")  # not in allowed_ips, still served

    def test_the_collected_list_applies_once_the_mode_is_on(self):
        rcv, port = self.start_receiver(allow_mode="all", allowed_ips=[])
        config.allow_add("10.0.0.99")
        rcv.reload()
        config.set_allow_mode("specific")
        self.assertTrue(rcv.reload())
        self.assertEqual(rcv.proxy.allowed, {"10.0.0.99"})
        self.assertEqual(self.talk(port), b"")

    def test_a_downed_listener_is_rebuilt(self):
        rcv, _port = self.start_receiver()
        rcv.stop_proxy()  # a reload that failed part way through
        rebuilt = []
        rcv.start_publisher = lambda: rebuilt.append("publisher")
        self.assertTrue(rcv.reload())
        self.assertEqual(rebuilt, ["publisher"])
        self.assertIsNotNone(rcv.proxy)

    def test_explicit_port_and_name_win_over_the_config(self):
        """'receive --port/--name' keeps winning on every later re-read."""
        rcv, _port = self.start_receiver()
        rcv.port_override, rcv.name_override = 9999, "fixed"
        rcv.load_settings()
        config.set_value("port", 4800)
        config.set_value("display_name", "renamed")
        rcv.load_settings()
        self.assertEqual(rcv.port, 9999)
        self.assertEqual(rcv.name, "fixed")


class RemoteNameTests(unittest.TestCase):
    """A host name arrives from another machine's mDNS record, not from us."""

    def test_quotes_and_newlines_do_not_reach_the_property_list(self):
        """It lands in pactl's sink_properties=, which parses what it gets."""
        description = pulse._description('evil" device.class="sink" x="')
        self.assertNotIn('"', description)
        self.assertNotIn("\n", description)
        self.assertNotIn(" ", description)  # one token, spaces are non-breaking

    def test_control_characters_are_dropped(self):
        self.assertEqual(pulse._description("a\nb\x00c"), "Cast:\u00a0abc")

    def test_the_description_is_bounded(self):
        self.assertLessEqual(len(pulse._description("x" * 5000)), 64)

    def test_an_ordinary_name_survives(self):
        self.assertEqual(pulse._description("Living Room"), "Cast:\u00a0Living\u00a0Room")

    def test_the_sink_name_stays_an_identifier(self):
        sink = pulse.sink_name_for('../../etc/passwd "x"')
        self.assertTrue(sink.replace("_", "").isalnum(), sink)


class ReceiverStateTests(unittest.TestCase):
    """The CLI must know which build is running before it promises anything."""

    def setUp(self):
        state.clear()

    tearDown = setUp

    def test_round_trip(self):
        state.write(4713, "specific", ["10.0.0.5"])
        live = state.read()
        self.assertEqual(live["version"], __version__)
        self.assertEqual(live["allowed_ips"], ["10.0.0.5"])
        self.assertEqual(live["pid"], os.getpid())
        self.assertTrue(state.is_current(live))

    def test_no_state_means_nothing_is_running(self):
        self.assertIsNone(state.read())
        self.assertFalse(state.is_current(None))

    def test_stale_state_is_ignored(self):
        state.write(4713, "specific", [])
        data = json.loads(Path(state.path()).read_text())
        data["updated"] = time.time() - (state.STALE_AFTER + 1)
        Path(state.path()).write_text(json.dumps(data))
        self.assertIsNone(state.read())

    def test_unreadable_state_is_ignored(self):
        Path(state.path()).write_text("{ not json")
        self.assertIsNone(state.read())

    def test_another_build_is_not_current(self):
        state.write(4713, "specific", [])
        data = json.loads(Path(state.path()).read_text())
        data["version"] = "0.9.0"
        Path(state.path()).write_text(json.dumps(data))
        live = state.read()
        self.assertIsNotNone(live)
        self.assertFalse(state.is_current(live))

    def test_receiver_publishes_what_it_enforces(self):
        unix_echo_server(self)
        config.save(
            dict(
                config.DEFAULTS,
                display_name="box",
                port=free_port(),
                allowed_ips=["10.0.0.5"],
            )
        )
        self.addCleanup(Path(config.CONFIG_PATH).unlink, True)
        rcv = receiver.Receiver()
        rcv.start_proxy()
        self.addCleanup(rcv.stop_proxy)
        rcv.publish_state()
        live = state.read()
        self.assertEqual(live["allowed_ips"], ["10.0.0.5"])
        self.assertEqual(live["allow_mode"], "specific")

    def test_nothing_is_published_while_the_listeners_are_down(self):
        """A receiver that is not serving must not claim to be enforcing.

        The CLI reads this state to tell the user their change has been
        applied; with no listener there is nothing applying anything.
        """
        config.save(dict(config.DEFAULTS, display_name="box", allowed_ips=["10.0.0.5"]))
        self.addCleanup(Path(config.CONFIG_PATH).unlink, True)
        state.write(4713, "specific", ["10.0.0.5"])
        rcv = receiver.Receiver()  # start_proxy never called: nothing is listening
        rcv.publish_state()
        self.assertIsNone(state.read())


class ApplyNoteTests(unittest.TestCase):
    """'allow add/remove' may only claim a live apply when it is true.

    Reinstalling used to leave an older receiver running with the allowlist it
    had cached, so the note had to stop assuming the running process is this
    build.
    """

    def setUp(self):
        state.clear()
        self.addCleanup(state.clear)
        self.active = True
        self.real_is_active = service.is_active
        service.is_active = lambda: self.active
        self.addCleanup(lambda: setattr(service, "is_active", self.real_is_active))

    def note(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli._apply_note()
        return out.getvalue(), err.getvalue()

    def test_this_build_applies_live(self):
        state.write(4713, "specific", [])
        out, err = self.note()
        self.assertIn("applies this within", out)
        self.assertEqual(err, "")

    def test_older_build_gets_a_restart_warning(self):
        state.write(4713, "specific", [])
        data = json.loads(Path(state.path()).read_text())
        data["version"] = "0.9.0"
        Path(state.path()).write_text(json.dumps(data))
        out, err = self.note()
        self.assertEqual(out, "")
        self.assertIn("version 0.9.0", err)
        self.assertIn("does NOT reach it", err)
        self.assertIn("stop-receiver", err)

    def test_active_without_state_gets_a_restart_warning(self):
        out, err = self.note()
        self.assertEqual(out, "")
        self.assertIn("older build", err)
        self.assertIn("stop-receiver", err)

    def test_nothing_running_says_nothing(self):
        self.active = False
        self.assertEqual(self.note(), ("", ""))

    def test_no_systemd_says_nothing(self):
        def boom():
            raise util.CommandError(["systemctl"], 127, "systemctl: not found")

        service.is_active = boom
        self.assertEqual(self.note(), ("", ""))


class AllowListReportTests(unittest.TestCase):
    """'allow list --json' has to say whether the receiver has applied this.

    The bar panel and the GTK window read it, and that is where a user removes
    a host -- so a stale receiver must be visible there, not only in the CLI.
    """

    def setUp(self):
        Path(config.CONFIG_PATH).unlink(missing_ok=True)
        state.clear()
        self.addCleanup(state.clear)
        self.addCleanup(Path(config.CONFIG_PATH).unlink, True)
        self.active = True
        real = service.is_active
        service.is_active = lambda: self.active
        self.addCleanup(lambda: setattr(service, "is_active", real))

    def report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["allow", "list", "--json"])
        return json.loads(out.getvalue())

    def test_this_build_is_not_stale(self):
        config.allow_add("10.0.0.5")
        state.write(4713, "specific", ["10.0.0.5"])
        data = self.report()
        self.assertFalse(data["stale"])
        self.assertTrue(data["live"]["current"])
        self.assertEqual(data["ips"], ["10.0.0.5"])

    def test_older_receiver_is_reported_as_stale(self):
        config.allow_add("10.0.0.5")
        state.write(4713, "specific", [])
        raw = json.loads(Path(state.path()).read_text())
        raw["version"] = "0.9.0"
        Path(state.path()).write_text(json.dumps(raw))
        data = self.report()
        self.assertTrue(data["stale"])
        self.assertFalse(data["live"]["current"])
        # the list the user sees vs. the list in force
        self.assertEqual(data["ips"], ["10.0.0.5"])
        self.assertEqual(data["live"]["ips"], [])

    def test_active_receiver_without_state_is_stale(self):
        data = self.report()
        self.assertTrue(data["stale"])
        self.assertIsNone(data["live"])

    def test_nothing_running_is_not_stale(self):
        self.active = False
        self.assertFalse(self.report()["stale"])


class InstallRestartTests(unittest.TestCase):
    """Reinstalling has to replace a receiver that is already running."""

    def setUp(self):
        self.calls = []
        real = service.systemctl
        self.addCleanup(lambda: setattr(service, "systemctl", real))

        def fake(*args, check=False):
            self.calls.append(args)
            return types.SimpleNamespace(stdout=self.state_of(args), returncode=0)

        service.systemctl = fake

    def state_of(self, args):
        return self.unit_state if args and args[0] == "is-active" else ""

    def test_a_running_receiver_is_restarted(self):
        self.unit_state = "active\n"
        self.assertTrue(service.refresh_running_receiver())
        self.assertIn(("restart", service.UNIT_NAME), self.calls)

    def test_a_stopped_receiver_is_left_alone(self):
        self.unit_state = "inactive\n"
        self.assertFalse(service.refresh_running_receiver())
        self.assertNotIn(("restart", service.UNIT_NAME), self.calls)

    def test_no_systemd_is_not_an_error(self):
        def boom(*args, check=False):
            raise util.CommandError(["systemctl"], 127, "systemctl: not found")

        service.systemctl = boom
        self.assertFalse(service.refresh_running_receiver())


if __name__ == "__main__":
    unittest.main()
