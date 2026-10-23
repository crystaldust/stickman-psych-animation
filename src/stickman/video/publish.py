"""发布信息：标题、简介（带出处）和封面。

Claude 按 .claude/skills/package-video 写 publish.json（英文版 publish.en.json），这里只做确定性的事：
校验、把出处排进简介、出封面。出处只能来自草稿 front matter 里核实过的作者，英文版和中文版一一对应，
免得简介里出现草稿没核实过的来源。出处的 link 先留空，以后加商品链接只改这一个字段。
"""

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import VideoError
from .ffmpeg import Runner
from .lang import PRIMARY
from .shots import ShotSpec, load_spec
from .thumbnail import LAYOUTS, Line, make_headline, make_thumbnail, parse_text
from .workdir import WorkDir

KINDS = {"book": ("图书", "book"), "paper": ("论文", "paper"), "interview": ("访谈", "interview"),
         "lecture": ("演讲", "lecture"), "article": ("文章", "article")}
MAX_TITLE_CHARS = 100  # YouTube 标题上限
MAX_DESCRIPTION_CHARS = 1000  # 中文版也发抖音，抖音文案上限 1000 字
MAX_DESCRIPTION_BYTES = 5000  # YouTube 简介上限按字节算
LAYOUTS_BY_LANG = {PRIMARY: ("16x9", "3x4", "9x16"), "en": ("16x9",)}  # 3:4、9:16 竖版只给抖音（中文）
_FIELDS = {"title", "description", "sources", "thumbnail"}
_SOURCE_REQUIRED = ("author", "title", "kind")
_SOURCE_OPTIONAL = ("detail", "note", "link")
_FORBIDDEN = re.compile(r"[<>]")  # YouTube 的标题和简介不接受尖括号
_FRONT_AUTHOR = re.compile(r"^\s*-?\s*author:\s*[\"']?(.+?)[\"']?\s*$")


@dataclass(frozen=True)
class Source:
    author: str
    title: str
    kind: str
    detail: str = ""
    note: str = ""
    link: str = ""


@dataclass(frozen=True)
class Thumbnail:
    shot: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Publish:
    title: str
    description: str
    sources: tuple[Source, ...]
    thumbnail: Thumbnail


def _text(raw: dict, key: str, errors: list[str], where: str, required: bool = True) -> str:
    value = raw.get(key, None if required else "")
    if not isinstance(value, str) or (required and not value.strip()):
        errors.append(f"{where}{key} 应该是{'非空' if required else ''}字符串")
        return ""
    return value.strip()


def _source(raw: Any, i: int, errors: list[str]) -> Source | None:
    where = f"sources[{i}] 的 "
    if not isinstance(raw, dict):
        errors.append(f"sources[{i}] 应该是对象")
        return None
    unknown = sorted(set(raw) - set(_SOURCE_REQUIRED) - set(_SOURCE_OPTIONAL))
    if unknown:
        errors.append(f"sources[{i}] 里有不认识的字段：{', '.join(unknown)}")
    fields = {k: _text(raw, k, errors, where) for k in _SOURCE_REQUIRED}
    fields |= {k: _text(raw, k, errors, where, required=False) for k in _SOURCE_OPTIONAL}
    if fields["kind"] and fields["kind"] not in KINDS:
        errors.append(f"{where}kind 应该是 {' / '.join(KINDS)} 之一")
    if fields["link"] and not fields["link"].startswith(("https://", "http://")):
        errors.append(f"{where}link 应该是 http(s) 链接或留空")
    bad = [k for k, v in fields.items() if "\n" in v or _FORBIDDEN.search(v)]
    if bad:
        errors.append(f"{where}{', '.join(bad)} 不能换行，也不能有 < 或 >（会排进简介）")
    return Source(**fields)


def _thumbnail(raw: Any, shot_ids: tuple[str, ...], errors: list[str]) -> Thumbnail | None:
    if not isinstance(raw, dict):
        errors.append("thumbnail 应该是对象：{\"shot\": 镜头 id, \"text\": 封面字}")
        return None
    shot, text = _text(raw, "shot", errors, "thumbnail 的 "), _text(raw, "text", errors, "thumbnail 的 ")
    if shot and shot not in shot_ids:
        errors.append(f"thumbnail 的镜头 {shot} 不在 shots.json 里")
    try:
        lines = parse_text(text) if text else ()
    except VideoError as exc:
        errors.append(str(exc))
        lines = ()
    return Thumbnail(shot, lines)


def parse_publish(raw: Any, shot_ids: tuple[str, ...], label: str) -> Publish:
    """校验 publish.json 的内容；所有问题一次列出来。"""
    if not isinstance(raw, dict):
        raise VideoError(f"{label} 应该是 JSON 对象")
    errors = [f"不认识的字段：{', '.join(sorted(set(raw) - _FIELDS))}"] if set(raw) - _FIELDS else []
    title = _text(raw, "title", errors, "")
    description = _text(raw, "description", errors, "")
    if len(title) > MAX_TITLE_CHARS or "\n" in title:
        errors.append(f"title 最多 {MAX_TITLE_CHARS} 个字符，不能换行")
    if _FORBIDDEN.search(title + description):
        errors.append("title 和 description 里不能有 < 或 >（YouTube 不接受）")
    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        errors.append("sources 应该是非空列表（视频主题的出处）")
        sources_raw = []
    sources = tuple(_source(s, i, errors) for i, s in enumerate(sources_raw))
    thumbnail = _thumbnail(raw.get("thumbnail"), shot_ids, errors)
    if errors or thumbnail is None or None in sources:
        raise VideoError(f"{label} 有问题：\n  " + "\n  ".join(errors))
    return Publish(title, description, tuple(s for s in sources if s is not None), thumbnail)


def draft_authors(text: str) -> frozenset[str]:
    """草稿 front matter 里 sources 的作者（正文里的不算）。"""
    rows = text.splitlines()
    if not rows or rows[0].strip() != "---":
        return frozenset()
    authors = set()
    for row in rows[1:]:
        if row.strip() == "---":
            break
        match = _FRONT_AUTHOR.match(row)
        if match:
            authors.add(match[1].strip())
    return frozenset(authors)


def _source_lines(source: Source, lang: str) -> list[str]:
    kind = KINDS[source.kind][0 if lang == PRIMARY else 1]
    if lang == PRIMARY:
        head = f"· {source.author}《{source.title}》（{kind}{'，' + source.detail if source.detail else ''}）"
        link = f"  链接：{source.link}"
    else:
        head = f"· {source.author}, {source.title} ({kind}{', ' + source.detail if source.detail else ''})"
        link = f"  Link: {source.link}"
    return [head] + ([f"  {source.note}"] if source.note else []) + ([link] if source.link else [])


def description_text(pub: Publish, lang: str) -> str:
    """简介正文 + 「本期出处」：每条出处一行，有备注、有链接再各占一行。"""
    header = "本期出处" if lang == PRIMARY else "Sources"
    body = [line for s in pub.sources for line in _source_lines(s, lang)]
    text = f"{pub.description}\n\n{header}\n" + "\n".join(body) + "\n"
    if lang == PRIMARY and len(text) > MAX_DESCRIPTION_CHARS:
        raise VideoError(f"简介连同出处有 {len(text)} 字，抖音最多 {MAX_DESCRIPTION_CHARS} 字，删短一些")
    if len(text.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        raise VideoError(f"简介连同出处有 {len(text.encode('utf-8'))} 字节，YouTube 最多 {MAX_DESCRIPTION_BYTES} 字节")
    return text


def load_publish(path: Path, shot_ids: tuple[str, ...]) -> Publish:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VideoError(f"找不到 {path.name}：先用 /package-video 写好发布信息") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VideoError(f"{path.name} 不是合法的 UTF-8 JSON：{exc}") from exc
    except OSError as exc:
        raise VideoError(f"读不了 {path}：{exc}") from exc
    return parse_publish(raw, shot_ids, path.name)


def _check_sources(work: WorkDir, pub: Publish, draft: Path, shot_ids: tuple[str, ...]) -> None:
    """中文版：作者都要在草稿的出处里。英文版：条数和中文版 publish.json 一样（作者名是英文，没法逐个比）。"""
    if work.lang != PRIMARY:
        primary = WorkDir(work.root).publish
        if not primary.is_file():
            raise VideoError(f"英文版的出处要和中文版一一对应：先写中文版的 {primary.name}")
        n = len(load_publish(primary, shot_ids).sources)
        if n != len(pub.sources):
            raise VideoError(f"中文版 {primary.name} 有 {n} 条出处，{work.publish.name} 有 {len(pub.sources)} 条，要一一对应")
        return
    if not draft.is_file():
        raise VideoError(f"找不到草稿 {draft}：出处要和草稿 front matter 核对（可用 --draft 指定）")
    try:
        authors = draft_authors(draft.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise VideoError(f"读不了草稿 {draft}：{exc}") from exc
    unknown = [s.author for s in pub.sources if s.author not in authors]
    if unknown:
        raise VideoError(f"这些作者不在草稿 {draft.name} 的出处里：{', '.join(unknown)}。"
                         f"简介只能写草稿里核实过的出处（草稿里有：{', '.join(sorted(authors)) or '无'}）")


def _cover_image(work: WorkDir, spec: ShotSpec, shot_id: str) -> Path:
    shot = next(s for s in spec.shots if s.id == shot_id)
    if shot.motion is not None and shot.motion.layers:
        raise VideoError(f"{shot_id} 是分层镜头，没有整幅图，封面换一个镜头")
    image = work.image(shot_id)
    if not image.is_file():
        raise VideoError(f"{shot_id} 还没有图 {image}：先渲染中文版")
    return image


def package(work: WorkDir, font: Path, draft: Path, runner: Runner) -> tuple[Path, ...]:
    """校验发布信息，写 title.txt、description.txt，出封面；返回写出的文件。"""
    if not font.is_file():
        raise VideoError(f"找不到封面字体 {font}：下载得意黑（Smiley Sans）的 SmileySans-Oblique.ttf 放到这里，见 README")
    spec, _ = load_spec(WorkDir(work.root))  # 分镜和图归中文版
    pub = load_publish(work.publish, spec.ids())
    _check_sources(work, pub, draft, spec.ids())
    image = _cover_image(work, spec, pub.thumbnail.shot)
    description = description_text(pub, work.lang)
    covers = {name: work.thumbnail(name) for name in LAYOUTS_BY_LANG[work.lang]}
    try:
        for name, out in covers.items():  # 先出封面：ffmpeg 失败时不留下只有文字的半成品
            make_thumbnail(image, out, pub.thumbnail.lines, LAYOUTS[name], font, work.package_tmp, runner)
    finally:
        shutil.rmtree(work.package_tmp, ignore_errors=True)
    work.title.write_text(pub.title + "\n", encoding="utf-8")
    work.description.write_text(description, encoding="utf-8")
    return (work.title, work.description, *covers.values())


@dataclass(frozen=True)
class Headline:
    path: Path
    fingerprint: str  # 封面字、封面镜头的图和字体：三者不变，画出来的字就不变


def headline(work: WorkDir, spec: ShotSpec, font: Path, runner: Runner) -> Headline:
    """竖屏视频顶部的封面字：用 publish.json 里的封面字和封面镜头的配色，写到 work.headline。"""
    if not font.is_file():
        raise VideoError(f"找不到封面字体 {font}：下载得意黑（Smiley Sans）的 SmileySans-Oblique.ttf 放到这里，见 README")
    pub = load_publish(work.publish, spec.ids())
    image = _cover_image(work, spec, pub.thumbnail.shot)
    with tempfile.TemporaryDirectory() as tmp:  # 不用 package_tmp：和 package 同时跑时会互相删文件
        make_headline(image, work.headline, pub.thumbnail.lines, font, Path(tmp), runner)
    inputs = (pub.thumbnail.lines, *(hashlib.sha256(p.read_bytes()).hexdigest() for p in (image, font)))
    return Headline(work.headline, hashlib.sha256(repr(inputs).encode()).hexdigest())
