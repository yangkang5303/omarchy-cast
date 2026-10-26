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
    "listen": "",
    "allow_mode": "specific",
    "allowed_ips": [],
    "set_default": True,
    "move_streams": True,
}


def _hostname():
    return os.uname().nodename


def normalize_allow_mode(value):
    """Return a valid allow mode, falling back to the restrictive one.

    Anything that is not exactly ``"all"`` -- a missing key, a typo, a value
    left over from an older release -- resolves to ``"specific"``, so a config
    we cannot make sense of never opens the receiver to every host.
    """
    return "all" if value == "all" else "specific"


def load():
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    if not cfg.get("display_name"):
        cfg["display_name"] = _hostname()
    cfg["allow_mode"] = normalize_allow_mode(cfg.get("allow_mode"))
    return cfg


def save(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def set_value(key, value):
    cfg = load()
    if key not in DEFAULTS:
        raise KeyError(key)
    if key == "allow_mode" and value not in ("all", "specific"):
        raise ValueError("allow_mode must be 'specific' or 'all'")
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
    """Return the current allow mode: 'specific' (the default) or 'all'."""
    return load()["allow_mode"]


def allowed_ips():
    """Return the current list of allowed remote IPs."""
    return list(load().get("allowed_ips", []))


def set_allow_mode(mode):
    """Set allow mode to 'specific' (allowlist, the default) or 'all'."""
    return set_value("allow_mode", mode)


def accepts_nothing(cfg=None):
    """True when the receiver is restricted but no host has been allowed yet.

    In that state the receiver runs and advertises itself, but every incoming
    connection is refused -- which is what a fresh install looks like until the
    user picks the hosts they trust.
    """
    cfg = load() if cfg is None else cfg
    return normalize_allow_mode(cfg.get("allow_mode")) == "specific" and not cfg.get(
        "allowed_ips"
    )


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
