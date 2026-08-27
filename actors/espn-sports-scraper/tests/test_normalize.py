from datetime import date
from zoneinfo import ZoneInfo

import pytest
from src.leagues import LEAGUES
from src.normalize import (
    FORBIDDEN_KEYS,
    GAME_COLUMNS,
    assert_clean,
    date_windows,
    dates_param,
    extract_odds,
    find_team,
    local_date,
    map_status,
    matches_status,
    matches_teams,
    scoreboard_rows,
    site_teams,
    standings_rows,
    summary_row,
    team_game_row,
    team_row,
    tennis_rows,
    years_in,
)

UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- windows


def test_31_days_is_5_windows():
    w = date_windows(date(2025, 1, 1), date(2025, 1, 31))
    assert len(w) == 5
    assert w[0] == (date(2025, 1, 1), date(2025, 1, 7))
    assert w[-1] == (date(2025, 1, 29), date(2025, 1, 31))
    assert all((b - a).days <= 6 for a, b in w)


def test_single_day_and_366_cap():
    assert date_windows(date(2025, 5, 5), date(2025, 5, 5)) == [
        (date(2025, 5, 5), date(2025, 5, 5))
    ]
    w = date_windows(date(2024, 1, 1), date(2026, 1, 1))
    assert w[-1][1] == date(2024, 12, 31)
    assert sum((b - a).days + 1 for a, b in w) == 366


def test_dates_param_and_years():
    assert dates_param(date(2025, 1, 1), date(2025, 1, 7)) == "20250101-20250107"
    assert dates_param(date(2025, 1, 1), date(2025, 1, 1)) == "20250101"
    assert years_in(date(2024, 12, 30), date(2025, 1, 2)) == [2024, 2025]


def test_local_date_uses_timezone():
    assert local_date("2024-09-06T00:40Z", UTC) == "2024-09-06"
    assert local_date("2024-09-06T00:40Z", NY) == "2024-09-05"
    assert local_date(None, NY) is None
    assert local_date("garbage", NY) is None


# --------------------------------------------------------------------------- status


@pytest.mark.parametrize(
    ("name", "state", "expected"),
    [
        ("STATUS_SCHEDULED", "pre", "scheduled"),
        ("STATUS_IN_PROGRESS", "in", "live"),
        ("STATUS_HALFTIME", "in", "live"),
        ("STATUS_FINAL", "post", "final"),
        ("STATUS_FULL_TIME", "post", "final"),
        ("STATUS_RETIRED", "post", "final"),
        ("STATUS_WALKOVER", "post", "final"),
        ("STATUS_POSTPONED", "post", "postponed"),
        ("STATUS_CANCELED", "post", "canceled"),
        ("STATUS_SUSPENDED", "in", "suspended"),
        ("STATUS_DELAYED", "pre", "delayed"),
        ("STATUS_RAIN_DELAY", "in", "delayed"),
        ("STATUS_SOMETHING_NEW", "in", "live"),
        ("STATUS_SOMETHING_NEW", "post", "final"),
        ("", "", "scheduled"),
    ],
)
def test_status_map(name, state, expected):
    status, _, _ = map_status({"type": {"name": name, "state": state}})
    assert status == expected


def test_status_completed_flag():
    assert map_status({"type": {"name": "STATUS_FINAL", "completed": True}})[2] is True
    assert map_status({"type": {"name": "STATUS_SCHEDULED", "completed": False}})[2] is False
    assert map_status(None) == ("scheduled", None, False)


# --------------------------------------------------------------------------- team rows


def test_nfl_game_row(fx):
    rows = scoreboard_rows(
        fx("nfl_scoreboard.json"), LEAGUES["nfl"], NY, date(2025, 9, 7), date(2025, 9, 13)
    )
    assert len(rows) == 4
    r = rows[0]
    assert tuple(r) == GAME_COLUMNS
    assert r["recordType"] == "game" and r["type"] == "game" and r["competitorType"] == "team"
    assert r["id"] == "401772830" and r["league"] == "nfl" and r["espnPath"] == "football/nfl"
    assert r["season"] == 2025 and r["seasonType"] == 2 and r["week"] == 1
    assert r["homeAbbr"] == "ATL" and r["awayAbbr"] == "TB"
    assert r["homeScore"] == 20 and r["awayScore"] == 23 and r["winnerId"] == "27"
    assert r["homeLinescores"] == [7, 3, 3, 7] and r["awayLinescores"] == [0, 10, 7, 6]
    assert r["homeRecord"] == "0-1" and r["awayRecord"] == "1-0"
    assert r["homeRank"] is None  # curatedRank 99 = unranked
    assert r["status"] == "final" and r["completed"] is True and r["period"] == 4
    assert (
        r["venueName"] == "Mercedes-Benz Stadium"
        and r["venueCity"] == "Atlanta"
        and r["venueIndoor"] is True
    )
    assert r["attendance"] == 71610 and r["broadcasts"] == ["FOX"]
    assert r["dateLocal"] == "2025-09-07" and r["date"] == "2025-09-07T17:00Z"
    assert r["espnUrl"].startswith("https://www.espn.com/nfl/game/")
    assert r["oddsProvider"] is None and r["spread"] is None
    assert r["sourceHost"] == "site.api.espn.com"


def test_nfl_headlines_never_emitted(fx):
    payload = fx("nfl_scoreboard.json")
    assert "headlines" in payload["events"][0]["competitions"][0]  # the fixture carries it
    for r in scoreboard_rows(payload, LEAGUES["nfl"], UTC, date(2025, 9, 7), date(2025, 9, 13)):
        assert not FORBIDDEN_KEYS.intersection(r)
        assert "editorial" not in str(r)


def test_epl_row(fx):
    rows = scoreboard_rows(
        fx("epl_scoreboard.json"), LEAGUES["epl"], UTC, date(2025, 8, 16), date(2025, 8, 22)
    )
    r = rows[0]
    assert r["status"] == "final" and r["statusDetail"] == "FT" and r["clock"] == "90'+7'"
    assert r["homeScore"] == 0 and r["awayScore"] == 0 and r["homeLinescores"] is None
    assert r["homeRecord"] == "0-1-0" and r["venueCity"] == "Birmingham"
    assert "Joelinton" not in str(r)  # details/athletes are not copied


def test_team_filter_by_abbr_id_and_name(fx):
    rows = scoreboard_rows(
        fx("nfl_scoreboard.json"), LEAGUES["nfl"], UTC, date(2025, 9, 7), date(2025, 9, 13)
    )
    assert [r["id"] for r in rows if matches_teams(r, ["atl"])] == ["401772830"]
    assert [r["id"] for r in rows if matches_teams(r, ["27"])] == ["401772830"]
    assert [r["id"] for r in rows if matches_teams(r, ["buccaneers"])] == ["401772830"]
    assert all(matches_teams(r, []) for r in rows)
    assert not any(matches_teams(r, ["Arsenal"]) for r in rows)


def test_status_filter():
    assert matches_status({"status": "final"}, "all")
    assert matches_status({"status": "final"}, "final")
    assert not matches_status({"status": "scheduled"}, "final")


# --------------------------------------------------------------------------- odds


def test_odds_from_nfl_upcoming(fx):
    rows = scoreboard_rows(
        fx("nfl_scoreboard_odds.json"), LEAGUES["nfl"], UTC, date(2026, 9, 3), date(2026, 9, 15)
    )
    with_odds = [r for r in rows if r["oddsProvider"]]
    assert with_odds
    r = with_odds[0]
    assert r["oddsProvider"] == "Draft Kings" and r["oddsDetails"] == "SEA -3.5"
    assert r["spread"] == -3.5 and r["overUnder"] == 44.5
    assert r["homeMoneyline"] == -180 and r["awayMoneyline"] == 150
    assert "draftkings.com" not in str(r)  # no sportsbook deep links


def test_odds_null_and_legacy_shapes():
    empty = {
        "oddsProvider": None,
        "oddsDetails": None,
        "spread": None,
        "overUnder": None,
        "homeMoneyline": None,
        "awayMoneyline": None,
    }
    assert extract_odds({}) == empty
    assert extract_odds({"odds": [None]}) == empty
    assert extract_odds({"odds": []}) == empty
    legacy = {
        "odds": [
            {
                "provider": {"name": "X"},
                "details": "LAL -2",
                "spread": -2,
                "overUnder": 220.5,
                "homeTeamOdds": {"moneyLine": -130},
                "awayTeamOdds": {"moneyLine": 110},
            }
        ]
    }
    assert extract_odds(legacy) == {
        "oddsProvider": "X",
        "oddsDetails": "LAL -2",
        "spread": -2,
        "overUnder": 220.5,
        "homeMoneyline": -130,
        "awayMoneyline": 110,
    }


def test_finished_nba_games_have_no_odds(fx):
    rows = scoreboard_rows(
        fx("nba_scoreboard.json"), LEAGUES["nba"], UTC, date(2025, 1, 1), date(2025, 1, 31)
    )
    assert rows and all(r["status"] == "final" and r["oddsProvider"] is None for r in rows)


# --------------------------------------------------------------------------- athlete rows


def test_tennis_window_filter_and_fields(fx):
    payload = fx("atp_scoreboard.json")
    event = payload["events"][0]
    singles = next(g for g in event["groupings"] if g["grouping"]["slug"] == "mens-singles")
    assert len(singles["competitions"]) == 239
    rows = tennis_rows(event, LEAGUES["atp"], UTC, date(2025, 7, 6), date(2025, 7, 6))
    assert len(rows) == 4 and all(r["date"].startswith("2025-07-06") for r in rows)
    r = rows[0]
    assert (
        r["type"] == "match" and r["competitorType"] == "athlete" and r["tournament"] == "Wimbledon"
    )
    assert (
        r["grouping"] == "mens-singles" and r["round"] == "Round 4" and r["tournamentId"] == "188"
    )
    assert r["homeLinescores"] == [6, 6, 6] and r["awayLinescores"] == [4, 2, 3]
    assert r["homeRank"] == 17 and r["homeCountry"] == "Russia" and r["awayCountry"] == "Poland"
    assert r["resultText"].endswith("6-4 6-2 6-3") and r["winnerId"] == r["homeId"]
    assert r["court"] and r["venueName"] == "London, Great Britain"
    assert r["homeAbbr"] is None and r["oddsProvider"] is None
    assert "headshot" not in str(r) and "guid" not in str(r)


def test_tennis_doubles_and_player_filter(fx):
    rows = scoreboard_rows(
        fx("atp_scoreboard.json"), LEAGUES["atp"], UTC, date(2025, 6, 23), date(2025, 7, 13)
    )
    doubles = [r for r in rows if r["grouping"] == "mens-doubles"]
    assert len(doubles) == 3 and " / " in doubles[0]["homeName"]
    assert [r for r in rows if matches_teams(r, ["khachanov"])]
    assert len({r["id"] for r in rows}) == len(rows)


def test_tennis_dedupe_across_days(fx):
    """The same tournament payload comes back for every queried day; ids must not repeat."""
    payload = fx("atp_scoreboard.json")
    seen: dict[str, dict] = {}
    for _day_query in range(3):  # 5, 6 and 7 July all return the same Wimbledon payload
        for r in scoreboard_rows(payload, LEAGUES["atp"], UTC, date(2025, 7, 5), date(2025, 7, 7)):
            seen.setdefault(r["id"], r)
    assert len(seen) == 8 + 4 + 4
    assert all(
        date(2025, 7, 5) <= date.fromisoformat(r["date"][:10]) <= date(2025, 7, 7)
        for r in seen.values()
    )


def test_golf_leaderboard_rows(fx):
    rows = scoreboard_rows(
        fx("pga_scoreboard.json"), LEAGUES["pga"], UTC, date(2025, 6, 12), date(2025, 6, 18)
    )
    assert len(rows) == 5 and [r["position"] for r in rows] == [1, 2, 3, 4, 5]
    r = rows[0]
    assert (
        r["type"] == "leaderboard" and r["homeName"] == "J.J. Spaun" and r["scoreDisplay"] == "-1"
    )
    assert r["homeScore"] == -1 and r["homeLinescores"] == [66, 72, 69, 72]
    assert r["awayId"] is None and r["awayName"] is None and r["winnerId"] == r["homeId"]
    assert (
        r["id"] == "401703515:10166"
        and r["tournamentId"] == "401703515"
        and r["tournament"] == "U.S. Open"
    )
    assert rows[1]["winnerId"] is None


def test_f1_session_rows(fx):
    rows = scoreboard_rows(
        fx("f1_scoreboard.json"), LEAGUES["f1"], UTC, date(2025, 3, 14), date(2025, 3, 16)
    )
    assert [r["round"] for r in rows] == ["FP1", "FP2", "FP3", "Qual", "Race"]
    race = rows[-1]
    assert (
        race["type"] == "session"
        and race["homeName"] == "Lando Norris"
        and race["winnerId"] == "5579"
    )
    assert (
        race["venueName"] == "Melbourne Grand Prix Circuit" and race["venueCountry"] == "Australia"
    )
    assert race["tournament"].endswith("Australian Grand Prix") and race["awayName"] is None
    all_rows = scoreboard_rows(
        fx("f1_scoreboard.json"), LEAGUES["f1"], UTC, date(2025, 1, 1), date(2025, 12, 31)
    )
    assert len(all_rows) == 10


def test_ufc_bout_rows(fx):
    rows = scoreboard_rows(
        fx("ufc_scoreboard.json"), LEAGUES["ufc"], UTC, date(2025, 6, 28), date(2025, 6, 28)
    )
    assert len(rows) == 11 and [r["round"] for r in rows] == list(range(1, 12))
    r = rows[0]
    assert (
        r["type"] == "match"
        and r["grouping"] == "Heavyweight"
        and r["tournament"].startswith("UFC 317")
    )
    assert (
        r["homeName"] == "Jhonata Diniz"
        and r["homeRecord"] == "9-2-0"
        and r["winnerId"] == r["homeId"]
    )
    assert r["venueName"] == "T-Mobile Arena" and r["oddsProvider"] is None


# --------------------------------------------------------------------------- standings


def test_standings_recursion_conference_to_division(fx):
    rows = standings_rows(fx("nfl_standings.json"), LEAGUES["nfl"], 2024)
    assert len(rows) == 8
    assert {r["group"] for r in rows} == {"AFC East", "AFC North", "NFC East", "NFC North"}
    r = rows[0]
    assert r["recordType"] == "standing" and r["teamAbbr"] == "BUF" and r["rank"] == 1
    assert (r["wins"], r["losses"], r["ties"], r["gamesPlayed"]) == (13, 4, 0, 17)
    assert r["pointsFor"] == 525 and r["pointDifferential"] == 157 and r["playoffSeed"] == 2
    assert r["streak"] == "L1" and r["clincher"] == "z"
    assert (r["homeRecord"], r["awayRecord"], r["divisionRecord"], r["conferenceRecord"]) == (
        "8-0",
        "5-4",
        "5-1",
        "9-3",
    )
    assert (
        r["stats"]["overall"] == "13-4"
        and r["stats"]["wins"] == 13.0
        and "lockedDivRank" in r["stats"]
    )
    assert r["season"] == 2024 and r["seasonType"] == 2


def test_epl_standings_note_points_rank(fx):
    rows = standings_rows(fx("epl_standings.json"), LEAGUES["epl"], 2024)
    assert len(rows) == 4 and rows[0]["teamName"] == "Liverpool"
    assert (
        rows[0]["points"] == 84
        and rows[0]["gamesPlayed"] == 38
        and rows[0]["note"] == "Champions League"
    )
    assert rows[0]["rank"] == 1 and rows[0]["rankChange"] == 0


def test_f1_standings_drivers_and_constructors(fx):
    rows = standings_rows(fx("f1_standings.json"), LEAGUES["f1"], 2024)
    assert [r["group"] for r in rows] == ["Driver Standings"] * 3 + ["Constructor Standings"] * 2
    assert (
        rows[0]["teamName"] == "Max Verstappen"
        and rows[0]["points"] == 437
        and rows[0]["teamLogo"].endswith("ned.png")
    )
    assert rows[3]["teamName"] == "McLaren" and rows[3]["points"] == 666


def test_empty_children_gives_no_rows(fx):
    assert standings_rows(fx("atp_standings.json"), LEAGUES["atp"], None) == []


# --------------------------------------------------------------------------- teams


def test_core_team_row(fx):
    r = team_row(fx("nba_core_team_1.json"), LEAGUES["nba"], 2025)
    assert r["recordType"] == "team" and r["teamId"] == "1" and r["slug"] == "atlanta-hawks"
    assert (
        r["abbreviation"] == "ATL"
        and r["logo"].endswith("atl.png")
        and r["venueName"] == "State Farm Arena"
    )
    assert r["espnUrl"].startswith("https://www.espn.com/nba/team/")
    assert "injuries" not in r


def test_find_team_by_abbr_id_name(fx):
    teams = site_teams(fx("epl_teams.json"))
    assert len(teams) == 20
    assert find_team(teams, "bou")["id"] == "349"
    assert find_team(teams, "349")["id"] == "349"
    assert find_team(teams, "Arsenal")["displayName"] == "Arsenal"
    assert find_team(teams, "bournemouth")["id"] == "349"
    assert find_team(teams, "nope") is None and find_team(teams, "") is None
    nfl = site_teams(fx("nfl_teams.json"))
    assert find_team(nfl, "KC")["id"] == "12" and find_team(nfl, "Kansas City")["id"] == "12"


# --------------------------------------------------------------------------- schedule / summary


def test_schedule_event_row(fx):
    rows = [team_game_row(e, LEAGUES["nfl"], NY) for e in fx("nfl_team_schedule.json")["events"]]
    assert rows[0]["homeAbbr"] == "KC" and rows[0]["homeScore"] == 27 and rows[0]["awayScore"] == 20
    assert rows[0]["week"] == 1 and rows[0]["season"] == 2024 and rows[0]["seasonType"] == 2
    assert rows[0]["broadcasts"] == ["NBC", "Peacock"] and rows[0]["dateLocal"] == "2024-09-05"


def test_summary_row_extras_and_exclusions(fx):
    doc = fx("nfl_summary.json")
    assert {"article", "news", "videos", "injuries"} <= set(doc)
    r = summary_row(doc, LEAGUES["nfl"], UTC, "401671789")
    assert r["recordType"] == "summary" and r["id"] == "401671789"
    assert r["homeAbbr"] == "KC" and r["homeScore"] == 27 and r["homeLinescores"] == [7, 6, 7, 7]
    assert r["venueName"] == "Arrowhead Stadium" and r["attendance"] == 73611 and r["week"] == 1
    assert r["leaders"][0] == {
        "category": "Passing Yards",
        "athleteName": "Patrick Mahomes",
        "teamAbbr": "KC",
        "value": "20/28, 291 YDS, 1 TD, 1 INT",
    }
    assert (
        r["scoringPlays"][0]["text"].startswith("Derrick Henry")
        and r["scoringPlays"][0]["period"] == 1
    )
    assert r["winProbabilityFinal"] == 1.0
    assert {"teamAbbr": "BAL", "stat": "firstDowns", "value": "25"} in r["boxscoreTeamStats"]
    assert {s["teamAbbr"] for s in r["boxscoreTeamStats"]} == {"KC", "BAL"}
    assert not FORBIDDEN_KEYS.intersection(r)
    assert "editorial" not in str(r) and "never" not in str(r)
    assert_clean(r)


def test_assert_clean_rejects():
    with pytest.raises(ValueError):
        assert_clean({"id": 1, "headlines": []})
