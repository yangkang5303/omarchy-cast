import json
import os
from pathlib import Path

from . import DEFAULT_PORT

CONFIG_DIR = Path(
    os.environ.get("OMARCHY_CAST_CONFIG_DIR", Path.home() / ".config" / "omarchy-cast")
)
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    "display_name": None,
    "port": DEFAULT_PORT,
    "auth": "anonymous",
    "accept": True,
    "lan_only": True,
    "set_default": True,
    "move_streams": True,
}

LAN_ACL = "127.0.0.1;192.168.0.0/16;10.0.0.0/8;172.16.0.0/12"


def _hostname():
    return os.uname().nodename


def load():
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    if not cfg.get("display_name"):
        cfg["display_name"] = _hostname()
    return cfg


def save(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def set_value(key, value):
    cfg = load()
    if key not in DEFAULTS:
        raise KeyError(key)
    default = DEFAULTS[key]
    if default is None:
        cfg[key] = value
    elif isinstance(default, bool):
        cfg[key] = str(value).lower() in ("1", "true", "yes", "on")
    elif isinstance(default, int):
        cfg[key] = int(value)
    else:
        cfg[key] = value
    save(cfg)
    return cfg
