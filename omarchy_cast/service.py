import shutil
import sys
from pathlib import Path

from . import APP_ID, APP_NAME
from . import util

APP_HOME = Path.home() / ".local" / "share" / "omarchy-cast"
BIN_PATH = Path.home() / ".local" / "bin" / "omarchy-cast"
UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / "omarchy-cast-receiver.service"
DESKTOP_PATH = Path.home() / ".local" / "share" / "applications" / f"{APP_ID}.desktop"
UNIT_NAME = "omarchy-cast-receiver"

LAUNCHER = """#!/usr/bin/env python3
import sys
sys.path.insert(0, {app_home!r})
from omarchy_cast.cli import main
sys.exit(main())
"""

UNIT = """[Unit]
Description=Omarchy Cast audio receiver
After=pipewire-pulse.service avahi-daemon.service
Wants=pipewire-pulse.service

[Service]
Type=simple
ExecStart={bin_path} receive
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name={name}
Comment=Play audio on another computer on your local network
Exec={bin_path} gui
Icon=audio-speakers
Terminal=false
Categories=Audio;AudioVideo;Network;
"""


def systemctl(*args, check=False):
    return util.run(["systemctl", "--user", *args], check=check)


def is_active():
    return systemctl("is-active", UNIT_NAME).stdout.strip() == "active"


def is_enabled():
    return systemctl("is-enabled", UNIT_NAME).stdout.strip() == "enabled"


def start():
    systemctl("start", UNIT_NAME, check=True)


def stop():
    systemctl("stop", UNIT_NAME, check=True)


def enable(now=True):
    systemctl("enable", *(["--now"] if now else []), UNIT_NAME, check=True)


def disable(now=True):
    systemctl("disable", *(["--now"] if now else []), UNIT_NAME, check=True)


def _copy_package():
    source = Path(__file__).resolve().parent
    target = APP_HOME / "omarchy_cast"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def install(enable_now=False):
    APP_HOME.mkdir(parents=True, exist_ok=True)
    (Path.home() / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_PATH.parent.mkdir(parents=True, exist_ok=True)

    _copy_package()
    BIN_PATH.write_text(LAUNCHER.format(app_home=str(APP_HOME)))
    BIN_PATH.chmod(0o755)
    UNIT_PATH.write_text(UNIT.format(bin_path=str(BIN_PATH)))
    DESKTOP_PATH.write_text(DESKTOP.format(name=APP_NAME, bin_path=str(BIN_PATH)))

    systemctl("daemon-reload")
    if enable_now:
        enable(now=True)
    return {
        "app_home": str(APP_HOME),
        "bin": str(BIN_PATH),
        "unit": str(UNIT_PATH),
        "desktop": str(DESKTOP_PATH),
    }


def uninstall():
    disable(now=True)
    for path in (UNIT_PATH, DESKTOP_PATH, BIN_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    if APP_HOME.exists():
        shutil.rmtree(APP_HOME)
    systemctl("daemon-reload")
    return True


def main_install(argv):
    enable_now = "--enable" in argv
    info = install(enable_now=enable_now)
    print(f"installed to {info['app_home']}")
    print(f"launcher: {info['bin']}")
    print(f"service:  {info['unit']}")
    print(f"desktop:  {info['desktop']}")
    if not enable_now:
        print("\nRun 'omarchy-cast enable-receiver' to receive audio on this host.")
    if not util.have("avahi-publish-service"):
        print("warning: avahi-publish-service missing; install the 'avahi' package", file=sys.stderr)
    return 0


def main_uninstall(argv):
    uninstall()
    print("uninstalled")
    return 0
