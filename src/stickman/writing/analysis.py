"""文案分析结果：analysis/<bvid>.json 的格式校验、保存、读取和可读报告。

分析由 Claude Code 的 /analyze-script skill 产出；这里只负责「格式不对就拒收」，保证后续汇总可靠。
"""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..collect.bilibili.storage import write_json
from ..collect.bilibili.targets import BVID_PATTERN

ROLES = ("hook", "concept", "example", "reframe", "closing", "other")
VERDICT_LABELS = {
    "verbatim": "原话",
    "paraphrase": "意译",
    "misattributed": "误引",
    "unverified": "无法核实",
    "no_attribution": "未注明出处",
}
NEEDS_SOURCE = ("verbatim", "paraphrase")  # 判定为原话或意译，就必须给出原始出处
TOP_KEYS = ("bvid", "title", "summary", "themes", "structure", "claims", "concepts", "analyzed_at", "notes")


MAX_TEXT_CHARS = 2000
MAX_URL_CHARS = 500
# 控制字符、零宽字符、双向文本控制符：网页里可能藏着看不见的注入文字
HIDDEN_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


class AnalysisError(ValueError):
    """分析结果格式不对，或读取失败。"""


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_errors(obj: dict, keys: tuple[str, ...], where: str, *, required: bool) -> list[str]:
    """字符串字段：必填时不能为空；可选时可为 null；都不能过长或含隐藏字符。"""
    errors = []
    for key in keys:
        value = obj.get(key)
        if value is None and not required:
            continue
        if not isinstance(value, str) or (required and not value.strip()):
            errors.append(f"{where}.{key} {'不能为空' if required else '必须是字符串或 null'}")
        elif len(value) > MAX_TEXT_CHARS:
            errors.append(f"{where}.{key} 太长（超过 {MAX_TEXT_CHARS} 字）")
        elif HIDDEN_CHARS.search(value):
            errors.append(f"{where}.{key} 含有控制字符或零宽字符")
    return errors


def _url_errors(url: Any, where: str) -> list[str]:
    if url is None:
        return []
    parsed = urlparse(url) if isinstance(url, str) else None
    ok = (
        parsed is not None
        and parsed.scheme in ("http", "https")
        and bool(parsed.netloc)
        and len(url) <= MAX_URL_CHARS
        and not re.search(r"\s", url)
    )
    return [] if ok else [f"{where}.url 必须是 http(s) 链接"]


def _check_source(source: Any, where: str) -> list[str]:
    if source is None:
        return []
    if not isinstance(source, dict):
        return [f"{where}.source 必须是对象"]
    return (
        _text_errors(source, ("author", "title"), f"{where}.source", required=True)
        + _text_errors(source, ("locator",), f"{where}.source", required=False)
        + _url_errors(source.get("url"), f"{where}.source")
    )


def _items(value: Any, name: str, *, non_empty: bool = False) -> tuple[list[tuple[str, dict]], list[str]]:
    """把列表拆成 (位置, 对象)，并报告不是列表或元素不是对象的情况。"""
    if not isinstance(value, list) or (non_empty and not value):
        return [], [f"{name} 必须是{'非空' if non_empty else ''}列表"]
    items, errors = [], []
    for i, item in enumerate(value):
        where = f"{name}[{i}]"
        if isinstance(item, dict):
            items.append((where, item))
        else:
            errors.append(f"{where} 必须是对象")
    return items, errors


def _check_structure(value: Any) -> list[str]:
    items, errors = _items(value, "structure", non_empty=True)
    for where, item in items:
        if item.get("role") not in ROLES:
            errors.append(f"{where}.role 必须是 {'/'.join(ROLES)} 之一")
        start = item.get("start")
        if isinstance(start, bool) or not isinstance(start, (int, float)) or not 0 <= start < float("inf"):
            errors.append(f"{where}.start 必须是非负秒数")
        errors += _text_errors(item, ("text",), where, required=True)
    return errors


def _check_claim(claim: dict, where: str) -> list[str]:
    errors = _text_errors(claim, ("id", "quote"), where, required=True)
    errors += _text_errors(claim, ("attributed_to", "work", "original", "notes"), where, required=False)
    verdict = claim.get("verdict")
    if verdict not in VERDICT_LABELS:
        errors.append(f"{where}.verdict 必须是 {'/'.join(VERDICT_LABELS)} 之一")
    if verdict in NEEDS_SOURCE and not claim.get("source"):
        errors.append(f"{where}.source 必填：判定为{VERDICT_LABELS[verdict]}时必须给出原始出处")
    return errors + _check_source(claim.get("source"), where)


def _check_claims(value: Any) -> list[str]:
    items, errors = _items(value, "claims")
    seen: set[str] = set()
    for where, claim in items:
        errors += _check_claim(claim, where)
        claim_id = claim.get("id")
        if isinstance(claim_id, str):
            if claim_id in seen:
                errors.append(f"{where}.id 重复：{claim_id}")
            seen.add(claim_id)
    return errors


def _check_concepts(value: Any) -> list[str]:
    items, errors = _items(value, "concepts")
    for where, concept in items:
        errors += _text_errors(concept, ("name",), where, required=True)
        errors += _text_errors(concept, ("origin",), where, required=False)
        errors += _check_source(concept.get("source"), where)
    return errors


def validate_analysis(data: Any) -> list[str]:
    """返回所有格式错误；空列表表示合法。"""
    if not isinstance(data, dict):
        return ["分析结果必须是 JSON 对象"]
    errors = [f"未知字段：{k}" for k in data if k not in TOP_KEYS]
    if not isinstance(data.get("bvid"), str) or not BVID_PATTERN.fullmatch(data["bvid"]):
        errors.append("bvid 必须是合法的 BV 号")
    errors += [e.removeprefix(".") for e in _text_errors(data, ("title", "summary"), "", required=True)]
    errors += [e.removeprefix(".") for e in _text_errors(data, ("analyzed_at", "notes"), "", required=False)]
    themes = data.get("themes")
    if not isinstance(themes, list) or not all(_text(t) for t in themes):
        errors.append("themes 必须是字符串列表")
    return errors + _check_structure(data.get("structure")) + _check_claims(data.get("claims")) + _check_concepts(
        data.get("concepts")
    )


def save_analysis(data: Any, analysis_dir: Path) -> Path:
    errors = validate_analysis(data)
    if errors:
        raise AnalysisError("分析结果格式不对：\n" + "\n".join(f"- {e}" for e in errors))
    path = Path(analysis_dir) / f"{data['bvid']}.json"
    write_json(path, data)
    return path


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"{path.name} 读取失败：{exc}") from exc
    errors = validate_analysis(data)
    if errors:
        raise AnalysisError(f"{path.name} 格式不对：" + "；".join(errors))
    return data


def load_analysis(analysis_dir: Path, bvid: str) -> dict:
    path = Path(analysis_dir) / f"{bvid}.json"
    if not BVID_PATTERN.fullmatch(bvid) or not path.exists():
        raise AnalysisError(f"{bvid} 还没有分析结果，先在 Claude Code 里运行 /analyze-script {bvid}")
    return _read(path)


def load_analyses(analysis_dir: Path) -> list[dict]:
    return [_read(p) for p in sorted(Path(analysis_dir).glob("BV*.json"))]


def _format_source(source: dict | None) -> str:
    if not source:
        return "无"
    parts = [source["author"], f"《{source['title'].strip('《》')}》"]
    if source.get("locator"):
        parts.append(source["locator"])
    if source.get("url"):
        parts.append(source["url"])
    return " ".join(parts)


def render_report(data: dict) -> str:
    lines = [f"## {data['title']}（{data['bvid']}）", "", f"**主旨**：{data['summary']}", ""]
    lines += [f"**主题**：{'、'.join(data['themes'])}", "", "### 结构", ""]
    lines += [f"- [{s['role']}] {s['text']}" for s in data["structure"]]
    lines += ["", "### 引用核查", ""]
    for c in data["claims"]:
        label = VERDICT_LABELS[c["verdict"]]
        who = c.get("attributed_to") or "未注明"
        lines.append(f"- **{label}**｜{who}：「{c['quote']}」")
        lines.append(f"  - 原始出处：{_format_source(c.get('source'))}")
        if c.get("original"):
            lines.append(f"  - 原文：{c['original']}")
        if c.get("notes"):
            lines.append(f"  - 备注：{c['notes']}")
    if data["concepts"]:
        lines += ["", "### 概念", ""]
        lines += [f"- {c['name']}（{c.get('origin') or '来源未知'}）：{_format_source(c.get('source'))}" for c in data["concepts"]]
    return "\n".join(lines) + "\n"
