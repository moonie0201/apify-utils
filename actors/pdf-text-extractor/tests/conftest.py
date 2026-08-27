from __future__ import annotations

import math
import os
import socket
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import make_fixtures  # noqa: E402

PUBLIC_IP = "93.184.216.34"
FIXTURE_HOST = "files.test"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures() -> dict[str, Path]:
    return make_fixtures.build_all(FIXTURE_DIR)


@pytest.fixture
def resolver(monkeypatch):
    """Names under .test resolve to a public address; literals go through the real resolver."""
    real = socket.getaddrinfo
    table = {FIXTURE_HOST: PUBLIC_IP, "other.test": "93.184.216.35", "private.test": "10.0.0.1"}

    def fake(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return table


def fixture_url(name: str, host: str = FIXTURE_HOST) -> str:
    return f"https://{host}/{name}"


def serve(router: respx.MockRouter, fixtures: dict[str, Path], *names: str, ip: str = PUBLIC_IP):
    for name in names:
        router.get(f"https://{ip}/{name}").mock(
            return_value=httpx.Response(
                200,
                content=fixtures[name].read_bytes(),
                headers={"content-type": "application/pdf"},
            )
        )


@pytest.fixture
def no_wait(monkeypatch):
    """Skip the real per-host gap and retry sleeps; record them."""
    from src import fetch

    sleeps: list[float] = []
    now = [1000.0]

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(fetch, "sleep", fake_sleep)
    monkeypatch.setattr(fetch, "clock", lambda: now[0])
    return SimpleNamespace(sleeps=sleeps, now=now)


class FakeChargingManager:
    """Mirrors apify 4.0.1 ChargingManager semantics: None = no price or infinite limit."""

    def __init__(self, prices: dict[str, float] | None, max_total: float | None):
        self.prices = prices or {}
        self.max_total = max_total
        self.charged: dict[str, int] = {}

    def total(self) -> float:
        return sum(self.prices.get(e, 0) * n for e, n in self.charged.items())

    def calculate_max_event_charge_count_within_limit(self, event: str) -> int | None:
        price = self.prices.get(event)
        if not price or self.max_total is None:
            return None
        return max(0, math.floor((self.max_total - self.total()) / price + 1e-9))

    def is_event_charge_limit_reached(self, event: str) -> bool:
        cap = self.calculate_max_event_charge_count_within_limit(event)
        return cap is not None and cap < 1

    def charge(self, event: str, count: int):
        cap = self.calculate_max_event_charge_count_within_limit(event)
        charged = count if cap is None else min(cap, count)
        if event in self.prices:
            self.charged[event] = self.charged.get(event, 0) + charged
        return SimpleNamespace(
            event_charge_limit_reached=self.is_event_charge_limit_reached(event),
            charged_count=charged if event in self.prices else 0,
            chargeable_within_limit={},
        )


class FakeActor:
    def __init__(
        self,
        input: dict,
        *,
        prices: dict[str, float] | None = None,
        max_total: float | None = None,
        timeout_at=None,
        memory: int = 1024,
    ):
        self.input = input
        self.cm = FakeChargingManager(prices, max_total)
        self.configuration = SimpleNamespace(timeout_at=timeout_at, memory_mbytes=memory)
        self.dataset: list[dict] = []
        self.charge_calls: list[tuple[str, int]] = []
        self.status: str | None = None
        self.failed: dict | None = None

    async def get_input(self):
        return self.input

    def get_charging_manager(self):
        return self.cm

    async def push_data(self, data, *, charged_event_name: str | None = None):
        items = data if isinstance(data, list) else [data]
        if charged_event_name is None:
            self.dataset.extend(items)
            return SimpleNamespace(
                event_charge_limit_reached=False, charged_count=0, chargeable_within_limit={}
            )
        cap = self.cm.calculate_max_event_charge_count_within_limit(charged_event_name)
        n = len(items) if cap is None else min(cap, len(items))
        self.dataset.extend(items[:n])
        return self.cm.charge(charged_event_name, n)

    async def charge(self, event: str, *, count: int = 1):
        self.charge_calls.append((event, count))
        return self.cm.charge(event, count)

    async def set_status_message(self, message: str, **kwargs):
        self.status = message

    async def fail(self, *, exit_code: int = 1, exception=None, status_message: str | None = None):
        self.failed = {"exit_code": exit_code, "status_message": status_message}

    # helpers
    def rows(self, record_type: str) -> list[dict]:
        return [r for r in self.dataset if r["recordType"] == record_type]


@pytest.fixture
def fake_tesseract(tmp_path, monkeypatch):
    """A stub `tesseract` that prints the PNG size and fixed text, so the OCR path runs
    end-to-end (in the child process too) without the real binary."""
    script = tmp_path / "tesseract"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from PIL import Image\n"
        "im = Image.open(sys.argv[1])\n"
        "print(f'SIZE {im.size[0]}x{im.size[1]}')\n"
        "print('HELLO OCR WORLD 12345')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TESSERACT_CMD", str(script))
    from src import extract

    monkeypatch.setattr(extract, "TESSERACT_CMD", str(script))
    return script


@pytest.fixture
def work_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    return d


def has_tesseract() -> bool:
    from shutil import which

    return which(os.environ.get("TESSERACT_CMD", "tesseract")) is not None
