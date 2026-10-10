"""Mixed-polarity social criticism must reach discovery without new rows."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates" / "r75" / "film-brief-cleaning" / "scripts"))
from social_criticism_probe import social_criticism_probe_group  # noqa: E402


def row(identity, channel, text, sentiment):
    return {"id": identity, "channel": channel,
            "data": {"标题": "金色观剧讨论", "正文内容": text, "情感倾向": sentiment,
                     "作者": identity}}


sources = {
    "adapt": row("adapt", "微博", "《金色》加原创女主爆改原著，又切割原著，改编方式让我很不满。", "中性"),
    "mixed": row("mixed", "微博", "金色质感不错，但同样的中毒过程三波人演了三遍，个人觉得有点水！", "正面"),
    "pace": row("pace", "小红书", "看完金色第一集，不太满意的点是剧集节奏有点慢，差点坚持不下去。", "中性"),
    "praise": row("praise", "小红书", "金色节奏紧凑，零注水、无拖沓，演技全程无拉胯。", "负面"),
    "other": row("other", "微博", "另一部剧爆改原著，和金色并无关系。", "负面"),
}
group = social_criticism_probe_group(sources, ["金色"])
assert group and group["stance"] == "负向词倾向"
ids = {item["source_id"] for item in group["candidates"]}
assert {"adapt", "mixed", "pace"} <= ids
assert "praise" not in ids
for item in group["candidates"]:
    text = sources[item["source_id"]]["data"][item["text_field"]]
    assert text[item["start"]:item["end"]]
assert len(group["candidates"]) <= 6
print("r75 social criticism probe checks PASS")
