"""URL parser table, dedupe, playlist/channel detection and input aliases (§1.7)."""

from __future__ import annotations

import pytest

from src import parse

ID = "dQw4w9WgXcQ"

PARSE_TABLE = [
    (ID, ID, "ok"),
    (f"  {ID}  ", ID, "ok"),
    (f"<https://www.youtube.com/watch?v={ID}>", ID, "ok"),
    (f"https://www.youtube.com/watch?v={ID}", ID, "ok"),
    (f"https://www.youtube.com/watch?v={ID}&t=43s", ID, "ok"),
    (f"https://www.youtube.com/watch?v={ID}&list=PLx&index=2", ID, "ok"),
    (f"https://www.youtube.com/watch?feature=share&v={ID}", ID, "ok"),
    (f"youtube.com/watch?v={ID}", ID, "ok"),
    (f"http://youtube.com/watch?v={ID}", ID, "ok"),
    (f"https://youtu.be/{ID}", ID, "ok"),
    (f"https://youtu.be/{ID}?si=abc", ID, "ok"),
    (f"https://www.youtube.com/shorts/{ID}", ID, "ok"),
    (f"https://www.youtube.com/shorts/{ID}?feature=share", ID, "ok"),
    (f"https://www.youtube.com/embed/{ID}", ID, "ok"),
    (f"https://www.youtube.com/embed/{ID}?autoplay=1", ID, "ok"),
    (f"https://www.youtube.com/live/{ID}", ID, "ok"),
    (f"https://www.youtube.com/v/{ID}", ID, "ok"),
    (f"https://www.youtube.com/e/{ID}", ID, "ok"),
    (f"https://m.youtube.com/watch?v={ID}", ID, "ok"),
    (f"https://music.youtube.com/watch?v={ID}&feature=share", ID, "ok"),
    (f"https://www.youtube-nocookie.com/embed/{ID}", ID, "ok"),
    (f"https://i.ytimg.com/vi/{ID}/maxresdefault.jpg", ID, "ok"),
    (f"https://i.ytimg.com/vi_webp/{ID}/hqdefault.webp", ID, "ok"),
    (f"https://img.youtube.com/vi/{ID}/0.jpg", ID, "ok"),
    (
        f"https://www.youtube.com/attribution_link?a=xyz&u=%2Fwatch%3Fv%3D{ID}%26feature%3Dshare",
        ID,
        "ok",
    ),
    (
        f"https://www.youtube.com/attribution_link?u=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D{ID}",
        ID,
        "ok",
    ),
    ("https://www.youtube.com/playlist?list=PLabc", None, "playlist_not_supported"),
    ("https://www.youtube.com/embed/videoseries?list=PLabc", None, "playlist_not_supported"),
    ("https://www.youtube.com/@RickAstleyYT", None, "playlist_not_supported"),
    ("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw", None, "playlist_not_supported"),
    ("https://www.youtube.com/c/RickAstley", None, "playlist_not_supported"),
    ("https://www.youtube.com/user/RickAstley", None, "playlist_not_supported"),
    ("", None, "invalid_input"),
    ("not a url", None, "invalid_input"),
    ("https://vimeo.com/12345", None, "invalid_input"),
    ("https://notyoutube.com/watch?v=" + ID, None, "invalid_input"),
    ("https://www.youtube.com/watch?v=tooshort", None, "invalid_input"),
    ("https://www.youtube.com/watch", None, "invalid_input"),
    ("https://youtu.be/", None, "invalid_input"),
    ("dQw4w9WgXc!", None, "invalid_input"),
    (
        "https://www.youtube.com/attribution_link?u=%2Fplaylist%3Flist%3DPL1",
        None,
        "playlist_not_supported",
    ),
]


@pytest.mark.parametrize(("raw", "vid", "status"), PARSE_TABLE)
def test_parse_video(raw, vid, status):
    assert parse.parse_video(raw) == (vid, status)


def test_dedupe_keeps_input_order_and_marks_second_occurrence():
    rows = parse.parse_inputs(
        [
            f"https://youtu.be/{ID}",
            "jNQXAC9IVRw",
            ID,
            "junk",
            f"https://www.youtube.com/shorts/{ID}",
        ]
    )
    assert [r[2] for r in rows] == ["ok", "ok", "duplicate", "invalid_input", "duplicate"]
    assert rows[2][1] == ID and rows[2][0] == ID
    assert rows[3] == ("junk", None, "invalid_input")


def test_normalize_defaults():
    inp = parse.normalize_input({"videos": [ID]})
    assert inp == {
        "videos": [ID],
        "sizes": ["best"],
        "format": "jpg",
        "includeMetadata": True,
        "saveImages": True,
        "maxVideos": 1000,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"videoUrls": [ID]}, {"videos": [ID]}),
        ({"urls": [ID]}, {"videos": [ID]}),
        ({"startUrls": [{"url": ID}, "jNQXAC9IVRw"]}, {"videos": [ID, "jNQXAC9IVRw"]}),
        ({"videos": ID}, {"videos": [ID]}),
        ({"videoUrls": 123}, {"videos": ["123"]}),
        ({"videos": [ID], "sizes": 5}, {"sizes": ["best"]}),
        ({"videos": [ID], "sizes": {"a": 1}}, {"sizes": ["best"]}),
        ({"videos": [ID], "quality": "maxresdefault"}, {"sizes": ["maxresdefault"]}),
        ({"videos": [ID], "thumbnailQuality": "maxres"}, {"sizes": ["maxresdefault"]}),
        ({"videos": [ID], "quality": "high"}, {"sizes": ["hqdefault"]}),
        ({"videos": [ID], "sizes": "all"}, {"sizes": ["all"]}),
        ({"videos": [ID], "sizes": ["bogus"]}, {"sizes": ["best"]}),
        ({"videos": [ID], "sizes": []}, {"sizes": ["best"]}),
        ({"videos": [ID], "uploadToKeyValueStore": False}, {"saveImages": False}),
        ({"videos": [ID], "saveToStore": False}, {"saveImages": False}),
        ({"videos": [ID], "saveImages": True, "saveToStore": False}, {"saveImages": True}),
        ({"videos": [ID], "format": "PNG"}, {"format": "jpg"}),
        ({"videos": [ID], "format": "both"}, {"format": "both"}),
        ({"videos": [ID], "maxVideos": -5}, {"maxVideos": 0}),
        ({"videos": [ID], "maxVideos": "x"}, {"maxVideos": 1000}),
        ({"videos": [ID], "includeMetadata": False}, {"includeMetadata": False}),
    ],
)
def test_normalize_aliases(raw, expected):
    inp = parse.normalize_input(raw)
    for key, value in expected.items():
        assert inp[key] == value, key


def test_normalize_none_and_empty():
    assert parse.normalize_input(None)["videos"] == []
    assert parse.normalize_input({})["videos"] == []
