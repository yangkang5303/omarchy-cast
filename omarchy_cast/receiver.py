import os
import signal
import sys
import time

from . import config
from . import discovery
from . import proxy
from . import pulse
from . import state
from . import util

# How often the running receiver re-reads the config file.
POLL_INTERVAL = 1.0


class Receiver:
    def __init__(self, port=None, name=None):
        # An explicit --port/--name on 'omarchy-cast receive' wins over the
        # config file, now and on every later re-read.
        self.port_override = int(port) if port else None
        self.name_override = name or None
        self.module_id = None
        self.publisher = None
        self.proxy = None
        self.running = True
        self.load_settings()

    def load_settings(self):
        """Read the settings that shape the listeners out of the config file."""
        cfg = config.load()
        self.port = self.port_override or int(cfg["port"])
        self.name = self.name_override or cfg["display_name"]
        self.listen = (cfg.get("listen") or "").strip()
        self.allow_mode = config.normalize_allow_mode(cfg.get("allow_mode"))
        self.allowed_ips = list(cfg.get("allowed_ips", []))

    def _listener_key(self):
        """Everything but the allowlist itself that the listeners are built from.

        A change here cannot be applied in place: the PipeWire module has to be
        reloaded and the proxy rebound.
        """
        return (self.port, self.name, self.listen, self.allow_mode)

    def backend(self):
        """What the proxy forwards allowed clients to.

        Always the audio server's own unix socket. A TCP listener would be
        open to every other user account on this machine -- loopback TCP has
        no owner check -- and PipeWire's ``module-native-protocol-tcp`` has no
        way to restrict one: see ``clear_modules`` for why its cookie option
        is not an answer either.
        """
        socket_path = pulse.native_socket()
        if not os.path.exists(socket_path):
            raise RuntimeError(
                f"PipeWire/Pulse socket not found at {socket_path}; "
                "is the audio server running?"
            )
        return socket_path

    def clear_modules(self):
        """Make sure no native-protocol-tcp listener is loaded at all.

        The receiver never loads one. An earlier version did, on loopback,
        and such a module outlives the process that asked for it, so an
        upgrade has to take it down rather than leave it open beside us.

        Loading it without ``auth-anonymous=1`` is not a way to keep other
        local users out: PipeWire's pulse server admits a client that
        presents no valid cookie anyway, so the port would be open to anyone
        with a login on this machine regardless of the allowlist.
        """
        for module in pulse.modules_of("module-native-protocol-tcp"):
            util.pactl(["unload-module", module["id"]], check=False)
        self.module_id = None

    def _proxy_allowlist(self):
        """What the proxy should filter on, which is nothing in "all" mode.

        ``allowed_ips`` keeps whatever the user has collected there, so the
        list survives turning the restriction off and on. Handing it to the
        proxy while the mode is "all" would quietly restrict a receiver that
        every front end describes as open.
        """
        return None if self.allow_mode == "all" else self.allowed_ips

    def start_proxy(self):
        listen_host = self.listen or "0.0.0.0"
        self.proxy = proxy.TcpProxy(
            listen_host,
            self.port,
            self.backend(),
            allowed=self._proxy_allowlist(),
        )
        self.proxy.start()

    def stop_proxy(self):
        if self.proxy:
            self.proxy.stop()
            self.proxy = None

    def start_publisher(self):
        self.publisher = discovery.publish(self.name, self.port)

    def stop_publisher(self):
        if self.publisher and self.publisher.poll() is None:
            self.publisher.terminate()
            try:
                self.publisher.wait(timeout=3)
            except Exception:
                self.publisher.kill()
        self.publisher = None

    def start(self):
        self.clear_modules()
        self.start_proxy()
        self.start_publisher()
        self.announce()
        self.publish_state()

    def publish_state(self):
        """Record what is being enforced, for 'status' and the allow commands.

        The CLI reads this to tell whether the receiver that is running is
        this build -- an older one keeps the allowlist it cached at startup,
        and only a restart reaches it.

        Nothing is published while the listeners are down, so the CLI does not
        promise that a change has been applied by a receiver that is not
        serving anything.
        """
        if self._listener_down():
            state.clear()
            return
        state.write(self.port, self.allow_mode, self.allowed_ips)

    def _listener_down(self):
        """True when we are not actually serving what we claim to serve."""
        return self.proxy is None

    def reload(self):
        """Pick up config changes made while the receiver is running.

        The panel, the GTK window, and ``omarchy-cast allow`` only write the
        config file. Until the receiver reads it back, a host removed from the
        allowlist is still allowed by the running proxy and can open new
        connections to the audio server, even though every front end shows it
        as removed.
        """
        before_key = self._listener_key()
        before_allowed = list(self.allowed_ips)
        self.load_settings()
        changed = self._listener_key() != before_key
        # A previous reload may have failed part way through and left nothing
        # listening: rebuild then too.
        if changed or self._listener_down():
            print(
                "[omarchy-cast] configuration changed, reloading"
                if changed
                else "[omarchy-cast] listener is down, restarting it",
                flush=True,
            )
            self.stop_proxy()
            self.stop_publisher()
            self.start()
            return True
        if self.allowed_ips == before_allowed:
            return False
        if not self.proxy.set_allowed(self._proxy_allowlist()):
            # "all" mode: the list moved but the proxy filters on none of it.
            return False
        dropped = self.proxy.drop_disallowed()
        print(
            f"[omarchy-cast] allowlist updated: "
            f"{len(self.allowed_ips)} allowed host(s)",
            flush=True,
        )
        for ip in sorted(set(dropped)):
            print(f"[omarchy-cast]   cut off {ip} (no longer allowed)", flush=True)
        if not self.allowed_ips:
            print(
                "[omarchy-cast] no host is allowed any more, so every incoming "
                "connection is refused",
                file=sys.stderr,
                flush=True,
            )
        return True

    def announce(self):
        """Log what the receiver accepts, and warn when that is everything."""
        where = self.listen or "all interfaces"
        print(
            f"[omarchy-cast] audio server reached over {pulse.native_socket()}; "
            "no TCP port of its own",
            flush=True,
        )
        if self.allow_mode == "specific":
            print(
                f"[omarchy-cast] receiving on {where}:{self.port} as '{self.name}' "
                f"(allowed hosts: {len(self.allowed_ips)})",
                flush=True,
            )
            for ip in self.allowed_ips:
                print(f"[omarchy-cast]   allowed: {ip}", flush=True)
            if not self.allowed_ips:
                print(
                    "[omarchy-cast] no host is allowed yet, so every incoming "
                    "connection is refused. Allow the hosts you trust with "
                    "'omarchy-cast allow add <ip>', or from the host list in the "
                    "panel.",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    "[omarchy-cast] note: allowed hosts are identified by source IP "
                    "only. That is a LAN trust decision, not authentication.",
                    file=sys.stderr,
                    flush=True,
                )
        else:
            print(
                f"[omarchy-cast] receiving on {where}:{self.port} as '{self.name}' "
                f"(allow_mode=all)",
                flush=True,
            )
            print(
                "[omarchy-cast] warning: allow_mode is 'all', so every client that "
                "can reach the listener may cast to this host with unrestricted "
                "access to the audio server. Restrict it again with "
                "'omarchy-cast allow mode specific', set 'listen' to a LAN address, "
                "or restrict the port with a firewall.",
                file=sys.stderr,
                flush=True,
            )

    def _stop(self, *_):
        self.running = False

    def run(self):
        try:
            self.start()
        except Exception as exc:
            # Nothing is listening, which refuses audio rather than serving it
            # unguarded. The loop below retries.
            print(f"[omarchy-cast] start failed: {exc}", file=sys.stderr, flush=True)
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        try:
            while self.running:
                time.sleep(POLL_INTERVAL)
                try:
                    self.reload()
                except Exception as exc:
                    # A failed reload leaves the receiver without a listener,
                    # which refuses audio rather than letting a stale
                    # allowlist stand. Say so and keep trying.
                    print(f"[omarchy-cast] reload failed: {exc}", file=sys.stderr, flush=True)
                self.publish_state()
                if self.publisher is None or self.publisher.poll() is not None:
                    self.start_publisher()
        finally:
            print("[omarchy-cast] stopping", flush=True)
            state.clear()
            self.stop_publisher()
            self.stop_proxy()
            self.clear_modules()


def run(port=None, name=None):
    if not util.have("avahi-publish-service"):
        print("avahi-publish-service not found (install avahi)", file=sys.stderr)
        return 1
    Receiver(port=port, name=name).run()
    return 0
