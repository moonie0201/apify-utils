"""Offline Linux measurements of our synthetic fixtures; never platform cost estimates.

Run with the Actor's Python: python scripts/benchmark_synthetic.py > measurements.json
Each sample uses a fresh process and the production memory-capped parsing child.
Fixture generation is outside the timer. No downloads, Actor SDK, charging, or OCR runs.
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from make_fixtures import image_only, long_text, ruled_table, simple_text  # noqa: E402
from src.chunk import chunk_pages  # noqa: E402
from src.extract import Options  # noqa: E402
from src.main import Run  # noqa: E402
from src.worker import parse_blocking  # noqa: E402

CASES = {
    "text_3_pages": simple_text,
    "text_60_pages": lambda: long_text(pages=60),
    "ruled_table": ruled_table,
    "scan_ocr_off": image_only,
}


def sample(name: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        pdf = work / "synthetic.pdf"
        pdf.write_bytes(CASES[name]())
        opts = Options(document_id="synthetic", work_dir=work, extract_tables=name == "ruled_table")
        started = time.perf_counter()
        parsed = parse_blocking(pdf, opts, memory_mb=512, wall_limit=30)
        parse_ms = (time.perf_counter() - started) * 1000
        if parsed.get("errorCode"):
            raise RuntimeError(parsed["errorCode"])
        started = time.perf_counter()
        pages = parsed["pages"]
        outputs = {
            "page": pages,
            "document": Run.document_body(parsed),
            "chunk": chunk_pages(pages, 1500, 200),
        }
        sizes = {
            mode: len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            for mode, data in outputs.items()
        }
        output_ms = (time.perf_counter() - started) * 1000
        return {
            "parse_ms": round(parse_ms, 3),
            "output_ms": round(output_ms, 3),
            "parser_child_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            * 1024,
            "driver_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "input_bytes": pdf.stat().st_size,
            "pages": len(pages),
            "text_chars": sum(p["charCount"] for p in pages),
            "chunks": len(outputs["chunk"]),
            "output_body_json_bytes": sizes,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", choices=CASES)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("RSS units in this measurement are defined for Linux only")
    if args.sample:
        print(json.dumps(sample(args.sample)))
        return
    report = {
        "observed_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repetitions": 3,
        "scope": "local synthetic parser and output bodies; no network, OCR, SDK or platform costs",
        "memory_note": "peak RSS per fresh process; child/driver peaks must not be added",
        "output_note": "UTF-8 JSON bodies; excludes Actor envelope, timestamps and summary",
        "cases": {},
    }
    for name in CASES:
        samples = []
        for _ in range(report["repetitions"]):
            proc = subprocess.run(
                [sys.executable, __file__, "--sample", name],
                capture_output=True,
                text=True,
                check=True,
                timeout=45,
            )
            samples.append(json.loads(proc.stdout))
        report["cases"][name] = {
            "samples": samples,
            "median_parse_ms": statistics.median(s["parse_ms"] for s in samples),
            "median_output_ms": statistics.median(s["output_ms"] for s in samples),
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
