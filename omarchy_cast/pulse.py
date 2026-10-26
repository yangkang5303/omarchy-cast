import os
import re
import time

from . import SINK_PREFIX
from . import util


def native_socket():
    """Path of the local PipeWire/Pulse native unix socket.

    This is the audio server's own socket, reachable only by its owner: the
    runtime directory above it is mode 0700, so another local user cannot
    open it the way they could open a loopback TCP port.
    """
    for part in os.environ.get("PULSE_SERVER", "").split(";"):
        part = part.strip()
        if part.startswith("unix:"):
            return part[len("unix:"):]
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "pulse", "native")


def wait_for_sink(name, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        names = {s["name"] for s in all_sinks()}
        if name in names:
            return True
        time.sleep(0.2)
    return False


def list_modules():
    proc = util.pactl(["list", "modules"])
    modules = []
    current = None
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Module #"):
            current = {"id": stripped.split("#", 1)[1].strip(), "name": "", "args": ""}
            modules.append(current)
        elif current is not None and stripped.startswith("Name:"):
            current["name"] = stripped.split(":", 1)[1].strip()
        elif current is not None and stripped.startswith("Argument:"):
            current["args"] = stripped.split(":", 1)[1].strip()
    return modules


def modules_of(name):
    return [m for m in list_modules() if m["name"] == name]


def slug(name):
    value = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return value or "host"


def sink_name_for(name):
    return SINK_PREFIX + slug(name)


def _nbsp(text):
    return text.replace(" ", "\u00a0")


def tunnel_sinks():
    sinks = {}
    for module in modules_of("module-tunnel-sink"):
        match = re.search(r"sink_name=(\S+)", module.get("args", ""))
        if match:
            sinks[match.group(1)] = module
    return sinks


def connect(address, port, name, set_default=False, move_streams=False):
    sink = sink_name_for(name)
    for module in modules_of("module-tunnel-sink"):
        if f"sink_name={sink}" in module.get("args", ""):
            util.pactl(["unload-module", module["id"]], check=False)
    description = _nbsp(f"Cast: {name}")
    args = [
        "load-module",
        "module-tunnel-sink",
        f"server={address}:{port}",
        f"sink_name={sink}",
        f"sink_properties=device.description={description}",
    ]
    util.pactl(args)
    wait_for_sink(sink)
    if set_default:
        util.pactl(["set-default-sink", sink], check=False)
    if move_streams:
        move_all_streams(sink)
    return sink


def disconnect(name):
    sink = sink_name_for(name)
    removed = False
    for module in modules_of("module-tunnel-sink"):
        if f"sink_name={sink}" in module.get("args", ""):
            util.pactl(["unload-module", module["id"]], check=False)
            removed = True
    return removed


def disconnect_all():
    count = 0
    for module in modules_of("module-tunnel-sink"):
        util.pactl(["unload-module", module["id"]], check=False)
        count += 1
    return count


def connected_names():
    result = {}
    for sink, module in tunnel_sinks().items():
        result[sink] = module
    return result


def move_all_streams(sink):
    proc = util.pactl(["list", "short", "sink-inputs"], check=False)
    moved = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if parts and parts[0].strip().isdigit():
            util.pactl(["move-sink-input", parts[0], sink], check=False)
            moved += 1
    return moved


def default_sink():
    proc = util.pactl(["get-default-sink"], check=False)
    return proc.stdout.strip()


def all_sinks():
    proc = util.pactl(["list", "short", "sinks"], check=False)
    sinks = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            sinks.append({"id": parts[0], "name": parts[1], "raw": line})
    return sinks
