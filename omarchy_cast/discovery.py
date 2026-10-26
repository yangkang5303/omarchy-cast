import subprocess

from . import SERVICE_TYPE
from . import config
from . import util


def _parse_txt(raw):
    props = {}
    cleaned = raw.replace('"', " ").strip()
    for token in cleaned.split():
        if "=" in token:
            key, value = token.split("=", 1)
            props[key] = value
    return props


def discover(timeout=6):
    proc = util.run(
        ["avahi-browse", "-rtpl", SERVICE_TYPE],
        timeout=timeout,
        check=False,
    )
    hosts = {}
    for line in proc.stdout.splitlines():
        if not line.startswith("="):
            continue
        parts = line.split(";")
        if len(parts) < 9:
            continue
        _, iface, proto, service, _type, _domain, hostname, address, port = parts[:9]
        if iface == "lo":
            continue
        txt = ";".join(parts[9:])
        props = _parse_txt(txt)
        # Published by the other host, so it is whatever that host chose:
        # keep it printable and bounded before it reaches a list or a label.
        name = props.get("name") or service
        name = "".join(c for c in name if c.isprintable())[:64] or service
        entry = {
            "name": name,
            "service": service,
            "hostname": hostname,
            "address": address,
            "port": int(port) if port.isdigit() else config.load()["port"],
            "proto": proto,
        }
        existing = hosts.get(name)
        if existing is None or (existing["proto"] != "IPv4" and proto == "IPv4"):
            hosts[name] = entry
    return list(hosts.values())


def resolve(target=None, hosts=None):
    hosts = hosts if hosts is not None else discover()
    if not hosts:
        raise LookupError("no hosts found on the local network")
    if target is None:
        if len(hosts) == 1:
            return hosts[0]
        raise LookupError("multiple hosts found; specify one: " + ", ".join(h["name"] for h in hosts))
    if target.isdigit():
        index = int(target)
        if 0 <= index < len(hosts):
            return hosts[index]
    lowered = target.lower()
    for host in hosts:
        if lowered in (
            host["name"].lower(),
            host["hostname"].lower(),
            host["address"].lower(),
        ):
            return host
    matches = [h for h in hosts if lowered in h["name"].lower() or lowered in h["address"]]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise LookupError("ambiguous target, matches: " + ", ".join(h["name"] for h in matches))
    raise LookupError(f"host not found: {target}")


def publish(display_name, port):
    return subprocess.Popen(
        [
            "avahi-publish-service",
            display_name,
            SERVICE_TYPE,
            str(port),
            f"name={display_name}",
            f"port={port}",
            "ver=1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
