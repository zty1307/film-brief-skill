"""Check bounded source recall across generic works and publisher channels."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "candidates" / "r77" / "film-brief-cleaning" / "scripts"
sys.path.insert(0, str(SCRIPT))

from media_viewpoint_probe import media_viewpoint_probe_groups  # noqa: E402
from social_criticism_probe import social_criticism_probe_group  # noqa: E402
from final_sample_review import near_identical_media_excerpt, collapse_selected_media_copies  # noqa: E402


def row(identity, author, channel, title, body):
    return {"id": identity, "channel": channel,
            "data": {"作者": author, "标题": title, "正文内容": body}}


sources = {
    "media_tv_1": row("media_tv_1", "人民日报", "公众文章", "《测试剧》观众反响",
                      "《测试剧》以紧凑的叙事展现人物选择，许多观众认为表演自然，人物关系也有层次。"),
    "media_tv_2": row("media_tv_2", "人民日报", "微博", "《测试剧》制作观察",
                      "《测试剧》的摄影和画面强调冷暖反差，镜头让角色处境更清楚，也引发了观众讨论。"),
    "self_media": row("self_media", "普通影评号", "境内新闻", "《测试剧》短评",
                      "《测试剧》节奏扎实，几位演员表演自然，故事主题也值得继续讨论。"),
    "other_work": row("other_work", "央视新闻", "微博", "《另一部剧》开播",
                      "《另一部剧》今日开播，角色提到测试剧的筹备经历，本文只讨论另一部作品。"),
    "social_criticism": row("social_criticism", "普通观众", "小红书", "《测试剧》观后感",
                            "《测试剧》前几集剪辑有点乱，有些场景重复出现，追起来让人出戏。"),
    "media_criticism": row("media_criticism", "央视新闻", "微博", "《测试剧》讨论",
                           "《测试剧》的剪辑有点乱，部分镜头衔接生硬，让观众难以跟上人物变化。"),
}

groups, audit = media_viewpoint_probe_groups(sources, ["测试剧"])
ids = {item["source_id"] for group in groups for item in group["candidates"]}
assert audit["identified_count"] == 4
assert ids == {"media_tv_1", "media_tv_2", "media_criticism"}
assert audit["discovery_count"] == 3
assert len(groups) == 1

social = social_criticism_probe_group(sources, ["测试剧"])
assert social and {item["source_id"] for item in social["candidates"]} == {"social_criticism"}

for target, channel, body in (
    ("测试电影", "境内新闻", "《测试电影》以长镜头呈现人物困境，画面和配乐保持克制，影片的情感表达获得关注。"),
    ("测试综艺", "微博", "《测试综艺》的赛制给嘉宾留下更多发挥空间，节目节奏和舞台呈现也得到观众讨论。"),
):
    item = row(target, "人民日报", channel, f"《{target}》观察", body)
    found, checked = media_viewpoint_probe_groups({target: item}, [target])
    assert checked["identified_count"] == 1
    assert {c["source_id"] for g in found for c in g["candidates"]} == {target}

many = {str(i): row(str(i), "人民日报", "微博", "《测试电影》观感",
                    f"《测试电影》第{i}场的镜头处理很有层次，画面叙事与人物处境衔接紧密，值得讨论。")
        for i in range(30)}
limited, checked = media_viewpoint_probe_groups(many, ["测试电影"])
assert checked["identified_count"] == 30
assert checked["discovery_count"] == 24
assert sum(len(group["candidates"]) for group in limited) == 24

long_article = row("long_article", "人民日报", "境内新闻", "《测试电影》观察",
                   "《测试电影》今日开播，主演阵容公布，播出平台发布了新的预告片和宣传海报。"
                   "影片的镜头语言克制，叙事节奏扎实，人物选择得到细腻呈现，也引发观众讨论。")
found, _ = media_viewpoint_probe_groups({"long_article": long_article}, ["测试电影"])
excerpt = found[0]["candidates"][0]
text = long_article["data"][excerpt["text_field"]][excerpt["start"]:excerpt["end"]]
assert "镜头语言克制" in text

same_copy = ("故事承袭古早武侠的精神气韵，塑造荒蛮神秘的大漠江湖。"
             "风沙下真实硬核的打戏拳拳到肉，依托1500平米大型棚景构筑故事空间，"
             "贴合人物特质的兵器设计与招式细节考究，更加夯实了剧集的武侠质感。")
number_variant = same_copy.replace("1500", "一千五百")
different_report = "影片通过克制的摄影和自然表演呈现人物选择，观众关注的重点是情感关系和故事节奏，评论也讨论了结尾留白的作用。"
assert near_identical_media_excerpt(same_copy, number_variant)
assert not near_identical_media_excerpt(same_copy, different_report)

# Reviewers can choose the same short quote from two different long proposal windows.
copy_sources = {
    "media_a": row("media_a", "人民日报", "境内新闻", "样本一", same_copy),
    "media_b": row("media_b", "央视新闻", "微博", "样本二", same_copy),
    "ordinary": row("ordinary", "普通影评号", "公众文章", "样本三", same_copy),
}
state = {"pool": [{"stance": "正向词倾向"}], "judgments": {
    name: {"candidate_id": name, "source_id": name, "group_index": 0,
           "decision": "keep", "excerpt": same_copy, "score": 70,
           "media_authority_rank": 2 if name != "ordinary" else 0,
           "proposal_start": index * 100, "proposal_end": index * 100 + 250}
    for index, name in enumerate(copy_sources)}}
collapse_selected_media_copies(state, copy_sources)
assert [state["judgments"][name]["decision"] for name in copy_sources] == ["keep", "reject", "keep"]
assert state["selected_media_copy_audit"][0]["duplicate_of"] == "media_a"
collapse_selected_media_copies(state, copy_sources)
assert len(state["selected_media_copy_audit"]) == 1

for file in (SCRIPT / "media_viewpoint_probe.py", SCRIPT / "social_criticism_probe.py"):
    code = file.read_text(encoding="utf-8")
    assert all(name not in code for name in ("金色", "藏锋", "三波", "舞蹈痕迹"))

print("r77 generic media and social recall checks PASS")
