"""Actor entrypoint for `youtube-thumbnail-downloader` (UTILS_SPEC §1.4).

One row per unique video. A `video` event is charged only for `status: "ok"` rows, and
only after the SDK confirms the row landed; not-found, invalid, duplicate, playlist,
removed and over-budget rows are pushed free.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from apify import Actor

from . import parse, probe

EVENT = "video"
CONCURRENCY = 10
KV_CONCURRENCY = 5
STOP_MESSAGE = "Stopped at maxVideos / max total charge"
BLOCKLIST = Path(__file__).resolve().parent.parent / "blocklist.txt"
_CONTENT_TYPES = {"jpg": "image/jpeg", "webp": "image/webp"}
_ROW_KEYS = (
    "recordType",
    "videoId",
    "inputUrl",
    "canonicalUrl",
    "status",
    "title",
    "authorName",
    "authorUrl",
    "metadataSource",
    "isVertical",
    "aspectHint",
    "best",
    "thumbnails",
    "availableSizes",
    "files",
    "fetchedAt",
    "errorMessage",
)


def load_blocklist(path: Path = BLOCKLIST) -> set[str]:
    """Video ids removed at a rightholder's request (TAKEDOWN.md); one per line, `#` comments."""
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if parse.VIDEO_ID.match(line):
            ids.add(line)
    return ids


def charge_cap(input_cap: int | None, event: str, actor: Any) -> int | None:
    """None = unlimited. calculate_max_event_charge_count_within_limit returns None when the
    event has no price (every non-PPE run: `apify run` locally, the dev's own runs, FREE-record
    runs) or when the limit is infinite (apify 4.0.1 _charging.py:516-523) — never min() it raw."""
    cm = actor.get_charging_manager()
    caps = [
        c
        for c in (input_cap or None, cm.calculate_max_event_charge_count_within_limit(event))
        if c is not None
    ]
    return min(caps) if caps else None


class Budget:
    """Owns every push, so "charge only for a delivered row" lives in one place.

    `reserve()` holds a slot before a video is probed, so the cap is exact under
    concurrency and videos past it are never touched; a free outcome releases the slot.
    """

    def __init__(self, cap: int | None, actor: Any):
        self.cap = cap
        self.actor = actor
        self.reserved = 0
        self.delivered = 0
        self.charged = 0
        self.free = 0
        self.exhausted = False

    def reserve(self) -> bool:
        if self.exhausted or (self.cap is not None and self.reserved >= self.cap):
            return False
        self.reserved += 1
        return True

    def release(self) -> None:
        self.reserved -= 1

    async def push_charged(self, row: dict[str, Any]) -> bool:
        """Push one `ok` row with the `video` event. Returns whether it landed."""
        try:
            result = await self.actor.push_data(row, charged_event_name=EVENT)
        except Exception:
            self.release()
            raise
        charged = getattr(result, "charged_count", 0) or 0
        limit = bool(getattr(result, "event_charge_limit_reached", False))
        # On a PPE run the SDK pushes exactly as many rows as it charges (apify 4.0.1
        # _actor.py push_data → compute_push_data_limit), so 0 charged == 0 pushed — even when
        # `event_charge_limit_reached` is False because that flag prices the event alone. On a
        # non-PPE run charged is always 0 and the row did land.
        ppe = self.actor.get_charging_manager().get_pricing_info().is_pay_per_event
        if charged == 0 and (limit or ppe):
            self.exhausted = True
            self.release()
            return False
        self.delivered += 1
        self.charged += charged
        if limit:
            self.exhausted = True
        return True

    async def push_free(self, row: dict[str, Any]) -> None:
        await self.actor.push_data(row)
        self.free += 1


def make_row(input_url: str, video_id: str | None, status: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(_ROW_KEYS)
    row.update(
        recordType="video" if status == "ok" else "error",
        videoId=video_id,
        inputUrl=input_url,
        canonicalUrl=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        status=status,
        thumbnails={},
        availableSizes=[],
        files=[],
        fetchedAt=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    row.update(fields)
    return row


def selected_sizes(sizes: list[str], available: list[str]) -> list[str]:
    """Expand `best`/`all`/`oar` into concrete names, keeping only what exists."""
    out: list[str] = []
    for choice in sizes:
        if choice == "best":
            names = available[:1]
        elif choice == "all":
            names = list(available)
        elif choice == "oar":
            names = [n for n in probe.OAR if n in available]
        else:
            names = [choice] if choice in available else []
        out.extend(n for n in names if n not in out)
    return out


class Run:
    def __init__(self, inp: dict[str, Any], *, actor: Any, client: httpx.AsyncClient):
        self.inp = inp
        self.actor = actor
        self.client = client
        self.log = actor.log
        self.budget = Budget(charge_cap(inp["maxVideos"], EVENT, actor), actor)
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self.kv_sem = asyncio.Semaphore(KV_CONCURRENCY)
        self.store_id: str | None = None
        self.blocked = load_blocklist(BLOCKLIST)

    async def save(self, video_id: str, name: str, fmt: str, body: bytes) -> dict[str, Any] | None:
        key = f"{video_id}_{name}.{fmt}"
        async with self.kv_sem:
            await self.actor.set_value(key, body, content_type=_CONTENT_TYPES[fmt])
        return {
            "size": name,
            "format": fmt,
            "key": key,
            "storeId": self.store_id,
            "url": f"https://api.apify.com/v2/key-value-stores/{self.store_id}/records/{key}",
        }

    async def probe_video(self, input_url: str, video_id: str) -> dict[str, Any]:
        inp = self.inp
        heads = await probe.head_sizes(self.client, video_id)
        if not any(h["available"] for h in heads.values()):
            return make_row(input_url, video_id, "not_found", errorMessage="every size 404")

        if "oar" in inp["sizes"]:
            heads.update(await probe.head_sizes(self.client, video_id, ("oar2", "oar3")))

        dims = await probe.frame0_dimensions(self.client, video_id)
        meta = await probe.fetch_oembed(self.client, video_id) if inp["includeMetadata"] else None

        thumbnails: dict[str, Any] = {}
        for name, head in heads.items():
            w, h = probe.SIZES.get(name, (None, None))
            thumbnails[name] = {
                **head,
                "url": probe.thumb_url(video_id, name),
                "webpUrl": probe.thumb_url(video_id, name, "webp"),
                "width": w if head["available"] else None,
                "height": h if head["available"] else None,
            }
        standard = [n for n in probe.SIZES if thumbnails[n]["available"]]
        available = standard + [
            n for n in probe.OAR if n in thumbnails and thumbnails[n]["available"]
        ]
        best = None
        if standard:
            keys = ("url", "webpUrl", "width", "height", "bytes", "etag")
            best = {"size": standard[0], **{k: thumbnails[standard[0]][k] for k in keys}}

        hint = probe.aspect_hint(*dims) if dims else None
        if hint is None and meta and meta.get("width") == 200 and meta.get("height") == 150:
            hint = "4:3"

        files: list[dict[str, Any]] = []
        errors: list[str] = []
        formats = ["jpg", "webp"] if inp["format"] == "both" else [inp["format"]]
        wanted = selected_sizes(inp["sizes"], available) if inp["saveImages"] else []
        for name in wanted:
            for fmt in formats:
                body = await probe.download(self.client, probe.thumb_url(video_id, name, fmt))
                if body is None:
                    errors.append(f"{name}.{fmt} not saved (CDN did not serve it)")
                    continue
                if name in probe.OAR and fmt == "jpg" and (d := probe.jpeg_dimensions(body)):
                    thumbnails[name]["width"], thumbnails[name]["height"] = d
                try:
                    files.append(await self.save(video_id, name, fmt, body))
                except Exception as exc:  # the URLs are the product; the file is a convenience
                    reason = type(exc).__name__
                    errors.append(f"{name}.{fmt} not saved (store write failed: {reason})")

        return make_row(
            input_url,
            video_id,
            "ok",
            title=(meta or {}).get("title"),
            authorName=(meta or {}).get("author_name"),
            authorUrl=(meta or {}).get("author_url"),
            metadataSource="oembed" if meta else None,
            isVertical=(dims[1] > dims[0]) if dims else None,
            aspectHint=hint,
            best=best,
            thumbnails=thumbnails,
            availableSizes=available,
            files=files,
            errorMessage="; ".join(errors) or None,
        )

    async def worker(self, input_url: str, video_id: str) -> None:
        async with self.sem:
            if not self.budget.reserve():
                await self.budget.push_free(make_row(input_url, video_id, "budget_exhausted"))
                return
            try:
                row = await self.probe_video(input_url, video_id)
            except Exception as exc:
                self.budget.release()
                self.log.warning("video %s failed: %s", video_id, type(exc).__name__)
                await self.budget.push_free(
                    make_row(
                        input_url,
                        video_id,
                        "not_found",
                        errorMessage=f"{type(exc).__name__}: {exc}",
                    )
                )
                return
            if row["status"] != "ok":
                self.budget.release()
                await self.budget.push_free(row)
                return
            try:
                if not await self.budget.push_charged(row):
                    self.log.info("budget reached before %s; row not delivered", video_id)
                    await self.budget.push_free(make_row(input_url, video_id, "budget_exhausted"))
            except Exception as exc:
                self.log.error("push failed for %s: %s", video_id, type(exc).__name__)

    async def run(self) -> Budget:
        entries = parse.parse_inputs(self.inp["videos"])
        self.log.info(
            "%d inputs, cap %s, sizes %s, format %s",
            len(entries),
            self.budget.cap,
            self.inp["sizes"],
            self.inp["format"],
        )
        if self.inp["saveImages"]:
            self.store_id = (await self.actor.open_key_value_store()).id
        tasks = []
        for input_url, video_id, status in entries:
            if status != parse.OK:
                await self.budget.push_free(make_row(input_url, video_id, status))
            elif video_id in self.blocked:
                await self.budget.push_free(
                    make_row(
                        input_url,
                        video_id,
                        "removed",
                        errorMessage="removed at the rightholder's request",
                    )
                )
            else:
                tasks.append(asyncio.create_task(self.worker(input_url, video_id)))
        if tasks:
            await asyncio.gather(*tasks)
        b = self.budget
        self.log.info("delivered %d, charged %d, free %d", b.delivered, b.charged, b.free)
        return b


async def main() -> None:
    async with Actor:
        inp = parse.normalize_input(await Actor.get_input())
        if not inp["videos"]:
            await Actor.fail(status_message="No video URLs or ids in input")
            return
        async with probe.make_client() as client:
            budget = await Run(inp, actor=Actor, client=client).run()
        if budget.exhausted or (budget.cap is not None and budget.delivered >= budget.cap):
            await Actor.set_status_message(STOP_MESSAGE, is_terminal=True)
        else:
            await Actor.set_status_message(
                f"{budget.delivered} videos delivered, {budget.free} free rows", is_terminal=True
            )


if __name__ == "__main__":
    asyncio.run(main())
