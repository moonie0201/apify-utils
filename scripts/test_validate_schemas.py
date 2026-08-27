"""validate_schemas.py against a synthetic Actor directory: green when whole, one finding
per rule when broken."""

import json
from pathlib import Path

import pytest
import validate_schemas as vs
from PIL import Image

TITLE = "Example Feed Reader — Flat Rows"
DESCRIPTION = "Rows from an example feed. Unofficial; no proxy, no key, no standby."

ACTOR = {
    "actorSpecification": 1,
    "name": "example-reader",
    "title": TITLE,
    "description": DESCRIPTION,
    "version": "0.1",
    "buildTag": "latest",
    "minMemoryMbytes": 256,
    "maxMemoryMbytes": 1024,
    "usesStandbyMode": False,
    "dockerfile": "../Dockerfile",
    "readme": "../README.md",
    "input": "./input_schema.json",
    "output": "./output_schema.json",
    "storages": {"dataset": "./dataset_schema.json"},
}
INPUT = {
    "title": TITLE,
    "type": "object",
    "schemaVersion": 1,
    "properties": {
        "leagues": {
            "title": "Leagues",
            "type": "array",
            "editor": "select",
            "description": "League codes.",
            "items": {"type": "string", "enum": ["nba", "nfl"]},
            "prefill": ["nba"],
            "minItems": 1,
        },
        "maxItems": {
            "title": "Max rows",
            "type": "integer",
            "editor": "number",
            "description": "Stop after this many charged rows.",
            "default": 100,
            "minimum": 0,
            "maximum": 10000,
        },
        "teams": {
            "title": "Teams",
            "type": "array",
            "editor": "stringList",
            "description": "Filter to these teams.",
            "default": [],
        },
    },
    "required": ["leagues"],
}
OUTPUT = {
    "actorOutputSchemaVersion": 1,
    "title": "Output",
    "properties": {
        "rows": {
            "type": "string",
            "title": "Rows",
            "description": "All rows.",
            "template": "{{links.apiDefaultDatasetUrl}}/items?clean=true",
        }
    },
}
DATASET = {
    "actorSpecification": 1,
    "fields": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "title": "Id", "description": "Row id.", "example": "1"},
            "score": {"type": "integer", "title": "Score", "description": "Points.", "example": 0},
        },
    },
    "views": {"overview": {"title": "Overview", "transformation": {"fields": ["id", "score"]}}},
}
PRICING = {
    "title": TITLE,
    "description": DESCRIPTION,
    "seoTitle": "Example Feed Reader – Flat Rows",
    "seoDescription": "Rows from an example feed. Failures free.",
    "categories": ["AUTOMATION"],
    "minimalMaxTotalChargeUsd": 0.1,
    "events": {
        "game": {
            "title": "Game",
            "description": "One game.",
            "priceUsd": 0.002,
            "isPrimaryEvent": True,
        },
        "row": {"title": "Row", "description": "One entry.", "priceUsd": 0.001},
    },
}
README = (
    f"# {TITLE}\n\n"
    "> **Unofficial.** Not affiliated with the feed's owner. Removal requests: TAKEDOWN.md. "
    "Privacy: PRIVACY.md.\n\n"
    "## FAQ\n\n**Is this legal?** We call one unauthenticated feed with a tool User-Agent.\n\n"
    "## Support\n\nmooniegilog@gmail.com\n\n" + ("filler word " * 300)
)


def write_actor(root: Path, name: str, **overrides) -> Path:
    actor_dir = root / name
    dot = actor_dir / ".actor"
    dot.mkdir(parents=True)
    docs = {
        "actor.json": {**ACTOR, "name": name},
        "input_schema.json": INPUT,
        "output_schema.json": OUTPUT,
        "dataset_schema.json": DATASET,
        "pricing.json": PRICING,
    }
    docs.update(overrides.get("docs", {}))
    for filename, doc in docs.items():
        if doc is not None:
            (dot / filename).write_text(json.dumps(doc), encoding="utf-8")
    (actor_dir / "Dockerfile").write_text("FROM apify/actor-python:3.12\n")
    (actor_dir / "README.md").write_text(overrides.get("readme", README), encoding="utf-8")
    Image.new("RGB", overrides.get("logo_size", (512, 512))).save(actor_dir / "logo-512.png")
    return actor_dir


def findings(actor_dir: Path) -> list[str]:
    errors: list[str] = []
    vs.validate_actor(actor_dir, errors)
    return errors


def test_valid_actor_has_no_findings(tmp_path):
    assert findings(write_actor(tmp_path, "example-reader")) == []


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"readme": README.replace("Unofficial.", "The official reader.")}, "'official'"),
        ({"readme": README + "\nWe keep no personal data."}, "'no personal data'"),
        ({"readme": README.replace(f"# {TITLE}", "# Other")}, "H1 = title only"),
        ({"readme": README.replace("> **Unofficial.**", "Unofficial.")}, "blockquote"),
        ({"readme": README.split("filler")[0]}, "words"),
        ({"readme": README.replace("Is this legal?", "Legal?")}, "Is this legal?"),
        ({"logo_size": (256, 256)}, "512x512"),
        ({"docs": {"pricing.json": None}}, "no .actor/pricing.json"),
        ({"docs": {"output_schema.json": None}}, "no .actor/output_schema.json"),
        (
            {
                "docs": {
                    "pricing.json": {
                        **PRICING,
                        "events": {
                            **PRICING["events"],
                            "row": {**PRICING["events"]["row"], "title": "Rows"},
                        },
                    }
                }
            },
            "singular",
        ),
        ({"docs": {"pricing.json": {**PRICING, "seoDescription": "Rows at $0.002."}}}, "no price"),
        ({"docs": {"pricing.json": {**PRICING, "description": "Different."}}}, "congruency"),
        (
            {
                "docs": {
                    "pricing.json": {
                        **PRICING,
                        "events": {
                            **PRICING["events"],
                            "apify-default-dataset-item": {
                                "title": "Item",
                                "description": "x",
                                "priceUsd": 0.01,
                            },
                        },
                    }
                }
            },
            "apify-default-dataset-item",
        ),
        ({"docs": {"actor.json": {**ACTOR, "usesStandbyMode": True}}}, "usesStandbyMode"),
        ({"docs": {"actor.json": {**ACTOR, "pricingInfos": []}}}, "pricingInfos"),
        (
            {"docs": {"input_schema.json": {**INPUT, "required": ["leagues", "teams"]}}},
            "exactly one required",
        ),
        (
            {
                "docs": {
                    "input_schema.json": {
                        **INPUT,
                        "properties": {
                            **INPUT["properties"],
                            "leagues": {**INPUT["properties"]["leagues"], "prefill": ["mlb"]},
                        },
                    }
                }
            },
            "not in enum",
        ),
        (
            {
                "docs": {
                    "input_schema.json": {
                        **INPUT,
                        "properties": {
                            **INPUT["properties"],
                            "maxItems": {**INPUT["properties"]["maxItems"], "default": -1},
                        },
                    }
                }
            },
            "below minimum",
        ),
        (
            {
                "docs": {
                    "input_schema.json": {
                        **INPUT,
                        "properties": {
                            **INPUT["properties"],
                            "proxyConfiguration": {
                                "title": "Proxy",
                                "description": "x",
                                "type": "object",
                            },
                        },
                    }
                }
            },
            "proxy",
        ),
        (
            {
                "docs": {
                    "dataset_schema.json": {
                        **DATASET,
                        "fields": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "title": "Id", "description": "Row id."}
                            },
                        },
                    }
                }
            },
            "missing 'example'",
        ),
    ],
)
def test_each_rule_yields_a_finding(tmp_path, overrides, needle):
    errors = findings(write_actor(tmp_path, "example-reader", **overrides))
    assert any(needle in e for e in errors), errors


def test_derived_listing_may_change_only_the_allowed_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "ACTORS_DIR", tmp_path)
    monkeypatch.setattr(vs, "DERIVED_FROM", {"derived-reader": "example-reader"})
    write_actor(tmp_path, "example-reader")
    props = json.loads(json.dumps(INPUT["properties"]))
    props["leagues"]["prefill"] = ["nfl"]
    props["leagues"]["description"] = "Football first."
    props["teams"]["title"] = "Players"
    derived = write_actor(
        tmp_path, "derived-reader", docs={"input_schema.json": {**INPUT, "properties": props}}
    )
    (derived / "Dockerfile").unlink()  # the derived listing ships no Dockerfile of its own
    assert findings(derived) == []

    props["maxItems"]["default"] = 5
    (derived / ".actor" / "input_schema.json").write_text(
        json.dumps({**INPUT, "properties": props}), encoding="utf-8"
    )
    assert any("maxItems.default" in e for e in findings(derived))

    (derived / ".actor" / "pricing.json").write_text(
        json.dumps({**PRICING, "events": {"game": PRICING["events"]["game"]}}), encoding="utf-8"
    )
    assert any("events must equal" in e for e in findings(derived))


def test_main_exit_codes(tmp_path, capsys):
    good = write_actor(tmp_path, "example-reader")
    assert vs.main([str(good)]) == 0
    assert vs.main([str(tmp_path / "missing")]) == 1
    assert "no .actor/" in capsys.readouterr().err
