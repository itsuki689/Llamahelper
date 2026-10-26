"""Convert server bind addresses into usable local client destinations."""
from ipaddress import ip_address


def client_host(host: str) -> str:
    host = host.strip().strip("[]")
    try:
        address = ip_address(host)
    except ValueError:
        return host
    if address.is_unspecified:
        return "127.0.0.1" if address.version == 4 else "::1"
    return host


def http_base_url(host: str, port: int) -> str:
    host = client_host(host)
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"
