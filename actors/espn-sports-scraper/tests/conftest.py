"""Shared fixtures: trimmed real ESPN payloads, a respx router for the three hosts and a
fake ``Actor`` that records pushes and charges the way apify 4.0.1 reports them."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from src import main as main_mod
from src.client import CORE, SITE, STANDINGS

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def fx():
    return load


@dataclass
class ChargeResult:
    event_charge_limit_reached: bool = False
    charged_count: int = 0
    chargeable_within_limit: dict[str, int | None] = field(default_factory=dict)


class FakeChargingManager:
    """``calculate_max_event_charge_count_within_limit`` is the REMAINING affordable count —
    it shrinks as the actor charges (apify 4.0.1 _charging.py:516-523); None = unlimited."""

    def __init__(self, actor: FakeActor, caps: dict[str, int | None] | None = None, *, priced=True):
        self.actor = actor
        self.caps = caps or {}
        self.priced = priced

    def calculate_max_event_charge_count_within_limit(self, event: str) -> int | None:
        cap = self.caps.get(event)
        return None if cap is None else max(0, cap - self.actor.charged.get(event, 0))


class FakeActor:
    """Just enough of ``apify.Actor`` for main.py: pushes, charges, fail, input, status."""

    def __init__(
        self,
        input_: dict[str, Any] | None = None,
        caps: dict[str, int | None] | None = None,
        *,
        priced=True,
    ):
        self.input = input_ or {}
        self.manager = FakeChargingManager(self, caps, priced=priced)
        self.pushed: list[tuple[dict[str, Any], str | None]] = []
        self.charged: dict[str, int] = {}
        self.failed: str | None = None
        self.status: str | None = None
        self.push_error: Exception | None = None
        self.log = _Log()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_input(self):
        return self.input

    def get_charging_manager(self):
        return self.manager

    async def push_data(self, data, *, charged_event_name=None):
        if self.push_error is not None:
            raise self.push_error
        self.pushed.append((data, charged_event_name))
        if charged_event_name is None:
            return ChargeResult()
        cap = self.manager.caps.get(charged_event_name)
        already = self.charged.get(charged_event_name, 0)
        if cap is not None and already >= cap:
            return ChargeResult(event_charge_limit_reached=True, charged_count=0)
        self.charged[charged_event_name] = already + 1
        reached = cap is not None and already + 1 >= cap
        return ChargeResult(
            event_charge_limit_reached=reached, charged_count=1 if self.manager.priced else 0
        )

    async def fail(self, *, exit_code=1, exception=None, status_message=None):
        self.failed = status_message or f"exit {exit_code}"

    async def set_status_message(self, message, **kwargs):
        self.status = message

    # -- helpers for assertions -----------------------------------------------------
    def rows(self, record_type: str | None = None) -> list[dict[str, Any]]:
        return [
            r for r, _ in self.pushed if record_type is None or r.get("recordType") == record_type
        ]

    def charged_rows(self) -> list[tuple[dict[str, Any], str]]:
        return [(r, e) for r, e in self.pushed if e is not None]


class _Log:
    def info(self, *a, **k):
        pass

    warning = info
    error = info


@pytest.fixture
def actor(monkeypatch):
    """Install a FakeActor into src.main and return it; tests set ``actor.input``."""
    fake = FakeActor()
    monkeypatch.setattr(main_mod, "Actor", fake)
    return fake


@pytest.fixture
def espn():
    """A respx router covering the three ESPN hosts, with the UA-gate route pre-mocked."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.get(url__regex=re.escape(SITE + "football/nfl/scoreboard") + r"$").mock(
            return_value=httpx.Response(200, json={"events": [], "leagues": []})
        )
        router.site = SITE  # type: ignore[attr-defined]
        router.standings = STANDINGS  # type: ignore[attr-defined]
        router.core = CORE  # type: ignore[attr-defined]
        yield router


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def html_403() -> httpx.Response:
    return httpx.Response(
        403,
        text=(FIXTURES / "forbidden_403.html").read_text(),
        headers={"content-type": "text/html"},
    )
