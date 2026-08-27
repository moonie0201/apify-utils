# Privacy notice

This is the notice for **the developer of the `espn-sports-scraper`, `tennis-scores-scraper`,
`pdf-text-extractor` and `youtube-thumbnail-downloader` Actors**. If you ran one of them
yourself, you are the controller of the dataset and key-value records it wrote into your own
Apify account, and Apify's own [privacy policy](https://apify.com/privacy-policy) governs
the platform.

Last updated: 2026-08-28.

## What we hold, and what we do not

| Data | Where it lives | Do we hold it? |
|---|---|---|
| Rows a run produced | The buyer's own Apify dataset | **No.** Every Actor opens no outbound connection to the developer and embeds no developer credential, so the Actor T&C creator-access clause is not exercised. |
| Thumbnail files (`youtube-thumbnail-downloader`, `saveImages` on) | A key-value store in the buyer's account | **No.** |
| PDFs (`pdf-text-extractor`) | Inside the run container only — memory or its ephemeral disk — and gone when the run ends | **No.** User-supplied PDFs are never held by us. |
| Your input (URLs, video ids, league codes, team names) | The run record in the buyer's account | **No.** It is visible to us only through Apify's Console if you open an issue about a run. |
| Logs | Apify run logs in the buyer's account | Hostnames, ids and counts only. We do not log document text or full input URLs; a test greps the captured logs for each release. |

We run no analytics, set no cookies, keep no snapshot store and operate no website that
collects anything.

## Where personal data can appear anyway

Per Actor, named rather than denied:

- **`youtube-thumbnail-downloader`** — output contains the channel's public display name and
  URL (which may be a person's name) and images that may depict people; no viewer or
  commenter data; faces are not detected or analysed; images are stored only in your own
  key-value store; you are the controller of what you keep.
- **`pdf-text-extractor`** — the output contains whatever your documents contain. If a
  document holds personal data, you are the controller of the resulting dataset. The file
  exists only inside the run container and is gone when the run ends; the dataset lives in
  your Apify account under Apify's retention — delete the run to erase it.
- **`espn-sports-scraper` / `tennis-scores-scraper`** — rows name teams and, for individual
  sports and summary leaders, professional athletes by display name and country as ESPN
  publishes them, as public performance facts only: no biographical data, rosters, salaries,
  injuries or images.

## Your rights, and how to use them

We honour **erasure (Art. 17), objection (Art. 21) and rectification (Art. 16)
unconditionally** — we do not run a balancing test against you, ask for your reasons, or
require you to prove who you are to a standard we invent. Because we hold no output, what we
can do is stop future runs from producing it: a video id, hostname, athlete or team you name
goes into the relevant Actor's `blocklist.txt` and the image is rebuilt and pushed within
**48 hours** (see [`TAKEDOWN.md`](TAKEDOWN.md)). We cannot reach into a dataset a buyer
already has.

**How:** email **`mooniegilog@gmail.com`** with `privacy` in the subject — the private
route, and the right one for anything about a person. Or open an issue at
<https://github.com/moonie0201/apify-utils/issues> containing only the words
`private request`; we reply there with a private channel within one business day.

## Retention

| Store | Kept for |
|---|---|
| Anything about a buyer, a run, a query, a document or a file | Not collected, so nothing to retain. |
| `blocklist.txt` entries | Until you ask for them to be removed. They hold an identifier, never a name, unless the identifier is one. |

## Legal basis and territorial position

We process nothing on our own account beyond the blocklists above, which exist to honour
removal requests (Art. 6(1)(c) and (f)). The operator is established in the **Republic of
Korea** and is not established in the EU. No EU or UK representative has been designated
under Art. 27, on the assessment that the service is offered to buyers rather than to the
data subjects, and that no behaviour of people in the Union is monitored. If you disagree,
the route above reaches us and the answer will not be "prove it".

## Changes

This file is versioned in a public repository. Its history is the changelog.
