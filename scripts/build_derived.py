#!/usr/bin/env python3
"""Assemble the derived tennis listing's push directory (UTILS_SPEC §4, SPEC_v2 §3.2).

    python scripts/build_derived.py            # -> build/tennis-scores-scraper/
    python scripts/build_derived.py --clean    # remove it

`build/tennis-scores-scraper/` = espn-sports-scraper/{src,Dockerfile,requirements.txt,
blocklist.txt} + tennis-scores-scraper/{.actor,README.md,logo-512.png}. Run `apify push`
from there. Idempotent: the build directory is removed and rebuilt on every call, so nothing
stale survives a rename in either source.

"Same image" (UTILS_SPEC §3.10, §7) means the same Docker build context — byte-identical
src/, Dockerfile, requirements.txt and blocklist.txt, which test_build_derived.py asserts —
not an equal image digest: `pip install` writes timestamped files, so two builds of one
directory already differ.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTORS = ROOT / "actors"
BUILD = ROOT / "build"

DERIVED = {
    "tennis-scores-scraper": {
        "base": "espn-sports-scraper",
        "from_base": ("src", "Dockerfile", "requirements.txt", "blocklist.txt"),
        "own": (".actor", "README.md", "logo-512.png"),
    },
}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "storage", ".venv")


def _copy(src: Path, dst: Path) -> None:
    if not src.exists():
        sys.exit(f"missing {src}")
    if src.is_dir():
        shutil.copytree(src, dst, ignore=IGNORE)
    else:
        shutil.copy2(src, dst)


def build(name: str) -> Path:
    spec = DERIVED[name]
    out = BUILD / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for item in spec["from_base"]:
        _copy(ACTORS / spec["base"] / item, out / item)
    for item in spec["own"]:
        _copy(ACTORS / name / item, out / item)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--clean", action="store_true", help="remove build/ output instead")
    args = parser.parse_args(argv)
    for name in DERIVED:
        if args.clean:
            shutil.rmtree(BUILD / name, ignore_errors=True)
            print(f"removed {BUILD / name}")
        else:
            print(f"assembled {build(name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
