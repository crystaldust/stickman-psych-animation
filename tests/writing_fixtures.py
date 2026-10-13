"""writing 模块测试用的合成数据：按 bili 采集的目录结构写出 meta.json 和 transcript.json。"""

import json
from pathlib import Path

MID = "42"


def make_video(
    root: Path,
    bvid: str,
    title: str,
    segments: list[tuple[float, float, str]],
    *,
    mid: str = MID,
    upload_date: str = "20261001",
    tags: tuple[str, ...] = ("心理学", "发现《been loving》"),
    view_count: int = 1000,
) -> Path:
    directory = root / mid / bvid
    directory.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": bvid,
        "title": title,
        "duration": segments[-1][1] if segments else 0,
        "view_count": view_count,
        "upload_date": upload_date,
        "tags": list(tags),
        "uploader": "测试 up 主",
        "uploader_id": mid,
    }
    (directory / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    data = [{"start": s, "end": e, "text": t} for s, e, t in segments]
    (directory / "transcript.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return directory


def valid_analysis(bvid: str = "BV1aaaaaaaaa") -> dict:
    return {
        "bvid": bvid,
        "title": "为什么缺爱的人谁陪他就爱谁",
        "summary": "用弗洛伊德的对象选择理论解释缺爱者的依恋模式。",
        "themes": ["亲密关系", "原生家庭"],
        "structure": [
            {"role": "hook", "start": 0.0, "text": "为什么从小缺爱的人，谁陪在他身边，他就爱谁。"},
            {"role": "concept", "start": 5.0, "text": "弗洛伊德提出了对象选择的概念。"},
            {"role": "closing", "start": 120.0, "text": "你才可能爱一个具体的人。"},
        ],
        "claims": [
            {
                "id": "c1",
                "quote": "在爱中，自我变得贫乏，对象变得崇高。",
                "attributed_to": "弗洛伊德",
                "work": "《性学三论》",
                "verdict": "verbatim",
                "original": "the ego becomes impoverished in favour of the object",
                "source": {
                    "author": "西格蒙德·弗洛伊德",
                    "title": "论自恋：一篇导论",
                    "locator": "第二节",
                    "url": None,
                },
                "notes": "出处应为《论自恋》而非《性学三论》。",
            },
            {
                "id": "c2",
                "quote": "每个人都有两次生命，第二次从四十岁开始。",
                "attributed_to": "荣格",
                "work": None,
                "verdict": "misattributed",
                "original": None,
                "source": {"author": "卡尔·荣格", "title": "人生的阶段", "locator": None, "url": None},
                "notes": "荣格原著中查不到这句话；最接近的是关于「人生下午」的论述。",
            },
        ],
        "concepts": [
            {
                "name": "对象选择",
                "origin": "弗洛伊德",
                "source": {"author": "西格蒙德·弗洛伊德", "title": "论自恋：一篇导论", "locator": None, "url": None},
            }
        ],
    }
