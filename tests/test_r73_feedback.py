"""Focused checks for acting-cluster and negative-source feedback."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates" / "r73" / "film-brief-cleaning" / "scripts"))

from rapid_film_workbench import rank_candidates, source_selection_key  # noqa: E402
from viewpoint_discovery import ACTING_TITLE_CUES, apply_discovery  # noqa: E402


def check_error(clusters, expected):
    state = {"status": "VIEWPOINT_DISCOVERY_REQUIRED", "discovery_binding": "current"}
    answer = {"fingerprint": "current", "clusters": clusters}
    try:
        apply_discovery(Path("unused"), state, answer, {}, lambda _: "unused")
    except ValueError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected discovery rejection: {expected}")


clusters = [
    {"id": f"P{i}", "stance": "positive", "performance_focus": i < 3}
    for i in range(10)
]
check_error(clusters, "演技/表演观点合计最多2组")
check_error(clusters[:9] + [{"id": "O1", "stance": "objective", "performance_focus": False}],
            "正面观点至少10组")
assert all(ACTING_TITLE_CUES.search(title) for title in (
    "认可谭松韵表演层次细腻", "认可刘学义打戏力量感", "赞赏配角演技出彩"))
assert not ACTING_TITLE_CUES.search("赞赏反串造型带来视觉反差")

sources = {
    "news": {"channel": "境内新闻", "data": {}},
    "public": {"channel": "公众文章", "data": {}},
    "weibo": {"channel": "微博", "data": {}},
    "xhs": {"channel": "小红书", "data": {}},
}
items = [{"source_id": key, "score": 100, "start": 0} for key in sources]
assert [x["source_id"] for x in rank_candidates(items, sources, stance="negative")] == [
    "weibo", "xhs", "public", "news"
]
assert [x["source_id"] for x in rank_candidates(items, sources, stance="positive")][:2] == [
    "news", "public"
]
out_of_band = [{"source_id": "news", "score": 130, "start": 0},
               {"source_id": "weibo", "score": 100, "start": 0}]
assert rank_candidates(out_of_band, sources, stance="negative")[0]["source_id"] == "news"
assert source_selection_key(sources["weibo"], "负向词倾向") < source_selection_key(
    sources["public"], "负向词倾向")
print("r73 feedback checks PASS")
