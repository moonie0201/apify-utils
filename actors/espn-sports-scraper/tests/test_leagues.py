import json
from pathlib import Path

from src.leagues import (
    KIND_F1,
    KIND_GOLF,
    KIND_TEAM,
    KIND_TENNIS,
    KIND_UFC,
    LEAGUES,
    resolve_league,
)

INPUT_SCHEMA = Path(__file__).parent.parent / ".actor" / "input_schema.json"
TENNIS_SCHEMA = (
    Path(__file__).parent.parent.parent / "tennis-scores-scraper" / ".actor" / "input_schema.json"
)


def _enum(path: Path) -> list[str]:
    return json.loads(path.read_text())["properties"]["leagues"]["items"]["enum"]


def test_every_enum_key_resolves():
    for key in _enum(INPUT_SCHEMA):
        league = resolve_league(key)
        assert league is not None and league.key == key
        assert league.path.count("/") == 1


def test_enum_and_table_are_the_same_set():
    assert set(_enum(INPUT_SCHEMA)) == set(LEAGUES)
    if TENNIS_SCHEMA.exists():
        assert _enum(TENNIS_SCHEMA) == _enum(INPUT_SCHEMA)


def test_ncaa_groups_set_and_paths_match_spec():
    assert LEAGUES["ncaaf"].groups == 80 and LEAGUES["ncaaf"].path == "football/college-football"
    assert (
        LEAGUES["ncaab"].groups == 50
        and LEAGUES["ncaab"].path == "basketball/mens-college-basketball"
    )
    assert LEAGUES["ncaaw"].groups == 50
    assert all(lg.groups is None for k, lg in LEAGUES.items() if not k.startswith("ncaa"))
    assert LEAGUES["epl"].path == "soccer/eng.1"
    assert LEAGUES["ucl"].path == "soccer/uefa.champions"
    assert LEAGUES["copa-libertadores"].path == "soccer/conmebol.libertadores"


def test_kinds():
    assert LEAGUES["nfl"].kind == KIND_TEAM and not LEAGUES["nfl"].athlete_sport
    assert LEAGUES["atp"].kind == KIND_TENNIS and LEAGUES["wta"].kind == KIND_TENNIS
    assert LEAGUES["pga"].kind == KIND_GOLF
    assert LEAGUES["f1"].kind == KIND_F1
    assert LEAGUES["ufc"].kind == KIND_UFC
    assert LEAGUES["atp"].sport == "tennis" and LEAGUES["atp"].slug == "atp"


def test_raw_path_passthrough_and_rejects():
    raw = resolve_league("soccer/bra.1")
    assert (
        raw is not None
        and raw.path == "soccer/bra.1"
        and raw.kind == KIND_TEAM
        and raw.key == "soccer/bra.1"
    )
    assert resolve_league("tennis/itf").kind == KIND_TENNIS
    assert resolve_league("NBA").key == "nba"
    assert resolve_league("") is None
    assert resolve_league("nope") is None
    assert resolve_league("a/b/c") is None
    assert resolve_league("soccer/") is None
    assert resolve_league("foo/\x00bar") is None  # would raise from httpx.URL
    assert resolve_league("soccer/x y") is None
    assert resolve_league("soccer/.hidden") is None
