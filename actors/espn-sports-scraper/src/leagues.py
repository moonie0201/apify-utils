"""League key -> ESPN path table (UTILS_SPEC §3.2).

Every key in the input schema enum resolves here. A raw ``sport/league`` string is also
accepted so an advanced user can reach a league we do not list; the feed answers 400 for an
unknown one and that becomes a free error row (§3.4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: How the scoreboard payload is shaped for the league. ``team`` is every two-team sport.
KIND_TEAM = "team"
KIND_TENNIS = "tennis"
KIND_GOLF = "golf"
KIND_F1 = "f1"
KIND_UFC = "ufc"

_SPORT_KIND = {"tennis": KIND_TENNIS, "golf": KIND_GOLF, "racing": KIND_F1, "mma": KIND_UFC}


@dataclass(frozen=True)
class League:
    key: str
    path: str
    name: str
    groups: int | None = None

    @property
    def sport(self) -> str:
        return self.path.split("/", 1)[0]

    @property
    def slug(self) -> str:
        return self.path.split("/", 1)[1]

    @property
    def kind(self) -> str:
        return _SPORT_KIND.get(self.sport, KIND_TEAM)

    @property
    def athlete_sport(self) -> bool:
        return self.kind != KIND_TEAM


_TABLE: list[tuple[str, str, str, int | None]] = [
    ("nfl", "football/nfl", "NFL", None),
    ("ncaaf", "football/college-football", "NCAA Football (FBS)", 80),
    ("nba", "basketball/nba", "NBA", None),
    ("wnba", "basketball/wnba", "WNBA", None),
    ("ncaab", "basketball/mens-college-basketball", "NCAA Men's Basketball (D-I)", 50),
    ("ncaaw", "basketball/womens-college-basketball", "NCAA Women's Basketball", 50),
    ("mlb", "baseball/mlb", "MLB", None),
    ("nhl", "hockey/nhl", "NHL", None),
    ("mls", "soccer/usa.1", "MLS", None),
    ("nwsl", "soccer/usa.nwsl", "NWSL", None),
    ("epl", "soccer/eng.1", "Premier League", None),
    ("championship", "soccer/eng.2", "EFL Championship", None),
    ("fa-cup", "soccer/eng.fa", "FA Cup", None),
    ("la-liga", "soccer/esp.1", "La Liga", None),
    ("serie-a", "soccer/ita.1", "Serie A", None),
    ("bundesliga", "soccer/ger.1", "Bundesliga", None),
    ("ligue-1", "soccer/fra.1", "Ligue 1", None),
    ("eredivisie", "soccer/ned.1", "Eredivisie", None),
    ("primeira-liga", "soccer/por.1", "Primeira Liga", None),
    ("liga-mx", "soccer/mex.1", "Liga MX", None),
    ("saudi-pro-league", "soccer/ksa.1", "Saudi Pro League", None),
    ("wsl", "soccer/eng.w.1", "Women's Super League", None),
    ("ucl", "soccer/uefa.champions", "Champions League", None),
    ("uel", "soccer/uefa.europa", "Europa League", None),
    ("uecl", "soccer/uefa.europa.conf", "Conference League", None),
    ("nations-league", "soccer/uefa.nations", "UEFA Nations League", None),
    ("world-cup", "soccer/fifa.world", "FIFA World Cup", None),
    ("copa-libertadores", "soccer/conmebol.libertadores", "Copa Libertadores", None),
    ("atp", "tennis/atp", "ATP Tennis", None),
    ("wta", "tennis/wta", "WTA Tennis", None),
    ("pga", "golf/pga", "PGA Tour", None),
    ("f1", "racing/f1", "Formula 1", None),
    ("ufc", "mma/ufc", "UFC", None),
]

LEAGUES: dict[str, League] = {k: League(k, p, n, g) for k, p, n, g in _TABLE}

#: A raw ``sport/league`` path: URL-safe characters only, so it can never break the request
#: URL (a control byte would raise from ``httpx.URL`` before the host check).
RAW_PATH = re.compile(r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*")


def resolve_league(value: str) -> League | None:
    """A known key, or a raw ``sport/league`` path passed through. None = not a league."""
    key = (value or "").strip()
    if key in LEAGUES:
        return LEAGUES[key]
    low = key.lower()
    if low in LEAGUES:
        return LEAGUES[low]
    if RAW_PATH.fullmatch(low):
        return League(low, low, low)
    return None
