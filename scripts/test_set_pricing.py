"""Body construction for set_pricing.py — no network, no token."""

from datetime import UTC, datetime

import set_pricing

PRICING = {
    "title": "ESPN Sports Scores & Standings — 30+ Leagues",
    "description": "Scores from the feeds espn.com uses.",
    "seoTitle": "Sports Scores Scraper (ESPN) – NFL, NBA, Soccer",
    "seoDescription": "Scores and standings. Flat rows, failures free.",
    "categories": ["SPORTS", "AUTOMATION"],
    "minimalMaxTotalChargeUsd": 0.10,
    "events": {
        "game": {
            "title": "Game",
            "description": "One game.",
            "priceUsd": 0.002,
            "isPrimaryEvent": True,
        },
        "row": {"title": "Row", "description": "One standings entry.", "priceUsd": 0.001},
        # Must be dropped: the platform text replaces it, and it is always $0.
        "apify-actor-start": {"title": "x", "description": "x", "priceUsd": 0.1},
        # Must be dropped: its absence is what deletes the double charge.
        "apify-default-dataset-item": {"title": "Item", "description": "x", "priceUsd": 0.01},
    },
}
EXISTING = {
    "pricingModel": "FREE",
    "createdAt": "2026-08-01T00:00:00.000Z",
    "startedAt": "2026-08-01T00:00:00.000Z",
    "apifyMarginPercentage": 0,
}
NOW = datetime(2026, 8, 28, 12, 30, 0, tzinfo=UTC)


def test_body_keeps_existing_verbatim_and_appends_one_record():
    body = set_pricing.build_body(PRICING, [EXISTING], NOW)
    assert body["pricingInfos"][0] == EXISTING
    assert len(body["pricingInfos"]) == 2
    record = body["pricingInfos"][1]
    assert record["pricingModel"] == "PAY_PER_EVENT"
    assert record["startedAt"] == record["createdAt"] == "2026-08-28T12:30:00.000Z"
    assert record["apifyMarginPercentage"] == 0.2
    assert record["minimalMaxTotalChargeUsd"] == 0.10
    assert "isPPEPlatformUsagePaidByUser" not in record


def test_events_shape():
    events = set_pricing.new_record(PRICING, NOW)["pricingPerEvent"]["actorChargeEvents"]
    assert list(events) == ["game", "row"]
    assert "apify-actor-start" not in events
    assert events["game"] == {
        "eventTitle": "Game",
        "eventDescription": "One game.",
        "eventPriceUsd": 0.002,
        "isPrimaryEvent": True,
    }
    assert "isPrimaryEvent" not in events["row"]
    assert "apify-default-dataset-item" not in events


def test_listing_fields_and_empty_existing():
    body = set_pricing.build_body(PRICING, [], NOW)
    assert len(body["pricingInfos"]) == 1
    assert body["title"] == PRICING["title"]
    assert body["description"] == PRICING["description"]
    assert body["seoTitle"] == PRICING["seoTitle"]
    assert body["seoDescription"] == PRICING["seoDescription"]
    assert body["categories"] == ["SPORTS", "AUTOMATION"]
    assert "isPublic" not in body
