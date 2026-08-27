"""`espn-sports-scraper` Actor (UTILS_SPEC §3). Also the image behind `tennis-scores-scraper`.

Flow: read input -> UA gate (one NFL scoreboard request; 403 = fail, nothing charged) ->
per league, per mode, build rows -> filters -> budget check -> ``push_data`` with the
event name -> one free ``league_summary`` row per league. Every failure of one league is a
free ``error`` row, never a raised exception.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from apify import Actor

from .client import CONCURRENCY, CORE, SITE, STANDINGS, EdgeBlocked, EspnClient, EspnError
from .leagues import League, resolve_league
from .normalize import (
    MAX_SPAN_DAYS,
    assert_clean,
    date_windows,
    dates_param,
    find_team,
    matches_status,
    matches_teams,
    parse_day,
    scoreboard_rows,
    site_teams,
    standings_rows,
    summary_row,
    team_game_row,
    team_row,
    years_in,
)

logger = logging.getLogger(__name__)

EVENT_GAME = "game"
EVENT_ROW = "row"
EVENT_SUMMARY = "summary"
MODES = ("scoreboard", "schedule", "standings", "teams", "summary")
UA_GATE_URL = SITE + "football/nfl/scoreboard"
BLOCKLIST_PATH = Path(__file__).resolve().parent.parent / "blocklist.txt"
BLOCK_KEYS = ("homeId", "awayId", "teamId", "tournamentId", "venueName")


def load_blocklist(path: Path | None = None) -> frozenset[str]:
    """TAKEDOWN.md mechanism: one identifier per line (league key or path, team, athlete or
    tournament id), ``#`` comments; applied before any request and before any push."""
    path = path or BLOCKLIST_PATH
    if not path.exists():
        return frozenset()
    lines = (ln.split("#", 1)[0].strip().lower() for ln in path.read_text().splitlines())
    return frozenset(ln for ln in lines if ln)


# --------------------------------------------------------------------------- charging


@dataclass
class Budget:
    """Owns every push. Charged rows count only when the SDK reports the push landed."""

    max_items: int = 0
    charged: int = 0
    pushed: int = 0
    free: int = 0
    stopped: str | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def allows(self, event: str) -> bool:
        if self.stopped:
            return False
        if self.max_items and self.pushed >= self.max_items:
            self.stopped = "maxItems reached"
            return False
        # The SDK returns how many MORE of this event fit in ACTOR_MAX_TOTAL_CHARGE_USD (it
        # shrinks with every charge; apify 4.0.1 _charging.py:516-523) — None when the event
        # has no price (local `apify run`, FREE record) or the limit is infinite.
        remaining = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(
            event
        )
        if remaining is not None and remaining <= 0:
            self.stopped = "ACTOR_MAX_TOTAL_CHARGE_USD reached"
            return False
        return True

    async def push_charged(self, row: dict[str, Any], event: str) -> bool:
        if not self.allows(event):
            return False
        assert_clean(row)
        result = await Actor.push_data(row, charged_event_name=event)
        charged = getattr(result, "charged_count", 0) or 0
        limit_reached = bool(getattr(result, "event_charge_limit_reached", False))
        if limit_reached and charged == 0:
            self.stopped = "ACTOR_MAX_TOTAL_CHARGE_USD reached"
            return False
        self.pushed += 1
        self.charged += charged
        self.counts[event] = self.counts.get(event, 0) + 1
        if limit_reached:
            self.stopped = "ACTOR_MAX_TOTAL_CHARGE_USD reached"
        return True

    async def push_free(self, row: dict[str, Any]) -> None:
        await Actor.push_data(row)
        self.free += 1


# --------------------------------------------------------------------------- input


@dataclass
class RunInput:
    leagues: list[str]
    mode: str
    date_from: date
    date_to: date
    teams: list[str]
    status: str
    season: int | None
    season_type: int | None
    event_ids: list[str]
    include_odds: bool
    max_items: int
    tz: ZoneInfo
    notices: list[str] = field(default_factory=list)


def parse_input(raw: dict[str, Any], *, today: date | None = None) -> RunInput:
    """Validate the Console input. Raises ValueError for input that cannot be run."""
    today = today or datetime.now(UTC).date()
    notices: list[str] = []
    leagues = [str(x) for x in (raw.get("leagues") or []) if str(x).strip()]
    if not leagues:
        raise ValueError("leagues is required")
    mode = str(raw.get("mode") or "scoreboard")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; one of {', '.join(MODES)}")
    try:
        date_from = parse_day(raw.get("dateFrom") or None) or today
        date_to = parse_day(raw.get("dateTo") or None) or date_from
    except ValueError as exc:
        raise ValueError(f"dates must be YYYY-MM-DD: {exc}") from exc
    if date_to < date_from:
        raise ValueError("dateTo is before dateFrom")
    span = (date_to - date_from).days + 1
    if span > MAX_SPAN_DAYS:
        date_to = date.fromordinal(date_from.toordinal() + MAX_SPAN_DAYS - 1)
        notices.append(
            f"date span of {span} days clamped to {MAX_SPAN_DAYS} (dateTo={date_to.isoformat()})"
        )
    tz_name = str(raw.get("timezone") or "UTC").strip() or "UTC"
    if tz_name not in available_timezones():
        notices.append(f"unknown timezone {tz_name!r}, dateLocal computed in UTC")
        tz_name = "UTC"
    season = raw.get("season")
    season_type = raw.get("seasonType")
    return RunInput(
        leagues=leagues,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        teams=[str(t) for t in (raw.get("teams") or []) if str(t).strip()],
        status=str(raw.get("status") or "all"),
        season=int(season) if season not in (None, "") else None,
        season_type=int(season_type) if season_type not in (None, "") else None,
        event_ids=[str(e) for e in (raw.get("eventIds") or []) if str(e).strip()],
        include_odds=bool(raw.get("includeOdds", True)),
        max_items=max(0, int(raw.get("maxItems") or 0)),
        tz=ZoneInfo(tz_name),
        notices=notices,
    )


# --------------------------------------------------------------------------- run


ODDS_KEYS = ("oddsProvider", "oddsDetails", "spread", "overUnder", "homeMoneyline", "awayMoneyline")


class Runner:
    def __init__(
        self,
        client: EspnClient,
        inp: RunInput,
        budget: Budget,
        blocklist: frozenset[str] = frozenset(),
    ):
        self.client = client
        self.inp = inp
        self.budget = budget
        self.blocklist = blocklist
        self._teams_cache: dict[str, list[dict[str, Any]]] = {}
        self._seen: set[str] = set()

    def blocked(self, row: dict[str, Any]) -> bool:
        return any(str(row.get(k)).lower() in self.blocklist for k in BLOCK_KEYS)

    def free_row(
        self, league: str, message: str, *, kind: str = "error", **extra: Any
    ) -> dict[str, Any]:
        return {
            "recordType": kind, "league": league, "mode": self.inp.mode,
            "window": f"{self.inp.date_from.isoformat()}..{self.inp.date_to.isoformat()}",
            "requests": extra.pop("requests", None), "itemsFound": extra.pop("itemsFound", None),
            "message": message,
        }  # fmt: skip

    async def run(self) -> None:
        for raw in self.inp.leagues:
            if self.budget.stopped:
                break
            league = resolve_league(raw)
            if league is None:
                await self.budget.push_free(self.free_row(raw, f"unknown league path {raw!r}"))
                continue
            if {league.key, league.path} & self.blocklist:
                message = f"league {league.key!r} removed on request (TAKEDOWN.md)"
                await self.budget.push_free(self.free_row(league.key, message, requests=0))
                continue
            before = self.client.requests
            try:
                found, pushed, note = await self.run_league(league)
            except EspnError as exc:
                row = self.free_row(league.key, exc.message, requests=self.client.requests - before)
                await self.budget.push_free(row)
                continue
            except EdgeBlocked:
                raise
            except Exception as exc:  # malformed feed payload: a free row, never a traceback
                logger.warning("league %s: unexpected payload (%r)", league.key, exc)
                message = f"unexpected payload: {type(exc).__name__}"
                row = self.free_row(league.key, message, requests=self.client.requests - before)
                await self.budget.push_free(row)
                continue
            message = note or (
                f"{pushed} rows pushed" if pushed else "no fixtures in window — off-season"
            )
            if self.budget.stopped:
                message = f"{message}; stopped: {self.budget.stopped}"
            row = self.free_row(
                league.key,
                message,
                kind="league_summary",
                requests=self.client.requests - before,
                itemsFound=found,
            )
            await self.budget.push_free(row)

    async def run_league(self, league: League) -> tuple[int, int, str | None]:
        mode = self.inp.mode
        if mode == "scoreboard":
            return await self.scoreboard(league)
        if mode == "schedule":
            return await self.schedule(league)
        if mode == "standings":
            return await self.standings(league)
        if mode == "teams":
            return await self.teams(league)
        return await self.summary(league)

    # -- game rows -----------------------------------------------------------------

    async def _emit_games(
        self, rows: list[dict[str, Any]], event: str = EVENT_GAME
    ) -> tuple[int, int]:
        found = pushed = 0
        for row in sorted(rows, key=lambda r: (r.get("date") or "", str(r.get("id")))):
            key = f"{row['league']}:{row['id']}"
            if key in self._seen:
                continue
            self._seen.add(key)
            found += 1
            if self.blocked(row):
                continue
            if not matches_teams(row, self.inp.teams) or not matches_status(row, self.inp.status):
                continue
            if not self.inp.include_odds:
                row.update(dict.fromkeys(ODDS_KEYS))
            if not await self.budget.push_charged(row, event):
                break
            pushed += 1
        return found, pushed

    async def scoreboard(self, league: League) -> tuple[int, int, str | None]:
        start, end = self.inp.date_from, self.inp.date_to
        url = f"{SITE}{league.path}/scoreboard"
        params: dict[str, Any] = {"limit": 1000}
        if league.groups:
            params["groups"] = league.groups
        if league.kind == "f1":
            queries = [dict(params, dates=str(y)) for y in years_in(start, end)]
        elif league.kind == "tennis":
            queries = [dict(params, dates=dates_param(d, d)) for d in _each_day(start, end)]
        else:
            queries = [dict(params, dates=dates_param(a, b)) for a, b in date_windows(start, end)]
        # One window at a time: a payload is parsed, normalised and dropped before the next
        # request (§3.4 step 8) — a 366-day NCAAB/tennis span gathered at once exceeds 256 MB.
        rows: list[dict[str, Any]] = []
        for q in queries:
            payload = await self.client.get_json(url, q)
            rows.extend(scoreboard_rows(payload, league, self.inp.tz, start, end))
            del payload
        found, pushed = await self._emit_games(rows)
        return found, pushed, None

    async def _site_teams(self, league: League) -> list[dict[str, Any]]:
        if league.key not in self._teams_cache:
            payload = await self.client.get_json(f"{SITE}{league.path}/teams", {"limit": 1000})
            self._teams_cache[league.key] = site_teams(payload)
        return self._teams_cache[league.key]

    async def schedule(self, league: League) -> tuple[int, int, str | None]:
        if league.athlete_sport:
            raise EspnError(None, "schedule mode needs a team sport")
        if not self.inp.teams:
            raise EspnError(None, "schedule mode needs teams")
        directory = await self._site_teams(league)
        found = pushed = 0
        missing: list[str] = []
        for query in self.inp.teams:
            team = find_team(directory, query)
            if team is None:
                missing.append(query)
                continue
            params: dict[str, Any] = {}
            if self.inp.season:
                params["season"] = self.inp.season
            if self.inp.season_type:
                params["seasontype"] = self.inp.season_type
            payload = await self.client.get_json(
                f"{SITE}{league.path}/teams/{team['id']}/schedule", params or None
            )
            rows = [team_game_row(e, league, self.inp.tz) for e in payload.get("events") or []]
            f, p = await self._emit_games(rows)
            found += f
            pushed += p
            if self.budget.stopped:
                break
        note = f"unknown teams: {', '.join(missing)}" if missing else None
        return found, pushed, note

    # -- standings / teams --------------------------------------------------------

    async def standings(self, league: League) -> tuple[int, int, str | None]:
        # level=3 walks down to divisions (NFL 8 x 4, MLB 6 x 5); without it the feed stops at
        # conferences. A no-op for single-table leagues (soccer, F1, college).
        params: dict[str, Any] = {"level": 3}
        if self.inp.season:
            params["season"] = self.inp.season
        doc = await self.client.get_json(f"{STANDINGS}{league.path}/standings", params)
        rows = standings_rows(doc, league, self.inp.season)
        if not rows:
            return 0, 0, "no standings for this league"
        pushed = 0
        for row in rows:
            if self.blocked(row):
                continue
            if not await self.budget.push_charged(row, EVENT_ROW):
                break
            pushed += 1
        return len(rows), pushed, None

    async def teams(self, league: League) -> tuple[int, int, str | None]:
        if league.athlete_sport:
            return 0, 0, "no teams for this league"
        base = f"{CORE}{league.sport}/leagues/{league.slug}"
        season = self.inp.season
        if not season:
            info = await self.client.get_json(base)
            season = (info.get("season") or {}).get("year")
            if not season:
                raise EspnError(None, "could not determine the current season")
        listing = await self.client.get_json(f"{base}/seasons/{season}/teams", {"limit": 200})
        refs = [
            it["$ref"].replace("http://", "https://", 1)
            for it in listing.get("items") or []
            if isinstance(it, dict) and isinstance(it.get("$ref"), str)
        ]
        docs = await _chunked([lambda r=r: self.client.get_json(r) for r in refs])
        rows = [team_row(d, league, season) for d in docs]
        rows.sort(key=lambda r: str(r.get("displayName")))
        pushed = 0
        for row in rows:
            if self.blocked(row):
                continue
            if not await self.budget.push_charged(row, EVENT_ROW):
                break
            pushed += 1
        return len(rows), pushed, None

    # -- summary -------------------------------------------------------------------

    async def summary(self, league: League) -> tuple[int, int, str | None]:
        ids = self._event_ids_for(league)
        if not ids:
            return 0, 0, "no eventIds for this league"
        found = pushed = 0
        unknown: list[str] = []
        for event_id in ids:
            try:
                doc = await self.client.get_json(
                    f"{SITE}{league.path}/summary", {"event": event_id}
                )
            except EspnError as exc:
                unknown.append(f"{event_id} ({exc.message})")
                continue
            found += 1
            row = summary_row(doc, league, self.inp.tz, event_id)
            if self.blocked(row):
                continue
            if not self.inp.include_odds:
                row.update(dict.fromkeys(ODDS_KEYS))
            if not await self.budget.push_charged(row, EVENT_SUMMARY):
                break
            pushed += 1
        note = f"unknown event ids: {'; '.join(unknown)}" if unknown else None
        return found, pushed, note

    def _event_ids_for(self, league: League) -> list[str]:
        single = len(self.inp.leagues) == 1
        out: list[str] = []
        for raw in self.inp.event_ids:
            if "/" in raw:
                prefix, _, event_id = raw.rpartition("/")
                if prefix in (league.key, league.path) and event_id:
                    out.append(event_id)
            elif single:
                out.append(raw)
        return out


def _each_day(start: date, end: date) -> list[date]:
    return [date.fromordinal(o) for o in range(start.toordinal(), end.toordinal() + 1)]


async def _chunked(
    fns: list[Callable[[], Coroutine[Any, Any, Any]]], size: int = CONCURRENCY
) -> list[Any]:
    """Run ``fns`` at most ``size`` at a time; the first failure cancels its siblings and is
    re-raised as itself (``asyncio.gather`` would keep firing the rest after a 403)."""
    out: list[Any] = []
    for i in range(0, len(fns), size):
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(fn()) for fn in fns[i : i + size]]
        except BaseExceptionGroup as eg:
            raise eg.exceptions[0] from None
        out.extend(t.result() for t in tasks)
    return out


def _run_notice(message: str, mode: Any, requests: int = 0) -> dict[str, Any]:
    """A free error row that belongs to the run rather than to one league."""
    return {
        "recordType": "error", "league": None, "mode": mode, "window": None,
        "requests": requests, "itemsFound": None, "message": message,
    }  # fmt: skip


# --------------------------------------------------------------------------- entry


async def main() -> None:
    async with Actor:
        raw = await Actor.get_input() or {}
        budget = Budget()
        try:
            inp = parse_input(raw)
        except ValueError as exc:
            await budget.push_free(_run_notice(f"invalid input: {exc}", raw.get("mode")))
            await Actor.fail(status_message=f"Invalid input: {exc}")
            return
        budget.max_items = inp.max_items
        for notice in inp.notices:
            await budget.push_free(_run_notice(notice, inp.mode))

        client = EspnClient()
        try:
            try:
                await client.get_json(UA_GATE_URL)
            except EdgeBlocked:
                message = "ESPN edge returned 403 — run stopped, nothing charged"
                await budget.push_free(_run_notice(message, inp.mode, client.requests))
                await Actor.fail(status_message="ESPN edge returned 403; nothing charged")
                return
            except EspnError as exc:
                logger.warning("UA gate request failed without a 403 (%s); continuing", exc.message)

            runner = Runner(client, inp, budget, load_blocklist())
            try:
                await runner.run()
            except EdgeBlocked:
                message = f"ESPN edge returned 403 — run stopped after {budget.pushed} rows"
                await budget.push_free(_run_notice(message, inp.mode, client.requests))
                await Actor.fail(status_message=f"{message}; nothing further charged")
                return
        finally:
            await client.aclose()

        summary = ", ".join(f"{v} {k}" for k, v in budget.counts.items()) or "0 charged rows"
        message = f"Done: {summary}; {budget.free} free rows; {client.requests} requests"
        if budget.stopped:
            message += f"; stopped: {budget.stopped}"
        Actor.log.info(message)
        await Actor.set_status_message(message)


if __name__ == "__main__":
    asyncio.run(main())
