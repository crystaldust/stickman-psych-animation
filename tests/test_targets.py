import urllib.error
import urllib.request

import pytest

from stickman.collect.bilibili.targets import (
    BiliOnlyRedirectHandler,
    Target,
    TargetError,
    is_allowed_redirect,
    parse_target,
    resolve_short_link,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BV1CSWXzAEys", Target("video", "BV1CSWXzAEys")),
        ("  BV1CSWXzAEys  ", Target("video", "BV1CSWXzAEys")),
        (
            "https://www.bilibili.com/video/BV1CSWXzAEys/?spm_id_from=333.788&vd_source=abc",
            Target("video", "BV1CSWXzAEys"),
        ),
        ("https://m.bilibili.com/video/BV1CSWXzAEys", Target("video", "BV1CSWXzAEys")),
        ("3546974439607171", Target("user", "3546974439607171")),
        ("https://space.bilibili.com/3546974439607171", Target("user", "3546974439607171")),
        (
            "https://space.bilibili.com/3546974439607171/video?tid=0",
            Target("user", "3546974439607171"),
        ),
    ],
)
def test_parse_target_recognises_supported_inputs(raw, expected):
    assert parse_target(raw) == expected


def test_parse_target_resolves_short_links_with_injected_resolver():
    seen = []

    def resolver(url):
        seen.append(url)
        return "https://www.bilibili.com/video/BV1CSWXzAEys?share_source=copy"

    result = parse_target("https://b23.tv/abc123", resolve_short=resolver)

    assert result == Target("video", "BV1CSWXzAEys")
    assert seen == ["https://b23.tv/abc123"]


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "hello", "BV123", "https://example.com/video/BV1CSWXzAEys", "space.bilibili.com/abc"],
)
def test_parse_target_rejects_unsupported_inputs(raw):
    with pytest.raises(TargetError):
        parse_target(raw)


def test_plain_http_short_link_is_rejected():
    with pytest.raises(TargetError, match="https"):
        parse_target("http://b23.tv/abc123", resolve_short=lambda url: "https://www.bilibili.com/video/BV1CSWXzAEys")


@pytest.mark.parametrize(
    "url, allowed",
    [
        ("https://www.bilibili.com/video/BV1CSWXzAEys", True),
        ("https://space.bilibili.com/42", True),
        ("https://b23.tv/x", True),
        ("http://www.bilibili.com/video/BV1CSWXzAEys", False),
        ("https://127.0.0.1/admin", False),
        ("https://169.254.169.254/latest", False),
        ("https://evil.bilibili.com.attacker.io/", False),
    ],
)
def test_is_allowed_redirect(url, allowed):
    assert is_allowed_redirect(url) is allowed


def test_redirect_handler_blocks_non_bilibili_targets():
    handler = BiliOnlyRedirectHandler()
    request = urllib.request.Request("https://b23.tv/abc", method="HEAD")

    with pytest.raises(TargetError, match="127.0.0.1"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1:8080/admin")


def test_redirect_handler_allows_bilibili_targets():
    handler = BiliOnlyRedirectHandler()
    request = urllib.request.Request("https://b23.tv/abc", method="HEAD")

    new = handler.redirect_request(request, None, 302, "Found", {}, "https://www.bilibili.com/video/BV1CSWXzAEys")

    assert new.full_url == "https://www.bilibili.com/video/BV1CSWXzAEys"


def test_resolve_short_link_wraps_network_errors():
    class FailingOpener:
        def open(self, request, timeout):
            raise urllib.error.URLError("offline")

    with pytest.raises(TargetError, match="offline"):
        resolve_short_link("https://b23.tv/abc", opener=FailingOpener())


def test_resolve_short_link_returns_final_url():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://www.bilibili.com/video/BV1CSWXzAEys"

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "HEAD"
            return Response()

    assert resolve_short_link("https://b23.tv/abc", opener=Opener()).endswith("BV1CSWXzAEys")


def test_target_urls():
    assert Target("video", "BV1CSWXzAEys").url == "https://www.bilibili.com/video/BV1CSWXzAEys"
    assert Target("user", "42").url == "https://space.bilibili.com/42/video"
