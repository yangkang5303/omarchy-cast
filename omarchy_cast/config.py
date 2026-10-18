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
    "listen": "",
    "allow_mode": "all",
    "allowed_ips": [],
    "internal_port": 0,
    "set_default": True,
    "move_streams": True,
}


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
    elif isinstance(default, list):
        cfg[key] = _parse_list(value)
    else:
        cfg[key] = value
    save(cfg)
    return cfg


def _parse_list(value):
    """Parse a comma/space separated string into a list of trimmed items."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).replace(",", " ").split() if v.strip()]


def allow_mode():
    """Return the current allow mode: 'all' or 'specific'."""
    return load().get("allow_mode", "all")


def allowed_ips():
    """Return the current list of allowed remote IPs."""
    return list(load().get("allowed_ips", []))


def set_allow_mode(mode):
    """Set allow mode to 'all' (any reachable client) or 'specific' (allowlist)."""
    if mode not in ("all", "specific"):
        raise ValueError("allow mode must be 'all' or 'specific'")
    return set_value("allow_mode", mode)


def allow_add(ip):
    """Add an IP to the allowlist. Returns True if it was newly added."""
    ip = ip.strip()
    if not ip:
        raise ValueError("empty IP")
    cfg = load()
    ips = list(cfg.get("allowed_ips", []))
    if ip in ips:
        return False
    ips.append(ip)
    cfg["allowed_ips"] = ips
    save(cfg)
    return True


def allow_remove(ip):
    """Remove an IP from the allowlist. Returns True if it was present."""
    ip = ip.strip()
    cfg = load()
    ips = list(cfg.get("allowed_ips", []))
    if ip not in ips:
        return False
    ips.remove(ip)
    cfg["allowed_ips"] = ips
    save(cfg)
    return True
