"""Input parsing for `youtube-thumbnail-downloader` (UTILS_SPEC §1.4 step 1).

Turns whatever a buyer pasted — watch/short/embed/live URLs, `youtu.be`, `i.ytimg.com`
thumbnail links, `attribution_link`, bare ids — into an 11-character video id, or into the
free-row status that explains why it could not. Playlist and channel URLs are recognised
so they get their own `playlist_not_supported` row instead of a generic `invalid_input`.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH_ID = re.compile(r"^/(?:shorts|embed|live|v|e)/([A-Za-z0-9_-]{11})(?:[/?#]|$)")
_YTIMG_ID = re.compile(r"^/vi(?:_webp)?/([A-Za-z0-9_-]{11})/")
_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "i.ytimg.com", "img.youtube.com")
_LIST_PATHS = ("/playlist", "/embed/videoseries", "/channel/", "/c/", "/user/")

OK = "ok"
INVALID = "invalid_input"
PLAYLIST = "playlist_not_supported"
DUPLICATE = "duplicate"

SIZE_NAMES = ("maxresdefault", "sddefault", "hqdefault", "mqdefault", "default")
SIZE_CHOICES = ("best", "all", *SIZE_NAMES, "oar")
_SIZE_ALIASES = {
    "maxres": "maxresdefault",
    "max": "maxresdefault",
    "high": "hqdefault",
    "hq": "hqdefault",
    "sd": "sddefault",
    "standard": "sddefault",
    "medium": "mqdefault",
    "mq": "mqdefault",
    "low": "default",
}


def _host_ok(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in _HOSTS)


def parse_video(raw: str, *, _depth: int = 0) -> tuple[str | None, str]:
    """`(video_id, status)`; status is `ok`, `invalid_input` or `playlist_not_supported`."""
    text = raw.strip().strip("<>").strip()
    if VIDEO_ID.match(text):
        return text, OK
    if not text:
        return None, INVALID
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None, INVALID
    host = (parts.hostname or "").lower()
    if not _host_ok(host):
        return None, INVALID
    query = parse_qs(parts.query)
    path = parts.path or "/"

    vid = (query.get("v") or [""])[0]
    if VIDEO_ID.match(vid):
        return vid, OK
    if host == "youtu.be":
        vid = path.strip("/").split("/")[0]
        return (vid, OK) if VIDEO_ID.match(vid) else (None, INVALID)
    if path.startswith(_LIST_PATHS) or path.startswith("/@"):
        return None, PLAYLIST
    m = _PATH_ID.match(path) or _YTIMG_ID.match(path)
    if m:
        return m.group(1), OK
    if path.startswith("/attribution_link") and _depth == 0:
        inner = unquote((query.get("u") or [""])[0])
        if inner.startswith("/"):
            inner = "https://www.youtube.com" + inner
        return parse_video(inner, _depth=1)
    if "list" in query:
        return None, PLAYLIST
    return None, INVALID


def parse_inputs(lines: list[Any]) -> list[tuple[str, str | None, str]]:
    """`[(inputUrl, videoId, status)]` in input order; a repeated id becomes `duplicate`."""
    seen: set[str] = set()
    out: list[tuple[str, str | None, str]] = []
    for raw in lines:
        raw = str(raw) if raw is not None else ""
        vid, status = parse_video(raw)
        if vid is not None and vid in seen:
            status = DUPLICATE
        elif vid is not None:
            seen.add(vid)
        out.append((raw, vid, status))
    return out


def _size(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in SIZE_CHOICES:
        return text
    if text in _SIZE_ALIASES:
        return _SIZE_ALIASES[text]
    return text if text in SIZE_NAMES else None


def normalize_input(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Apply the silent input aliases (§1.2) and defaults; returns the canonical input."""
    raw = dict(raw or {})
    videos = raw.get("videos") or raw.get("videoUrls") or raw.get("urls") or []
    if not videos and isinstance(raw.get("startUrls"), list):
        videos = [u.get("url") if isinstance(u, dict) else u for u in raw["startUrls"]]
    if not isinstance(videos, list):
        videos = [videos]

    sizes_raw = raw.get("sizes")
    if not sizes_raw:
        single = raw.get("quality") or raw.get("thumbnailQuality")
        sizes_raw = [single] if single else ["best"]
    if not isinstance(sizes_raw, list):
        sizes_raw = [sizes_raw]
    sizes = [s for s in (_size(v) for v in sizes_raw) if s] or ["best"]

    fmt = str(raw.get("format") or "jpg").lower()
    if fmt not in ("jpg", "webp", "both"):
        fmt = "jpg"

    save = raw.get("saveImages")
    if save is None:
        save = raw.get("uploadToKeyValueStore", raw.get("saveToStore", True))

    try:
        max_videos = max(0, int(raw.get("maxVideos", 1000) or 0))
    except (TypeError, ValueError):
        max_videos = 1000

    return {
        "videos": [str(v) for v in videos if v is not None],
        "sizes": sizes,
        "format": fmt,
        "includeMetadata": bool(raw.get("includeMetadata", True)),
        "saveImages": bool(save),
        "maxVideos": max_videos,
    }
