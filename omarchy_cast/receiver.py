import signal
import sys
import time

from . import config
from . import discovery
from . import proxy
from . import pulse
from . import util


class Receiver:
    def __init__(self, port=None, name=None):
        cfg = config.load()
        self.port = int(port or cfg["port"])
        self.name = name or cfg["display_name"]
        self.auth = cfg.get("auth", "anonymous")
        self.listen = (cfg.get("listen") or "").strip()
        self.allow_mode = cfg.get("allow_mode", "all")
        self.allowed_ips = list(cfg.get("allowed_ips", []))
        self.internal_port = int(cfg.get("internal_port") or 0) or (self.port + 1)
        self.module_id = None
        self.publisher = None
        self.proxy = None
        self.running = True

    def _module_args(self):
        # In "specific" mode the PipeWire module is bound to loopback only and
        # the public port is served by the allowlist proxy.
        if self.allow_mode == "specific":
            args = [f"port={self.internal_port}", "listen=127.0.0.1"]
        else:
            args = [f"port={self.port}"]
            if self.listen:
                args.append(f"listen={self.listen}")
        if self.auth == "anonymous":
            args.append("auth-anonymous=1")
        return args

    def load_module(self):
        for module in pulse.modules_of("module-native-protocol-tcp"):
            util.pactl(["unload-module", module["id"]], check=False)
        proc = util.pactl(["load-module", "module-native-protocol-tcp", *self._module_args()])
        self.module_id = proc.stdout.strip()
        return self.module_id

    def unload_module(self):
        if self.module_id:
            util.pactl(["unload-module", self.module_id], check=False)
            self.module_id = None

    def start_proxy(self):
        if self.allow_mode != "specific":
            return
        listen_host = self.listen or "0.0.0.0"
        self.proxy = proxy.TcpProxy(
            listen_host,
            self.port,
            "127.0.0.1",
            self.internal_port,
            allowed=self.allowed_ips,
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

    def _stop(self, *_):
        self.running = False

    def run(self):
        self.load_module()
        self.start_proxy()
        self.start_publisher()
        if self.allow_mode == "specific":
            where = self.listen or "all interfaces"
            print(
                f"[omarchy-cast] receiving on {where}:{self.port} as '{self.name}' "
                f"(auth={self.auth}, allowlist={len(self.allowed_ips)} IP(s))",
                flush=True,
            )
            if not self.allowed_ips:
                print(
                    "[omarchy-cast] warning: allowlist is empty; no client can connect. "
                    "Add IPs with 'omarchy-cast allow add <ip>'.",
                    file=sys.stderr,
                    flush=True,
                )
        else:
            where = self.listen or "all interfaces"
            print(
                f"[omarchy-cast] receiving on {where}:{self.port} as '{self.name}' "
                f"(auth={self.auth})",
                flush=True,
            )
        if self.auth == "anonymous":
            print(
                "[omarchy-cast] warning: anonymous auth grants unrestricted access to "
                "any client that can reach the listener. Set 'listen' to a LAN address, "
                "restrict the port with a firewall, or use cookie auth.",
                file=sys.stderr,
                flush=True,
            )
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        try:
            while self.running:
                time.sleep(2)
                if self.publisher is None or self.publisher.poll() is not None:
                    self.start_publisher()
        finally:
            print("[omarchy-cast] stopping", flush=True)
            self.stop_publisher()
            self.stop_proxy()
            self.unload_module()


def run(port=None, name=None):
    if not util.have("avahi-publish-service"):
        print("avahi-publish-service not found (install avahi)", file=sys.stderr)
        return 1
    Receiver(port=port, name=name).run()
    return 0
