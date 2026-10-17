import signal
import sys
import time

from . import config
from . import discovery
from . import pulse
from . import util


class Receiver:
    def __init__(self, port=None, name=None):
        cfg = config.load()
        self.port = int(port or cfg["port"])
        self.name = name or cfg["display_name"]
        self.auth = cfg.get("auth", "anonymous")
        self.lan_only = cfg.get("lan_only", True)
        self.module_id = None
        self.publisher = None
        self.running = True

    def _module_args(self):
        args = [f"port={self.port}"]
        if self.auth == "anonymous":
            args.append("auth-anonymous=1")
        if self.lan_only:
            args.append(f"auth-ip-acl={config.LAN_ACL}")
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
        self.start_publisher()
        print(
            f"[omarchy-cast] receiving on port {self.port} as '{self.name}' "
            f"(auth={self.auth}, lan_only={self.lan_only})",
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
            self.unload_module()


def run(port=None, name=None):
    if not util.have("avahi-publish-service"):
        print("avahi-publish-service not found (install avahi)", file=sys.stderr)
        return 1
    Receiver(port=port, name=name).run()
    return 0
