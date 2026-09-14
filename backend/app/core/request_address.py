"""Rate-limit identity for the explicitly trusted CloudFront -> ALB -> app path."""

import ipaddress
import os
import re

from starlette.requests import Request


def _ip(value: str):
    if "%" in value:
        raise ValueError("Scoped addresses are not public client addresses")
    address = ipaddress.ip_address(value)
    return address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address


def _edge_ip(value: str):
    try:
        return _ip(value)
    except ValueError:
        # ALB can optionally preserve its client's port in its appended entry.
        match = re.fullmatch(r"(?:\[([0-9a-fA-F:.]+)\]|([0-9.]+)):(\d{1,5})", value)
        if not match or not 1 <= int(match[3]) <= 65535:
            raise ValueError("Invalid proxy address") from None
        return _ip(match[1] or match[2])


def request_address(request: Request) -> str:
    """Ignore forwarded headers unless both the topology and transport peer are trusted.

    CloudFront appends the viewer IP and ALB appends the edge IP. Everything
    before those two entries can be supplied by the viewer and is never trusted.
    Requires ALB ingress restricted to CloudFront and app ingress to the ALB.
    Uvicorn must retain the real transport peer, not trust arbitrary proxy headers.
    """
    try:
        peer = _ip(request.client.host) if request.client else None
    except ValueError:
        peer = None
    fallback = str(peer) if peer else "unknown"
    if peer is None or os.getenv("TRUSTED_CLOUDFRONT_ALB_HOPS", "") != "2":
        return fallback
    try:
        networks = [ipaddress.ip_network(value.strip()) for value in os.getenv("TRUSTED_PROXY_CIDRS", "").split(",")]
        if any(network.prefixlen == 0 for network in networks) or not any(peer in network for network in networks):
            return fallback
        headers = request.headers.getlist("x-forwarded-for")
        if len(headers) != 1 or len(headers[0]) > 4096:
            return fallback
        chain = headers[0].rsplit(",", 2)
        if len(chain) < 2:
            return fallback
        _edge_ip(chain[-1].strip())
        return str(_ip(chain[-2].strip()))
    except ValueError:
        return fallback
