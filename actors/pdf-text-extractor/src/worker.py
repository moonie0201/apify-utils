"""Parse in a child process (spec §2.4 step 3).

The child receives the file path, never the bytes; sets RLIMIT_AS to 75 % of the Actor's
memory and RLIMIT_CPU to the wall limit; returns JSON over a pipe. A child that dies
(PDFium aborts on a deflate bomb), runs out of memory or overruns the wall limit becomes a
free `malformed` / `timeout` row and the run continues.
"""

from __future__ import annotations

import asyncio
import json
import math
import multiprocessing
import resource
import time
from pathlib import Path

from .extract import ExtractError, Options, parse_document

MEMORY_FRACTION = 0.75
POLL_SECONDS = 0.25

clock = time.monotonic


def child_main(path: str, options: dict, memory_mb: int, wall_limit: float, conn) -> None:
    limit = int(memory_mb * MEMORY_FRACTION) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    cpu = max(1, math.ceil(wall_limit))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    try:
        result = parse_document(Path(path), Options.from_json(options))
    except ExtractError as exc:
        result = {"errorCode": exc.code}
    except MemoryError:
        result = {"errorCode": "malformed", "exception": "MemoryError"}
    except Exception as exc:  # noqa: BLE001 - the parent maps any parser failure to one free row
        result = {"errorCode": "malformed", "exception": type(exc).__name__}
    conn.send_bytes(json.dumps(result).encode("utf-8"))
    conn.close()


def parse_blocking(path: Path, options: Options, *, memory_mb: int, wall_limit: float) -> dict:
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=child_main,
        args=(str(path), options.to_json(), memory_mb, wall_limit, child_conn),
    )
    process.start()
    child_conn.close()
    deadline = clock() + wall_limit
    try:
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                return {"errorCode": "timeout"}
            if parent_conn.poll(min(remaining, POLL_SECONDS)):
                try:
                    payload = parent_conn.recv_bytes()
                except EOFError:
                    return {"errorCode": "malformed", "exception": f"exit:{process.exitcode}"}
                return json.loads(payload.decode("utf-8"))
            if not process.is_alive() and not parent_conn.poll(0):
                return {"errorCode": "malformed", "exception": f"exit:{process.exitcode}"}
    finally:
        parent_conn.close()
        if process.is_alive():
            process.kill()
        process.join(5)
        process.close()


async def parse_in_child(
    path: Path, options: Options, *, memory_mb: int, wall_limit: float
) -> dict:
    return await asyncio.to_thread(
        parse_blocking, path, options, memory_mb=memory_mb, wall_limit=wall_limit
    )
