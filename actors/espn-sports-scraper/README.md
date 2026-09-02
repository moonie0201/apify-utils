# ESPN Sports Odds, Scores & Standings — 30 Leagues

> **Unofficial.** Not affiliated with, endorsed by or sponsored by ESPN, The Walt Disney Company or any league.
> Data comes from the unauthenticated JSON feeds that espn.com itself uses; they are undocumented,
> **Disney's Terms of Use for espn.com restrict automated access**, and ESPN **may change or block access without notice** —
> if that happens the Actor fails the run and charges nothing, and we stop within **48 h** of any notice from ESPN or Disney.
> Scores, schedules and standings are facts; ESPN's articles, video and images are ESPN's copyrighted content and are not included.
> Odds are one sportsbook's pregame line shown on ESPN, for information only — **not for wagering**.
> Team and league names/logos are trademarks of their owners.
> **Removal requests:** [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md) — honoured in 48 h.
> **Privacy:** [PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).

One flat row schema across **33 leagues and five modes** — scoreboard, team schedule, standings, team directory and game summary. NFL, NBA, WNBA, MLB, NHL, college football and basketball, MLS, NWSL, 18 more soccer competitions, ATP and WTA tennis with full draws, sets, rounds and seeds, PGA Tour leaderboards, Formula 1 sessions and UFC bouts. Date ranges go back to **2001** for MLB (`dates=20010405` returns 13 games), 2005 for the NFL and 2015 for the ATP. The single pregame line ESPN displays — spread, over/under and both moneylines — is copied into the row at no extra request; it is present on upcoming games and drops off once a game is final (13 of 13 NFL games on 2026-09-13 carried a full line, 68 of 68 college football games on 2026-09-05 carried spread and total). No proxy, no key, no standby, no browser — and every failure, filtered-out game and off-season notice is free.

**What this is not:** no news, articles, rosters, injuries, athlete bios, play-by-play or video — those are ESPN's editorial content and this Actor never requests them. No ITF or Challenger tennis, no cricket, no rugby, no NASCAR: ESPN's feeds do not carry them.

## Leagues and modes

| League(s) | scoreboard | schedule | standings | teams | summary | odds field |
|---|---|---|---|---|---|---|
| NFL, NCAA Football | game rows | yes | conference → division | yes | yes | pregame |
| NBA, WNBA, NCAA M/W Basketball | game rows | yes | yes | yes (NBA via the core feed) | yes | pregame |
| MLB, NHL | game rows | yes | league → division | yes | yes | pregame |
| 14 soccer leagues + UCL, UEL, UECL, Nations League, World Cup, Copa Libertadores | game rows | yes (numeric team id) | league table with notes | yes | yes | pregame |
| ATP, WTA tennis | match rows: sets, round, seed, court, result text | — | no standings (free notice) | — | — | none |
| PGA Tour | one leaderboard row per player, position and score to par | — | — | — | — | none |
| Formula 1 | one row per session (FP1 … Race) with the winner | — | driver and constructor standings | — | — | none |
| UFC | one row per bout, weight class, bout order | — | — | — | — | none |

Any other ESPN league can be passed as a raw path (`soccer/bra.1`, `hockey/nhl`) — an unknown path comes back as a free error row.

## How to use

1. **Pick leagues.** Multi-select, or paste raw `sport/league` paths.
2. **Pick a mode and window.** Scoreboard takes `dateFrom`/`dateTo` (YYYY-MM-DD, up to 366 days). Standings, schedule and teams take a `season`; summary takes `eventIds` from an earlier scoreboard run (`nfl/401772830`).
3. **Run it, or schedule it.** Each row is a snapshot at run time; a daily schedule over a rolling window is how you keep scores fresh.

The prefill (`nba` + `nfl`, today) finishes in well under a minute even in the off-season, when it returns only free `league_summary` rows.

### Date ranges done right

ESPN's scoreboard feed silently caps a response at 100 events unless `limit` is set, so this Actor fetches ranges in **7-day windows with `limit=1000`**: January 2025 in the NBA returns 232 games here versus 100 from a naive call, and June 2025 in MLB returns its full 401 games. College feeds are queried with ESPN's `groups` parameter (FBS = 80, Division I = 50) — 146 Division I men's basketball games on 2025-02-01 rather than the 16 the default returns. Tennis draws are whole-tournament payloads (Wimbledon = 635 matches in one response); the Actor queries each day in your window, keeps only matches dated inside it and dedupes by match id. F1 is queried per calendar year and split into sessions.

## Coming from pixflor, hgservices or scrapesage

| Their input | Here |
|---|---|
| `dateFrom` / `dateTo` (YYYY-MM-DD) | `dateFrom` / `dateTo` — same format |
| league codes (`nfl`, `nba`, `eng.1`, `mens-college-basketball`) | `leagues` keys (`nfl`, `nba`, `epl`, `ncaab`), or the raw ESPN path |
| `statusFilter` | `status` (`all`, `scheduled`, `live`, `final`) |
| `team` | `teams` (abbreviation, id or name; several allowed) |
| `maxResults` / `maxItems` | `maxItems` |

**What you gain:** standings, team schedules, team directories, tennis and game summaries in the same flat schema; no start fee (`apify-actor-start` is priced at $0); error rows, filtered-out games and off-season notices free; ranges fetched without the 100-event cap.

**What you lose, stated plainly:** no news, roster or odds-comparison modes; no proxy option (none is needed for this feed); no ITF or Challenger tennis.

Public prices as of 2026-08-28 (Apify Store API): hgservices charges $0.001 per row for a scoreboard-only actor and bills every row; pixflor charges $0.002 per row plus $0.002 per start; extractify-labs charges $0.001 per row on a different source. scrapesage and crawlstone do not publish a price, so none is quoted.

## Input

```json
{
  "leagues": ["nba", "nfl"],
  "mode": "scoreboard",
  "dateFrom": "2025-01-01",
  "dateTo": "2025-01-31",
  "teams": [],
  "status": "all",
  "includeOdds": true,
  "maxItems": 1000,
  "timezone": "America/New_York"
}
```

| Field | Type | Default | Effect on cost |
|---|---|---|---|
| `leagues` | array | `["nba", "nfl"]` | More leagues, more charged rows |
| `mode` | string | `scoreboard` | Decides which event is charged (`game`, `row` or `summary`) |
| `dateFrom` / `dateTo` | string | today | Scoreboard window, 366 days max |
| `teams` | array | `[]` | Games without these teams are dropped **before billing**; in schedule mode, the teams to fetch; tennis: player-name substring |
| `status` | string | `all` | `scheduled`, `live` or `final` — filtered before billing |
| `season` / `seasonType` | integer / string | ESPN's current (teams mode: the most recent complete season) | Standings, schedule, teams |
| `eventIds` | array | `[]` | Summary mode targets (`league/id`, or bare ids with one league) |
| `includeOdds` | boolean | `true` | No cost effect; blanks the six odds fields when off |
| `maxItems` | integer | `1000` | **Your cost ceiling** — hard stop on charged rows; `0` = no limit |
| `timezone` | string | `UTC` | Only affects `dateLocal`; unknown names fall back to UTC with a free notice |

## Output

One flat row per game, match, session or leaderboard entry. Standing and team rows share the dataset with their own columns; `league_summary` and `error` rows are free. Three dataset views: **Games**, **Standings**, **Teams**.

An NFL game row:

```json
{
  "recordType": "game", "type": "game", "id": "401772830", "league": "nfl", "espnPath": "football/nfl",
  "season": 2025, "seasonType": 2, "week": 1,
  "name": "Tampa Bay Buccaneers at Atlanta Falcons", "shortName": "TB @ ATL",
  "date": "2025-09-07T17:00Z", "dateLocal": "2025-09-07", "status": "final", "completed": true, "period": 4,
  "homeId": "1", "homeName": "Atlanta Falcons", "homeAbbr": "ATL", "homeScore": 20, "homeWinner": false, "homeRecord": "0-1", "homeLinescores": [7, 3, 3, 7],
  "awayId": "27", "awayName": "Tampa Bay Buccaneers", "awayAbbr": "TB", "awayScore": 23, "awayWinner": true, "awayLinescores": [0, 10, 7, 6],
  "winnerId": "27", "venueName": "Mercedes-Benz Stadium", "venueCity": "Atlanta", "attendance": 71610, "broadcasts": ["FOX"],
  "oddsProvider": null, "spread": null, "overUnder": null, "homeMoneyline": null, "awayMoneyline": null,
  "espnUrl": "https://www.espn.com/nfl/game/_/gameId/401772830/buccaneers-falcons", "scrapedAt": "2026-08-28T03:00:12Z"
}
```

A Wimbledon match row (tennis fills the same slots):

```json
{
  "recordType": "game", "type": "match", "id": "157754", "league": "atp", "tournament": "Wimbledon", "tournamentId": "188",
  "round": "Round 4", "grouping": "mens-singles", "name": "Karen Khachanov vs Kamil Majchrzak",
  "date": "2025-07-06T10:10Z", "status": "final", "competitorType": "athlete",
  "homeName": "Karen Khachanov", "homeCountry": "Russia", "homeRank": 17, "homeWinner": true, "homeLinescores": [6, 6, 6],
  "awayName": "Kamil Majchrzak", "awayCountry": "Poland", "awayLinescores": [4, 2, 3],
  "resultText": "(17) Karen Khachanov (RUS) bt Kamil Majchrzak (POL) 6-4 6-2 6-3", "liveNote": null,
  "court": "No. 1 Court", "venueName": "London, Great Britain"
}
```

ESPN writes one note line per tennis or UFC match and phrases it the same way whether the match is over or still on court, so the Actor splits it: `resultText` carries it only when `completed` is true, and while the match is unfinished the same text goes to `liveNote` — the state of play at fetch time, not a result — with `resultText` null.

```json
{"id": "184679", "status": "scheduled", "completed": false, "resultText": null, "liveNote": "Jurij Rodionov (AUT) bt Jacob Fearnley (GBR) 4-6 6-4 3-2"}
```

A Premier League standing row:

```json
{
  "recordType": "standing", "league": "epl", "season": 2024, "group": "English Premier League 2024-2025", "rank": 1,
  "teamId": "364", "teamName": "Liverpool", "teamAbbr": "LIV", "gamesPlayed": 38, "wins": 25, "losses": 4, "ties": 9,
  "points": 84, "pointsFor": 86, "pointsAgainst": 41, "pointDifferential": 45, "note": "Champions League",
  "stats": {"gamesPlayed": 38.0, "points": 84.0, "rank": 1.0, "overall": "25-9-4"}
}
```

A free off-season row:

```json
{"recordType": "league_summary", "league": "nba", "mode": "scoreboard", "window": "2026-08-28..2026-08-28", "requests": 1, "itemsFound": 0, "message": "no fixtures in window — off-season"}
```

`null` means ESPN did not publish the value. We never guess.

## Pricing

Three flat events, no start fee: `apify-actor-start` is priced at **$0**, so a run that fails or finds nothing costs nothing.

| Event | Price | What it is |
|---|---|---|
| `game` | **$0.002** | One game, tennis match, F1 session or golf leaderboard row that passed your filters |
| `row` | $0.001 | One standings entry or one team-directory entry |
| `summary` | $0.004 | One summary-mode row with leaders, scoring plays, team box-score stats and final win probability |

Free, always: `league_summary` and `error` rows, games removed by `teams` or `status`, off-season windows, unknown leagues or event ids, the run that hits a 403.

| Run shape | Charged rows | Cost |
|---|---|---|
| Today's NBA slate, 8 games | 8 × `game` | **$0.016** |
| Four leagues, one month, about 600 games | 600 × `game` | **$1.20** |
| NCAA men's basketball standings, 364 rows (verified 2025) | 364 × `row` | **$0.364** |
| 100 game summaries | 100 × `summary` | **$0.40** |

Set `maxItems` to bound any run, or `ACTOR_MAX_TOTAL_CHARGE_USD` to bound spend directly — when it is reached the Actor stops pushing, notes it in the league summary and the run still succeeds.

## Tennis on the ESPN feed

ATP and WTA tour events and all four Grand Slams including qualifying, every draw (singles, doubles, mixed), per-set scores with the tiebreak reflected in ESPN's result text, round name, court, seed (`homeRank`/`awayRank`), country and the one-line result. `teams` doubles as a player filter (`["Alcaraz"]`). The gap: no ITF or Challenger events, no point-by-point, no rankings mode yet. A tennis draw is one payload per tournament, so a 14-day Wimbledon window is 14 small requests and one charged row per match inside the window. A sibling listing, `tennis-scores-scraper`, is this same Actor with tennis defaults.

## Odds field

One provider (the sportsbook ESPN shows, currently DraftKings), pregame snapshot: `oddsProvider`, `oddsDetails`, `spread`, `overUnder`, `homeMoneyline`, `awayMoneyline`. Null for tennis, UFC, golf, F1 and for finished games — ESPN removes the line once the game is over (verified on the NBA's January 2025 results). Informational only; there are no bookmaker links in the output and nothing here is meant for wagering.

## Reliability and the User-Agent rule

ESPN's edge refuses browser-style User-Agents on these JSON endpoints and answers tool-style ones. This Actor sends **one fixed, honest tool User-Agent** — the HTTP library's own default string — never a browser identity, never a second identity, never a rotation, and there is no input to change it. If the edge answers 403, the Actor pushes one free error row and fails the run without charging. Requests run at most four at a time with two retries on server errors and `Retry-After` honoured; per-league failures become free error rows instead of aborting the run.

## Integrations

- **n8n / Make / Zapier** — run the Actor, then read dataset items; the `Games` view is spreadsheet-ready.
- **Google Sheets** — export the view as CSV from the Storage tab.
- **Slack** — schedule a daily run over today's window with `status: "final"` and post the rows.

## For AI agents and MCP

Every input and output field carries a description. Chain modes with `espnPath` + `id`: run scoreboard, take `nfl/401772830` into `eventIds`, run summary. Always set `maxItems` on an agent call, and `ACTOR_MAX_TOTAL_CHARGE_USD` on the run. Errors are typed dataset rows (`recordType: "error"`), never a traceback.

## Limitations you should know before you buy

- The feed is undocumented and unauthenticated; ESPN may change or block it, and a block fails the run without charging.
- Historical depth varies by league: MLB to 2001, NFL to 2005, ATP to 2015, most soccer leagues later.
- Teams mode reads the core feed, not the site feed, and returns the full team list for the most recent complete season. ESPN opens the next season's directory before it is filled — on 2026-08-28 the NBA's "current" season was 2027 and listed 13 clubs while 2026 listed all 30 — so with no `season` the Actor compares the two, charges for the fuller list, and pushes a free `league_summary` row saying which season it used. Pass `season` to pin one and it is used exactly as given.
- Tennis draws are whole-tournament payloads filtered to your dates; the tournament venue is used when a match has none.
- College scoreboards need ESPN's `groups` parameter; the Actor sets it (FBS, Division I).
- No play-by-play, news, rosters, athlete bios, injuries, video. Cricket, rugby and NASCAR are not carried. Odds are absent for tennis, UFC, golf, F1 and finished games.
- An unknown `timezone` falls back to UTC with a free notice; `dateLocal` is the only field it affects.

## Privacy

Rows name teams and, for individual sports and summary leaders, professional athletes by display name and country as ESPN publishes them; no biographical data, rosters, salaries or injuries; the Actor opens no outbound connection to the developer and embeds no developer credential. Everything lands in your own Apify dataset, and we hold no copy.

## FAQ

**Is this legal?** Here is exactly what it does: it calls three ESPN JSON hosts (`site.api.espn.com`, `site.web.api.espn.com`, `sports.core.api.espn.com`) without authentication, at most four requests at a time, with one fixed tool User-Agent. It does not fetch HTML pages, articles, video or images, it uses no login, no proxy and no User-Agent rotation. The other side: Disney's Terms of Use for espn.com restrict automated access; ESPN may withdraw access at any time, and we stop within 48 h of any notice. Scores, schedules and standings are facts; ESPN's editorial content is ESPN's and is not in the output. If you are a rightsholder and want something out, [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md) is the route.

**Why did my run fail with 403?** ESPN's edge refused the Actor's User-Agent. The run charged nothing. The Actor never retries with a different identity; if the block persists, the listing is withdrawn.

**Why is the odds field null?** Tennis, UFC, golf and F1 carry no line on ESPN, and finished games lose theirs. Set `includeOdds: false` if you never want it.

**Can I get live updates?** Schedule the Actor; each row is a snapshot with `status`, `period` and `clock` at run time.

**Challenger or ITF tennis?** Not on ESPN's feed, so not here.

**NASCAR, cricket, rugby?** Not carried.

**How do I export to CSV?** Storage → the **Games**, **Standings** or **Teams** view → Export.

## Support

Open an issue on the Actor's Issues tab or at <https://github.com/moonie0201/apify-utils/issues>, or email **mooniegilog@gmail.com**. Every issue gets a reply within 14 days, usually within 48 hours. Removal, takedown, copyright or privacy requests jump the queue and are answered within 48 hours.

## Disclaimer

This Actor is unofficial. It is not affiliated with, endorsed by or sponsored by ESPN, The Walt Disney Company, any league, team or sportsbook. It reads the unauthenticated JSON feeds that espn.com itself uses; those feeds are undocumented, Disney's Terms of Use for espn.com restrict automated access, and ESPN may change or block access without notice — in which case the run fails and charges nothing. Scores, schedules and standings are facts; ESPN's articles, video and images are ESPN's copyrighted content and are not included. The odds fields reproduce one sportsbook's pregame line as shown on ESPN, for information only and not for wagering. Team, league and event names and logos are trademarks of their owners; logos are delivered as URLs only. We stop within 48 h of any notice from ESPN or Disney: [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md) · [PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).
