"""The social recall lane must work across film, TV and variety language."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "candidates" / "r76" / "film-brief-cleaning" / "scripts"
sys.path.insert(0, str(SCRIPT))
from social_criticism_probe import social_criticism_probe_group  # noqa: E402


def row(identity, channel, title, body, sentiment):
    return {"id": identity, "channel": channel,
            "data": {"标题": title, "正文内容": body,
                     "情感倾向": sentiment, "作者": identity}}


sources = {
    "tv_adaptation": row("tv_adaptation", "微博", "测试剧观后感",
                         "《测试剧》把原著魔改得太远，重要人物的动机也变了，个人不太满意。", "正面"),
    "tv_praise": row("tv_praise", "微博", "测试剧幕后",
                     "《测试剧》演员为角色增重，演技爆改成功，表现一直不让人失望。", "负面"),
    "film_edit": row("film_edit", "小红书", "测试电影观后感",
                     "看完《测试电影》，我觉得剪辑太乱，关键情节衔接不上，观感很受影响。", "中性"),
    "variety_format": row("variety_format", "微博", "测试综艺讨论",
                           "追了《测试综艺》两期，赛制有点乱，嘉宾的发挥空间也被压得太少。", "正面"),
    "variety_praise": row("variety_praise", "小红书", "测试综艺推荐",
                           "《测试综艺》全程不拖沓、零注水，赛制越来越清楚，很值得追。", "负面"),
}
for target, expected, excluded in (
    ("测试剧", "tv_adaptation", "tv_praise"),
    ("测试电影", "film_edit", None),
    ("测试综艺", "variety_format", "variety_praise"),
):
    group = social_criticism_probe_group(sources, [target])
    assert group and len(group["candidates"]) <= 6
    ids = {item["source_id"] for item in group["candidates"]}
    assert expected in ids
    assert excluded not in ids
    for item in group["candidates"]:
        text = sources[item["source_id"]]["data"][item["text_field"]]
        assert text[item["start"]:item["end"]]

code = (SCRIPT / "social_criticism_probe.py").read_text(encoding="utf-8")
assert all(name not in code for name in ("金色", "藏锋", "三波", "舞蹈痕迹"))
print("r76 cross-format social recall checks PASS")
