import pytest
from src import guard
from src.guard import BlockedUrl, check_url, load_blocklist

BLOCKED = [
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.1/a.pdf",
    "http://127.1/a.pdf",
    "http://0x7f000001/a.pdf",
    "http://2130706433/a.pdf",
    "http://[::1]/a.pdf",
    "http://[::ffff:127.0.0.1]/a.pdf",
    "http://[64:ff9b::7f00:1]/a.pdf",
    "http://224.0.0.1/a.pdf",
    "http://localhost/a.pdf",
    "http://foo.internal/a.pdf",
    "http://printer.local/a.pdf",
    "https://files.test:22/a.pdf",
    "https://files.test:8000/a.pdf",
    "file:///etc/passwd",
    "ftp://files.test/a.pdf",
    "https://private.test/a.pdf",
    "https://100.64.0.1/a.pdf",
    "https://",
    "not a url",
    "http://[::1/a.pdf",
    "http://[foo]/a.pdf",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_blocked(resolver, url):
    with pytest.raises(BlockedUrl):
        check_url(url)


def test_pin_public_host(resolver):
    pinned = check_url("https://files.test/dir/a.pdf?x=1")
    assert pinned.url == "https://93.184.216.34/dir/a.pdf?x=1"
    assert pinned.host == "files.test"
    assert pinned.host_header == "files.test"
    assert pinned.ip == "93.184.216.34"
    assert pinned.scheme == "https"


def test_pin_keeps_explicit_port(resolver):
    pinned = check_url("http://files.test:8080/a.pdf")
    assert pinned.url == "http://93.184.216.34:8080/a.pdf"
    assert pinned.host_header == "files.test:8080"


def test_literal_public_ip_allowed(resolver):
    pinned = check_url("https://93.184.216.34/a.pdf")
    assert pinned.url == "https://93.184.216.34/a.pdf"


def test_blocklist_parsing(tmp_path):
    block = tmp_path / "blocklist.txt"
    block.write_text("# comment\nExample.com.  # trailing comment\n\n  cdn.other.test\n")
    assert load_blocklist(block) == {"example.com", "cdn.other.test"}
    assert load_blocklist(tmp_path / "missing.txt") == frozenset()
    assert load_blocklist() == frozenset()  # the shipped file carries comments only


def test_blocklisted_host_and_subdomain_refused(resolver, monkeypatch):
    monkeypatch.setattr(guard, "BLOCKED_HOSTS", frozenset({"files.test"}))
    for url in (
        "https://files.test/a.pdf",
        "https://cdn.files.test/a.pdf",
        "https://FILES.test./a",
    ):
        with pytest.raises(BlockedUrl, match="blocklist"):
            check_url(url)
    assert check_url("https://other.test/a.pdf").host == "other.test"


def test_reason_never_contains_url(resolver):
    with pytest.raises(BlockedUrl) as info:
        check_url("http://10.0.0.1/secret-token.pdf")
    assert "secret" not in str(info.value)
