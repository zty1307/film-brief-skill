"""The discovery handoff must account for social posts before negative clusters freeze."""
from pathlib import Path
from collections import Counter
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates" / "r74" / "film-brief-cleaning" / "scripts"))
from viewpoint_discovery import (representative_candidates, social_criticism_cue_score,
                                 validate_social_negative_review)  # noqa: E402


excerpts = {
    "E001": {"source_channel": "小红书"},
    "E002": {"source_channel": "微博"},
    "E003": {"source_channel": "境内新闻"},
}
clusters = [{"id": "N1", "stance": "negative", "seed_ids": ["E001"]},
            {"id": "P1", "stance": "positive", "seed_ids": ["E003"]}]


def rejected(rows, needle):
    try:
        validate_social_negative_review({"social_negative_review": rows}, excerpts, clusters)
    except ValueError as exc:
        assert needle in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected rejection: {needle}")


valid = [
    {"excerpt_id": "E001", "decision": "criticism", "reason": "明确指出动作僵硬", "cluster_id": "N1"},
    {"excerpt_id": "E002", "decision": "not_criticism", "reason": "否定式好评", "cluster_id": None},
]
assert len(validate_social_negative_review({"social_negative_review": valid}, excerpts, clusters)) == 2
rejected(valid[:1], "须覆盖全部")
rejected(valid + [valid[0]], "须覆盖全部")
rejected([dict(valid[0], cluster_id="P1"), valid[1]], "只能关联本次负面簇")
rejected([dict(valid[0], decision="not_criticism"), valid[1]], "不能作负面簇证据")

assert social_criticism_cue_score("预告没让人失望，节奏不拖沓") == 0
assert social_criticism_cue_score("我有点不太满意，动作有点僵硬") > 0
sources = {
    "media": {"channel": "境内新闻", "data": {"text": "媒体大段评论"}},
    "praise": {"channel": "微博", "data": {"text": "预告没让人失望，节奏不拖沓"}},
    "critical": {"channel": "小红书", "data": {"text": "我有点不太满意，动作有点僵硬"}},
}
candidates = [
    {"source_id": identity, "score": score, "text_field": "text", "start": 0,
     "end": len(record["data"]["text"]), "identity": identity}
    for identity, score, record in (("media", 100, sources["media"]),
                                    ("praise", 90, sources["praise"]),
                                    ("critical", 20, sources["critical"]))
]
picked = representative_candidates(candidates, sources, set(), set(), Counter(), limit=2,
                                   review_social_criticism=True)
assert [item["source_id"] for item in picked] == ["media", "critical"]
print("r74 social negative review checks PASS")
