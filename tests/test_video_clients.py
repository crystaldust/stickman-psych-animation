import base64
import http.client
import io
import json
import subprocess
import urllib.error
import urllib.request

import pytest
from video_fakes import word_meta

from stickman.video import images, keychain, net, tts
from stickman.video.config import VideoConfig
from stickman.video.errors import VideoError

SECRET = "sk-secret-123"
JPEG = b"\xff\xd8\xffdata"


class FakeResponse:
    def __init__(self, body=b"", content_type="application/json"):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Type": content_type}

    def read(self, n=-1):
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """按顺序返回预置结果；Exception 实例会被抛出。记录每个请求。"""

    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _http_error(code, body=b"err"):
    return urllib.error.HTTPError("https://x", code, "msg", {}, io.BytesIO(body))


# ---------- keychain


def test_read_secret_returns_the_stripped_password():
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, SECRET + "\n", "")

    assert keychain.read_secret("ark-api-key", run=run) == SECRET
    assert calls == [["security", "find-generic-password", "-s", "ark-api-key", "-w"]]


@pytest.mark.parametrize("result", [(44, "", "not found"), (0, "\n", "")])
def test_read_secret_failure_does_not_echo_output(result):
    code, out, err = result

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, code, out, err)

    with pytest.raises(VideoError) as exc:
        keychain.read_secret("ark-api-key", run=run)
    assert "ark-api-key" in str(exc.value) and "not found" not in str(exc.value)


# ---------- http


def test_post_json_sends_json_and_parses_reply():
    opener = FakeOpener(FakeResponse(b'{"ok": 1}'))

    data = net.post_json("https://api/x", {"a": "中"}, {"X-Api-Key": SECRET}, 10, SECRET, "tts s01", opener=opener)

    assert data == {"ok": 1}
    req = opener.requests[0]
    assert req.get_method() == "POST" and json.loads(req.data) == {"a": "中"}
    assert req.get_header("Content-type") == "application/json"


def test_post_json_error_body_is_redacted_and_truncated():
    opener = FakeOpener(_http_error(400, (SECRET + "x" * 1000).encode()))

    with pytest.raises(net.HttpError) as exc:
        net.post_json("https://api/x", {}, {}, 10, SECRET, "img s01", opener=opener)

    assert SECRET not in str(exc.value) and "***" in str(exc.value)
    assert len(str(exc.value)) < 400 and not exc.value.retryable


def test_post_json_rejects_non_object_replies():
    with pytest.raises(VideoError):
        net.post_json("https://api/x", {}, {}, 10, "", "x", opener=FakeOpener(FakeResponse(b"[1]")))
    with pytest.raises(VideoError):
        net.post_json("https://api/x", {}, {}, 10, "", "x", opener=FakeOpener(FakeResponse(b"<html>")))


def test_with_retry_backs_off_on_transient_errors_only():
    sleeps = []
    opener = FakeOpener(_http_error(503), urllib.error.URLError("SSL EOF"), FakeResponse(b"{}"))

    data = net.with_retry(lambda: net.post_json("https://a", {}, {}, 1, "", "x", opener=opener), "x", sleep=sleeps.append)

    assert data == {} and sleeps == [5, 10]


def test_with_retry_gives_up_on_client_errors_and_after_max_attempts():
    sleeps = []
    with pytest.raises(net.HttpError):
        net.with_retry(lambda: net.post_json("https://a", {}, {}, 1, "", "x", opener=FakeOpener(_http_error(401))),
                        "x", sleep=sleeps.append)
    assert sleeps == []

    opener = FakeOpener(*[_http_error(429) for _ in range(4)])
    with pytest.raises(net.HttpError):
        net.with_retry(lambda: net.post_json("https://a", {}, {}, 1, "", "x", opener=opener), "x", sleep=sleeps.append)
    assert sleeps == [5, 10, 20]


def test_download_checks_scheme_host_type_and_size(tmp_path):
    dest = tmp_path / "img" / "s01.jpg"
    ok = FakeOpener(FakeResponse(JPEG, "image/jpeg"))

    net.download("https://bucket.tos-cn-beijing.volces.com/a.jpg", dest, ("volces.com",), 100, "img s01", opener=ok)

    assert dest.read_bytes() == JPEG and not dest.with_suffix(".jpg.part").exists()
    bad = [
        ("http://bucket.volces.com/a.jpg", FakeResponse(b"x", "image/jpeg")),
        ("https://evil.com/a.jpg", FakeResponse(b"x", "image/jpeg")),
        ("https://evilvolces.com/a.jpg", FakeResponse(b"x", "image/jpeg")),
        ("https://a.volces.com/a.jpg", FakeResponse(b"x", "text/html")),
        ("https://a.volces.com/a.jpg", FakeResponse(b"x" * 101, "image/jpeg")),
        ("https://a.volces.com/a.jpg", FakeResponse(b"<html>", "image/jpeg")),
    ]
    for url, response in bad:
        with pytest.raises(VideoError):
            net.download(url, tmp_path / "bad.jpg", ("volces.com",), 100, "img", opener=FakeOpener(response))
        assert not (tmp_path / "bad.jpg").exists()


def test_post_redirects_are_refused_so_keys_never_follow():
    handler = net._NoRedirect()
    req = urllib.request.Request("https://api/x", headers={"X-Api-Key": SECRET})

    assert handler.redirect_request(req, None, 302, "Found", {}, "http://evil.com/") is None


def test_download_redirects_are_rechecked_against_the_allowlist():
    handler = net._AllowlistRedirect(("volces.com",))
    req = urllib.request.Request("https://a.volces.com/x.jpg")

    assert handler.redirect_request(req, None, 302, "Found", {}, "https://b.volces.com/y.jpg") is not None
    for target in ("http://169.254.169.254/latest", "https://evil.com/x.jpg"):
        with pytest.raises(VideoError):
            handler.redirect_request(req, None, 302, "Found", {}, target)


def test_incomplete_reads_are_retryable_http_errors(tmp_path):
    with pytest.raises(net.HttpError) as exc:
        net.post_json("https://a", {}, {}, 1, "", "x", opener=FakeOpener(http.client.IncompleteRead(b"")))
    assert exc.value.retryable
    with pytest.raises(net.HttpError):
        net.download("https://a.volces.com/x.jpg", tmp_path / "x.jpg", ("volces.com",), 100, "img",
                     opener=FakeOpener(http.client.IncompleteRead(b"")))
    with pytest.raises(net.HttpError):
        net.download("https://a.volces.com/x.jpg", tmp_path / "x.jpg", ("volces.com",), 100, "img",
                     opener=FakeOpener(_http_error(503)))


# ---------- tts


def _tts_reply(text="想起他。"):
    return {**word_meta(text), "audio": base64.b64encode(b"mp3bytes").decode(), "url": "https://x"}


def test_tts_body_uses_spoken_text_style_speaker_and_rate():
    body = tts.tts_body("想起「ta」", VideoConfig())

    assert body["model"] == "seed-audio-1.0"
    assert body["text_prompt"].endswith("一字不差地说：“想起他”")
    assert body["references"] == [{"speaker": "S_VQ18Xi2i2"}]
    assert body["audio_config"]["speech_rate"] == 10 and body["audio_config"]["enable_subtitle"] is True


def test_synthesize_parses_audio_and_metadata():
    calls = []

    def post(url, body, headers, timeout, secret, label):
        calls.append(headers)
        return _tts_reply()

    result = tts.synthesize("想起ta。", SECRET, VideoConfig(), post=post, sleep=lambda s: None)

    assert result.audio == b"mp3bytes"
    assert result.meta["source_text"] == "想起ta。" and "audio" not in result.meta and "url" not in result.meta
    assert result.billed_s == pytest.approx(result.meta["original_duration"])
    assert calls == [{"X-Api-Key": SECRET}]


@pytest.mark.parametrize("reply", [{}, {"audio": "!!notb64"}, {**_tts_reply(), "subtitle": None},
                                   {**_tts_reply(), "original_duration": "5"}])
def test_synthesize_rejects_incomplete_replies(reply):
    with pytest.raises(VideoError):
        tts.synthesize("想起ta。", SECRET, VideoConfig(), post=lambda *a: reply, sleep=lambda s: None)


def test_save_voice_writes_both_files_atomically(tmp_path):
    result = tts.TtsResult(b"mp3", {"source_text": "x"}, 1.0)
    mp3, meta = tmp_path / "voice" / "s01.mp3", tmp_path / "voice" / "s01.mp3.json"

    tts.save_voice(result, mp3, meta)

    assert mp3.read_bytes() == b"mp3" and json.loads(meta.read_text(encoding="utf-8")) == {"source_text": "x"}
    assert sorted(p.name for p in mp3.parent.iterdir()) == ["s01.mp3", "s01.mp3.json"]


# ---------- images


def test_ref_data_url_encodes_jpeg_and_rejects_others(tmp_path):
    ref = tmp_path / "ref.jpg"
    ref.write_bytes(b"\xff\xd8\xff")
    assert images.ref_data_url(ref) == "data:image/jpeg;base64,/9j/"
    (tmp_path / "fake.jpg").write_bytes(b"not an image")
    with pytest.raises(VideoError):
        images.ref_data_url(tmp_path / "fake.jpg")
    with pytest.raises(VideoError):
        images.ref_data_url(tmp_path / "missing.jpg")
    (tmp_path / "ref.gif").write_bytes(b"x")
    with pytest.raises(VideoError):
        images.ref_data_url(tmp_path / "ref.gif")


def test_generate_image_posts_prompt_with_reference_and_downloads(tmp_path):
    posted, fetched = [], []

    def post(url, body, headers, timeout, secret, label):
        posted.append((body, headers))
        return {"data": [{"url": "https://a.volces.com/x.jpg"}]}

    def fetch(url, dest, hosts, max_bytes, label):
        fetched.append(url)
        dest.write_bytes(b"jpg")

    out = tmp_path / "s01.jpg"
    images.generate_image("提示词", "data:image/jpeg;base64,AA==", SECRET, VideoConfig(), out,
                          post=post, fetch=fetch, sleep=lambda s: None)

    body, headers = posted[0]
    assert body["prompt"] == "提示词" and body["image"].startswith("data:image/jpeg")
    assert body["model"] == VideoConfig().image_model and body["size"] == "1920x1080" and body["watermark"] is False
    assert headers == {"Authorization": f"Bearer {SECRET}"}
    assert fetched == ["https://a.volces.com/x.jpg"] and out.exists()


@pytest.mark.parametrize("reply", [{}, {"data": []}, {"data": [{"url": 3}]}])
def test_generate_image_rejects_replies_without_a_url(tmp_path, reply):
    with pytest.raises(VideoError):
        images.generate_image("p", "d", SECRET, VideoConfig(), tmp_path / "s.jpg",
                              post=lambda *a: reply, fetch=lambda *a: None, sleep=lambda s: None)
