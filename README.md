# apify-utils

Three self-contained pay-per-event Apify Actors and one derived listing, each built from
its own directory with no shared package: a sports-scores reader for the JSON feeds behind
espn.com (plus a tennis-first listing of the same image), a PDF text/table/OCR extractor,
and a YouTube thumbnail downloader. No proxies, no logins, no browser, no standby mode;
every listing charges only for rows that were actually pushed, and error rows are free.

## Actors

| Directory | Store listing | Primary event |
|---|---|---|
| `actors/espn-sports-scraper` | <https://apify.com/acotr_moonie/espn-sports-scraper> | `game` |
| `actors/tennis-scores-scraper` (derived from `espn-sports-scraper`) | <https://apify.com/acotr_moonie/tennis-scores-scraper> | `game` |
| `actors/pdf-text-extractor` | <https://apify.com/acotr_moonie/pdf-text-extractor> | `page` |
| `actors/youtube-thumbnail-downloader` | <https://apify.com/acotr_moonie/youtube-thumbnail-downloader> | `video` |

## Layout

| Path | What lives there |
|---|---|
| `actors/<name>/` | one Actor: `.actor/*.json`, `src/`, `tests/`, `Dockerfile`, `requirements.txt`, `README.md`, `logo-512.png` |
| `actors/<name>/.actor/pricing.json` | the Store listing text and PPE events `scripts/set_pricing.py` uploads (never read by the Actor itself) |
| `scripts/validate_schemas.py` | schema and listing lint, run per Actor before every push |
| `scripts/set_pricing.py` | appends one PAY_PER_EVENT pricing record and the listing fields via the Apify API |
| `scripts/build_derived.py` | assembles `build/tennis-scores-scraper/` from the ESPN Actor's code and the tennis listing files |
| `.github/workflows/ci.yml` | ruff + offline pytest + schema lint, one matrix job per Actor directory |

Each Actor directory is its own Docker build context and must build alone
(`cd actors/<name> && docker build .`). Duplication between Actors is deliberate; nothing is
imported across directories. The one exception is the derived tennis listing, which reuses
the ESPN image byte for byte: `scripts/build_derived.py` copies the code next to the tennis
`.actor/`, README and logo, and `apify push` runs from that build directory.

## Development

```bash
cd actors/<name>
uv venv .venv && uv pip install -r requirements.txt -r ../../requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m pytest -q -m "not live"    # offline; -m live opts into the real endpoints
cd ../.. && python scripts/validate_schemas.py actors/<name>
```

Deploy order per Actor: `apify push` (private) → `validate_schemas.py` → prefill run with
`ACTOR_MAX_TOTAL_CHARGE_USD=0.05` → `scripts/set_pricing.py <name> --token "$(apify auth token)"`
→ logo and screenshots in the Console → `set_pricing.py <name> --publish` last.

## Removal, takedown and privacy

[`TAKEDOWN.md`](TAKEDOWN.md) — 48 hours, no argument, no justification asked for; states
plainly what a takedown can and cannot reach. [`PRIVACY.md`](PRIVACY.md) — what is held
(nothing, per Actor) and how to reach us about a person.

Contact: `mooniegilog@gmail.com`.

## License

MIT — see [`LICENSE`](LICENSE), including the scope note on test fixtures.

## Disclaimer

Unofficial. Not affiliated with, sponsored by or endorsed by ESPN, The Walt Disney Company,
any sports league, YouTube, Google LLC, the PDFium or Tesseract projects, or Apify. Product,
league and team names are trademarks of their owners; scores are facts, and the articles,
video, images, documents and thumbnails these tools point at remain their authors' works.
