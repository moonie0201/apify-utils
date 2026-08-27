#!/usr/bin/env python3
"""Schema and listing lint for one or more Actor directories (UTILS_SPEC §4, §7).

    python scripts/validate_schemas.py actors/<name> [actors/<other> ...]
    python scripts/validate_schemas.py                # every actors/*/ with a .actor/

Checks (each is a §7 checklist line or the ats-jobs rule it was ported from):

* every `.actor/*.json` parses; `actor.json` + `input_schema.json` + `output_schema.json`
  + `dataset_schema.json` all exist and `actor.json`'s file references resolve
* `actor.json` carries no pricing fields, `usesStandbyMode: false`, `buildTag: latest`
* the input schema is under 500 kB; exactly one `required` field, and it never carries a
  `default`; no `proxyConfiguration` input; every field has title + description under
  500 chars (MCP truncates there); combined enums under 2000 chars
* the prefill input (`prefill` else `default` per field) validates against the schema —
  the closest an offline lint gets to "prefill runs"
* every dataset field has title + description + example; views only name declared fields
* congruency: `actor.json.title` == README H1 == `pricing.json.title`,
  and `pricing.json.description` == `actor.json.description` (the Store shows the pricing.json
  copy, the Console shows actor.json's — a drift is invisible until a buyer reads both)
* `pricing.json` (what `set_pricing.py` uploads): event titles are singular nouns starting
  with a capital, one `isPrimaryEvent`, no `apify-default-dataset-item`, `apify-actor-start`
  absent or $0, title ≤ 50, description ≤ 300, no price in `seoDescription`
* forbidden user-facing strings — the word "official", "no personal data", "no PII" — in
  every `.actor/*.json` string and in README.md
* README: H1 is the title alone, the disclaimer blockquote is the first element after it,
  ≥ 300 words, says "unofficial", answers "Is this legal?", links TAKEDOWN.md and
  PRIVACY.md, names the contact address; `logo-512.png` is a 512×512 PNG
* a derived listing (`DERIVED_FROM`) differs from its base only where §3.10 allows

Exits 1 and prints every finding; exits 0 on success.
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ACTORS_DIR = ROOT / "actors"

MAX_INPUT_SCHEMA_BYTES = 500_000
MAX_DESCRIPTION_CHARS = 500
MAX_COMBINED_ENUM_CHARS = 2000
MAX_TITLE_CHARS = 50
MAX_LISTING_DESCRIPTION_CHARS = 300
MIN_README_WORDS = 300
CONTACT = "mooniegilog@gmail.com"
FORBIDDEN = {
    "the word 'official'": re.compile(r"\bofficial\b", re.IGNORECASE),
    "'no personal data'": re.compile(r"\bno personal data\b", re.IGNORECASE),
    "'no PII'": re.compile(r"\bno pii\b", re.IGNORECASE),
}
PRICING_KEYS = ("pricingInfos", "pricingModel", "pricingPerEvent", "pricePerUnitUsd")

#: Derived listing → base Actor (§3.10 / SPEC_v2 §3.2): same image, same events; only the
#: listing text, the `leagues` defaults/description and the `teams` title/description move.
DERIVED_FROM = {"tennis-scores-scraper": "espn-sports-scraper"}
DERIVED_FREE_KEYS = {
    "leagues": {"description", "default", "prefill"},
    "teams": {"title", "description"},
}


def _strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string value in the document with its dotted path."""
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in _strings(v, f"{path}.{k}" if path else k)]
    if isinstance(node, list):
        return [s for i, v in enumerate(node) for s in _strings(v, f"{path}[{i}]")]
    if isinstance(node, str):
        return [(path, node)]
    return []


def _enums(node: Any) -> list[list[Any]]:
    if isinstance(node, dict):
        found = [node["enum"]] if isinstance(node.get("enum"), list) else []
        return found + [e for v in node.values() for e in _enums(v)]
    if isinstance(node, list):
        return [e for item in node for e in _enums(item)]
    return []


def check_forbidden(texts: list[tuple[str, str]], errors: list[str]) -> None:
    for path, text in texts:
        for what, pattern in FORBIDDEN.items():
            if pattern.search(text):
                errors.append(f"{path}: {what} is forbidden in user-facing text (§7)")


def check_field_docs(
    properties: dict[str, Any], label: str, errors: list[str], *, keys: tuple[str, ...]
) -> None:
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            errors.append(f"{label}.{name}: expected an object")
            continue
        for key in keys:
            # Presence, not truthiness: `"example": 0` and `"example": false` are real.
            if key not in spec or spec[key] in (None, ""):
                errors.append(f"{label}.{name}: missing '{key}'")
        description = spec.get("description", "")
        if len(description) >= MAX_DESCRIPTION_CHARS:
            errors.append(
                f"{label}.{name}: description is {len(description)} chars, "
                f"must stay under {MAX_DESCRIPTION_CHARS} (MCP truncates)"
            )


JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_value(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    """The subset of JSON Schema the Apify input schema uses, enough to reject a bad prefill."""
    expected = schema.get("type")
    if expected in JSON_TYPES:
        ok = isinstance(value, JSON_TYPES[expected])
        if expected in ("integer", "number") and isinstance(value, bool):
            ok = False
        if not ok:
            errors.append(f"{path}: prefill {value!r} is not of type {expected}")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: prefill {value!r} is not in enum {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: prefill {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: prefill {value} is above maximum {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: prefill shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: prefill longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: prefill {value!r} does not match pattern {schema['pattern']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: prefill has fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: prefill has more than maxItems {schema['maxItems']}")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(
            value
        ):
            errors.append(f"{path}: prefill repeats an item but uniqueItems is set")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                validate_value(item, items, f"{path}[{i}]", errors)


def check_prefill(doc: dict[str, Any], label: str, errors: list[str]) -> None:
    properties = doc.get("properties", {})
    filled = {
        name: spec["prefill"] if "prefill" in spec else spec["default"]
        for name, spec in properties.items()
        if isinstance(spec, dict) and ("prefill" in spec or "default" in spec)
    }
    for name in doc.get("required", []):
        if name not in filled:
            errors.append(f"{label}: required field '{name}' has no prefill, the form cannot run")
    for name, value in filled.items():
        validate_value(value, properties[name], f"{label}.{name}", errors)


def check_input_schema(path: Path, doc: dict[str, Any], errors: list[str]) -> None:
    label = path.name
    size = path.stat().st_size
    if size > MAX_INPUT_SCHEMA_BYTES:
        errors.append(f"{label}: {size} bytes, over the {MAX_INPUT_SCHEMA_BYTES} byte limit")
    properties = doc.get("properties", {})
    check_field_docs(properties, label, errors, keys=("title", "description"))
    if "proxyConfiguration" in properties:
        errors.append(f"{label}: no proxy input is allowed (hard gate: no proxies)")

    enum_chars = sum(len(json.dumps(enum)) for enum in _enums(doc))
    if enum_chars >= MAX_COMBINED_ENUM_CHARS:
        errors.append(
            f"{label}: enum lists total {enum_chars} chars, must stay under "
            f"{MAX_COMBINED_ENUM_CHARS} (MCP combines them)"
        )

    required = doc.get("required", [])
    if len(required) != 1:
        errors.append(f"{label}: exactly one required field expected, got {required} (§7)")
    for name in required:
        if name not in properties:
            errors.append(f"{label}: required field '{name}' is not in properties")
        elif "default" in properties[name]:
            errors.append(f"{label}.{name}: required fields must use 'prefill', never 'default'")
    check_prefill(doc, label, errors)


def check_dataset_schema(doc: dict[str, Any], label: str, errors: list[str]) -> None:
    properties = doc.get("fields", {}).get("properties", {})
    if not properties:
        errors.append(f"{label}: no fields.properties")
        return
    check_field_docs(properties, label, errors, keys=("title", "description", "example"))
    for view_name, view in doc.get("views", {}).items():
        for field_name in view.get("transformation", {}).get("fields", []):
            # `flatten: ["best"]` lets a view show `best.url`; the root must be declared.
            if field_name.split(".", 1)[0] not in properties:
                errors.append(f"{label}: view '{view_name}' shows undeclared field '{field_name}'")


def check_actor_json(doc: dict[str, Any], label: str, errors: list[str]) -> None:
    for key in PRICING_KEYS:
        if key in doc:
            errors.append(f"{label}: '{key}' must not be in actor.json (pricing lives in the API)")
    if doc.get("usesStandbyMode") is not False:
        errors.append(f"{label}: usesStandbyMode must be false")
    if doc.get("buildTag") != "latest":
        errors.append(f"{label}: buildTag must be 'latest'")
    title = str(doc.get("title") or "")
    if len(title) > MAX_TITLE_CHARS:
        errors.append(f"{label}: title is {len(title)} chars, max {MAX_TITLE_CHARS}")
    description = str(doc.get("description") or "")
    if not description:
        errors.append(f"{label}: no description")
    elif len(description) > MAX_LISTING_DESCRIPTION_CHARS:
        errors.append(
            f"{label}: description is {len(description)} chars, max {MAX_LISTING_DESCRIPTION_CHARS}"
        )


def check_pricing(
    doc: dict[str, Any], label: str, actor_json: dict[str, Any], errors: list[str]
) -> None:
    for key in ("title", "description", "seoTitle", "seoDescription", "categories", "events"):
        if not doc.get(key):
            errors.append(f"{label}: missing '{key}'")
    if not isinstance(doc.get("minimalMaxTotalChargeUsd"), (int, float)):
        errors.append(f"{label}: missing numeric 'minimalMaxTotalChargeUsd'")
    for key in ("title", "description"):
        if doc.get(key) and actor_json.get(key) and doc[key] != actor_json[key]:
            errors.append(f"{label}: {key} differs from actor.json (§13.5 congruency)")
    if len(str(doc.get("seoTitle") or "")) > MAX_TITLE_CHARS:
        errors.append(f"{label}: seoTitle over {MAX_TITLE_CHARS} chars")
    if re.search(r"\$\s?\d", str(doc.get("seoDescription") or "")):
        errors.append(f"{label}: no price in the SEO description (§7)")

    events = doc.get("events") or {}
    if "apify-default-dataset-item" in events:
        errors.append(f"{label}: apify-default-dataset-item must not exist (double charge)")
    if events.get("apify-actor-start", {}).get("priceUsd", 0) != 0:
        errors.append(f"{label}: apify-actor-start must be priced $0")
    primary = [name for name, ev in events.items() if ev.get("isPrimaryEvent")]
    if len(primary) != 1:
        errors.append(f"{label}: exactly one isPrimaryEvent expected, got {primary}")
    for name, ev in events.items():
        if name == "apify-actor-start":
            continue
        title = str(ev.get("title") or "")
        if not title or not title[0].isupper() or title.endswith("s"):
            errors.append(
                f"{label}.events.{name}: title {title!r} must be a capitalised singular "
                "noun — the Store card appends 's'"
            )
        if not ev.get("description"):
            errors.append(f"{label}.events.{name}: missing description")
        if not isinstance(ev.get("priceUsd"), (int, float)) or ev["priceUsd"] < 0:
            errors.append(f"{label}.events.{name}: priceUsd must be a non-negative number")


def check_readme(readme: Path, title: str, errors: list[str]) -> None:
    label = readme.name
    lines = readme.read_text(encoding="utf-8").splitlines()
    text = "\n".join(lines)
    if not lines or lines[0].strip() != f"# {title}":
        errors.append(f"{label}: first line must be exactly '# {title}' (H1 = title only)")
    rest = [line for line in lines[1:] if line.strip()]
    if not rest or not rest[0].startswith(">"):
        errors.append(f"{label}: the disclaimer blockquote must directly follow the H1 (H9)")
    words = len(re.findall(r"\S+", text))
    if words < MIN_README_WORDS:
        errors.append(f"{label}: {words} words, need at least {MIN_README_WORDS}")
    lowered = text.casefold()
    for needle, why in (
        ("unofficial", "the disclaimer must say 'unofficial'"),
        ("is this legal?", "the FAQ must answer 'Is this legal?' in the house pattern"),
        ("takedown.md", "must link TAKEDOWN.md"),
        ("privacy.md", "must link PRIVACY.md"),
        (CONTACT, f"must name the contact {CONTACT}"),
    ):
        if needle not in lowered:
            errors.append(f"{label}: {why}")
    check_forbidden([(label, text)], errors)


def check_logo(logo: Path, label: str, errors: list[str]) -> None:
    if not logo.exists():
        errors.append(f"{label}: logo-512.png missing")
        return
    head = logo.read_bytes()[:24]
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        errors.append(f"{label}: logo-512.png is not a PNG")
        return
    width, height = struct.unpack(">II", head[16:24])
    if (width, height) != (512, 512):
        errors.append(f"{label}: logo-512.png is {width}x{height}, must be 512x512")


def check_derived(actor_dir: Path, docs: dict[str, Any], errors: list[str]) -> None:
    base_name = DERIVED_FROM.get(actor_dir.name)
    if base_name is None:
        return
    base_actor = ACTORS_DIR / base_name / ".actor"
    if not (base_actor / "input_schema.json").is_file():
        errors.append(f"{actor_dir.name}: base Actor {base_name} not found for the derived check")
        return
    label = f"{actor_dir.name}/input_schema.json"
    base_input = json.loads((base_actor / "input_schema.json").read_text(encoding="utf-8"))
    base_props = base_input.get("properties", {})
    props = docs.get("input_schema.json", {}).get("properties", {})
    for name in sorted(set(base_props) ^ set(props)):
        errors.append(f"{label}: field '{name}' differs in presence from {base_name} (§3.10)")
    for name in set(base_props) & set(props):
        free = DERIVED_FREE_KEYS.get(name, set())
        for key in set(base_props[name]) | set(props[name]):
            if key not in free and base_props[name].get(key) != props[name].get(key):
                errors.append(
                    f"{label}.{name}.{key}: differs from {base_name}, only {free or '{}'} may"
                )
    if (base_actor / "pricing.json").exists() and "pricing.json" in docs:
        base_events = json.loads((base_actor / "pricing.json").read_text(encoding="utf-8")).get(
            "events"
        )
        if docs["pricing.json"].get("events") != base_events:
            errors.append(f"{actor_dir.name}/pricing.json: events must equal {base_name}'s (§3.10)")


def validate_actor(actor_dir: Path, errors: list[str]) -> None:
    name = actor_dir.name
    dot_actor = actor_dir / ".actor"
    docs: dict[str, Any] = {}
    for path in sorted(dot_actor.glob("*.json")):
        try:
            docs[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{name}/{path.name}: invalid JSON — {exc}")
    for required in (
        "actor.json",
        "input_schema.json",
        "output_schema.json",
        "dataset_schema.json",
    ):
        if required not in docs:
            errors.append(f"{name}: no .actor/{required}")
    for filename, doc in docs.items():
        check_forbidden([(f"{name}/{filename}.{p}", s) for p, s in _strings(doc)], errors)

    actor_json = docs.get("actor.json") or {}
    if actor_json:
        check_actor_json(actor_json, f"{name}/actor.json", errors)
        derived = name in DERIVED_FROM
        for key in ("dockerfile", "readme", "input", "output"):
            rel = actor_json.get(key)
            if not rel:
                errors.append(f"{name}/actor.json: missing '{key}'")
            elif not (dot_actor / rel).exists() and not (key == "dockerfile" and derived):
                errors.append(f"{name}/actor.json references missing file '{rel}'")
        rel = actor_json.get("storages", {}).get("dataset")
        if not rel or not (dot_actor / rel).exists():
            errors.append(f"{name}/actor.json: storages.dataset must point at an existing file")
    title = str(actor_json.get("title") or "")

    if "input_schema.json" in docs:
        check_input_schema(dot_actor / "input_schema.json", docs["input_schema.json"], errors)
    if "dataset_schema.json" in docs:
        check_dataset_schema(docs["dataset_schema.json"], f"{name}/dataset_schema.json", errors)
    if "pricing.json" in docs:
        check_pricing(docs["pricing.json"], f"{name}/pricing.json", actor_json, errors)
    else:
        errors.append(f"{name}: no .actor/pricing.json (set_pricing.py needs it)")

    readme = actor_dir / "README.md"
    if readme.exists():
        check_readme(readme, title, errors)
    else:
        errors.append(f"{name}: README.md missing")
    check_logo(actor_dir / "logo-512.png", name, errors)
    check_derived(actor_dir, docs, errors)


def main(argv: list[str]) -> int:
    if argv:
        actor_dirs = [Path(a).resolve() for a in argv]
    else:
        actor_dirs = sorted(d for d in ACTORS_DIR.glob("*") if (d / ".actor").is_dir())
    errors: list[str] = []
    for actor_dir in actor_dirs:
        if not (actor_dir / ".actor").is_dir():
            errors.append(f"{actor_dir}: no .actor/ directory")
            continue
        validate_actor(actor_dir, errors)
    if not actor_dirs:
        errors.append(f"no Actors with a .actor/ directory under {ACTORS_DIR}")

    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} problem(s)", file=sys.stderr)
        return 1
    print(f"schemas OK: {', '.join(d.name for d in actor_dirs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
