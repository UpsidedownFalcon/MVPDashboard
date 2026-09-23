"""GET /api/config/udp-target (PLAN_msd_management.md 4.5, user decision G).

A bare FastAPI app with only the config router and hand-set Settings: no DB,
no Redis, and no real DNS -- the resolver is monkeypatched at two levels, the
route's `_resolve_ipv4` and the event loop's `getaddrinfo` beneath it, so the
suite never touches the network and never waits on a resolver.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api.routes.config as config_routes
from api.routes.config import _resolve_ipv4, router as config_router
from common.config import Settings

DOMAIN = "dash.example.com"
ADDRINFO_7 = [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("203.0.113.7", 0))]


def _app(udp_public_ip: str = "", udp_port: int = 5005) -> FastAPI:
    # init kwargs outrank process env vars in pydantic-settings, so a developer
    # shell with UDP_PUBLIC_IP exported cannot leak into these cases
    app = FastAPI()
    app.include_router(config_router)
    app.state.settings = Settings(
        _env_file=None, domain=DOMAIN, udp_port=udp_port,
        udp_public_ip=udp_public_ip,
    )
    return app


async def _get(app: FastAPI) -> tuple[int, dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config/udp-target")
    return resp.status_code, resp.json()


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, result: str | None) -> list[str]:
    calls: list[str] = []

    async def fake(host: str) -> str | None:
        calls.append(host)
        return result

    monkeypatch.setattr(config_routes, "_resolve_ipv4", fake)
    return calls


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    config_routes._cache.clear()


# --- the route -----------------------------------------------------------------

async def test_resolution_is_cached_per_host_until_it_expires(monkeypatch) -> None:
    calls = _patch_resolver(monkeypatch, "203.0.113.7")
    app = _app()
    for _ in range(3):
        status, body = await _get(app)
        assert status == 200 and body["ip"] == "203.0.113.7" and body["source"] == "dns"
    assert calls == [DOMAIN]
    stamp, ip = config_routes._cache[DOMAIN]
    config_routes._cache[DOMAIN] = (stamp - config_routes.UDP_TARGET_CACHE_S - 1, ip)
    await _get(app)
    assert calls == [DOMAIN, DOMAIN]


async def test_an_unresolved_answer_is_cached_too(monkeypatch) -> None:
    calls = _patch_resolver(monkeypatch, None)
    app = _app()
    for _ in range(2):
        status, body = await _get(app)
        assert status == 200 and body["ip"] is None and body["source"] == "unresolved"
    assert calls == [DOMAIN]


async def test_env_override_wins_without_touching_dns(monkeypatch) -> None:
    calls = _patch_resolver(monkeypatch, "203.0.113.7")
    status, body = await _get(_app(udp_public_ip="198.51.100.4", udp_port=6001))
    assert status == 200
    assert body == {"ip": "198.51.100.4", "port": 6001, "source": "env"}
    assert calls == []


async def test_blank_override_resolves_domain(monkeypatch) -> None:
    calls = _patch_resolver(monkeypatch, "203.0.113.7")
    status, body = await _get(_app())
    assert status == 200
    assert body == {"ip": "203.0.113.7", "port": 5005, "source": "dns"}
    assert calls == [DOMAIN]


async def test_unresolved_domain_is_null_and_still_200(monkeypatch) -> None:
    _patch_resolver(monkeypatch, None)
    status, body = await _get(_app())
    assert status == 200
    assert body == {"ip": None, "port": 5005, "source": "unresolved"}


async def test_resolver_exception_reaches_the_client_as_unresolved(monkeypatch) -> None:
    """End to end with the real `_resolve_ipv4`: getaddrinfo raising must not
    become a 500 -- the page needs the port and the reason either way."""
    async def boom(host, port, *, family=0, type=0, proto=0, flags=0):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", boom)
    status, body = await _get(_app())
    assert status == 200
    assert body == {"ip": None, "port": 5005, "source": "unresolved"}


# --- _resolve_ipv4 --------------------------------------------------------------

async def test_resolve_ipv4_returns_the_first_address(monkeypatch) -> None:
    seen: list[tuple] = []

    async def fake(host, port, *, family=0, type=0, proto=0, flags=0):
        seen.append((host, port, family))
        return ADDRINFO_7 + [
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("203.0.113.8", 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake)
    assert await _resolve_ipv4(DOMAIN) == "203.0.113.7"
    # IPv4 only: the sleeve firmware's udp_ip is a dotted quad
    assert seen == [(DOMAIN, None, socket.AF_INET)]


@pytest.mark.parametrize("outcome", [
    socket.gaierror(socket.EAI_NONAME, "no such host"),
    OSError("resolver down"),
    [],                                   # resolved, but no IPv4 record
])
async def test_resolve_ipv4_swallows_failures(monkeypatch, outcome) -> None:
    async def fake(host, port, *, family=0, type=0, proto=0, flags=0):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake)
    assert await _resolve_ipv4(DOMAIN) is None


async def test_resolve_ipv4_gives_up_after_the_timeout(monkeypatch) -> None:
    async def hangs(host, port, *, family=0, type=0, proto=0, flags=0):
        await asyncio.sleep(30)
        return ADDRINFO_7

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", hangs)
    monkeypatch.setattr(config_routes, "UDP_TARGET_RESOLVE_TIMEOUT_S", 0.02)
    assert await _resolve_ipv4(DOMAIN) is None
