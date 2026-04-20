import ipaddress
import re
import socket
from urllib.parse import urlparse

_SSRF_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),        # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),     # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),    # RFC1918
    ipaddress.ip_network("169.254.0.0/16"),    # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
]

_SSRF_BLOCKED_HOST_PATTERNS = re.compile(
    r"(^localhost$"
    r"|\.svc(\.cluster\.local)?$"
    r"|\.internal$"
    r"|kubernetes\.default"
    r")",
    re.IGNORECASE,
)


def _is_blocked_host(host: str) -> bool:
    if _SSRF_BLOCKED_HOST_PATTERNS.search(host):
        return True
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _SSRF_BLOCKED_NETWORKS)
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, None)
            return any(
                ipaddress.ip_address(r[4][0]) in net
                for r in resolved
                for net in _SSRF_BLOCKED_NETWORKS
            )
        except socket.gaierror:
            return False


def validate_external_url(url: str) -> str | None:
    """Return an error message if the URL targets an internal/private network, else None."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL."

    if parsed.scheme not in ("http", "https"):
        return "Only http and https URLs are allowed."

    host = parsed.hostname or ""
    if not host:
        return "Invalid URL: missing host."

    if _is_blocked_host(host):
        return "Access to private or internal networks is forbidden."

    return None


def validate_external_host(host: str) -> str | None:
    """Return an error message if the host targets an internal/private network, else None."""
    host = host.strip()
    if not host:
        return "Host is required."

    if _is_blocked_host(host):
        return "Access to private or internal networks is forbidden."

    return None
