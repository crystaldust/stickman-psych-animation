from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class VideoEntry:
    """videos.jsonl 中的一行：视频的索引信息。"""

    bvid: str
    title: str
    duration: float | None = None
    view_count: int | None = None
    upload_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoEntry":
        known = {f.name for f in fields(cls)}
        if not isinstance(data.get("bvid"), str):
            raise ValueError("missing bvid")
        return cls(**{k: v for k, v in data.items() if k in known})
