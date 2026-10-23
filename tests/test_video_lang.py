"""英文版：同一个工作目录里另一套旁白、配音、字幕和成片，图片和分镜共用中文版。"""

import json
from dataclasses import fields

import pytest
from video_fakes import FakeImages, FakeRunner, FakeSecrets, FakeTts, word_meta, write_voice

from stickman.video import cli, tts
from stickman.video.audio import build_voice
from stickman.video.config import KEYCHAIN_TTS, VideoConfig
from stickman.video.errors import VideoError
from stickman.video.lang import EN, LANGS, PRIMARY, ZH
from stickman.video.pipeline import Services, render, reshoot
from stickman.video import render as render_mod
from stickman.video.render import read_manifest
from stickman.video.shots import load_spec
from stickman.video.subtitles import SUB_STYLE_EN, srt_document
from stickman.video.text import BINDS_NEXT_EN, _awkward_en, norm_en, split_sub_en, spoken_en
from stickman.video.timeline import char_times, keep_intervals, shot_events
from stickman.video.workdir import LANG_CODES, WorkDir

ZH_LINES = ["你好，世界。", "第二个镜头。", "最后一句话。"]
EN_LINES = ["Hello, world.", "The second shot.", "One last line."]
EN_TEXTS = {"s01": "Hello, world.", "s02": "The second shot. One last line."}


# ---------- 文本


def test_norm_en_keeps_lowercase_letters_and_digits_only():
    assert norm_en("Don't stop, OK? 2 more—late-night.") == "dontstopok2morelatenight"
    assert norm_en(" ") == "" and norm_en(",") == ""


def test_spoken_en_swaps_double_quotes_so_the_prompt_quote_stays_closed():
    assert spoken_en('He said "stay" and “go”.') == "He said 'stay' and 'go'."


def test_split_sub_en_breaks_between_sentences_and_packs_short_clauses():
    assert split_sub_en("Hello, world.") == ["Hello, world"]
    assert split_sub_en("Is that all? No.") == ["Is that all?", "No"]
    assert split_sub_en("At two in the morning, you replay tomorrow.") == ["At two in the morning, you replay tomorrow"]
    assert split_sub_en("At two in the morning, you replay all of tomorrow.") == [
        "At two in the morning", "you replay all of tomorrow"]
    assert split_sub_en("Wait, what if, just once, it went fine?") == ["Wait, what if, just once, it went fine?"]
    assert split_sub_en("Maybe, maybe not, I honestly can't tell anymore, not tonight anyway.") == [
        "Maybe, maybe not", "I honestly can't tell anymore", "not tonight anyway"]


def test_split_sub_en_does_not_break_before_a_lowercase_word():
    assert split_sub_en("He said wait... and then nothing.") == ["He said wait... and then nothing"]


def test_split_sub_en_wraps_long_runs_at_word_boundaries_into_even_pieces():
    text = "every single thought you have is followed by the worst possible ending you can imagine"

    chunks = split_sub_en(text)

    assert " ".join(chunks) == text
    assert len(chunks) == 3 and all(len(c) <= 42 for c in chunks)
    assert max(map(len, chunks)) - min(map(len, chunks)) < 15


def test_split_sub_en_does_not_leave_articles_or_prepositions_at_the_end_of_a_line():
    assert split_sub_en("You stopped going to the places you used to go.") == [
        "You stopped going", "to the places you used to go"]
    # 真实旁白里原来切坏的句子：切点不能落在冠词、介词、物主代词、连词后面
    for text in ["And close relationships are one of the most important ways we expand ourselves.",
                 "A good relationship won't ask you to make yourself smaller to fit inside it.",
                 "Every thought ends with the worst possible outcome.",
                 "The study also found that the more of someone's worries failed to come true,",
                 "Stendhal called the process of falling in love 'crystallization.'",
                 "So what you can't let go of may not be only that person."]:
        chunks = split_sub_en(text)
        assert " ".join(chunks) == text.rstrip(",.")
        assert len(chunks) <= 3 and all(len(c) <= 42 for c in chunks)
        assert all(c.split()[-1].lower() not in BINDS_NEXT_EN for c in chunks[:-1]), chunks
    # 后面紧跟连词时可以切：两个分句本来就该分开
    assert _awkward_en("the", "places") and _awkward_en("to", "make")
    assert not _awkward_en("in", "and") and not _awkward_en("going", "to")
    # 有一个单词本身就超过上限：放不进任何一行，就一个词一行
    assert split_sub_en("x" * 50 + " end") == ["x" * 50, "end"]


def test_split_sub_en_handles_a_very_long_line_without_punctuation():
    text = " ".join(["word"] * 80)  # 枚举所有切法会组合爆炸，这里要很快出结果

    chunks = split_sub_en(text)

    assert " ".join(chunks) == text and all(len(c) <= 42 for c in chunks) and len(chunks) == 10


def test_split_sub_en_never_breaks_a_word():
    assert split_sub_en("x" * 50) == ["x" * 50]
    long_words = " ".join(["a" * 22, "b" * 22, "c" * 22])  # 两行放不下，退回按上限折行
    assert split_sub_en(long_words) == ["a" * 22, "b" * 22, "c" * 22]


def test_langs_registry_matches_workdir_and_config():
    assert set(LANGS) == set(LANG_CODES) and LANG_CODES[0] == PRIMARY == "zh"
    assert LANGS["en"] is EN and ZH.joiner == "" and EN.joiner == " "
    names = {f.name for f in fields(VideoConfig)}
    assert {f for lang in LANGS.values() for f in (lang.speaker_field, lang.rate_field)} <= names


@pytest.mark.parametrize("line", ["It costs 2 dollars.", "Half & half.", "A café.", "Fifty%.", "Hi {\\an8} there.",
                                  "Hello，world."])
def test_load_spec_en_rejects_what_the_voice_would_read_differently(tmp_path, line):
    work, _ = _setup(tmp_path, en_lines=[EN_LINES[0], line, EN_LINES[2]])

    with pytest.raises(VideoError) as exc:
        load_spec(work)

    assert "第 2 行" in str(exc.value)


def test_load_spec_en_accepts_ordinary_punctuation(tmp_path):
    work, _ = _setup(tmp_path, en_lines=["Don't — wait; it's 'fine'…", "Well-known: yes!", "Really? (No.)"])

    assert load_spec(work)[1][0].startswith("Don't")


# ---------- 时间线


def test_char_times_spreads_each_word_over_its_letters():
    meta = word_meta("Hi, you.", lang=EN)

    times, tokens = char_times(meta, norm_en("Hi, you."), "voice_en/s01.mp3", EN)

    assert len(times) == 5  # h i y o u
    assert times[0] == times[1] == (0.1, 0.3) and times[2] == (0.3, 0.5)
    assert [t["text"] for t in tokens] == ["Hi", ",", " ", "you", "."]


def test_char_times_en_rejects_a_stale_voice():
    with pytest.raises(VideoError) as exc:
        char_times(word_meta("Old line.", lang=EN), norm_en("New line."), "voice_en/s02.mp3", EN)

    assert "s02" in str(exc.value)


def test_keep_intervals_en_treats_spaces_as_pauses_not_words():
    meta = word_meta("Hi. You", pauses={1: 2000}, lang=EN)
    _, tokens = char_times(meta, norm_en("Hi. You"), "s01", EN)

    spans = keep_intervals(tokens, total=3.0, lang=EN)

    assert len(spans) == 2  # 句号后 2 秒的停顿被裁短


def test_shot_events_en_follow_subtitle_chunks():
    lines = ("Hi. You.",)
    times = ((0.0, 0.1), (0.0, 0.1), (0.2, 0.3), (0.2, 0.3), (0.2, 0.3))

    assert shot_events(lines, times, offset=5.0, lang=EN) == ((5.0, 5.1, "Hi"), (5.2, 5.3, "You"))


# ---------- 字幕文件


def test_srt_document_numbers_cues_with_millisecond_commas():
    doc = srt_document(((0.0, 1.5, "Hello"), (61.2345, 62.0, "world")))

    assert doc == "1\n00:00:00,000 --> 00:00:01,500\nHello\n\n2\n00:01:01,234 --> 00:01:02,000\nworld\n"


# ---------- 配音


def test_tts_body_en_uses_the_english_voice_and_prompt():
    body = tts.tts_body('He said "go".', VideoConfig(), EN)

    assert body["references"] == [{"speaker": "en_male_hades_uranus_bigtts"}]
    assert body["text_prompt"].endswith("word for word: \"He said 'go'.\"")
    assert "一字不差" not in body["text_prompt"]


def test_tts_body_zh_keeps_the_cloned_voice():
    assert tts.tts_body("你好", VideoConfig(), ZH)["references"] == [{"speaker": "S_VQ18Xi2i2"}]


def test_tts_body_en_speaks_slower_than_zh():
    # 中文的语速 10 套到英文上太赶；英文用用户在豆包网页上试听选定的 5，两种语言各管各的。
    assert tts.tts_body("Hi.", VideoConfig(), EN)["audio_config"]["speech_rate"] == 5
    assert tts.tts_body("你好", VideoConfig(), ZH)["audio_config"]["speech_rate"] == 10
    assert tts.tts_body("Hi.", VideoConfig(speech_rate_en=0), EN)["audio_config"]["speech_rate"] == 0


# ---------- 工作目录


def test_workdir_en_has_its_own_voice_subs_clips_and_output_but_shares_images(tmp_path):
    zh, en = WorkDir(tmp_path / "slug"), WorkDir(tmp_path / "slug", "en")

    assert en.narration.name == "narration.en.txt" and en.voice("s01").parent.name == "voice_en"
    assert en.trimmed("s01").parent.name == "voice_trim_en" and en.clip("s01").parent.name == "clips_en"
    assert (en.voice_wav.name, en.subs.name, en.srt.name) == ("voice.en.wav", "subs.en.ass", "subs.en.srt")
    assert (en.clip_list.name, en.manifest.name, en.timings.name) == ("clips.en.txt", "render.en.json", "timings.en.json")
    assert en.default_output().name == "slug.en.mp4" and zh.default_output().name == "slug.mp4"
    assert zh.srt.name == "subs.srt" and zh.narration.name == "narration.txt"
    assert en.image("s01") == zh.image("s01") and en.layer("s03", "fg") == zh.layer("s03", "fg")
    assert en.shots == zh.shots


def test_workdir_rejects_unknown_language(tmp_path):
    with pytest.raises(VideoError):
        WorkDir(tmp_path, "fr")


# ---------- 分镜和整条流水线


def _setup(tmp_path, en_lines=EN_LINES):
    work = WorkDir(tmp_path / "w", "en")
    work.root.mkdir()
    WorkDir(work.root).narration.write_text("\n".join(ZH_LINES) + "\n", encoding="utf-8")
    work.narration.write_text("\n".join(en_lines) + "\n", encoding="utf-8")
    shots = [{"id": "s01", "line": 1, "bg": "蓝色", "scene": "站着"}, {"id": "s02", "line": 2, "bg": "红色", "scene": "走路"}]
    work.shots.write_text(json.dumps({"style": "风格{bg}：", "shots": shots}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "ref.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "bgm.mp3").write_bytes(b"mp3")
    config = VideoConfig(ref_image=str(tmp_path / "ref.jpg"), bgm=str(tmp_path / "bgm.mp3"))
    return work, config


def _images(work):
    work.img_dir.mkdir(exist_ok=True)
    for sid in ("s01", "s02"):
        work.image(sid).write_bytes(b"jpg-" + sid.encode())


def _services(**overrides):
    defaults = dict(runner=FakeRunner(durations={"voice.wav": 8.0, "voice.en.wav": 8.0}, frames={"s01.mp4": 80, "s02.mp4": 160}),
                    read_secret=FakeSecrets(), synthesize=FakeTts(), generate_image=FakeImages())
    return Services(**{**defaults, **overrides})


def _render_zh(work, config, services=None):
    """英文版要跟着中文版成片走，所以先把中文版渲染出来。"""
    zh = WorkDir(work.root)
    render(zh, config, services or _services(), out=zh.default_output(), offline=False)
    return zh


def test_load_spec_en_reads_english_lines_against_the_same_shots(tmp_path):
    work, _ = _setup(tmp_path)

    spec, lines = load_spec(work)

    assert lines == tuple(EN_LINES) and spec.ids() == ("s01", "s02")


def test_load_spec_en_requires_one_english_line_per_chinese_line(tmp_path):
    work, _ = _setup(tmp_path, en_lines=EN_LINES[:2])

    with pytest.raises(VideoError) as exc:
        load_spec(work)

    assert "narration.en.txt" in str(exc.value) and "3" in str(exc.value)


def test_load_spec_en_rejects_chinese_left_in_the_translation(tmp_path):
    work, _ = _setup(tmp_path, en_lines=["Hello, world.", "The second 镜头.", "One last line."])

    with pytest.raises(VideoError) as exc:
        load_spec(work)

    assert "第 2 行" in str(exc.value)


def test_render_en_voices_english_reuses_images_and_writes_its_own_outputs(tmp_path):
    work, config = _setup(tmp_path)
    _images(work)
    _render_zh(work, config)
    services = _services()

    report = render(work, config, services, out=work.default_output(), offline=False)

    assert services.synthesize.texts == [EN_TEXTS["s01"], EN_TEXTS["s02"]]
    assert services.synthesize.langs == ["en", "en"]
    assert services.read_secret.services == [KEYCHAIN_TTS] and services.generate_image.prompts == {}
    assert report.out.name == "w.en.mp4" and report.out.exists()
    assert SUB_STYLE_EN in work.subs.read_text(encoding="utf-8")
    assert "Hello" in work.srt.read_text(encoding="utf-8")
    assert work.clip("s01").exists() and work.clip("s01") != WorkDir(work.root).clip("s01")
    assert read_manifest(work)["frames"] == {"s01": 80, "s02": 160}


def test_render_en_refuses_to_generate_shared_images(tmp_path):
    work, config = _setup(tmp_path)
    services = _services()

    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.default_output(), offline=False)

    assert "s01" in str(exc.value) and services.generate_image.prompts == {}
    assert services.read_secret.services == []


def test_render_en_reports_stale_english_voice(tmp_path):
    work, config = _setup(tmp_path)
    _images(work)
    _render_zh(work, config)
    write_voice(work, "s01", "Old line.", source_text="Old line.", lang=EN)
    services = _services()

    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.default_output(), offline=False)

    assert "s01" in str(exc.value) and "voice_en/" in str(exc.value) and services.synthesize.texts == []


def test_reshoot_en_only_rerenders_and_never_regenerates_shared_images(tmp_path):
    work, config = _setup(tmp_path)
    _images(work)
    _render_zh(work, config)
    services = _services()
    render(work, config, services, out=work.default_output(), offline=False)

    with pytest.raises(VideoError) as exc:
        reshoot(work, config, services, ("s01",), out=work.default_output(), generate=True)
    assert "--no-generate" in str(exc.value) and not work.rejected_dir.exists()

    reshoot(work, config, services, ("s01",), out=work.default_output(), generate=False)
    assert services.generate_image.prompts == {}


def test_render_en_requires_the_chinese_version_rendered_first(tmp_path):
    work, config = _setup(tmp_path)
    _images(work)
    services = _services()

    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.default_output(), offline=False)

    assert "中文版" in str(exc.value) and services.synthesize.texts == []  # 配音之前就拦下，不花钱


def test_render_en_refuses_when_the_chinese_cut_used_another_image(tmp_path):
    # 回归：中文版换了图（或改了 motion）却没重渲，英文版按新图出片，两版画面对不上。
    work, config = _setup(tmp_path)
    _images(work)
    _render_zh(work, config)
    work.image("s02").write_bytes(b"jpg-new")

    with pytest.raises(VideoError) as exc:
        render(work, config, _services(), out=work.default_output(), offline=False)

    assert "s02" in str(exc.value) and "s01" not in str(exc.value) and "reshoot" in str(exc.value)


def test_render_en_refuses_when_the_chinese_cut_used_an_older_motion(tmp_path):
    work, config = _setup(tmp_path)
    shots = [{"id": f"s0{n}", "line": n, "bg": "蓝色", "scene": "站着"} for n in (1, 2, 3)]
    shots[0]["motion"] = {"type": "glow", "color": "#FFE66D"}
    _write_shots(work, shots)
    _images(work)
    work.image("s03").write_bytes(b"jpg-s03")
    _render_zh(work, config)
    shots[0]["motion"]["color"] = "#88CCFF"
    _write_shots(work, shots)

    with pytest.raises(VideoError) as exc:
        render(work, config, _services(), out=work.default_output(), offline=False)

    assert "s01" in str(exc.value) and "s02" not in str(exc.value) and "中文版" in str(exc.value)


def _write_shots(work, shots):
    work.shots.write_text(json.dumps({"style": "风格{bg}：", "shots": shots}, ensure_ascii=False), encoding="utf-8")


def test_render_en_refuses_when_the_chinese_cut_predates_a_look_change(tmp_path, monkeypatch):
    # 回归：发光效果的代码修过以后中文版没重渲，英文版用的是新效果。
    work, config = _setup(tmp_path)
    _images(work)
    _render_zh(work, config)
    monkeypatch.setattr(render_mod, "LOOK_VERSION", render_mod.LOOK_VERSION + 1)

    with pytest.raises(VideoError) as exc:
        render(work, config, _services(), out=work.default_output(), offline=False)

    assert "s01" in str(exc.value) and "s02" in str(exc.value)


def test_reshoot_en_refuses_to_leave_other_english_shots_behind_the_chinese_cut(tmp_path):
    work, config = _setup(tmp_path)
    _images(work)
    zh = _render_zh(work, config)
    render(work, config, _services(), out=work.default_output(), offline=False)
    work.image("s02").write_bytes(b"jpg-new")
    reshoot(zh, config, _services(), ("s02",), out=zh.default_output(), generate=False)

    with pytest.raises(VideoError) as exc:
        reshoot(work, config, _services(), ("s01",), out=work.default_output(), generate=False)
    assert "s02" in str(exc.value)

    reshoot(work, config, _services(), ("s02",), out=work.default_output(), generate=False)


def test_reshoot_drops_image_keys_the_spec_no_longer_has(tmp_path):
    # 回归：旧清单里的图片键（镜头改成分层前的 s01）被 reshoot 原样带下去，指纹永远对不上。
    work, config = _setup(tmp_path)
    _images(work)
    zh = _render_zh(work, config)
    manifest = json.loads(zh.manifest.read_text(encoding="utf-8"))
    manifest["images"]["s01:old"] = "stale"
    zh.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    reshoot(zh, config, _services(), ("s01", "s02"), out=zh.default_output(), generate=False)

    assert set(read_manifest(zh)["images"]) == {"s01", "s02"}
    render(work, config, _services(), out=work.default_output(), offline=False)


def test_build_voice_en_uses_word_timestamps(tmp_path):
    work = WorkDir(tmp_path, "en")
    segments = (("s01", ("Hello, world.",)), ("s02", ("The second shot.", "One last line.")))
    for sid, lines in segments:
        write_voice(work, sid, " ".join(lines), lang=EN)

    timeline = build_voice(work, segments, FakeRunner())

    assert [e[2] for e in timeline.events] == ["Hello, world", "The second shot", "One last line"]


def test_cli_lang_en_checks_and_renders_the_english_version(tmp_path, monkeypatch, capsys):
    work, _ = _setup(tmp_path)
    _images(work)
    monkeypatch.chdir(tmp_path)
    services = _services()
    monkeypatch.setattr(cli, "_services", lambda: services)
    flags = ["--ref", "ref.jpg", "--bgm", "bgm.mp3", "--lang", "en"]
    assert cli.main(["render", "w", *flags[:4]]) == 0

    assert cli.main(["check", "w", "--lang", "en"]) == 0
    assert "Hello, world." in capsys.readouterr().out
    assert cli.main(["render", "w", *flags]) == 0
    assert "w.en.mp4" in capsys.readouterr().out
    assert cli.main(["render", "w", *flags, "--speaker", "en_other"]) == 0
    assert cli.main(["reshoot", "w", "s01", *flags]) == 1
    assert cli.main(["reshoot", "w", "s01", "--no-generate", *flags]) == 0
    assert cli.main(["render", "w", *flags, "--out", "w/w.mp4"]) == 1  # 不能覆盖中文版成片
    assert cli.main(["render", "w", *flags, "--out", "w/w.v2.en.mp4"]) == 0
