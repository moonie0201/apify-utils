#!/usr/bin/env python3
"""Upload one Actor's PPE pricing record and Store listing fields (UTILS_SPEC §5 step 4).

    python scripts/set_pricing.py <name> --token "$(apify auth token)" [--dry-run] [--publish]

Reads `actors/<name>/.actor/pricing.json`:

    {
      "title": "...", "description": "...", "seoTitle": "...", "seoDescription": "...",
      "categories": ["SPORTS", "AUTOMATION"],
      "minimalMaxTotalChargeUsd": 0.10,
      "events": {
        "game": {"title": "Game", "description": "...", "priceUsd": 0.002, "isPrimaryEvent": true},
        "row":  {"title": "Row",  "description": "...", "priceUsd": 0.001}
      }
    }

Rules learned from the API (§5): `createdAt` + `startedAt` are required; the body must be the
existing `pricingInfos` verbatim plus at most one new record; flat prices only;
`apify-actor-start` must be OMITTED (the API rejects a $0 price as "must contain price",
verified 2026-08-28) — omitted = no start fee at all; omitting
`apify-default-dataset-item` from the record is what deletes it; `isPPEPlatformUsagePaidByUser`
omitted = developer pays. `--publish` sends a separate `PUT {"isPublic": true}` afterwards —
irreversible via the API once someone has paid, so it is never implied.
Stdlib only, so it runs from any actor venv or none.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.apify.com/v2/acts"
USERNAME = "acotr_moonie"
APIFY_MARGIN = 0.2


def load_pricing(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "actors" / name / ".actor" / "pricing.json").read_text("utf-8"))


def new_record(pricing: dict[str, Any], now: datetime) -> dict[str, Any]:
    events: dict[str, Any] = {}
    for key, ev in pricing["events"].items():
        if key in ("apify-actor-start", "apify-default-dataset-item"):
            continue
        record = {
            "eventTitle": ev["title"],
            "eventDescription": ev["description"],
            "eventPriceUsd": ev["priceUsd"],
        }
        if ev.get("isPrimaryEvent"):
            record["isPrimaryEvent"] = True
        events[key] = record
    stamp = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "pricingModel": "PAY_PER_EVENT",
        "pricingPerEvent": {"actorChargeEvents": events},
        "minimalMaxTotalChargeUsd": pricing["minimalMaxTotalChargeUsd"],
        "apifyMarginPercentage": APIFY_MARGIN,
        "startedAt": stamp,
        "createdAt": stamp,
    }


def build_body(
    pricing: dict[str, Any], existing: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """Existing records verbatim + one new PAY_PER_EVENT record + the listing fields."""
    return {
        "pricingInfos": [*existing, new_record(pricing, now)],
        "categories": pricing["categories"],
        "title": pricing["title"],
        "description": pricing["description"],
        "seoTitle": pricing["seoTitle"],
        "seoDescription": pricing["seoDescription"],
    }


def request(
    method: str, url: str, token: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)["data"]
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {url} -> {exc.code}: {exc.read().decode(errors='replace')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("name", help="actor directory name under actors/")
    parser.add_argument("--token", default=os.environ.get("APIFY_TOKEN"))
    parser.add_argument("--username", default=USERNAME)
    parser.add_argument("--dry-run", action="store_true", help="print the body, send nothing")
    parser.add_argument("--publish", action="store_true", help='also PUT {"isPublic": true}')
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("--token or APIFY_TOKEN is required")

    pricing = load_pricing(args.name)
    actor = request("GET", f"{API}/{args.username}~{args.name}", args.token)
    body = build_body(pricing, actor.get("pricingInfos") or [], datetime.now(UTC))
    if args.dry_run:
        print(json.dumps(body, indent=2))
        return 0
    url = f"{API}/{actor['id']}"
    updated = request("PUT", url, args.token, body)
    print(f"pricing set: {len(updated.get('pricingInfos', []))} record(s) on {url}")
    if args.publish:
        request("PUT", url, args.token, {"isPublic": True})
        print(f"published: https://apify.com/{args.username}/{args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
