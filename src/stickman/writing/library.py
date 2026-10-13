"""出处库：把所有分析结果里的引用和概念按「作者 + 作品」汇总，供 /write-script 取材。"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from ..collect.bilibili.storage import write_text
from .analysis import VERDICT_LABELS


PARENTHETICAL = re.compile(r"[（(][^（）()]*[）)]")  # 只处理一层括号
NAME_NOISE = re.compile(r"[\s·•・.]+")


class LibraryError(ValueError):
    """出处库不存在或文件损坏。"""


def _title(raw: str) -> str:
    """作品的通行名：去掉书名号和括号里的外文名、年份等别名，避免同一部作品被拆成多条。"""
    title = PARENTHETICAL.sub("", raw).strip().strip("《》").strip()
    return title or raw.strip()


def _fold(text: str) -> str:
    """只用于比较：全角半角统一，去掉空格和间隔号，「卡尔·荣格」「卡尔 · 荣格」视为同一人。"""
    return NAME_NOISE.sub("", unicodedata.normalize("NFKC", text))


def _key(source: dict) -> tuple[str, str]:
    return _fold(source["author"]), _fold(_title(source["title"]))


def _quote_item(claim: dict, bvid: str) -> dict[str, Any]:
    source = claim["source"]
    return {
        "kind": "quote",
        "quote": claim["quote"],
        "attributed_to": claim.get("attributed_to"),
        "original": claim.get("original"),
        "verdict": claim["verdict"],
        "locator": source.get("locator"),
        "url": source.get("url"),
        "notes": claim.get("notes"),
        "bvid": bvid,
    }


def _concept_item(concept: dict, bvid: str) -> dict[str, Any]:
    return {"kind": "concept", "name": concept["name"], "origin": concept.get("origin"), "bvid": bvid}


def _collect(
    analyses: list[dict],
) -> tuple[dict[tuple[str, str], list[dict]], dict[tuple[str, str], tuple[str, str]]]:
    """按作品分组；同时记下每部作品第一次出现时的作者和书名写法，用于展示。"""
    grouped: dict[tuple[str, str], list[dict]] = {}
    display: dict[tuple[str, str], tuple[str, str]] = {}
    for analysis in analyses:
        bvid = analysis["bvid"]
        pairs = [(c["source"], _quote_item(c, bvid)) for c in analysis["claims"] if c.get("source")]
        pairs += [(c["source"], _concept_item(c, bvid)) for c in analysis["concepts"] if c.get("source")]
        for source, item in pairs:
            key = _key(source)
            display.setdefault(key, (source["author"].strip(), _title(source["title"])))
            grouped.setdefault(key, []).append(item)
    return grouped, display


def _dedupe_concepts(items: list[dict]) -> list[dict]:
    """同一部作品里的同名概念只保留第一次出现；引用按视频分别保留。"""
    seen: set[str] = set()
    result = []
    for item in items:
        if item["kind"] == "concept":
            if item["name"] in seen:
                continue
            seen.add(item["name"])
        result.append(item)
    return result


def build_library(analyses: list[dict]) -> list[dict]:
    grouped, display = _collect(analyses)
    entries = []
    for key, items in sorted(grouped.items()):
        author, title = display[key]
        entries.append(
            {
                "author": author,
                "title": title,
                "items": _dedupe_concepts(items),
                "used_in": sorted({i["bvid"] for i in items}),
            }
        )
    return entries


def write_library(entries: list[dict], path: Path) -> None:
    write_text(Path(path), "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def read_library(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise LibraryError(f"出处库 {path} 还不存在，先运行：stickman library build")
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError as exc:
            raise LibraryError(f"{path} 第 {number} 行损坏：{exc}") from exc
    return entries


def render_library(entries: list[dict]) -> str:
    lines = [f"# 出处库（{len(entries)} 部作品）", ""]
    for entry in entries:
        lines += [f"## 《{entry['title']}》— {entry['author']}", f"用于：{'、'.join(entry['used_in'])}", ""]
        for item in entry["items"]:
            if item["kind"] == "concept":
                lines.append(f"- 概念：{item['name']}")
                continue
            label = VERDICT_LABELS[item["verdict"]]
            locator = f"（{item['locator']}）" if item.get("locator") else ""
            lines.append(f"- {label}{locator}：「{item['quote']}」")
            if item.get("original"):
                lines.append(f"  - 原文：{item['original']}")
            if item.get("notes"):
                lines.append(f"  - 备注：{item['notes']}")
        lines.append("")
    return "\n".join(lines)
