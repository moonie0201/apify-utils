"""SSRF guard and IP pin (spec §2.4 step 1).

Scheme http/https only, no localhost / `.internal` / `.local`, port in {80, 443, 8080, 8443},
every resolved address global and not multicast/reserved/loopback/link-local. Resolution goes
through `socket.getaddrinfo` because `127.1`, `0x7f000001` and `2130706433` all resolve to
127.0.0.1 there while `ipaddress` would reject them as malformed. The caller then requests
the pinned address with a `Host` header and `sni_hostname`, so DNS cannot be rebound between
the check and the connect. Hostnames in `blocklist.txt` (removal requests, TAKEDOWN.md) are
refused before any request, together with their subdomains.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
DEFAULT_PORTS = {"http": 80, "https": 443}
BLOCKLIST = Path(__file__).resolve().parent.parent / "blocklist.txt"


class BlockedUrl(ValueError):
    """The URL failed the guard. The message is a reason code, never the URL."""


@dataclass(frozen=True)
class PinnedUrl:
    url: str  # scheme://ip[:port]/path?query
    host: str  # hostname for the Host header
    host_header: str  # hostname[:port] exactly as the origin server expects it
    ip: str
    scheme: str


def _is_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
    )


def load_blocklist(path: Path = BLOCKLIST) -> frozenset[str]:
    """Hostnames removed at a rightholder's request (TAKEDOWN.md); one per line, `#` comments."""
    if not path.exists():
        return frozenset()
    hosts = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        host = line.split("#", 1)[0].strip().lower().rstrip(".")
        if host:
            hosts.add(host)
    return frozenset(hosts)


BLOCKED_HOSTS = load_blocklist()


def check_url(url: str) -> PinnedUrl:
    try:  # urlsplit / .hostname / .port raise ValueError on e.g. `http://[::1/a.pdf`
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError as exc:
        raise BlockedUrl("url") from exc
    if parts.scheme not in DEFAULT_PORTS:
        raise BlockedUrl("scheme")
    port = port or DEFAULT_PORTS[parts.scheme]
    if not host or host == "localhost" or host.endswith((".internal", ".local", ".localhost")):
        raise BlockedUrl("host")
    if any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS):
        raise BlockedUrl("blocklist")
    if port not in ALLOWED_PORTS:
        raise BlockedUrl("port")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise BlockedUrl("resolve") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for *_unused, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise BlockedUrl("resolve") from exc
        if not _is_allowed(ip):
            raise BlockedUrl("private")
        addresses.append(ip)
    if not addresses:
        raise BlockedUrl("resolve")
    # Prefer IPv4 when the name has both: Actor containers commonly lack IPv6 egress.
    ip = next((a for a in addresses if a.version == 4), addresses[0])
    netloc_ip = f"[{ip}]" if ip.version == 6 else str(ip)
    explicit = port != DEFAULT_PORTS[parts.scheme]
    if explicit:
        netloc_ip = f"{netloc_ip}:{port}"
    pinned = urlunsplit((parts.scheme, netloc_ip, parts.path or "/", parts.query, ""))
    host_header = f"{host}:{port}" if explicit else host
    return PinnedUrl(
        url=pinned, host=host, host_header=host_header, ip=str(ip), scheme=parts.scheme
    )
