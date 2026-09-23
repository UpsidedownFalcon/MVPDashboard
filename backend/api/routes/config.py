"""Deployment facts the frontend needs to point a knee sleeve at this box
(PLAN_msd_management.md section 4.5, user decision G; schema section 3).

    GET /api/config/udp-target  ->  {"ip": str|null, "port": int,
                                     "source": "env"|"dns"|"unresolved"}

The Sleeve storage page shows "Streams to a.b.c.d:port (this dashboard / not
this dashboard)" and offers "Point at this dashboard", both of which need the
IPv4 the sleeves must send their datagrams to. That is UDP_PUBLIC_IP when the
operator set it, else whatever DOMAIN resolves to from inside the api container
-- which is the wrong address behind a proxy or CDN, hence the override
(TRD section 7). Not resolving is a valid answer, not an error: the page then
disables the button and says why, so this route never 5xxs over DNS.
"""

from __future__ import annotations

import asyncio
import socket
import time

from fastapi import APIRouter, Request

router = APIRouter()

# DNS budget for one request. The page refreshes this rarely (it is deployment
# state, not live data) and a hung resolver must not pin an api worker for the
# tens of seconds the OS resolver would allow before giving up.
UDP_TARGET_RESOLVE_TIMEOUT_S = 3.0

# Resolutions are remembered per host for this long. The answer only changes
# with a redeploy, and every uncached lookup pins a default-executor thread for
# the OS resolver's own timeout even after wait_for gives up (getaddrinfo cannot
# be cancelled), so a dead resolver must not cost one thread per page open.
UDP_TARGET_CACHE_S = 60.0
_cache: dict[str, tuple[float, str | None]] = {}


async def _resolve_ipv4(host: str) -> str | None:
    """First IPv4 address `host` resolves to, or None on ANY failure.

    Timeouts, NXDOMAIN, a resolver that is down and a host with only AAAA
    records all mean the same thing to the caller: the dashboard cannot tell
    the sleeve where to stream, so say so instead of guessing.
    """
    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=socket.AF_INET),
            UDP_TARGET_RESOLVE_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 -- unresolved is an answer, not a fault
        return None
    for _family, _type, _proto, _canonname, sockaddr in infos:
        return str(sockaddr[0])
    return None


async def _resolve_ipv4_cached(host: str) -> str | None:
    """`_resolve_ipv4` behind a per-host cache (UDP_TARGET_CACHE_S); an
    unresolved answer is cached too, so a dead resolver is asked once a
    minute, not once per request."""
    hit = _cache.get(host)
    if hit is not None and time.monotonic() - hit[0] < UDP_TARGET_CACHE_S:
        return hit[1]
    ip = await _resolve_ipv4(host)
    _cache[host] = (time.monotonic(), ip)
    return ip


@router.get("/api/config/udp-target")
async def udp_target(request: Request) -> dict:
    settings = request.app.state.settings
    if settings.udp_public_ip:
        return {"ip": settings.udp_public_ip, "port": settings.udp_port,
                "source": "env"}
    ip = await _resolve_ipv4_cached(settings.domain)
    return {"ip": ip, "port": settings.udp_port,
            "source": "dns" if ip is not None else "unresolved"}
