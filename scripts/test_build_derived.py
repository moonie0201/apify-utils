"""build_derived.py: assembles, is idempotent, --clean removes, and the real derived build
context is byte-identical to the base Actor's (the §7 "same image" gate)."""

import filecmp
import re

import build_derived as bd


def _sources(root):
    base = root / "actors" / "espn-sports-scraper"
    (base / "src").mkdir(parents=True)
    (base / "src" / "main.py").write_text("print('hi')\n")
    (base / "src" / "__pycache__").mkdir()
    (base / "src" / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
    (base / "Dockerfile").write_text("FROM apify/actor-python:3.12\n")
    (base / "requirements.txt").write_text("apify>=4.0,<5\n")
    (base / "blocklist.txt").write_text("# none\n")
    derived = root / "actors" / "tennis-scores-scraper"
    (derived / ".actor").mkdir(parents=True)
    (derived / ".actor" / "actor.json").write_text("{}")
    (derived / "README.md").write_text("# Tennis\n")
    (derived / "logo-512.png").write_bytes(b"\x89PNG")


def test_build_is_idempotent_and_clean_removes(tmp_path, monkeypatch):
    _sources(tmp_path)
    monkeypatch.setattr(bd, "ACTORS", tmp_path / "actors")
    monkeypatch.setattr(bd, "BUILD", tmp_path / "build")

    out = bd.build("tennis-scores-scraper")
    assert out == tmp_path / "build" / "tennis-scores-scraper"
    assert (out / "src" / "main.py").read_text() == "print('hi')\n"
    assert not (out / "src" / "__pycache__").exists()
    assert (out / "Dockerfile").exists() and (out / "requirements.txt").exists()
    assert (out / "blocklist.txt").read_text() == "# none\n"
    assert (out / ".actor" / "actor.json").exists()
    assert (out / "README.md").read_text() == "# Tennis\n"
    assert (out / "logo-512.png").read_bytes() == b"\x89PNG"

    (out / "stale.txt").write_text("left over from an earlier build")
    assert bd.main([]) == 0
    assert not (out / "stale.txt").exists()
    assert (out / "src" / "main.py").exists()

    assert bd.main(["--clean"]) == 0
    assert not out.exists()
    assert bd.main(["--clean"]) == 0  # cleaning twice is fine


def test_real_build_context_is_identical_to_base(tmp_path, monkeypatch):
    """Same image = same build context: everything the Dockerfile COPYs comes from the base
    Actor unchanged. (Image digests differ between any two builds, so they are not the gate.)"""
    monkeypatch.setattr(bd, "BUILD", tmp_path / "build")
    out = bd.build("tennis-scores-scraper")
    base = bd.ACTORS / "espn-sports-scraper"
    for name in ("Dockerfile", "requirements.txt", "blocklist.txt"):
        assert (out / name).read_bytes() == (base / name).read_bytes()
    cmp = filecmp.dircmp(base / "src", out / "src", ignore=["__pycache__"])
    assert not (cmp.diff_files or cmp.left_only or cmp.right_only or cmp.funny_files)
    copied = re.findall(r"^COPY\s+(\S+)", (out / "Dockerfile").read_text(), flags=re.M)
    assert copied and all((out / src).exists() for src in copied)
