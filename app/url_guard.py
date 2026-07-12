"""Outbound-URL guard for gallery ingest -- blocks SSRF at request time.

The ingest pipeline fetches CALLER-SUPPLIED URLs server-side (the worker's
``httpx.get``), so without a gate an authenticated caller can point the
server at cloud metadata (169.254.169.254), loopback, RFC1918/ULA ranges,
or the object store itself. This module is deliberately import-neutral
(no route/worker imports) so both the route -- the PRIMARY gate, rejecting
bad payloads before anything is enqueued -- and the worker -- a
defense-in-depth re-check right before the fetch, guarding direct/legacy
enqueue paths -- can share the exact same policy.

NOTE: full SSRF hardening (DNS-rebinding defense via pinned-IP connect,
i.e. connecting to the exact address that was validated) is a follow-up;
this closes the obvious internal-target vectors. ``httpx.get`` does not
follow redirects by default, so redirect-laundering is not currently live.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Hostnames that must never be fetched even if they were to resolve
#: publicly (e.g. through a poisoned or split-horizon resolver).
_STATIC_BLOCKED_HOSTS = frozenset({"localhost"})


def _blocked_hosts() -> frozenset[str]:
    """Literal-hostname denylist: localhost plus the configured S3/MinIO
    endpoint host, which is internal by definition."""
    hosts = set(_STATIC_BLOCKED_HOSTS)
    from .config import get_settings

    endpoint = get_settings().s3_endpoint_url
    if isinstance(endpoint, str):
        endpoint_host = urlsplit(endpoint).hostname
        if endpoint_host:
            hosts.add(endpoint_host.lower())
    return frozenset(hosts)


def _validate_ingest_url(url: str) -> None:
    """Raise ValueError unless ``url`` is a plain-http(s) URL whose host
    resolves EXCLUSIVELY to public addresses.

    Rejects: non-http(s) schemes; missing/denylisted hostnames (localhost,
    the configured object-store host); hosts that do not resolve; and any
    resolution containing a private, loopback, link-local, reserved,
    multicast, or unspecified address (RFC1918, 169.254/16 metadata, ::1,
    fc00::/7, 0.0.0.0, ...). ALL resolved addresses must be public -- one
    internal A/AAAA record poisons the URL.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"malformed url: {url!r}") from exc

    if parts.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"url scheme must be http or https: {url!r}")
    if not host:
        raise ValueError(f"url has no hostname: {url!r}")
    if host in _blocked_hosts():
        raise ValueError(f"url host is internal: {url!r}")

    try:
        addrinfos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"url host does not resolve: {url!r}") from exc

    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        # IPv6 sockaddrs may carry a scope id ("fe80::1%eth0") -- strip it.
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"url resolves to a non-public address: {url!r}")
