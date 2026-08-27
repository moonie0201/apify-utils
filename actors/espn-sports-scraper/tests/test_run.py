"""End-to-end runs of ``src.main.main`` against respx-mocked ESPN hosts and a fake Actor."""

from __future__ import annotations

import re
from datetime import date

import pytest
from src import main as main_mod
from src.client import USER_AGENT
from src.main import BLOCKLIST_PATH, Budget, load_blocklist, parse_input
from src.normalize import FORBIDDEN_KEYS
from tests.conftest import html_403, json_response


def _route(espn, path: str, **params):
    """Mock ``SITE + path`` (with optional query params); returns the route."""
    pattern = re.escape(espn.site + path) + r"(\?.*)?$"
    if params:
        return espn.get(url__regex=pattern, params__contains=params)
    return espn.get(url__regex=pattern)


# --------------------------------------------------------------------------- input


def test_parse_input_defaults_and_types():
    inp = parse_input(
        {"leagues": ["nba"], "seasonType": "2", "season": "2025"}, today=date(2026, 8, 28)
    )
    assert inp.mode == "scoreboard" and inp.date_from == inp.date_to == date(2026, 8, 28)
    assert inp.season_type == 2 and inp.season == 2025 and inp.max_items == 0
    assert inp.tz.key == "UTC" and inp.status == "all" and inp.include_odds is True


def test_parse_input_unknown_timezone_and_span_clamp():
    inp = parse_input(
        {
            "leagues": ["nba"],
            "timezone": "Mars/Olympus",
            "dateFrom": "2024-01-01",
            "dateTo": "2026-01-01",
        }
    )
    assert inp.tz.key == "UTC"
    assert inp.date_to == date(2024, 12, 31)
    assert any("unknown timezone" in n for n in inp.notices) and any(
        "clamped" in n for n in inp.notices
    )
    assert parse_input({"leagues": ["nba"], "timezone": "Asia/Seoul"}).tz.key == "Asia/Seoul"


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"leagues": []},
        {"leagues": ["nba"], "mode": "news"},
        {"leagues": ["nba"], "dateFrom": "2025-13-01"},
        {"leagues": ["nba"], "dateFrom": "2025-02-02", "dateTo": "2025-02-01"},
    ],
)
def test_parse_input_rejects(raw):
    with pytest.raises(ValueError):
        parse_input(raw)


async def test_invalid_input_fails_run_with_free_row(actor):
    actor.input = {"leagues": ["nba"], "dateFrom": "2025-99-01"}
    await main_mod.main()
    assert actor.failed and "Invalid input" in actor.failed
    assert actor.rows("error") and not actor.charged_rows()


# --------------------------------------------------------------------------- charging helper


def test_allows_checks_max_items_and_sdk_remaining_independently(actor):
    actor.manager.caps = {"game": None}
    assert Budget().allows("game")
    b = Budget(max_items=2, pushed=2)
    assert not b.allows("game") and b.stopped == "maxItems reached"
    actor.manager.caps = {"game": 0}
    b = Budget()
    assert not b.allows("game") and b.stopped == "ACTOR_MAX_TOTAL_CHARGE_USD reached"


async def test_budget_uses_whole_charge_limit_not_half(actor):
    """The SDK count is what REMAINS; comparing it with the cumulative push count stopped
    runs at half the buyer's ACTOR_MAX_TOTAL_CHARGE_USD."""
    actor.manager.caps = {"game": 50}
    budget = Budget()
    for i in range(200):
        if not await budget.push_charged({"recordType": "game", "id": str(i)}, "game"):
            break
    assert budget.pushed == 50 and len(actor.charged_rows()) == 50
    assert budget.stopped == "ACTOR_MAX_TOTAL_CHARGE_USD reached"


async def test_failed_push_never_increments(actor):
    budget = Budget()
    actor.push_error = RuntimeError("dataset down")
    with pytest.raises(RuntimeError):
        await budget.push_charged({"recordType": "game", "id": "1"}, "game")
    assert budget.pushed == 0 and budget.charged == 0 and budget.counts == {}


async def test_budget_stops_when_sdk_reports_limit(actor):
    actor.manager.caps = {"game": 2}
    budget = Budget()
    assert await budget.push_charged({"recordType": "game", "id": "1"}, "game")
    assert await budget.push_charged({"recordType": "game", "id": "2"}, "game")
    assert budget.stopped == "ACTOR_MAX_TOTAL_CHARGE_USD reached"
    assert not await budget.push_charged({"recordType": "game", "id": "3"}, "game")
    assert budget.pushed == 2 and len(actor.charged_rows()) == 2


# --------------------------------------------------------------------------- scoreboard runs


async def test_ua_gate_403_fails_before_anything_else(actor, espn):
    espn.get(url__regex=re.escape(espn.site + "football/nfl/scoreboard") + r"$").mock(
        return_value=html_403()
    )
    nba = _route(espn, "basketball/nba/scoreboard").mock(return_value=json_response({"events": []}))
    actor.input = {"leagues": ["nba", "nfl"]}
    await main_mod.main()
    assert actor.failed == "ESPN edge returned 403; nothing charged"
    assert not actor.charged_rows() and not nba.called
    calls = [c for c in espn.calls]
    assert len(calls) == 1 and calls[0].request.headers["user-agent"] == USER_AGENT
    assert actor.rows("error")[0]["message"].startswith("ESPN edge returned 403")


async def test_403_mid_run_stops_and_fails(actor, espn, fx):
    _route(espn, "football/nfl/scoreboard", dates="20250907-20250913").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    _route(espn, "basketball/nba/scoreboard").mock(return_value=html_403())
    actor.input = {"leagues": ["nfl", "nba"], "dateFrom": "2025-09-07", "dateTo": "2025-09-13"}
    await main_mod.main()
    assert len(actor.charged_rows()) == 4
    assert actor.failed and "after 4 rows" in actor.failed
    uas = {c.request.headers["user-agent"] for c in espn.calls}
    assert uas == {USER_AGENT}


async def test_nfl_scoreboard_run_charges_game_and_pushes_summary(actor, espn, fx):
    route = _route(espn, "football/nfl/scoreboard", dates="20250907-20250913", limit="1000").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    actor.input = {
        "leagues": ["nfl"],
        "dateFrom": "2025-09-07",
        "dateTo": "2025-09-13",
        "timezone": "America/New_York",
    }
    await main_mod.main()
    assert actor.failed is None
    charged = actor.charged_rows()
    assert len(charged) == 4 and {e for _, e in charged} == {"game"}
    assert actor.charged == {"game": 4}
    summary = actor.rows("league_summary")
    assert len(summary) == 1 and summary[0]["itemsFound"] == 4 and summary[0]["requests"] == 1
    assert summary[0]["message"] == "4 rows pushed"
    assert route.call_count == 1 and "limit=1000" in str(route.calls[0].request.url)
    assert "Done: 4 game" in actor.status
    for row, _ in charged:
        assert not FORBIDDEN_KEYS.intersection(row)


async def test_filters_run_before_billing(actor, espn, fx):
    _route(espn, "football/nfl/scoreboard").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    actor.input = {
        "leagues": ["nfl"],
        "dateFrom": "2025-09-07",
        "dateTo": "2025-09-13",
        "teams": ["ATL"],
        "status": "final",
    }
    await main_mod.main()
    charged = actor.charged_rows()
    assert len(charged) == 1 and charged[0][0]["homeAbbr"] == "ATL"
    assert actor.rows("league_summary")[0]["itemsFound"] == 4
    actor.pushed.clear()
    actor.charged.clear()
    actor.input = {
        "leagues": ["nfl"],
        "dateFrom": "2025-09-07",
        "dateTo": "2025-09-13",
        "status": "live",
    }
    await main_mod.main()
    assert (
        not actor.charged_rows()
        and actor.rows("league_summary")[0]["message"] == "no fixtures in window — off-season"
    )


async def test_max_items_stops_and_reports(actor, espn, fx):
    _route(espn, "football/nfl/scoreboard").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    actor.input = {
        "leagues": ["nfl", "nba"],
        "dateFrom": "2025-09-07",
        "dateTo": "2025-09-13",
        "maxItems": 2,
    }
    await main_mod.main()
    assert len(actor.charged_rows()) == 2
    assert "maxItems reached" in actor.rows("league_summary")[0]["message"]
    assert "stopped: maxItems reached" in actor.status
    assert not [c for c in espn.calls if "basketball" in str(c.request.url)]  # nba never fetched


async def test_charge_limit_from_sdk_stops_cleanly(actor, espn, fx):
    _route(espn, "football/nfl/scoreboard").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    actor.manager.caps = {"game": 3}
    actor.input = {"leagues": ["nfl"], "dateFrom": "2025-09-07", "dateTo": "2025-09-13"}
    await main_mod.main()
    assert len(actor.charged_rows()) == 3 and actor.failed is None
    assert "ACTOR_MAX_TOTAL_CHARGE_USD reached" in actor.status


async def test_unlimited_cap_none_no_type_error(actor, espn, fx):
    _route(espn, "football/nfl/scoreboard").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    actor.manager.caps = {"game": None}
    actor.manager.priced = False  # local `apify run`: charged_count is 0 but rows still land
    actor.input = {
        "leagues": ["nfl"],
        "dateFrom": "2025-09-07",
        "dateTo": "2025-09-13",
        "maxItems": 0,
    }
    await main_mod.main()
    assert (
        len(actor.charged_rows()) == 4 and actor.failed is None and "Done: 4 game" in actor.status
    )


async def test_include_odds_false_blanks_the_line(actor, espn, fx):
    _route(espn, "football/nfl/scoreboard").mock(
        return_value=json_response(fx("nfl_scoreboard_odds.json"))
    )
    actor.input = {
        "leagues": ["nfl"],
        "dateFrom": "2026-09-03",
        "dateTo": "2026-09-15",
        "includeOdds": False,
    }
    await main_mod.main()
    assert actor.charged_rows() and all(
        r["oddsProvider"] is None and r["spread"] is None for r, _ in actor.charged_rows()
    )


async def test_unknown_league_and_400_path_are_free(actor, espn, fx):
    _route(espn, "soccer/xxx.9/scoreboard").mock(
        return_value=json_response(fx("league_400.json"), 400)
    )
    actor.input = {"leagues": ["nope", "soccer/xxx.9"]}
    await main_mod.main()
    errors = actor.rows("error")
    assert len(errors) == 2 and not actor.charged_rows() and actor.failed is None
    assert "unknown league path" in errors[0]["message"] and "HTTP 400" in errors[1]["message"]


async def test_nul_byte_league_is_a_free_row_not_a_crash(actor, espn):
    actor.input = {"leagues": ["foo/\x00bar", "soccer/x y"]}
    await main_mod.main()
    errors = actor.rows("error")
    assert len(errors) == 2 and all("unknown league path" in e["message"] for e in errors)
    assert len(espn.calls) == 1 and actor.failed is None  # the UA gate only


async def test_non_object_body_is_a_free_error_row(actor, espn):
    _route(espn, "basketball/nba/scoreboard").mock(return_value=json_response([]))
    actor.input = {"leagues": ["nba"]}
    await main_mod.main()
    assert actor.rows("error")[0]["message"] == "unexpected payload shape"
    assert actor.failed is None and not actor.charged_rows()


async def test_malformed_event_is_a_free_error_row(actor, espn):
    bad = {"events": [{"id": "1", "competitions": [{"competitors": ["not-a-dict"]}]}]}
    _route(espn, "basketball/nba/scoreboard").mock(return_value=json_response(bad))
    _route(espn, "football/nfl/scoreboard").mock(return_value=json_response({"events": []}))
    actor.input = {"leagues": ["nba", "nfl"]}
    await main_mod.main()
    errors = actor.rows("error")
    assert len(errors) == 1 and errors[0]["message"].startswith("unexpected payload: ")
    assert errors[0]["league"] == "nba" and errors[0]["requests"] == 1
    assert actor.rows("league_summary")[0]["league"] == "nfl"  # the run went on
    assert actor.failed is None and "Done:" in actor.status


async def test_unknown_timezone_free_row_and_utc(actor, espn, fx):
    _route(espn, "football/nfl/scoreboard").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    actor.input = {
        "leagues": ["nfl"],
        "dateFrom": "2025-09-07",
        "dateTo": "2025-09-13",
        "timezone": "Not/AZone",
    }
    await main_mod.main()
    assert any("unknown timezone" in r["message"] for r in actor.rows("error"))
    assert actor.charged_rows()[0][0]["dateLocal"] == "2025-09-07"


async def test_windows_and_groups_in_requests(actor, espn):
    route = _route(espn, "basketball/mens-college-basketball/scoreboard").mock(
        return_value=json_response({"events": []})
    )
    actor.input = {"leagues": ["ncaab"], "dateFrom": "2025-01-01", "dateTo": "2025-01-31"}
    await main_mod.main()
    assert route.call_count == 5
    for call in route.calls:
        q = dict(call.request.url.params)
        assert (
            q["limit"] == "1000"
            and q["groups"] == "50"
            and re.fullmatch(r"\d{8}-\d{8}", q["dates"])
        )


async def test_windows_are_fetched_one_at_a_time_and_a_403_stops_at_once(actor, espn, monkeypatch):
    """§3.4 step 8: one payload alive at a time (normalised before the next request), and a
    403 on window k means exactly k requests — never the whole year fired in parallel."""
    seen_at: list[int] = []
    real_rows = main_mod.scoreboard_rows
    runner_client: list = []

    def counting_rows(payload, *a, **k):
        seen_at.append(runner_client[0].requests)
        return real_rows(payload, *a, **k)

    monkeypatch.setattr(main_mod, "scoreboard_rows", counting_rows)
    orig_init = main_mod.Runner.__init__

    def init(self, client, *a, **k):
        runner_client.append(client)
        orig_init(self, client, *a, **k)

    monkeypatch.setattr(main_mod.Runner, "__init__", init)

    def respond(req):
        n = len(route.calls) + 1  # this call is recorded after the side effect returns
        return html_403() if n == 7 else json_response({"events": []})

    route = _route(espn, "basketball/nba/scoreboard").mock(side_effect=respond)
    actor.input = {"leagues": ["nba"], "dateFrom": "2025-01-01", "dateTo": "2025-12-31"}
    await main_mod.main()
    assert route.call_count == 7  # 53 windows; the 7th was the 403 and nothing followed
    assert seen_at == [2, 3, 4, 5, 6, 7]  # gate + k windows: each normalised before the next
    assert actor.failed and "403" in actor.failed


async def test_f1_uses_year_dates(actor, espn, fx):
    route = _route(espn, "racing/f1/scoreboard").mock(
        return_value=json_response(fx("f1_scoreboard.json"))
    )
    actor.input = {"leagues": ["f1"], "dateFrom": "2025-03-14", "dateTo": "2025-03-16"}
    await main_mod.main()
    assert route.call_count == 1 and dict(route.calls[0].request.url.params)["dates"] == "2025"
    assert [r["round"] for r, _ in actor.charged_rows()] == ["FP1", "FP2", "FP3", "Qual", "Race"]


async def test_tennis_queries_each_day_and_dedupes(actor, espn, fx):
    route = _route(espn, "tennis/atp/scoreboard").mock(
        return_value=json_response(fx("atp_scoreboard.json"))
    )
    actor.input = {"leagues": ["atp"], "dateFrom": "2025-07-05", "dateTo": "2025-07-07"}
    await main_mod.main()
    assert route.call_count == 3
    assert sorted(dict(c.request.url.params)["dates"] for c in route.calls) == [
        "20250705",
        "20250706",
        "20250707",
    ]
    charged = actor.charged_rows()
    assert len(charged) == 16 and len({r["id"] for r, _ in charged}) == 16
    assert actor.rows("league_summary")[0]["itemsFound"] == 16


async def test_tennis_player_filter(actor, espn, fx):
    _route(espn, "tennis/atp/scoreboard").mock(
        return_value=json_response(fx("atp_scoreboard.json"))
    )
    actor.input = {"leagues": ["atp"], "dateFrom": "2025-07-06", "teams": ["Khachanov"]}
    await main_mod.main()
    assert [r["homeName"] for r, _ in actor.charged_rows()] == ["Karen Khachanov"]


# --------------------------------------------------------------------------- blocklist


def test_load_blocklist_parses_comments_and_case(tmp_path):
    f = tmp_path / "blocklist.txt"
    f.write_text("# takedown 2026-08-28\nNBA  # league\n\n  soccer/bra.1\n12\n")
    assert load_blocklist(f) == {"nba", "soccer/bra.1", "12"}
    assert load_blocklist(tmp_path / "missing.txt") == frozenset()
    assert load_blocklist(BLOCKLIST_PATH) == frozenset()  # the shipped file holds comments only


async def test_blocked_league_makes_no_request_and_blocked_id_is_never_pushed(
    actor, espn, fx, tmp_path, monkeypatch
):
    nba = _route(espn, "basketball/nba/scoreboard").mock(return_value=json_response({"events": []}))
    _route(espn, "football/nfl/scoreboard").mock(
        return_value=json_response(fx("nfl_scoreboard.json"))
    )
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("nba\n1\n")  # ATL's id in the fixture is "1"
    monkeypatch.setattr(main_mod, "BLOCKLIST_PATH", blocklist)
    actor.input = {"leagues": ["nba", "nfl"], "dateFrom": "2025-09-07", "dateTo": "2025-09-13"}
    await main_mod.main()
    assert not nba.called
    assert actor.rows("error")[0]["message"] == "league 'nba' removed on request (TAKEDOWN.md)"
    charged = actor.charged_rows()
    assert len(charged) == 3 and all("1" not in (r["homeId"], r["awayId"]) for r, _ in charged)
    assert actor.rows("league_summary")[0]["itemsFound"] == 4  # seen, filtered before billing


# --------------------------------------------------------------------------- other modes


async def test_standings_charges_row_and_walks_divisions(actor, espn, fx):
    route = espn.get(
        url__regex=re.escape(espn.standings + "football/nfl/standings") + r"(\?.*)?$"
    ).mock(return_value=json_response(fx("nfl_standings.json")))
    actor.input = {"leagues": ["nfl"], "mode": "standings", "season": 2024}
    await main_mod.main()
    assert dict(route.calls[0].request.url.params) == {"season": "2024", "level": "3"}
    charged = actor.charged_rows()
    assert len(charged) == 8 and {e for _, e in charged} == {"row"}
    assert charged[0][0]["recordType"] == "standing" and charged[0][0]["group"] == "AFC East"


async def test_standings_empty_children_is_free(actor, espn, fx):
    espn.get(url__regex=re.escape(espn.standings + "tennis/atp/standings") + r"(\?.*)?$").mock(
        return_value=json_response(fx("atp_standings.json"))
    )
    actor.input = {"leagues": ["atp"], "mode": "standings"}
    await main_mod.main()
    assert not actor.charged_rows()
    assert actor.rows("league_summary")[0]["message"] == "no standings for this league"


async def test_teams_mode_resolves_core_refs(actor, espn, fx):
    listing = fx("nba_core_teams.json")
    espn.get(
        url__regex=re.escape(espn.core + "basketball/leagues/nba/seasons/2025/teams") + r"(\?.*)?$"
    ).mock(return_value=json_response(listing))
    team = fx("nba_core_team_1.json")
    detail = espn.get(
        url__regex=re.escape(espn.core + "basketball/leagues/nba/seasons/2025/teams/")
        + r"\d+(\?.*)?$"
    ).mock(side_effect=lambda req: json_response(dict(team, id=req.url.path.rsplit("/", 1)[-1])))
    actor.input = {"leagues": ["nba"], "mode": "teams", "season": 2025}
    await main_mod.main()
    assert detail.call_count == 30
    charged = actor.charged_rows()
    assert len(charged) == 30 and {e for _, e in charged} == {"row"}
    assert all(r["recordType"] == "team" and r["season"] == 2025 for r, _ in charged)
    assert all(str(c.request.url).startswith("https://") for c in detail.calls)


async def test_teams_mode_403_on_a_ref_stops_without_firing_the_rest(actor, espn, fx):
    listing = fx("nba_core_teams.json")
    espn.get(
        url__regex=re.escape(espn.core + "basketball/leagues/nba/seasons/2025/teams") + r"(\?.*)?$"
    ).mock(return_value=json_response(listing))
    detail = espn.get(
        url__regex=re.escape(espn.core + "basketball/leagues/nba/seasons/2025/teams/")
        + r"\d+(\?.*)?$"
    ).mock(return_value=html_403())
    actor.input = {"leagues": ["nba"], "mode": "teams", "season": 2025}
    await main_mod.main()
    assert actor.failed and "403" in actor.failed
    assert detail.call_count <= main_mod.CONCURRENCY  # one chunk, not all 30 refs


async def test_teams_mode_current_season_from_league_doc(actor, espn, fx):
    espn.get(url__regex=re.escape(espn.core + "basketball/leagues/nba") + r"(\?.*)?$").mock(
        return_value=json_response({"season": {"year": 2025}})
    )
    espn.get(
        url__regex=re.escape(espn.core + "basketball/leagues/nba/seasons/2025/teams") + r"(\?.*)?$"
    ).mock(
        return_value=json_response(
            {"items": [{"$ref": espn.core + "basketball/leagues/nba/seasons/2025/teams/1"}]}
        )
    )
    espn.get(
        url__regex=re.escape(espn.core + "basketball/leagues/nba/seasons/2025/teams/1")
        + r"(\?.*)?$"
    ).mock(return_value=json_response(fx("nba_core_team_1.json")))
    actor.input = {"leagues": ["nba"], "mode": "teams"}
    await main_mod.main()
    assert len(actor.charged_rows()) == 1 and actor.charged_rows()[0][0]["season"] == 2025


async def test_teams_mode_athlete_sport_is_free(actor, espn):
    actor.input = {"leagues": ["atp"], "mode": "teams"}
    await main_mod.main()
    assert (
        not actor.charged_rows()
        and actor.rows("league_summary")[0]["message"] == "no teams for this league"
    )


async def test_schedule_mode_resolves_team_and_uses_numeric_id(actor, espn, fx):
    _route(espn, "football/nfl/teams").mock(return_value=json_response(fx("nfl_teams.json")))
    sched = _route(espn, "football/nfl/teams/12/schedule").mock(
        return_value=json_response(fx("nfl_team_schedule.json"))
    )
    actor.input = {
        "leagues": ["nfl"],
        "mode": "schedule",
        "teams": ["KC", "Nowhere FC"],
        "season": 2024,
        "seasonType": "2",
    }
    await main_mod.main()
    assert sched.call_count == 1 and dict(sched.calls[0].request.url.params) == {
        "season": "2024",
        "seasontype": "2",
    }
    charged = actor.charged_rows()
    assert len(charged) == 2 and {e for _, e in charged} == {"game"}
    assert actor.rows("league_summary")[0]["message"] == "unknown teams: Nowhere FC"


async def test_schedule_soccer_uses_numeric_id_not_abbr(actor, espn, fx):
    _route(espn, "soccer/eng.1/teams").mock(return_value=json_response(fx("epl_teams.json")))
    sched = _route(espn, "soccer/eng.1/teams/349/schedule").mock(
        return_value=json_response({"events": []})
    )
    actor.input = {"leagues": ["epl"], "mode": "schedule", "teams": ["BOU"]}
    await main_mod.main()
    assert sched.call_count == 1 and "/teams/349/schedule" in str(sched.calls[0].request.url)


async def test_schedule_without_teams_is_free_error(actor, espn):
    actor.input = {"leagues": ["nfl"], "mode": "schedule"}
    await main_mod.main()
    assert (
        actor.rows("error")[0]["message"] == "schedule mode needs teams"
        and not actor.charged_rows()
    )


async def test_summary_mode_charges_summary_and_unknown_id_is_free(actor, espn, fx):
    def respond(req):
        if dict(req.url.params)["event"] == "401671789":
            return json_response(fx("nfl_summary.json"))
        return json_response(fx("summary_404.json"), 404)

    _route(espn, "football/nfl/summary").mock(side_effect=respond)
    actor.input = {
        "leagues": ["nfl"],
        "mode": "summary",
        "eventIds": ["nfl/401671789", "1", "nba/99"],
    }
    await main_mod.main()
    charged = actor.charged_rows()
    assert (
        len(charged) == 1
        and charged[0][1] == "summary"
        and charged[0][0]["recordType"] == "summary"
    )
    assert charged[0][0]["leaders"] and "winProbabilityFinal" in charged[0][0]
    note = actor.rows("league_summary")[0]["message"]
    assert note.startswith("unknown event ids: 1") and "nba/99" not in note
