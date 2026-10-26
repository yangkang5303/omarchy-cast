"""What the receiver that is running has actually applied.

The config file says what the user asked for. This file says what the live
receiver enforces and which build is enforcing it, and the two can differ:
a receiver started by an older build keeps the allowlist it cached at
startup, so it would be wrong to tell the user their change is already in
effect. Only a receiver of this build re-reads its config.

The file is advisory -- every read and write tolerates failure -- and lives
in the runtime directory, so it does not survive a logout.
"""

import json
import os
import time
from pathlib import Path

from . import __version__
from . import config

# The running receiver rewrites this every tick. Anything older than this
# many seconds is treated as "no receiver of ours is running".
STALE_AFTER = 5.0


def directory():
    override = os.environ.get("OMARCHY_CAST_STATE_DIR")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "omarchy-cast"
    return config.CONFIG_DIR


def path():
    return directory() / "receiver-state.json"


def write(port, allow_mode, allowed_ips):
    """Record what this receiver is enforcing right now."""
    data = {
        "version": __version__,
        "pid": os.getpid(),
        "port": port,
        "allow_mode": allow_mode,
        "allowed_ips": list(allowed_ips),
        "updated": time.time(),
    }
    try:
        directory().mkdir(parents=True, exist_ok=True)
        tmp = path().with_name(path().name + ".tmp")
        tmp.write_text(json.dumps(data) + "\n")
        tmp.replace(path())
    except OSError:
        pass
    return data


def read():
    """The live receiver's state, or None when no fresh one was written."""
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    updated = data.get("updated")
    if not isinstance(updated, (int, float)) or isinstance(updated, bool):
        return None
    if time.time() - updated > STALE_AFTER:
        return None
    return data


def clear():
    try:
        path().unlink()
    except OSError:
        pass


def is_current(data):
    """True when the receiver that wrote this state is this build.

    Only then does a config change reach it on its own; an older build has
    no live reload and has to be restarted.
    """
    return bool(data) and data.get("version") == __version__


def stale_receiver(active=None):
    """True when a receiver that cannot apply config changes is running.

    ``active`` is whether the service is up, which settles the case where no
    state file is there to read: an older build writes none, so a unit that
    is active without one is an older receiver.
    """
    live = read()
    if live:
        return not is_current(live)
    return bool(active)
