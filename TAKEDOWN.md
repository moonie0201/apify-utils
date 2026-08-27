# Takedown and removal

We honour every removal request within **48 hours**, without argument and without asking you
to justify it. You do not need to be a lawyer, send a formal notice, or prove ownership to a
standard we set.

## How to reach us

**Email `mooniegilog@gmail.com`** — the fastest route and the right one for anything
involving personal data or that you do not want on a public page. Put `takedown` in the
subject.

Or **open an issue** at <https://github.com/moonie0201/apify-utils/issues>, or use the
**Issues** tab of the Actor on the Store (`https://apify.com/acotr_moonie/<actor-name>/issues`)
— both reach the same person. Issues are public: **if your request involves personal data,
open an issue containing only the words `private request`** and we reply there with a private
channel within one business day. Title the issue `TAKEDOWN` or `REMOVAL` so it is not queued
behind bug reports.

## What we will do

| Actor | You are | What happens within 48 h |
|---|---|---|
| `youtube-thumbnail-downloader` | A creator, a person depicted in a thumbnail, or YouTube/Google | The video id goes into the Actor's `blocklist.txt`, the image is rebuilt and pushed, and **future runs** skip it. A notice from YouTube or Google about the tool itself = the Actor stops: a `FREE` pricing record is appended, the listing is unpublished. |
| `pdf-text-extractor` | The rights-holder of a document a buyer pointed the Actor at | We hold no copy of any document. The Actor fetches only the URLs a buyer supplies, with one plain GET and an identifying User-Agent, so the place to act is the **hosting site or the user who supplied the URL**. A hostname you ask us to stop fetching goes into the Actor's `blocklist.txt` and future runs refuse it. |
| `espn-sports-scraper` / `tennis-scores-scraper` | A league, team, athlete or venue named in a row | The identifier goes into the Actor's `blocklist.txt`, the image is rebuilt and pushed, and future runs skip it. Athletes appear only as public performance facts. |
| `espn-sports-scraper` / `tennis-scores-scraper` | **ESPN or The Walt Disney Company** | Runbook, no negotiation step: within 48 h of the notice, **both** listings (they share one feed and one image) get a `FREE` pricing record appended, then `isPublic: false`, then deletion. We never continue after notice. |

## The limit we cannot fix, stated plainly

A run delivers rows and files into the buyer's own Apify account. We hold no copy, no
connection and no credential for it, so we can stop **future runs** but cannot reach into a
dataset or key-value store someone already has. The same applies to anything a buyer
downloaded from their own account before the removal.

A blocklist is a file baked into the Actor image, so an entry takes effect only after a
rebuild and `apify push`; that step is part of the 48-hour promise, not something after it.

## Blocklist

Each Actor directory that can skip a source carries a `blocklist.txt` — one identifier per
line (video id, hostname, league code or team), `#` comments allowed — read at start-up and
applied before any request. It is the mechanism, not a promise about one: a test in each
Actor asserts a blocked identifier produces no request.

## No affiliation

These tools are unofficial. They are not affiliated with, endorsed by, or sponsored by ESPN,
The Walt Disney Company, any sports league, YouTube, Google LLC, the PDFium or Tesseract
projects, Apify, or any site whose documents a buyer points them at. All trademarks belong to
their owners.
