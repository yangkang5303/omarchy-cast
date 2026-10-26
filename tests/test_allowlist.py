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
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONFIG_DIR = tempfile.mkdtemp(prefix="omarchy-cast-test-")
os.environ["OMARCHY_CAST_CONFIG_DIR"] = CONFIG_DIR

from omarchy_cast import config, proxy, receiver  # noqa: E402


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
    """Stands in for the loopback PipeWire listener behind the proxy."""

    def __init__(self):
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(4)
        self.port = self._server.getsockname()[1]
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

    def test_config_round_trip_is_valid_json(self):
        config.allow_add("10.0.0.5")
        saved = json.loads(Path(config.CONFIG_PATH).read_text())
        self.assertEqual(saved["allowed_ips"], ["10.0.0.5"])


class ReceiverBindingTests(unittest.TestCase):
    """The PipeWire listener must stay on loopback whenever the proxy gates it."""

    def setUp(self):
        Path(config.CONFIG_PATH).unlink(missing_ok=True)

    tearDown = setUp

    @staticmethod
    def module_args(**patch):
        config.save(dict(config.DEFAULTS, display_name="box", **patch))
        return receiver.Receiver()._module_args()

    def test_default_binds_pipewire_to_loopback(self):
        args = self.module_args()
        self.assertIn("listen=127.0.0.1", args)
        self.assertIn(f"port={config.DEFAULT_PORT + 1}", args)
        self.assertNotIn(f"port={config.DEFAULT_PORT}", args)

    def test_stale_mode_still_binds_to_loopback(self):
        self.assertIn("listen=127.0.0.1", self.module_args(allow_mode="lan_only"))

    def test_listen_setting_does_not_widen_the_module(self):
        args = self.module_args(listen="192.168.1.20")
        self.assertIn("listen=127.0.0.1", args)
        self.assertNotIn("listen=192.168.1.20", args)

    def test_all_mode_exposes_the_public_port(self):
        args = self.module_args(allow_mode="all")
        self.assertIn(f"port={config.DEFAULT_PORT}", args)
        self.assertNotIn("listen=127.0.0.1", args)

    def test_proxy_is_only_started_in_specific_mode(self):
        config.save(dict(config.DEFAULTS, display_name="box", allow_mode="all"))
        rcv = receiver.Receiver()
        rcv.start_proxy()
        self.assertIsNone(rcv.proxy)


class ProxyEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.backend = EchoServer()
        self.addCleanup(self.backend.close)
        self.proxy = None

    def start_proxy(self, allowed):
        port = free_port()
        self.proxy = proxy.TcpProxy(
            "127.0.0.1", port, "127.0.0.1", self.backend.port, allowed=allowed
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


class ReceiverReloadTests(unittest.TestCase):
    """An allowlist edit has to reach the receiver that is already running.

    Every front end -- the bar panel, the GTK window, and `omarchy-cast allow`
    -- only writes the config file, so if the receiver did not read it back, a
    removed host would stay allowed by the running proxy.
    """

    def setUp(self):
        Path(config.CONFIG_PATH).unlink(missing_ok=True)
        self.backend = EchoServer()
        self.addCleanup(self.backend.close)
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
            internal_port=self.backend.port,
            allowed_ips=["127.0.0.1"],
        )
        cfg.update(patch)
        config.save(cfg)
        rcv = receiver.Receiver()
        rcv.module_id = "stub"  # stands in for the loaded PipeWire module
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
        rcv, _port = self.start_receiver()
        rebuilt = []
        rcv.load_module = lambda: rebuilt.append("module") or "stub"
        rcv.start_publisher = lambda: rebuilt.append("publisher")
        rcv.stop_publisher = lambda: None
        rcv.unload_module = lambda: None
        config.set_allow_mode("all")
        self.assertTrue(rcv.reload())
        self.assertEqual(rebuilt, ["module", "publisher"])
        self.assertEqual(rcv.allow_mode, "all")
        self.assertIsNone(rcv.proxy)

    def test_a_downed_listener_is_rebuilt(self):
        rcv, _port = self.start_receiver()
        rcv.module_id = None  # a reload that failed part way through
        rebuilt = []
        rcv.load_module = lambda: rebuilt.append("module") or "stub"
        rcv.start_publisher = lambda: rebuilt.append("publisher")
        rcv.stop_publisher = lambda: None
        rcv.unload_module = lambda: None
        self.assertTrue(rcv.reload())
        self.assertEqual(rebuilt, ["module", "publisher"])

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


if __name__ == "__main__":
    unittest.main()
