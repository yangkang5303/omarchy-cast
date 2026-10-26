import argparse
import json
import sys

from . import DEFAULT_PORT, SERVICE_TYPE, __version__
from . import config
from . import discovery
from . import pulse
from . import receiver
from . import service
from . import state
from . import util


def _print_table(rows, headers):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


def cmd_list(args):
    cfg = config.load()
    hosts = discovery.discover(timeout=args.timeout)
    connected = pulse.tunnel_sinks()
    if args.json:
        for host in hosts:
            host["connected"] = pulse.sink_name_for(host["name"]) in connected
        print(json.dumps(hosts, indent=2))
        return 0
    if not hosts:
        print(f"No hosts found advertising {SERVICE_TYPE}.")
        print("Start the receiver on the other machine: omarchy-cast enable-receiver")
        return 0
    rows = []
    for i, host in enumerate(hosts):
        is_conn = pulse.sink_name_for(host["name"]) in connected
        rows.append([i, host["name"], host["address"], host["port"], "connected" if is_conn else "-"])
    _print_table(rows, ["#", "NAME", "ADDRESS", "PORT", "STATUS"])
    print(f"\nthis host: {cfg['display_name']}  (receiver {'active' if service.is_active() else 'inactive'})")
    return 0


def cmd_connect(args):
    cfg = config.load()
    hosts = discovery.discover(timeout=args.timeout)
    host = discovery.resolve(args.target, hosts)
    set_default = args.default if args.default is not None else cfg.get("set_default", True)
    move = args.move if args.move is not None else cfg.get("move_streams", True)
    sink = pulse.connect(host["address"], host["port"], host["name"], set_default=set_default, move_streams=move)
    print(f"connected to {host['name']} ({host['address']}:{host['port']})")
    print(f"output device: {sink}")
    if set_default:
        print("set as default output")
    if move:
        print("moved current playback")
    return 0


def cmd_disconnect(args):
    if args.all:
        count = pulse.disconnect_all()
        print(f"disconnected {count} host(s)")
        return 0
    if not args.target:
        print("specify a host or --all", file=sys.stderr)
        return 2
    hosts = discovery.discover(timeout=args.timeout)
    try:
        host = discovery.resolve(args.target, hosts)
        name = host["name"]
    except LookupError:
        name = args.target
    if pulse.disconnect(name):
        print(f"disconnected from {name}")
        return 0
    print(f"not connected: {name}", file=sys.stderr)
    return 1


def _format_allowed(ips):
    return ", ".join(ips) if ips else "none (all refused)"


def cmd_status(args):
    cfg = config.load()
    connected = pulse.tunnel_sinks()
    allowed = list(cfg.get("allowed_ips", []))
    live = state.read()
    active = service.is_active()
    print(f"this host      : {cfg['display_name']}")
    print(f"receiver port  : {cfg['port']}")
    print(f"receiver active: {'yes' if active else 'no'}")
    print(f"receiver boot  : {'enabled' if service.is_enabled() else 'disabled'}")
    if live and not state.is_current(live):
        print(
            f"receiver build : {live.get('version', 'unknown')} running, "
            f"{__version__} installed -- restart to apply config changes"
        )
    elif active and not live:
        print(
            f"receiver build : older build running, {__version__} installed "
            "-- restart to apply config changes"
        )
    print(f"allow mode     : {cfg['allow_mode']}")
    if cfg["allow_mode"] == "specific":
        print(f"allowed hosts  : {_format_allowed(allowed)}")
        # What the live process enforces: the config only once it has read it
        # back, so a difference means an older receiver is still running.
        if live and live.get("allow_mode") != cfg["allow_mode"]:
            print(f"enforcing now  : allow mode {live.get('allow_mode')}")
        elif live and live.get("allowed_ips") != allowed:
            print(f"enforcing now  : {_format_allowed(live.get('allowed_ips') or [])}")
    print(f"default sink   : {pulse.default_sink() or '-'}")
    print("tunnel sinks   :")
    if connected:
        for sink in connected:
            print(f"  - {sink}")
    else:
        print("  (none)")
    return 0


def cmd_receive(args):
    return receiver.run(port=args.port, name=args.name)


def cmd_service(action):
    def inner(args):
        getattr(service, action)()
        print(f"receiver {action}")
        if action in ("start", "enable") and config.accepts_nothing():
            print(
                "note: no host is allowed yet, so the receiver refuses every "
                "connection. Allow a host with 'omarchy-cast allow add <ip>'.",
                file=sys.stderr,
            )
        return 0

    return inner


def cmd_config(args):
    cfg = config.load()
    if args.set:
        key, value = args.set
        try:
            cfg = config.set_value(key, value)
        except KeyError:
            print(f"unknown key: {key}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(cfg, indent=2))
    return 0


def cmd_allow_list(args):
    cfg = config.load()
    live = state.read()
    data = {
        "mode": cfg["allow_mode"],
        "ips": list(cfg.get("allowed_ips", [])),
        # What the receiver that is running has actually applied, so a front
        # end can show the difference instead of implying there is none.
        "stale": bool(_stale_receiver_warning()),
        "live": None
        if not live
        else {
            "version": live.get("version"),
            "current": state.is_current(live),
            "mode": live.get("allow_mode"),
            "ips": list(live.get("allowed_ips") or []),
        },
    }
    if args.json:
        print(json.dumps(data, indent=2))
        _warn_stale_receiver()
        return 0
    print(f"mode: {data['mode']}")
    if data["ips"]:
        for ip in data["ips"]:
            print(f"  - {ip}")
    elif data["mode"] == "specific":
        print("  (no allowed IPs: every incoming connection is refused)")
    else:
        print("  (no allowed IPs; mode is 'all', so any reachable host may cast)")
    _warn_stale_receiver()
    return 0


def _stale_build_warning(version):
    """What to say when the receiver that is running cannot apply the change.

    A receiver started by an older build -- after an upgrade, before anything
    restarted it -- keeps the allowlist it read at startup, so the change sits
    in the config file and the live process goes on enforcing the old one.
    """
    running = f"version {version}" if version else "an older build"
    return (
        f"warning: the running receiver is {running}, which keeps the allowlist "
        "it read at startup, so this change does NOT reach it. Restart it to "
        "apply: omarchy-cast stop-receiver && omarchy-cast start-receiver"
    )


def _stale_receiver_warning():
    """The warning to show when the live receiver cannot apply a config change.

    None when the receiver that is running is this build, or when nothing is
    running at all.
    """
    try:
        active = service.is_active()
    except util.CommandError:
        active = False  # no systemctl: nothing of ours is being managed here
    if not state.stale_receiver(active=active):
        return None
    live = state.read()
    return _stale_build_warning(live.get("version") if live else None)


def _warn_stale_receiver():
    warning = _stale_receiver_warning()
    if warning:
        print(warning, file=sys.stderr)
    return bool(warning)


def _apply_note():
    """Say whether the change reaches the receiver that is already running."""
    if _warn_stale_receiver():
        return
    if state.read():
        print(f"the running receiver applies this within {receiver.POLL_INTERVAL:g}s")


def cmd_allow_add(args):
    try:
        added = config.allow_add(args.ip)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"added {args.ip}" if added else f"{args.ip} already allowed")
    if added:
        _apply_note()
    return 0


def cmd_allow_remove(args):
    removed = config.allow_remove(args.ip)
    print(f"removed {args.ip}" if removed else f"{args.ip} not in allowlist")
    if removed:
        _apply_note()
    return 0


def cmd_allow_mode(args):
    try:
        config.set_allow_mode(args.mode)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"allow mode set to {args.mode}")
    if args.mode == "all":
        print(
            "warning: any host that can reach the receiver port may now cast to "
            "this machine. Restrict it again with "
            "'omarchy-cast allow mode specific'.",
            file=sys.stderr,
        )
    elif config.accepts_nothing():
        print("no host is allowed yet; add one with 'omarchy-cast allow add <ip>'")
    _apply_note()
    return 0


def cmd_gui(args):
    from . import gui

    return gui.main()


def cmd_version(args):
    print(__version__)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="omarchy-cast", description="Play audio on another computer on your LAN.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("list", help="discover hosts on the local network")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=float, default=6)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("connect", help="route this host's audio to a remote host")
    p.add_argument("target", nargs="?", help="host name, address, or index from 'list'")
    p.add_argument("--default", dest="default", action="store_true", default=None)
    p.add_argument("--no-default", dest="default", action="store_false")
    p.add_argument("--move", dest="move", action="store_true", default=None)
    p.add_argument("--no-move", dest="move", action="store_false")
    p.add_argument("--timeout", type=float, default=6)
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("disconnect", help="stop routing audio")
    p.add_argument("target", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--timeout", type=float, default=6)
    p.set_defaults(func=cmd_disconnect)

    p = sub.add_parser("status", help="show receiver and connection state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("receive", help="run the receiver in the foreground")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--name", default=None)
    p.set_defaults(func=cmd_receive)

    for action, help_text in (
        ("start", "start the receiver service"),
        ("stop", "stop the receiver service"),
        ("enable", "enable + start the receiver service at login"),
        ("disable", "disable the receiver service"),
    ):
        p = sub.add_parser(f"{action}-receiver", help=help_text)
        p.set_defaults(func=cmd_service(action))

    p = sub.add_parser("config", help="show or update configuration")
    p.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"))
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("allow", help="manage which remote IPs may cast to this host")
    allow_sub = p.add_subparsers(dest="allow_command")

    p = allow_sub.add_parser("list", help="show allow mode and allowed IPs")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_allow_list)

    p = allow_sub.add_parser("add", help="allow a remote IP to cast to this host")
    p.add_argument("ip")
    p.set_defaults(func=cmd_allow_add)

    p = allow_sub.add_parser("remove", help="stop allowing a remote IP")
    p.add_argument("ip")
    p.set_defaults(func=cmd_allow_remove)

    p = allow_sub.add_parser(
        "mode", help="set allow mode: 'specific' (default) or 'all'"
    )
    p.add_argument("mode", choices=("specific", "all"))
    p.set_defaults(func=cmd_allow_mode)

    p = sub.add_parser("gui", help="launch the graphical interface")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("install", help="install launcher, service, and desktop entry")
    p.add_argument("--enable", action="store_true")
    p.set_defaults(func=lambda a: service.main_install(sys.argv))
    p = sub.add_parser("uninstall", help="remove installed files")
    p.set_defaults(func=lambda a: service.main_uninstall(sys.argv))

    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except util.CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
