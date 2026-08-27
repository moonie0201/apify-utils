# ATP & WTA Tennis Scores & Draws — ESPN Feed

> **Unofficial.** Not affiliated with, endorsed by or sponsored by ESPN, The Walt Disney Company, the ATP, the WTA or any tournament.
> Data comes from the unauthenticated JSON feeds that espn.com itself uses; they are undocumented,
> **Disney's Terms of Use for espn.com restrict automated access**, and ESPN **may change or block access without notice** —
> if that happens the Actor fails the run and charges nothing, and we stop within **48 h** of any notice from ESPN or Disney.
> Scores, draws and results are facts; ESPN's articles, video and images are ESPN's copyrighted content and are not included.
> No ITF or Challenger events, no point-by-point, no rankings — ESPN's feed does not carry them.
> Odds are one sportsbook's pregame line shown on ESPN for team sports, for information only — **not for wagering**; tennis rows carry none.
> Player, tournament and tour names/logos are trademarks of their owners.
> **Removal requests:** [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md) — honoured in 48 h.
> **Privacy:** [PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).

Every **ATP and WTA match** on the ESPN feed — tour events and all four Grand Slams including qualifying — as one flat row per match: per-set scores, tiebreaks reflected in the result text, round, draw (singles, doubles, mixed), seeds, countries, court and ESPN's one-line result. You give it a date range; you get the matches played inside it, deduplicated by match id, with errors, off-season days and filtered-out matches free. No proxy, no key, no standby, no browser.

**What this is not:** no ITF or Challenger events, no point-by-point, no rankings mode, no player bios, no news or video. If your source of truth today is a bot-walled live-score site, this is a slower, stabler feed with fewer tournaments.

## Tennis on the ESPN feed

A tournament is one payload on ESPN: Wimbledon 2025 is 635 matches across five draws in a single response. The Actor queries each day in your window (`dates=YYYYMMDD`), keeps only matches whose start date falls inside the window and dedupes across days, so a 14-day Grand Slam window is 14 small requests and one charged row per match. Rows carry:

| Field | Tennis meaning |
|---|---|
| `tournament`, `tournamentId` | Event name and ESPN tournament id |
| `grouping` | `mens-singles`, `womens-singles`, `mens-doubles`, `womens-doubles`, `mixed-doubles` |
| `round` | Round name (`Round 1`, `Quarterfinal`, `Final`) |
| `homeName` / `awayName` | Player or pair (`Simone Bolelli / Andrea Vavassori`) |
| `homeRank` / `awayRank` | Seed when seeded, else null |
| `homeCountry` / `awayCountry` | Country from ESPN's flag |
| `homeLinescores` / `awayLinescores` | Games per set (`[6, 6, 6]` vs `[4, 2, 3]`) |
| `status` | `scheduled`, `live`, `final` (retirements and walkovers map to `final` with `statusDetail` set) |
| `resultText` | ESPN's result line with tiebreak points and retirements |
| `court`, `venueName` | Court and tournament venue |

`teams` doubles as a **player filter** — `["Alcaraz", "Swiatek"]` keeps only matches involving those names, and the dropped matches are free.

## Also available in this Actor

This listing is the same Actor as `espn-sports-scraper` with tennis defaults, so the other 31 leagues work here too: NFL, NCAA Football, NBA, WNBA, NCAA men's and women's basketball, MLB, NHL, MLS, NWSL, Premier League, EFL Championship, FA Cup, La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie, Primeira Liga, Liga MX, Saudi Pro League, Women's Super League, Champions League, Europa League, Conference League, UEFA Nations League, FIFA World Cup, Copa Libertadores, PGA Tour, Formula 1 and UFC — with standings, team schedules, team directories and game summaries for the team sports. Tennis has scoreboard mode only: standings, teams and schedule return a free notice.

## How to use

1. **Pick `atp`, `wta` or both.**
2. **Set `dateFrom` / `dateTo`** (YYYY-MM-DD, up to 366 days). Empty = today.
3. **Optionally filter** by player name (`teams`) or `status`, set `maxItems` as a cost ceiling, and run or schedule it. Each row is a snapshot at run time.

## Input

```json
{
  "leagues": ["atp", "wta"],
  "mode": "scoreboard",
  "dateFrom": "2025-07-06",
  "dateTo": "2025-07-13",
  "teams": [],
  "status": "all",
  "maxItems": 1000,
  "timezone": "Europe/London"
}
```

| Field | Default | Effect on cost |
|---|---|---|
| `leagues` | `["atp", "wta"]` | Both tours, or one |
| `dateFrom` / `dateTo` | today | Window of match dates, 366 days max |
| `teams` | `[]` | Player-name substrings; dropped matches are free |
| `status` | `all` | `scheduled`, `live`, `final` — filtered before billing |
| `maxItems` | `1000` | **Your cost ceiling** — hard stop on charged rows; `0` = no limit |
| `timezone` | `UTC` | Only affects `dateLocal` |

## Output

```json
{
  "recordType": "game", "type": "match", "id": "157754", "league": "atp", "espnPath": "tennis/atp",
  "season": 2025, "tournament": "Wimbledon", "tournamentId": "188", "round": "Round 4", "grouping": "mens-singles",
  "name": "Karen Khachanov vs Kamil Majchrzak", "date": "2025-07-06T10:10Z", "dateLocal": "2025-07-06",
  "status": "final", "statusDetail": "Final", "completed": true, "period": 3, "competitorType": "athlete",
  "homeId": "2367", "homeName": "Karen Khachanov", "homeShortName": "K. Khachanov", "homeCountry": "Russia", "homeRank": 17, "homeWinner": true, "homeLinescores": [6, 6, 6],
  "awayId": "2416", "awayName": "Kamil Majchrzak", "awayShortName": "K. Majchrzak", "awayCountry": "Poland", "awayRank": null, "awayWinner": false, "awayLinescores": [4, 2, 3],
  "winnerId": "2367", "resultText": "(17) Karen Khachanov (RUS) bt Kamil Majchrzak (POL) 6-4 6-2 6-3",
  "court": "No. 1 Court", "venueName": "London, Great Britain", "oddsProvider": null,
  "espnUrl": "https://www.espn.com/tennis/scoreboard/tournament/_/eventId/188-2025/competitionType/1", "scrapedAt": "2026-08-28T03:00:12Z"
}
```

A free off-season row:

```json
{"recordType": "league_summary", "league": "wta", "mode": "scoreboard", "window": "2025-12-01..2025-12-07", "requests": 7, "itemsFound": 0, "message": "no fixtures in window — off-season"}
```

`null` means ESPN did not publish the value. We never guess.

## Pricing

Flat events, no start fee: `apify-actor-start` is priced at **$0**.

| Event | Price | What it is |
|---|---|---|
| `game` | **$0.002** | One match row that passed your filters (also one game, F1 session or golf leaderboard row in the other leagues) |
| `row` | $0.001 | One standings or team-directory entry — team sports only |
| `summary` | $0.004 | One game-summary row — team sports only |

Free, always: `league_summary` and `error` rows, matches removed by `teams` or `status`, days with no play, the run that hits a 403.

| Run shape | Charged rows | Cost |
|---|---|---|
| One day of a Grand Slam, both tours, about 60 matches | 60 × `game` | **$0.12** |
| Wimbledon 2025, ATP only, all draws (635 matches) | 635 × `game` | **$1.27** |
| A season of ATP + WTA tour play, about 6,000 matches | 6,000 × `game` | **$12.00** |
| One player's matches for a month, 8 matches | 8 × `game` | **$0.016** |

Public prices as of 2026-08-28 (Apify Store API): extractify-labs charges $0.001 per row on a bot-walled live-score source; crawlstone does not publish a price, so none is quoted. Set `maxItems` or `ACTOR_MAX_TOTAL_CHARGE_USD` to bound any run.

## Reliability and the User-Agent rule

ESPN's edge refuses browser-style User-Agents on these JSON endpoints and answers tool-style ones. The Actor sends **one fixed, honest tool User-Agent** — the HTTP library's own default — never a browser identity, never a second identity, and there is no input to change it. A 403 pushes one free error row and fails the run without charging. At most four requests run at a time, with two retries on server errors and `Retry-After` honoured.

## Integrations and AI agents

Run the Actor from n8n, Make or Zapier and read the **Games** dataset view; export CSV from the Storage tab. Every field carries a description for MCP clients; set `maxItems` on every agent call and `ACTOR_MAX_TOTAL_CHARGE_USD` on the run. Errors are typed dataset rows, never a traceback.

## Limitations you should know before you buy

- **No ITF or Challenger** tournaments, **no point-by-point**, **no rankings** — ESPN's feed stops at the tours and the Slams.
- The feed is undocumented and unauthenticated; ESPN may change or block it, and a block fails the run without charging.
- ATP history on the feed starts around 2015; earlier dates return free off-season rows.
- Draws are whole-tournament payloads filtered to your dates; the tournament venue is used when a match has none.
- Tennis has no standings, team or summary mode; those modes exist for the team sports also carried by this Actor.
- Odds are never present on tennis rows.

## Privacy

Rows name professional players by display name and country as ESPN publishes them; no biographical data, rankings, earnings or injuries; the Actor opens no outbound connection to the developer and embeds no developer credential. Everything lands in your own Apify dataset and we hold no copy.

## FAQ

**Is this legal?** Here is exactly what it does: it calls three ESPN JSON hosts (`site.api.espn.com`, `site.web.api.espn.com`, `sports.core.api.espn.com`) without authentication, at most four requests at a time, with one fixed tool User-Agent. It does not fetch HTML pages, articles, video or images, and it uses no login, no proxy and no User-Agent rotation. The other side: Disney's Terms of Use for espn.com restrict automated access; ESPN may withdraw access at any time, and we stop within 48 h of any notice. Match scores and draws are facts; ESPN's editorial content is ESPN's and is not in the output. Rightsholders who want something out use [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md).

**Challenger, ITF or rankings?** Not on ESPN's feed — no ITF/Challenger, no point-by-point, no rankings — so not here.

**Why did my run fail with 403?** ESPN's edge refused the Actor's User-Agent. Nothing was charged and the Actor never retries with a different identity.

**Can I get live scores?** Schedule the Actor; each row is a snapshot with `status` and `period` (sets completed) at run time.

**Why are there matches dated before my window?** There are none — the whole draw is fetched, then filtered to your dates before anything is billed.

**How do I export to CSV?** Storage → the **Games** view → Export.

## Support

Open an issue on the Actor's Issues tab or at <https://github.com/moonie0201/apify-utils/issues>, or email **mooniegilog@gmail.com**. Every issue gets a reply within 14 days, usually within 48 hours. Removal, takedown, copyright or privacy requests jump the queue and are answered within 48 hours.

## Disclaimer

This Actor is unofficial. It is not affiliated with, endorsed by or sponsored by ESPN, The Walt Disney Company, the ATP, the WTA, any tournament, team or sportsbook. It reads the unauthenticated JSON feeds that espn.com itself uses; those feeds are undocumented, Disney's Terms of Use for espn.com restrict automated access, and ESPN may change or block access without notice — in which case the run fails and charges nothing. Scores, draws and results are facts; ESPN's articles, video and images are ESPN's copyrighted content and are not included. Odds fields, where present on team-sport rows, reproduce one sportsbook's pregame line as shown on ESPN, for information only and not for wagering. Player, tournament, tour and team names and logos are trademarks of their owners; logos and flags are delivered as URLs only. We stop within 48 h of any notice from ESPN or Disney: [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md) · [PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).
