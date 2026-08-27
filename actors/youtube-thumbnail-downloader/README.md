# YouTube Thumbnail Downloader — All Sizes + WebP

> **Unofficial.** Not affiliated with, endorsed by or sponsored by YouTube or Google LLC.
> This Actor reads YouTube's public thumbnail CDN and public oEmbed endpoint.
> YouTube may change or block access without notice — if that happens the run fails and charges nothing.
> Thumbnails and titles are the creators' and YouTube's content; you are responsible for
> downstream use. **Removal requests:** [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md)
> — honoured in 48 hours, for future runs. **Privacy:** [PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).

Every thumbnail size of a YouTube video in **one row**: `maxresdefault` down to `default`, each with a real availability flag from a HEAD probe (never guessed), the matching **WebP** URL, the original-aspect auto-generated `oar` frames (up to 1920×1080, or 1080×1920 for Shorts), **title and channel** from YouTube's public oEmbed endpoint, and the files written into **your own** key-value store. Videos that do not exist, duplicates, playlists and bad input come back as free rows. No API key, no browser, no proxy.

## What this thumbnail downloader does

- **CDN pattern** — `https://i.ytimg.com/vi/{videoId}/{size}.jpg` for JPEG and `/vi_webp/{videoId}/{size}.webp` for WebP. The Actor HEAD-probes six names per video and reports `available`, `bytes` and `etag` for each.
- **Sizes** — `maxresdefault` (1280×720), `sddefault` (640×480), `hqdefault` (480×360), `mqdefault` (320×180), `default` (120×90), plus `oar1`–`oar3` original-aspect auto frames.
- **oEmbed** — `https://www.youtube.com/oembed?url=…watch?v={id}` gives `title`, `authorName`, `authorUrl`. One extra request per video, no key, and the row is still delivered if it fails.
- **No key, no browser** — two public endpoints, plain HTTPS, concurrency capped at 10.
- **Files in your store** — `{videoId}_{size}.{jpg|webp}` in the run's default key-value store; the row carries `storeId`, `key` and the record URL.

## Sizes and when they exist

| Name | Dimensions | Typically missing when |
|---|---|---|
| `maxresdefault` | 1280×720 (this is the largest static thumbnail; it is not 1080p) | Old or low-resolution uploads, many auto-generated thumbnails |
| `sddefault` | 640×480 | Same as above — the 2005-era `jNQXAC9IVRw` has neither |
| `hqdefault` | 480×360 | Almost never; this is the safe fallback |
| `mqdefault` | 320×180 | Almost never |
| `default` | 120×90 | Almost never |
| `oar1`, `oar2`, `oar3` | Up to 1920×1080 (1080×1920 for vertical videos) — rendered at the upload's source resolution, so older or low-resolution videos get 1280×720 or smaller; the measured `width`/`height` are on the row when `oar` is selected | Auto-generated frames, **not** the creator's custom thumbnail; `oar1` exists for most videos, `oar2`/`oar3` less often |

Shorts usually have no custom thumbnail at all — you get the auto frames. A 404 from the CDN is a real 404 (the body is a 1 KB grey placeholder image); the Actor decides by status code and never saves that placeholder.

## How to use

1. **Paste video URLs or ids**, one per line. `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, `/live/`, `m.youtube.com`, `music.youtube.com`, `youtube-nocookie.com`, `attribution_link`, an `i.ytimg.com` thumbnail URL or a bare 11-character id all work. `?t=`, `&list=`, `?si=` are ignored.
2. **Pick sizes and format.** `best` (default) saves the largest size that exists; `all` saves every size; `oar` adds the original-aspect auto frames (up to 1920×1080 — see the table above). `jpg`, `webp` or `both`.
3. **Run.** Rows land in the dataset, files in the key-value store. Set `maxVideos` to cap what you pay for.

Screenshots on this listing show the input form only.

## Coming from my-actor-73, parsebird or epicscrapers

| Their field | Here | Note |
|---|---|---|
| `videoUrls`, `urls`, `startUrls[].url` | `videos` | Accepted as-is |
| `quality`, `thumbnailQuality` (one string) | `sizes: [value]` | `maxres`, `high`, `sd`, `medium`, `low` are mapped |
| `uploadToKeyValueStore`, `saveToStore` | `saveImages` | Accepted as-is |

**What you gain:** every size on one row with real availability, WebP URLs, vertical-Short detection, title and channel, `etag` for change detection, and free failure rows. **What you lose:** the single-URL `status: "ok"` row shape — the one URL you used to read is now `best.url`.

Prices visible in the public Apify Store API, as of 2026-08-28: my-actor-73 charges `apify-default-dataset-item` at $0.01 per row including failed lookups; parsebird $0.003 per result; happitap `youtube_thumbnail_extracted` $0.001. This Actor charges $0.005 per delivered video and nothing for failures — see Pricing.

## Input

```json
{
  "videos": [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/9bZkp7q19f0?si=abc",
    "https://www.youtube.com/shorts/029uWKeBdEo",
    "jNQXAC9IVRw"
  ],
  "sizes": ["best"],
  "format": "jpg",
  "includeMetadata": true,
  "saveImages": true,
  "maxVideos": 1000
}
```

| Field | Default | Cost effect |
|---|---|---|
| `videos` | — | One row per unique video; only `ok` rows are charged |
| `sizes` | `["best"]` | None — `all` and `oar` cost the same per video, they only write more files |
| `format` | `jpg` | None |
| `includeMetadata` | `true` | None; one extra request per video |
| `saveImages` | `true` | None — off gives URLs and metadata only, same price |
| `maxVideos` | `1000` | Hard cap on charged rows; videos past it get a free `budget_exhausted` row |

## Output

An `ok` row (title shortened here):

```json
{
  "recordType": "video",
  "videoId": "dQw4w9WgXcQ",
  "inputUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "canonicalUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "status": "ok",
  "title": "Rick Astley - Never Gonna Give You Up (4K Remaster)",
  "authorName": "Rick Astley",
  "authorUrl": "https://www.youtube.com/@RickAstleyYT",
  "metadataSource": "oembed",
  "isVertical": false,
  "aspectHint": "16:9",
  "best": {
    "size": "maxresdefault",
    "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
    "webpUrl": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/maxresdefault.webp",
    "width": 1280, "height": 720, "bytes": 65324, "etag": "\"1749462010\""
  },
  "thumbnails": {
    "maxresdefault": { "available": true, "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", "webpUrl": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/maxresdefault.webp", "width": 1280, "height": 720, "bytes": 65324, "etag": "\"1749462010\"" },
    "sddefault":     { "available": true, "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/sddefault.jpg", "webpUrl": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/sddefault.webp", "width": 640, "height": 480, "bytes": 31029, "etag": "\"1749462010\"" },
    "hqdefault":     { "available": true, "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", "webpUrl": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/hqdefault.webp", "width": 480, "height": 360, "bytes": 21011, "etag": "\"1749462010\"" },
    "mqdefault":     { "available": true, "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg", "webpUrl": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/mqdefault.webp", "width": 320, "height": 180, "bytes": 10303, "etag": "\"1749462010\"" },
    "default":       { "available": true, "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg", "webpUrl": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/default.webp", "width": 120, "height": 90, "bytes": 2888, "etag": "\"1749462010\"" },
    "oar1":          { "available": true, "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/oar1.jpg", "webpUrl": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/oar1.webp", "width": null, "height": null, "bytes": 81684, "etag": "\"1691797336\"" }
  },
  "availableSizes": ["maxresdefault", "sddefault", "hqdefault", "mqdefault", "default", "oar1"],
  "files": [
    { "size": "maxresdefault", "format": "jpg", "key": "dQw4w9WgXcQ_maxresdefault.jpg", "storeId": "AbCdEfGhIjKlMnOpQ",
      "url": "https://api.apify.com/v2/key-value-stores/AbCdEfGhIjKlMnOpQ/records/dQw4w9WgXcQ_maxresdefault.jpg" }
  ],
  "fetchedAt": "2026-08-28T10:00:00Z",
  "errorMessage": null
}
```

A free `not_found` row (every size answered 404):

```json
{ "recordType": "error", "videoId": "0lZNDRuNLKY", "inputUrl": "0lZNDRuNLKY", "canonicalUrl": "https://www.youtube.com/watch?v=0lZNDRuNLKY", "status": "not_found", "title": null, "authorName": null, "authorUrl": null, "metadataSource": null, "isVertical": null, "aspectHint": null, "best": null, "thumbnails": {}, "availableSizes": [], "files": [], "fetchedAt": "2026-08-28T10:00:00Z", "errorMessage": "every size 404" }
```

A free `playlist_not_supported` row:

```json
{ "recordType": "error", "videoId": null, "inputUrl": "https://www.youtube.com/playlist?list=PLabc", "canonicalUrl": null, "status": "playlist_not_supported", "best": null, "thumbnails": {}, "availableSizes": [], "files": [], "fetchedAt": "2026-08-28T10:00:00Z", "errorMessage": null }
```

Files are stored under `{videoId}_{size}.{jpg|webp}` in the run's default key-value store. That store is private to your account, so the record URL `https://api.apify.com/v2/key-value-stores/{storeId}/records/{key}` needs `?token=<your API token>` outside the Console; `storeId` and `key` are on the row for SDK and API callers.

## Playlists and channels

Not supported, on purpose. A playlist, channel, `/@handle`, `/c/` or `/user/` URL produces a free `playlist_not_supported` row. YouTube's keyless feeds are disallowed by its robots.txt, and using the Data API would put this Actor under YouTube's API Services policies, which forbid combining API access with fetching from the CDN. Paste the video URLs instead — any playlist page's video links, or your own export. A watch URL that carries `&list=` is treated as that single video.

## Pricing

**$0.005 per delivered video — the only paid event.** No start fee. Free, always: `not_found`, `invalid_input`, `playlist_not_supported`, `duplicate`, `removed` and `budget_exhausted` rows.

| Run shape | Charged rows | Cost |
|---|---|---|
| 100 videos, `best`, `jpg` | 100 | **$0.50** |
| 1,000 videos, `all` sizes, `both` formats | 1,000 | **$5.00** — same price, more files |
| 500 URLs of which 50 are dead | 450 | **$2.25** |

A row is charged only after it has been written to your dataset. Set `maxVideos` to bound a run, or `ACTOR_MAX_TOTAL_CHARGE_USD` to bound the spend directly — when either limit is reached the Actor stops probing, writes free `budget_exhausted` rows for the rest and finishes with the status "Stopped at maxVideos / max total charge".

## Change detection

Every size carries the CDN `etag`. Store it and compare on the next run: a different `etag` for the same `videoId` and size means the creator replaced the thumbnail — a cheap key for monitoring A/B thumbnail tests without downloading anything.

## Integrations

- **n8n** — the *Apify* node, **Run an Actor**, then *Get dataset items*; map `best.url` into your workflow.
- **Make** — the Apify *Run an Actor* module plus *Watch dataset items*.
- **Zapier** — Apify's *Run Actor* action; `best.url`, `title`, `authorName` are the useful fields.
- **Google Sheets** — export the **Overview** view as CSV.
- **MCP and AI agents** — every field has a description in the schema; set `maxVideos` on every call and `ACTOR_MAX_TOTAL_CHARGE_USD` on the run. Errors are typed rows, never stack traces.

## Limitations you should know before you buy

- No view counts, likes, descriptions or upload dates — those would need scraping watch pages, which this Actor does not do.
- Private or deleted videos come back as `not_found` (free).
- The oEmbed thumbnail is always 480×360, so it is never used to decide sizes; HEAD probes are.
- Playlists and channels are not expanded.
- `maxresdefault` is 1280×720. The only frames that can reach 1080 pixels are `oar1`–`oar3`; they are auto-generated, not the creator's custom image, and only as large as the upload's source resolution.
- oEmbed may answer 404 on age-restricted or unlisted videos while the CDN still serves images (unverified); the row is then `ok` with `title: null`.
- Key-value record URLs need your API token outside the Console.
- Running with `sizes: all` and `format: both` writes up to 10 files per video (16 with `oar` added) and is slower than the default; 1,000 videos at default settings take roughly 2–4 minutes.

## Privacy

Output contains the channel's public display name and URL (which may be a person's name) and images that may depict people; no viewer or commenter data; faces are not detected or analysed; images are stored only in your own key-value store; you are the controller of what you keep. The Actor opens no outbound connection to the developer and embeds no developer credential, so the Actor T&C creator-access clause is not exercised; your input list is visible to us only through Apify's Console if you open an issue about a run. Full notice: [PRIVACY.md](https://github.com/moonie0201/apify-utils/blob/main/PRIVACY.md).

## FAQ

**Is this legal?** We call two public endpoints — YouTube's thumbnail CDN (`i.ytimg.com`) and its documented oEmbed endpoint — with a tool-style user agent that carries our contact address, at most ten requests at a time, honouring any rate-limit response. We load no watch pages, no internal endpoints, no Data API, no login and no proxy. Copyright is a separate question and we will not pretend otherwise: thumbnails and titles are the creators' and YouTube's content, YouTube's Terms restrict automated access and reproduction of Content, and access may be withdrawn at any time. Any rightholder who wants a video excluded gets it out of future runs within 48 hours — [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md).

**Do I need an API key?** No, and none is accepted.

**Why is `sddefault` missing?** The CDN never generated it for that upload — common for old or low-resolution videos. `hqdefault` is the safe fallback and `best` picks it automatically.

**Can I get 1080p?** Only from the `oar1`–`oar3` auto frames, and only when the upload is 1080p or better: they are rendered at the source resolution, up to 1920×1080 (1080×1920 for vertical videos), so older or low-resolution videos get 1280×720 or smaller. Select `oar` and read the measured `width`/`height` on the row. `maxresdefault` is 1280×720.

**How do I download all files at once?** Open the run's key-value store in the Apify Console and use its download; or list `files[]` on the rows and fetch each `url` with your API token.

**Shorts?** Paste the `/shorts/` URL. `isVertical: true` and `aspectHint: "9:16"` mark them, and the `oar` frames are vertical (up to 1080×1920).

**Why did I get a `duplicate` row?** The same video appeared earlier in your input under another URL. Duplicates are free.

## Support

Open an issue on the Actor's Issues tab or at <https://github.com/moonie0201/apify-utils/issues>, or email **mooniegilog@gmail.com**. Every issue gets a reply within 14 days, usually within 48 hours. Removal and privacy requests are answered within 48 hours.

## Disclaimer

This Actor is unofficial. It is not affiliated with, endorsed by or sponsored by YouTube or Google LLC. It reads YouTube's public thumbnail CDN and public oEmbed endpoint, both of which YouTube may change or block access without notice; if that happens the run fails and charges nothing. All trademarks belong to their respective owners.

You are responsible for how you use the images downstream; thumbnails remain the creators' works, and titles and channel names are the creators' and YouTube's content. Removal requests are honoured for future runs within 48 hours: [TAKEDOWN.md](https://github.com/moonie0201/apify-utils/blob/main/TAKEDOWN.md).
