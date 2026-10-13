from pathlib import Path

import pytest
from yt_dlp.utils import YoutubeDLError

from stickman.collect.bilibili.bili import AccessDeniedError, BiliClient, BiliError, ClientOptions
from stickman.collect.bilibili.models import VideoEntry

from fakes import make_factory

VIDEO_URL = "https://www.bilibili.com/video/BV1CSWXzAEys"
SPACE_URL = "https://space.bilibili.com/42/video"

VIDEO_INFO = {
    "id": "BV1CSWXzAEys",
    "title": "不要太急着去谈一场恋爱",
    "description": "简介",
    "duration": 61.579,
    "view_count": 26507,
    "like_count": 100,
    "upload_date": "20251021",
    "tags": ["心理学"],
    "uploader": "第欧根尼的灯-",
    "uploader_id": "3546974439607171",
    "formats": [{"format_id": "x"}],
    "http_headers": {"Cookie": "secret"},
}


def client(fake, **opts):
    return BiliClient(ClientOptions(**opts), ydl_factory=fake)


def test_list_user_videos_maps_flat_entries():
    fake, created = make_factory(
        {
            SPACE_URL: {
                "entries": [
                    {"id": "BV1aaaaaaaaa", "title": "一", "duration": 60},
                    {"id": "BV1bbbbbbbbb"},
                    None,
                ]
            }
        }
    )

    entries = client(fake).list_user_videos("42", limit=5)

    assert entries == [VideoEntry("BV1aaaaaaaaa", "一", duration=60.0), VideoEntry("BV1bbbbbbbbb", "")]
    params = created[0]
    assert params["extract_flat"] == "in_playlist"
    assert params["playlistend"] == 5


def test_options_pass_cookies_and_rate_limit():
    fake, created = make_factory({SPACE_URL: {"entries": []}})

    client(fake, cookies_from_browser="chrome", cookie_file=None, sleep_seconds=2.0).list_user_videos("42")

    params = created[0]
    assert params["cookiesfrombrowser"] == ("chrome",)
    assert params["sleep_interval_requests"] == 2.0
    assert params["quiet"] is True
    assert "playlistend" not in params


def test_cookie_file_is_passed_as_private_temporary_copy(tmp_path):
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape SESSDATA=x", encoding="utf-8")
    fake, created = make_factory({SPACE_URL: {"entries": []}})

    client(fake, cookie_file=cookie).list_user_videos("42")

    passed = created[0]["cookiefile"]
    assert passed != str(cookie)
    assert fake.cookie_snapshot == "# Netscape SESSDATA=x"
    assert not Path(passed).exists()
    assert cookie.read_text(encoding="utf-8") == "# Netscape SESSDATA=x"


def test_cookie_sources_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError):
        ClientOptions(cookies_from_browser="chrome", cookie_file=tmp_path / "c.txt")


def test_missing_cookie_file_is_reported(tmp_path):
    fake, _ = make_factory({SPACE_URL: {"entries": []}})

    with pytest.raises(BiliError, match="cookies"):
        client(fake, cookie_file=tmp_path / "missing.txt").list_user_videos("42")


def test_list_skips_entries_with_unexpected_ids():
    fake, _ = make_factory(
        {SPACE_URL: {"entries": [{"id": "../../etc"}, {"id": "BV1CSWXzAEys_p1"}, {"id": "BV1CSWXzAEyt"}]}}
    )

    entries = client(fake).list_user_videos("42")

    assert [e.bvid for e in entries] == ["BV1CSWXzAEys", "BV1CSWXzAEyt"]


def test_fetch_meta_normalises_multipart_id_and_strips_url_queries():
    info = {**VIDEO_INFO, "id": "BV1CSWXzAEys_p1", "thumbnail": "https://i0.hdslb.com/a.jpg?token=secret"}
    fake, _ = make_factory({VIDEO_URL: info})

    meta = client(fake).fetch_meta("BV1CSWXzAEys")

    assert meta["id"] == "BV1CSWXzAEys"
    assert meta["thumbnail"] == "https://i0.hdslb.com/a.jpg"


def test_fetch_meta_rejects_mismatched_id():
    fake, _ = make_factory({VIDEO_URL: {**VIDEO_INFO, "id": "BV9999999999"}})

    with pytest.raises(BiliError, match="mismatch"):
        client(fake).fetch_meta("BV1CSWXzAEys")


def test_fetch_meta_empty_response():
    fake, _ = make_factory({})

    with pytest.raises(BiliError, match="no data"):
        client(fake).fetch_meta("BV1CSWXzAEys")


def test_other_yt_dlp_errors_are_wrapped():
    fake, _ = make_factory(error="could not load cookies", error_cls=YoutubeDLError)

    with pytest.raises(BiliError, match="could not load cookies"):
        client(fake).fetch_meta("BV1CSWXzAEys")


def test_download_escapes_percent_in_directory(tmp_path):
    dest = tmp_path / "d%(title)s"
    fake, created = make_factory({VIDEO_URL: VIDEO_INFO}, files={VIDEO_URL: ["m4a"]})

    path = client(fake).download_audio("BV1CSWXzAEys", dest)

    assert created[0]["outtmpl"] == str(dest).replace("%", "%%") + "/source.%(ext)s"
    assert path == dest / "source.m4a"


@pytest.mark.parametrize("leftover", ["source.m4a.part", "source.m4a.part-Frag12", "source.temp.m4a", "source.f30280.m4a"])
def test_download_ignores_partial_or_intermediate_files(tmp_path, leftover):
    (tmp_path / leftover).write_bytes(b"partial")
    fake, created = make_factory({VIDEO_URL: VIDEO_INFO}, files={VIDEO_URL: ["m4a"]})

    path = client(fake).download_audio("BV1CSWXzAEys", tmp_path)

    assert created, "should download again"
    assert path == tmp_path / "source.m4a"


def test_fetch_meta_keeps_useful_fields_only():
    fake, created = make_factory({VIDEO_URL: VIDEO_INFO})

    meta = client(fake).fetch_meta("BV1CSWXzAEys")

    assert meta["title"] == "不要太急着去谈一场恋爱"
    assert meta["tags"] == ["心理学"]
    assert "formats" not in meta
    assert "http_headers" not in meta
    assert created[0]["noplaylist"] is True


def test_fetch_meta_tolerates_missing_formats():
    fake, created = make_factory({VIDEO_URL: VIDEO_INFO})

    client(fake).fetch_meta("BV1CSWXzAEys")

    assert created[0]["ignore_no_formats_error"] is True


def test_entry_from_meta():
    entry = BiliClient.entry_from_meta(VIDEO_INFO)

    assert entry == VideoEntry(
        "BV1CSWXzAEys", "不要太急着去谈一场恋爱", duration=61.579, view_count=26507, upload_date="20251021"
    )


@pytest.mark.parametrize("code", ["352", "412"])
def test_risk_control_errors_become_access_denied(code):
    fake, _ = make_factory(error=f"ERROR: [BilibiliSpaceVideo] 42: Request is rejected by server ({code})")

    with pytest.raises(AccessDeniedError, match="cookies"):
        client(fake).list_user_videos("42")


def test_other_download_errors_wrapped():
    fake, _ = make_factory(error="ERROR: Video unavailable")

    with pytest.raises(BiliError, match="Video unavailable"):
        client(fake).fetch_meta("BV1CSWXzAEys")


def test_download_audio_returns_downloaded_file(tmp_path):
    fake, created = make_factory({VIDEO_URL: VIDEO_INFO}, files={VIDEO_URL: ["m4a"]})

    path = client(fake).download_audio("BV1CSWXzAEys", tmp_path)

    assert path == tmp_path / "source.m4a"
    assert created[0]["format"] == "bestaudio/best"
    assert created[0]["outtmpl"] == str(tmp_path / "source.%(ext)s")


def test_download_audio_skips_existing(tmp_path):
    (tmp_path / "source.m4a").write_bytes(b"x")
    fake, created = make_factory()

    assert client(fake).download_audio("BV1CSWXzAEys", tmp_path) == tmp_path / "source.m4a"
    assert created == []


def test_download_video_merges_to_mp4(tmp_path):
    fake, created = make_factory({VIDEO_URL: VIDEO_INFO}, files={VIDEO_URL: ["mp4"]})

    path = client(fake).download_video("BV1CSWXzAEys", tmp_path)

    assert path == tmp_path / "video.mp4"
    assert created[0]["merge_output_format"] == "mp4"


def test_download_raises_when_nothing_written(tmp_path):
    fake, _ = make_factory({VIDEO_URL: VIDEO_INFO}, files={})

    with pytest.raises(BiliError, match="no file"):
        client(fake).download_audio("BV1CSWXzAEys", tmp_path)


SRT = "1\n00:00:00,000 --> 00:00:01,500\n你好\n"


def test_fetch_subtitles_returns_language_and_segments():
    info = {**VIDEO_INFO, "subtitles": {"danmaku": [{"ext": "xml"}], "ai-zh": [{"ext": "srt", "data": SRT}]}}
    fake, created = make_factory({VIDEO_URL: info})

    lang, segments = client(fake).fetch_subtitles("BV1CSWXzAEys")

    assert lang == "ai-zh"
    assert [s.text for s in segments] == ["你好"]
    assert created[0]["writesubtitles"] is True


def test_fetch_subtitles_none_without_login():
    fake, _ = make_factory({VIDEO_URL: {**VIDEO_INFO, "subtitles": {"danmaku": [{"ext": "xml"}]}}})

    assert client(fake).fetch_subtitles("BV1CSWXzAEys") is None


def test_fetch_subtitles_none_when_srt_is_empty():
    fake, _ = make_factory({VIDEO_URL: {**VIDEO_INFO, "subtitles": {"ai-zh": [{"ext": "srt", "data": ""}]}}})

    assert client(fake).fetch_subtitles("BV1CSWXzAEys") is None
