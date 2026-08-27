"""Payload -> flat rows (UTILS_SPEC §3.3). Pure functions, no I/O.

One game-row shape for every sport: team sports fill ``home*``/``away*`` from the two
competitors; tennis and UFC put the two athletes there; golf writes one ``leaderboard`` row
per player with ``position``/``scoreDisplay``; F1 writes one ``session`` row per FP/Qual/Race
with the winner in the ``home*`` slots. Nothing editorial is ever copied: no ``headlines``,
``article``, ``news``, ``videos``, ``injuries``, rosters or bios.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .leagues import KIND_F1, KIND_GOLF, KIND_TENNIS, KIND_UFC, League

MAX_SPAN_DAYS = 366
WINDOW_DAYS = 7

# status.type.name -> our status. Anything unknown falls back on status.type.state.
STATUS_MAP: dict[str, str] = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "live",
    "STATUS_HALFTIME": "live",
    "STATUS_END_PERIOD": "live",
    "STATUS_FIRST_HALF": "live",
    "STATUS_SECOND_HALF": "live",
    "STATUS_OVERTIME": "live",
    "STATUS_SHOOTOUT": "live",
    "STATUS_EXTRA_TIME": "live",
    "STATUS_PLAY_COMPLETE": "live",
    "STATUS_FINAL": "final",
    "STATUS_FINAL_OT": "final",
    "STATUS_FINAL_PEN": "final",
    "STATUS_FINAL_AET": "final",
    "STATUS_FULL_TIME": "final",
    "STATUS_RETIRED": "final",
    "STATUS_WALKOVER": "final",
    "STATUS_FORFEIT": "final",
    "STATUS_ABANDONED": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "canceled",
    "STATUS_CANCELLED": "canceled",
    "STATUS_SUSPENDED": "suspended",
    "STATUS_DELAYED": "delayed",
    "STATUS_RAIN_DELAY": "delayed",
}
_STATE_FALLBACK = {"pre": "scheduled", "in": "live", "post": "final"}

GAME_COLUMNS = (
    "recordType", "type", "id", "uid", "league", "leagueName", "sport", "espnPath",
    "season", "seasonType", "week", "tournament", "tournamentId", "round", "grouping",
    "name", "shortName", "date", "dateLocal", "endDate", "status", "statusDetail",
    "completed", "period", "clock", "competitorType",
    "homeId", "homeName", "homeAbbr", "homeShortName", "homeLogo", "homeScore", "homeWinner",
    "homeRecord", "homeRank", "homeCountry", "homeLinescores",
    "awayId", "awayName", "awayAbbr", "awayShortName", "awayLogo", "awayScore", "awayWinner",
    "awayRecord", "awayRank", "awayCountry", "awayLinescores",
    "winnerId", "resultText", "position", "scoreDisplay",
    "venueName", "venueCity", "venueState", "venueCountry", "venueIndoor", "court",
    "attendance", "neutralSite", "broadcasts",
    "oddsProvider", "oddsDetails", "spread", "overUnder", "homeMoneyline", "awayMoneyline",
    "espnUrl", "scrapedAt", "sourceHost",
)  # fmt: skip

FORBIDDEN_KEYS = frozenset(
    {"article", "headlines", "videos", "injuries", "news", "roster", "rosters"}
)


# --------------------------------------------------------------------------- dates


def parse_day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def date_windows(start: date, end: date, *, chunk: int = WINDOW_DAYS) -> list[tuple[date, date]]:
    """Inclusive 7-day windows covering [start, end]; end is clamped to a 366-day span."""
    end = min(end, start + timedelta(days=MAX_SPAN_DAYS - 1))
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk - 1), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def dates_param(a: date, b: date) -> str:
    return a.strftime("%Y%m%d") if a == b else f"{a:%Y%m%d}-{b:%Y%m%d}"


def years_in(start: date, end: date) -> list[int]:
    return list(range(start.year, end.year + 1))


def in_window(iso: str | None, start: date, end: date) -> bool:
    day = parse_iso(iso)
    return day is not None and start <= day.date() <= end


def parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def local_date(iso: str | None, tz: ZoneInfo) -> str | None:
    dt = parse_iso(iso)
    return dt.astimezone(tz).date().isoformat() if dt else None


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- helpers


def map_status(status: dict[str, Any] | None) -> tuple[str, str | None, bool]:
    t = (status or {}).get("type") or {}
    name = STATUS_MAP.get(str(t.get("name") or ""))
    if name is None:
        name = _STATE_FALLBACK.get(str(t.get("state") or ""), "scheduled")
    return name, t.get("detail") or t.get("description"), bool(t.get("completed", name == "final"))


def _num(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else value
    if isinstance(value, dict):
        return _num(value.get("value", value.get("displayValue")))
    if isinstance(value, str):
        s = value.strip().replace("+", "")
        if s.upper() in ("E", "EVEN"):
            return 0
        try:
            f = float(s)
        except ValueError:
            return None
        return int(f) if f.is_integer() else f
    return None


def _linescores(items: Any) -> list[int | float | None] | None:
    if not isinstance(items, list) or not items:
        return None
    return [_num(x) for x in items]


def _record(competitor: dict[str, Any]) -> str | None:
    for r in competitor.get("records") or competitor.get("record") or []:
        if isinstance(r, dict) and r.get("type") == "total":
            return r.get("summary") or r.get("displayValue")
    recs = competitor.get("records") or competitor.get("record") or []
    if recs and isinstance(recs[0], dict):
        return recs[0].get("summary") or recs[0].get("displayValue")
    return None


def _rank(competitor: dict[str, Any]) -> int | None:
    cur = (competitor.get("curatedRank") or {}).get("current")
    return int(cur) if isinstance(cur, (int, float)) and 0 < cur < 99 else None


def _logo(team: dict[str, Any]) -> str | None:
    if team.get("logo"):
        return team["logo"]
    logos = team.get("logos") or []
    return logos[0].get("href") if logos and isinstance(logos[0], dict) else None


def _broadcasts(comp: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for b in comp.get("broadcasts") or []:
        if not isinstance(b, dict):
            continue
        for n in b.get("names") or []:
            names.append(str(n))
        short = (b.get("media") or {}).get("shortName")
        if short:
            names.append(str(short))
    return list(dict.fromkeys(names))


def _venue(v: dict[str, Any] | None) -> dict[str, Any]:
    v = v or {}
    addr = v.get("address") or {}
    return {
        "venueName": v.get("fullName") or v.get("displayName"),
        "venueCity": addr.get("city"),
        "venueState": addr.get("state"),
        "venueCountry": addr.get("country"),
        "venueIndoor": v.get("indoor"),
        "court": v.get("court"),
    }


def _espn_url(node: dict[str, Any]) -> str | None:
    links = node.get("links") or []
    for link in links:
        if isinstance(link, dict) and "summary" in (link.get("rel") or []):
            return link.get("href")
    return links[0].get("href") if links and isinstance(links[0], dict) else None


def _team_side(c: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    if not c:
        return {f"{prefix}{k}": None for k in _SIDE_KEYS}
    team = c.get("team") or {}
    return {
        f"{prefix}Id": team.get("id") or c.get("id"),
        f"{prefix}Name": team.get("displayName") or team.get("name"),
        f"{prefix}Abbr": team.get("abbreviation"),
        f"{prefix}ShortName": team.get("shortDisplayName") or team.get("name"),
        f"{prefix}Logo": _logo(team),
        f"{prefix}Score": _num(c.get("score")),
        f"{prefix}Winner": c.get("winner"),
        f"{prefix}Record": _record(c),
        f"{prefix}Rank": _rank(c),
        f"{prefix}Country": None,
        f"{prefix}Linescores": _linescores(c.get("linescores")),
    }


def _athlete_name(c: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """(displayName, shortName, country) for a singles player or a doubles pair."""
    roster = c.get("roster")
    if isinstance(roster, dict) and roster.get("displayName"):
        players = roster.get("athletes") or []
        flags = [((p.get("flag") or {}).get("alt")) for p in players if isinstance(p, dict)]
        return (
            roster.get("displayName"),
            roster.get("shortDisplayName"),
            " / ".join(f for f in flags if f) or None,
        )
    a = c.get("athlete") or {}
    return (
        a.get("displayName") or a.get("fullName"),
        a.get("shortName"),
        (a.get("flag") or {}).get("alt"),
    )


def _athlete_side(c: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    if not c:
        return {f"{prefix}{k}": None for k in _SIDE_KEYS}
    name, short, country = _athlete_name(c)
    return {
        f"{prefix}Id": c.get("id"),
        f"{prefix}Name": name,
        f"{prefix}Abbr": None,
        f"{prefix}ShortName": short,
        f"{prefix}Logo": ((c.get("athlete") or {}).get("flag") or {}).get("href"),
        f"{prefix}Score": _num(c.get("score")),
        f"{prefix}Winner": c.get("winner"),
        f"{prefix}Record": _record(c),
        f"{prefix}Rank": _rank(c),
        f"{prefix}Country": country,
        f"{prefix}Linescores": _linescores(c.get("linescores")),
    }


_SIDE_KEYS = (
    "Id", "Name", "Abbr", "ShortName", "Logo", "Score", "Winner", "Record", "Rank",
    "Country", "Linescores",
)  # fmt: skip


def _vs(home: Any, away: Any) -> str:
    return f"{home} vs {away}"


def _split(competitors: list[dict[str, Any]]) -> tuple[dict | None, dict | None]:
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None and away is None and competitors:
        ordered = sorted(competitors, key=lambda c: c.get("order") or 0)
        home, away = ordered[0], (ordered[1] if len(ordered) > 1 else None)
    return home, away


def extract_odds(comp: dict[str, Any]) -> dict[str, Any]:
    empty = {
        "oddsProvider": None, "oddsDetails": None, "spread": None, "overUnder": None,
        "homeMoneyline": None, "awayMoneyline": None,
    }  # fmt: skip
    odds = comp.get("odds")
    if not isinstance(odds, list) or not odds or not isinstance(odds[0], dict):
        return empty
    o = odds[0]
    ml = o.get("moneyline") or {}

    def side_ml(side: str, legacy: str) -> int | float | None:
        close = ((ml.get(side) or {}).get("close") or {}).get("odds")
        if close is not None:
            return _num(close)
        return _num((o.get(legacy) or {}).get("moneyLine"))

    return {
        "oddsProvider": (o.get("provider") or {}).get("name"),
        "oddsDetails": o.get("details"),
        "spread": _num(o.get("spread")),
        "overUnder": _num(o.get("overUnder")),
        "homeMoneyline": side_ml("home", "homeTeamOdds"),
        "awayMoneyline": side_ml("away", "awayTeamOdds"),
    }


def _base(league: League, tz: ZoneInfo, host: str) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(GAME_COLUMNS)
    row.update(
        recordType="game",
        league=league.key,
        leagueName=league.name,
        sport=league.sport,
        espnPath=league.path,
        competitorType="team",
        broadcasts=[],
        scrapedAt=now_iso(),
        sourceHost=host,
    )
    return row


# --------------------------------------------------------------------------- game rows


def team_game_row(
    event: dict[str, Any], league: League, tz: ZoneInfo, *, host: str = "site.api.espn.com"
) -> dict[str, Any]:
    comp = (event.get("competitions") or [{}])[0]
    status, detail, completed = map_status(comp.get("status") or event.get("status"))
    st = comp.get("status") or event.get("status") or {}
    home, away = _split(comp.get("competitors") or [])
    season = event.get("season") or {}
    row = _base(league, tz, host)
    row.update(
        type="game",
        id=str(event.get("id")),
        uid=event.get("uid"),
        season=season.get("year"),
        seasonType=season.get("type")
        if isinstance(season.get("type"), int)
        else _num(
            (event.get("seasonType") or {}).get("type")
            if isinstance(event.get("seasonType"), dict)
            else None
        ),
        week=(event.get("week") or {}).get("number")
        if isinstance(event.get("week"), dict)
        else None,
        name=event.get("name"),
        shortName=event.get("shortName"),
        date=comp.get("date") or event.get("date"),
        dateLocal=local_date(comp.get("date") or event.get("date"), tz),
        endDate=comp.get("endDate") or event.get("endDate"),
        status=status,
        statusDetail=detail,
        completed=completed,
        period=st.get("period"),
        clock=st.get("displayClock"),
        attendance=comp.get("attendance") or None,
        neutralSite=comp.get("neutralSite"),
        broadcasts=_broadcasts(comp),
        espnUrl=_espn_url(event),
    )
    row.update(_venue(comp.get("venue") or event.get("venue")))
    row.update(_team_side(home, "home"))
    row.update(_team_side(away, "away"))
    row["winnerId"] = _winner_id(home, away)
    row.update(extract_odds(comp))
    return row


def _winner_id(home: dict | None, away: dict | None) -> str | None:
    for c in (home, away):
        if c and c.get("winner"):
            return (c.get("team") or {}).get("id") or c.get("id")
    return None


def tennis_rows(
    event: dict[str, Any], league: League, tz: ZoneInfo, start: date, end: date
) -> list[dict[str, Any]]:
    """Every match of the tournament payload whose date falls inside [start, end]."""
    rows: list[dict[str, Any]] = []
    season = event.get("season") or {}
    for grouping in event.get("groupings") or []:
        slug = (grouping.get("grouping") or {}).get("slug")
        for m in grouping.get("competitions") or []:
            if not in_window(m.get("date"), start, end):
                continue
            status, detail, completed = map_status(m.get("status"))
            home, away = _split(m.get("competitors") or [])
            row = _base(league, tz, "site.api.espn.com")
            hs, as_ = _athlete_side(home, "home"), _athlete_side(away, "away")
            row.update(
                type="match",
                id=str(m.get("id")),
                uid=m.get("uid"),
                season=season.get("year"),
                seasonType=season.get("type"),
                tournament=event.get("name"),
                tournamentId=str(m.get("tournamentId") or event.get("id")),
                round=(m.get("round") or {}).get("displayName"),
                grouping=slug or (m.get("type") or {}).get("slug"),
                name=_vs(hs["homeName"], as_["awayName"]),
                shortName=_vs(
                    hs["homeShortName"] or hs["homeName"], as_["awayShortName"] or as_["awayName"]
                ),
                date=m.get("date"),
                dateLocal=local_date(m.get("date"), tz),
                endDate=None,
                status=status,
                statusDetail=detail,
                completed=completed,
                period=(m.get("status") or {}).get("period"),
                competitorType="athlete",
                resultText=next(
                    (n.get("text") for n in m.get("notes") or [] if n.get("text")), None
                ),
                broadcasts=_broadcasts(m),
                espnUrl=_espn_url(event),
            )
            row.update(_venue(m.get("venue") or event.get("venue")))
            row.update(hs)
            row.update(as_)
            row["winnerId"] = _winner_id(home, away)
            rows.append(row)
    return rows


def golf_rows(event: dict[str, Any], league: League, tz: ZoneInfo) -> list[dict[str, Any]]:
    comp = (event.get("competitions") or [{}])[0]
    status, detail, completed = map_status(comp.get("status") or event.get("status"))
    season = event.get("season") or {}
    rows = []
    for c in sorted(comp.get("competitors") or [], key=lambda c: c.get("order") or 0):
        row = _base(league, tz, "site.api.espn.com")
        row.update(_athlete_side(c, "home"))
        row.update(_athlete_side(None, "away"))
        row.update(
            type="leaderboard",
            id=f"{event.get('id')}:{c.get('id')}",
            uid=c.get("uid"),
            season=season.get("year"),
            seasonType=season.get("type"),
            tournament=event.get("name"),
            tournamentId=str(event.get("id")),
            name=event.get("name"),
            shortName=event.get("shortName"),
            date=comp.get("date") or event.get("date"),
            dateLocal=local_date(comp.get("date") or event.get("date"), tz),
            endDate=comp.get("endDate") or event.get("endDate"),
            status=status,
            statusDetail=detail,
            completed=completed,
            period=(comp.get("status") or {}).get("period"),
            competitorType="athlete",
            position=c.get("order"),
            scoreDisplay=c.get("score") if isinstance(c.get("score"), str) else None,
            winnerId=c.get("id") if c.get("order") == 1 and completed else None,
            broadcasts=_broadcasts(comp),
            espnUrl=_espn_url(event),
        )
        row.update(_venue(comp.get("venue") or event.get("venue")))
        rows.append(row)
    return rows


def f1_rows(
    event: dict[str, Any], league: League, tz: ZoneInfo, start: date, end: date
) -> list[dict[str, Any]]:
    season = event.get("season") or {}
    circuit = event.get("circuit") or {}
    rows = []
    for comp in event.get("competitions") or []:
        if not in_window(comp.get("date"), start, end):
            continue
        status, detail, completed = map_status(comp.get("status"))
        session = (comp.get("type") or {}).get("abbreviation") or (comp.get("type") or {}).get(
            "text"
        )
        competitors = sorted(comp.get("competitors") or [], key=lambda c: c.get("order") or 0)
        fallback = competitors[0] if competitors and completed else None
        winner = next((c for c in competitors if c.get("winner")), fallback)
        row = _base(league, tz, "site.api.espn.com")
        row.update(_athlete_side(winner, "home"))
        row.update(_athlete_side(None, "away"))
        row.update(
            type="session",
            id=str(comp.get("id")),
            uid=comp.get("uid"),
            season=season.get("year"),
            seasonType=season.get("type"),
            tournament=event.get("name"),
            tournamentId=str(event.get("id")),
            round=session,
            name=f"{event.get('name')} — {session}" if session else event.get("name"),
            shortName=f"{event.get('shortName')} {session}".strip(),
            date=comp.get("date"),
            dateLocal=local_date(comp.get("date"), tz),
            endDate=comp.get("endDate"),
            status=status,
            statusDetail=detail,
            completed=completed,
            period=(comp.get("status") or {}).get("period"),
            clock=(comp.get("status") or {}).get("displayClock"),
            competitorType="athlete",
            position=1 if winner else None,
            winnerId=winner.get("id") if winner else None,
            broadcasts=_broadcasts(comp),
            espnUrl=_espn_url(event),
            venueName=circuit.get("fullName"),
            venueCity=(circuit.get("address") or {}).get("city"),
            venueCountry=(circuit.get("address") or {}).get("country"),
        )
        rows.append(row)
    return rows


def ufc_rows(event: dict[str, Any], league: League, tz: ZoneInfo) -> list[dict[str, Any]]:
    season = event.get("season") or {}
    venues = event.get("venues") or []
    rows = []
    for order, comp in enumerate(event.get("competitions") or [], start=1):
        status, detail, completed = map_status(comp.get("status"))
        home, away = _split(comp.get("competitors") or [])
        st = comp.get("status") or {}
        row = _base(league, tz, "site.api.espn.com")
        hs, as_ = _athlete_side(home, "home"), _athlete_side(away, "away")
        row.update(
            type="match",
            id=str(comp.get("id")),
            uid=comp.get("uid"),
            season=season.get("year"),
            seasonType=season.get("type"),
            tournament=event.get("name"),
            tournamentId=str(event.get("id")),
            round=order,
            grouping=(comp.get("type") or {}).get("abbreviation")
            or (comp.get("type") or {}).get("text"),
            name=_vs(hs["homeName"], as_["awayName"]),
            shortName=_vs(
                hs["homeShortName"] or hs["homeName"], as_["awayShortName"] or as_["awayName"]
            ),
            date=comp.get("date") or event.get("date"),
            dateLocal=local_date(comp.get("date") or event.get("date"), tz),
            endDate=comp.get("endDate"),
            status=status,
            statusDetail=detail,
            completed=completed,
            period=st.get("period"),
            clock=st.get("displayClock"),
            competitorType="athlete",
            resultText=next(
                (n.get("text") for n in comp.get("notes") or [] if n.get("text")), None
            ),
            broadcasts=_broadcasts(comp),
            espnUrl=_espn_url(event),
        )
        row.update(_venue(comp.get("venue") or (venues[0] if venues else None)))
        row.update(hs)
        row.update(as_)
        row["winnerId"] = _winner_id(home, away)
        rows.append(row)
    return rows


def scoreboard_rows(
    payload: dict[str, Any], league: League, tz: ZoneInfo, start: date, end: date
) -> list[dict[str, Any]]:
    """Dispatch on the league kind. Team sports: one row per event."""
    events = payload.get("events") or []
    rows: list[dict[str, Any]] = []
    for event in events:
        if league.kind == KIND_TENNIS:
            rows.extend(tennis_rows(event, league, tz, start, end))
        elif league.kind == KIND_GOLF:
            rows.extend(golf_rows(event, league, tz))
        elif league.kind == KIND_F1:
            rows.extend(f1_rows(event, league, tz, start, end))
        elif league.kind == KIND_UFC:
            rows.extend(ufc_rows(event, league, tz))
        else:
            rows.append(team_game_row(event, league, tz))
    return rows


# --------------------------------------------------------------------------- filters


def _norm(s: Any) -> str:
    return str(s or "").strip().casefold()


def matches_teams(row: dict[str, Any], teams: list[str]) -> bool:
    """Exact id/abbreviation or case-insensitive name substring on either side."""
    if not teams:
        return True
    wanted = [_norm(t) for t in teams if _norm(t)]
    if not wanted:
        return True
    for side in ("home", "away"):
        ids = {_norm(row.get(f"{side}Id")), _norm(row.get(f"{side}Abbr"))}
        names = [_norm(row.get(f"{side}Name")), _norm(row.get(f"{side}ShortName"))]
        for w in wanted:
            if w in ids or any(w in n for n in names if n):
                return True
    return False


def matches_status(row: dict[str, Any], status: str) -> bool:
    return status in ("all", "", None) or row.get("status") == status


# --------------------------------------------------------------------------- standings

_STAT_COLUMNS = {
    "gamesPlayed": "gamesPlayed", "wins": "wins", "losses": "losses", "ties": "ties",
    "otLosses": "otLosses", "OTLosses": "otLosses", "points": "points",
    "championshipPts": "points", "winPercent": "winPercent", "pointsFor": "pointsFor",
    "pointsAgainst": "pointsAgainst", "pointDifferential": "pointDifferential",
    "gamesBehind": "gamesBehind", "playoffSeed": "playoffSeed", "rankChange": "rankChange",
}  # fmt: skip
_DISPLAY_COLUMNS = {"streak": "streak", "clincher": "clincher"}
_RECORD_COLUMNS = {
    "home": "homeRecord", "road": "awayRecord", "away": "awayRecord",
    "vsdiv": "divisionRecord", "vsconf": "conferenceRecord",
}  # fmt: skip


def standings_rows(doc: dict[str, Any], league: League, season: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scraped = now_iso()

    def walk(node: dict[str, Any], group: str | None, group_id: str | None) -> None:
        st = node.get("standings")
        if isinstance(st, dict) and st.get("entries"):
            for i, entry in enumerate(st["entries"], start=1):
                rows.append(_standing_row(entry, i, league, st, group, group_id, season, scraped))
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child, child.get("name") or group, child.get("id") or group_id)

    walk(doc, doc.get("name"), doc.get("id"))
    return rows


def _standing_row(
    entry: dict[str, Any], index: int, league: League, st: dict[str, Any],
    group: str | None, group_id: str | None, season: int | None, scraped: str,
) -> dict[str, Any]:  # fmt: skip
    team = entry.get("team") or entry.get("athlete") or {}
    stats_map: dict[str, Any] = {}
    row: dict[str, Any] = {
        "recordType": "standing", "league": league.key, "season": st.get("season") or season,
        "seasonType": st.get("seasonType"), "group": group, "groupId": group_id, "rank": None,
        "teamId": team.get("id"), "teamName": team.get("displayName") or team.get("name"),
        "teamAbbr": team.get("abbreviation"),
        "teamLogo": _logo(team) or (team.get("flag") or {}).get("href"),
        "gamesPlayed": None, "wins": None, "losses": None, "ties": None, "otLosses": None,
        "points": None, "winPercent": None, "pointsFor": None, "pointsAgainst": None,
        "pointDifferential": None, "gamesBehind": None, "streak": None, "playoffSeed": None,
        "clincher": None, "homeRecord": None, "awayRecord": None, "divisionRecord": None,
        "conferenceRecord": None, "note": (entry.get("note") or {}).get("description"),
        "rankChange": None, "stats": stats_map, "scrapedAt": scraped,
    }  # fmt: skip
    for s in entry.get("stats") or []:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("type")
        if not name:
            continue
        value = (
            s.get("value")
            if s.get("value") is not None
            else (s.get("summary") or s.get("displayValue"))
        )
        stats_map[str(name)] = value
        if name == "rank":
            row["rank"] = _num(s.get("value"))
        elif name in _STAT_COLUMNS:
            row[_STAT_COLUMNS[name]] = _num(s.get("value"))
        elif name in _DISPLAY_COLUMNS:
            row[_DISPLAY_COLUMNS[name]] = s.get("displayValue")
        col = _RECORD_COLUMNS.get(str(s.get("type") or ""))
        if col and (s.get("summary") or s.get("displayValue")):
            row[col] = s.get("summary") or s.get("displayValue")
    if row["rank"] is None:
        row["rank"] = index
    if row["wins"] is not None and row["gamesPlayed"] is None:
        parts = (row["wins"], row["losses"], row["ties"])
        row["gamesPlayed"] = sum(v for v in parts if isinstance(v, (int, float))) or None
    return row


# --------------------------------------------------------------------------- teams


def team_row(team: dict[str, Any], league: League, season: int | None) -> dict[str, Any]:
    venue = team.get("venue") or {}
    return {
        "recordType": "team", "league": league.key, "season": season, "teamId": team.get("id"),
        "uid": team.get("uid"), "slug": team.get("slug"), "name": team.get("name"),
        "displayName": team.get("displayName"), "shortDisplayName": team.get("shortDisplayName"),
        "abbreviation": team.get("abbreviation"), "location": team.get("location"),
        "nickname": team.get("nickname") or team.get("name"), "color": team.get("color"),
        "alternateColor": team.get("alternateColor"), "logo": _logo(team),
        "isActive": team.get("isActive"), "venueName": venue.get("fullName"),
        "venueCity": (venue.get("address") or {}).get("city"), "espnUrl": _espn_url(team),
        "scrapedAt": now_iso(),
    }  # fmt: skip


def site_teams(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Team objects from ``/sports/{path}/teams``."""
    out = []
    for sport in payload.get("sports") or []:
        for lg in sport.get("leagues") or []:
            for t in lg.get("teams") or []:
                if isinstance(t, dict) and isinstance(t.get("team"), dict):
                    out.append(t["team"])
    return out


def find_team(teams: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Abbreviation or numeric id exactly, else display/location/name substring."""
    q = _norm(query)
    if not q:
        return None
    for t in teams:
        if q in (_norm(t.get("abbreviation")), _norm(t.get("id"))):
            return t
    for t in teams:
        if q in (
            _norm(t.get("displayName")),
            _norm(t.get("slug")),
            _norm(t.get("name")),
            _norm(t.get("location")),
        ):
            return t
    for t in teams:
        if any(q in _norm(t.get(k)) for k in ("displayName", "name", "location") if t.get(k)):
            return t
    return None


# --------------------------------------------------------------------------- summary


def summary_row(doc: dict[str, Any], league: League, tz: ZoneInfo, event_id: str) -> dict[str, Any]:
    header = doc.get("header") or {}
    comp = (header.get("competitions") or [{}])[0]
    info = doc.get("gameInfo") or {}
    event = {
        "id": header.get("id") or event_id,
        "uid": header.get("uid"),
        "season": header.get("season") or {},
        "week": header.get("week")
        if isinstance(header.get("week"), dict)
        else {"number": header.get("week")},
        "links": header.get("links") or [],
        "competitions": [dict(comp, venue=info.get("venue"), attendance=info.get("attendance"))],
    }
    row = team_game_row(event, league, tz)
    row["recordType"] = "summary"
    home_abbr, away_abbr = row.get("homeAbbr"), row.get("awayAbbr")
    if not row["name"] and row["homeName"]:
        row["name"] = f"{row['awayName']} at {row['homeName']}"
        row["shortName"] = f"{away_abbr} @ {home_abbr}"
    row["leaders"] = [
        {
            "category": cat.get("displayName") or cat.get("name"),
            "athleteName": (leader.get("athlete") or {}).get("displayName"),
            "teamAbbr": (team.get("team") or {}).get("abbreviation"),
            "value": leader.get("displayValue"),
        }
        for team in doc.get("leaders") or []
        for cat in team.get("leaders") or []
        for leader in (cat.get("leaders") or [])[:1]
    ]
    row["scoringPlays"] = [
        {
            "period": (p.get("period") or {}).get("number"),
            "clock": (p.get("clock") or {}).get("displayValue"),
            "teamAbbr": (p.get("team") or {}).get("abbreviation"),
            "text": p.get("text"),
            "homeScore": p.get("homeScore"),
            "awayScore": p.get("awayScore"),
        }
        for p in doc.get("scoringPlays") or []
    ]
    wp = doc.get("winprobability") or []
    row["winProbabilityFinal"] = (
        wp[-1].get("homeWinPercentage") if wp and isinstance(wp[-1], dict) else None
    )
    row["boxscoreTeamStats"] = [
        {
            "teamAbbr": (t.get("team") or {}).get("abbreviation"),
            "stat": s.get("name"),
            "value": s.get("displayValue"),
        }
        for t in (doc.get("boxscore") or {}).get("teams") or []
        for s in t.get("statistics") or []
    ]
    return row


def assert_clean(row: dict[str, Any]) -> None:
    """Guard: no editorial key ever leaves the normaliser (§3.4 step 9)."""
    bad = FORBIDDEN_KEYS.intersection(row)
    if bad:
        raise ValueError(f"forbidden keys in row: {sorted(bad)}")
