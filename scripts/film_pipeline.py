from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


CHANNEL_PRIORITY = {"境内新闻": 5, "公众文章": 4, "今日头条": 3, "微博": 2, "小红书": 1, "抖音": 0}
CHANNEL_BASE = {"境内新闻": 3.5, "公众文章": 3.5, "今日头条": 2.5, "微博": 1.0, "小红书": 0.5, "抖音": -2.0}
POST_BASE = {"原帖": 3.0, "评论": -2.0, "转帖": -1.0}
SOCIAL_CHANNELS = {"微博", "小红书"}
ALLOWED_DECISIONS = {"retain_core", "retain_consensus", "exclude"}
ALLOWED_CLUSTER_STANCES = {"positive", "objective", "negative"}
ALLOWED_CLUSTER_REPORT_ROLES = {"report_point", "subtopic", "data_note", "rare_signal"}
ALLOWED_CLUSTER_EXCLUSION_REASONS = {
    "out_of_scope",
    "no_reportable_viewpoint",
    "cross_work_mismatch",
    "abusive_non_viewpoint",
    "promotion_only",
    "duplicate_or_corrupt",
}
CLUSTER_ASSIGNMENT_ISSUE_GUIDANCE = {
    "passage_stance_conflict": "当前片段立场与簇不一致；重选原文窗口、移动到同立场簇，或对明确无可用观点项使用有证据的 __exclude__",
    "negative_cluster_missing_negative_cue": "自动词表未确认负向；阅读全文后若确为负向，填写 passage_stance=negative 和逐字证据即可",
    "no_positive_cluster_evidence": "当前窗口没有足够簇证据；重选含判断和具体依据的窗口，或转簇/有证据排除",
    "low_cluster_evidence": "当前窗口与簇标题的语义证据较弱；阅读全文后重选、转簇或有证据排除",
    "multi_work_passage_requires_review": "同段涉及多作品；必须用 target_evidence 明确当前判断属于哪部作品",
    "no_passage_target_anchor": "局部片段未重复作品名；用同来源逐字 target_evidence 建立归属，并用 anchor_terms 指向评价句",
    "passage_aspect_mismatch": "当前片段没有支持簇标题的评价方面；重选、转簇或有证据排除",
}
ALLOWED_OBJECTIVE_SUBTYPES = {"neutral_fact", "sentiment_distribution", "balanced_observation"}
DRAMA_CLUSTER_SCOPES = {"pre_broadcast_expectation", "current_broadcast_reaction", "later_reputation", "mixed_time_explicit"}
VARIETY_CLUSTER_SCOPES = {"latest_episode", "previous_episode_prominent", "program_level_current", "mixed_scope_explicit"}
ALLOWED_DROP_FAILURE_CHECKS = {
    "target", "aspect", "stance", "self_contained", "work_consistency", "evidence_specificity",
    "display_promotion", "excerpt_focus", "cluster_claim",
}
CLUSTER_TITLE_PREDICATE = re.compile(
    r"^(?:肯定|认可|称赞|关注|讨论|质疑|批评|担忧|认为|看好|指出|反映|赞赏|期待|吐槽|不满|"
    r"呈现|展现|聚焦|强调|记录|客观梳理|客观记录|汇集|分析|以|仅以|把|对|有媒体|节目官方|开播反馈|"
    r"[^，。]{2,16}以)|，(?:认为|称|指出|反映|质疑|批评|担忧|引发|带来|体现|呈现|展现)"
)
GENERIC_CLUSTER_TITLE = re.compile(
    r"其他综合|未分类|待归类|兜底|当前片段(?:中的)?(?:相关事实|直接表达)|未形成完整评价"
)
GENERIC_ROUTING_TERMS = {
    "影视", "电视剧", "电影", "剧集", "综艺", "节目", "作品", "武侠", "古装",
    "演员", "角色", "人物", "剧情", "内容", "表现", "热度", "关注", "讨论",
    "认为", "肯定", "认可", "期待", "看好", "质疑", "批评", "担忧", "吐槽",
}
# These words can retrieve a candidate, but alone they do not establish a
# narrow report claim. AI may still keep the item after reading the relation.
AMBIGUOUS_AUTO_ASPECT_TERMS = {
    "群像", "竞争", "白月光", "特效", "制作", "阵容", "演技", "角色", "人物",
    "表现", "热度", "口碑", "质感", "情怀", "联动", "物料", "话题", "市场",
}
BAD_EXCERPT_START = ("…", "...", "。", "，", "；", "：", "”", "’", "）", "】", ")", "]")
BAD_EXCERPT_END = ("…", "...", "，", "、", "；", "：", "—", "-")

FORBIDDEN_UPSTREAM_FIELDS = {
    "known_report_source", "report_hit", "历史报送命中", "claim_cluster_id", "card_id",
    "quality_score", "topics", "focus_spans", "old_cluster", "old_excerpt", "review_tier",
    "machine_status", "ai_decision", "ai_reason", "is_cluster_representative",
}

OPINION = re.compile(
    r"好看|难看|演技|表演|选角|适配|贴合|质感|真实|悬浮|节奏|拖沓|还原|武侠味|江湖味|"
    r"油腻|出戏|失望|惊艳|喜欢|认可|吐槽|批评|值得|逻辑|注水|张力|鲜活|细腻|合理|"
    r"违和|上头|弃剧|写实|带感|精彩|高能|无聊|拉胯|精良|用心|压迫感|代入感|亮点|"
    r"惊喜|厚重|利落|硬核|沉浸|可惜|离谱|经不起推敲|自然|可信|生活化|烟火气|活人感|"
    r"好笑|笑死|爆笑|笑出声|笑到|笑大|笑疯|破功|有梗|笑点|封神|绝了|太能打|能打|"
    r"天赋型|破防|共鸣|拉满|炸裂|神了|牛|表现好|表现稳|拿捏|太会|厉害|扎心|戳心|"
    r"看点|不意外|扎眼|不俗|颠覆|突兀|打折扣|有新意|新鲜感|舒服|锋利|松弛|稳了"
)
DIMENSION = re.compile(
    r"演员|演技|表演|选角|角色|人物|人设|剧情|节奏|改编|原著|台词|镜头|运镜|画面|构图|"
    r"实景|服化道|服装|兵器|道具|打戏|武打|动作|群像|职业|逻辑|悬疑|武侠|江湖|阵容|"
    r"导演|编剧|气质|状态|造型|集数|篇幅|价值观|女性|男性|关系|结局|节目|舞台|段子|文本|"
    r"包袱|笑点|互动|赛制|导师|学员|选手|嘉宾|发言|观点|表达|议题|主题|对手戏|"
    r"视觉|美学|非遗|成长|婚姻|模式|机制|联动|培养"
)
JUDGEMENT = re.compile(
    r"认为|觉得|感觉|我看|终于|这才|不像|符合|不符合|导致|在于|因为|所以|但是|却|反而|"
    r"相比|例如|比如|最大.{0,3}(?:亮点|问题)|好看|难看|真实|自然|细腻|精彩|拖沓|悬浮|"
    r"油腻|出戏|违和|魔改|失望|合理|不合理|有意思|有味道|值得|可惜|表现|笑点|"
    r"笑出声|笑到|笑大|笑疯|破功|拿捏|太会|厉害|亮眼|看点|短板|问题在于|优势在于|"
    r"有望|堪称|证明|意味着|本质上|关键是|最戳人|最鲜明|最革命性"
)
OBJECTIVE_ANALYSIS = re.compile(r"模式|机制|联动|闭环|培养|反馈|市场检验|行业|价值|意义|作用|改变|突破|探索|体现|展现|呈现|形成|提供")
WATCHED = re.compile(r"看完|看了|看过|追了|追到|连追|前几集|第[一二三四五六七八九十\d]+集|观后|越看|弃剧")
PREVIEW = re.compile(r"预告|海报|物料|定档|官宣|阵容|看片会|观剧会")
CTA = re.compile(r"今晚.{0,8}(?:开播|上线|锁定|追)|预约|蹲守|不见不散|必须支持|收视长虹|福利|云包场|点击链接|转发抽|直播间")
FAN = re.compile(r"超话创作官|截修|壁纸|营业|穿搭|同款|首饰|配饰|路透|应援|控评|打卡|抱图|修图|高清图|站姐|舔屏")
HARD_FAN = re.compile(r"截修|修图|穿搭|同款|首饰|抱图|高清图|角色图|超话创作官|美颜|磨皮|瘦脸")
ROUNDUP = re.compile(r"[一二三四五六七八九十\d]+部(?:新剧|好剧|大剧)|新剧上新|追哪部|片单|剧单|扎堆上新|多部(?:好剧|新剧)|排播合集|追剧日历")
GOSSIP = re.compile(r"前夫|前妻|二婚|离婚|婚变|夫妻关系|片酬|恋情|绯闻|感情史")
EPISODE_RECAP = re.compile(r"^(?:《?[^》]{0,20}》?)?第[一二三四五六七八九十百\d]+集[：:]|分集剧情|剧情解说|剧情合集|同步热播剧情")
QUESTION = re.compile(r"值得入坑吗|求看了的人|有人看吗|到底要不要|怎么样[？?]|好看吗[？?]|谁看了")
GENERIC_ONLY = re.compile(r"^(?:演得|演员演得)?(?:真)?(?:挺|很|太|非常)?(?:好|不错|真实|好看|难看|一般|喜欢|期待|失望)[!！。,.，\s]*$|^(?:支持|不支持|必须追|追起来|期待开播|收视长虹)[!！。,.，\s]*$")
RISK = re.compile(r"下架|停更|撤档|网盘|夸克|百度云|磁力|盗版|番位|撕番|口碑崩|扑街|差评|抵制|热度低")
ABUSIVE_REVIEW_RISK = re.compile(
    r"走狗|汉奸|卖国贼|滚出中国|去死|不得好死|脑残|智障|畜生|狗东西|贱人|婊子|垃圾人|"
    r"地域黑|排外|仇恨|人身攻击"
)
ENTITY_MENTION_PATTERNS = (
    re.compile(r"(?:尽管|虽然)?([\u4e00-\u9fff]{2,4})(?=的角色|饰演|扮演|不是传统)"),
    re.compile(r"([\u4e00-\u9fff]{2,4})与([\u4e00-\u9fff]{2,4})(?=的关系)"),
    re.compile(
        r"(?:男主角|女主角|男主|女主|前男友|前女友)\s*[：:，,]?\s*"
        r"(?!的|角|光环|角色|设定)([\u4e00-\u9fff]{2,4})(?=[，。；、与的\s])"
    ),
)
PIRACY = re.compile(r"百度(?:云|网盘)?[：:]?\s*(?:网页)?链接|夸克(?:[：:]?\s*(?:网页)?链接|资源)|网盘资源|磁力链接|资源下载|在线观看地址")
FACT_WARNING = re.compile(r"\d+(?:\.\d+)?(?:万|亿|集|%|％)|收视|热度|市占率|排名|第一|下架|撤档|停播")
LEADING_FRAGMENT = re.compile(r"^(?:而且|并且|同时|此外|另外|其次|所以|因此|但|但是|不过|然而|可|也|还|更|再|这|他|她|它|其|其中|对此)[，、\s]?")
HASHTAG = re.compile(r"#([^#\r\n]{2,40})#")
GENERIC_CONTEXT_TAG = re.compile(r"^(?:电视剧|综艺|娱乐|影视|追剧|热播|开播|演员|明星|今日热点|微博热搜|内容过于真实)$")
DETAIL_CUE = re.compile(r"第[一二三四五六七八九十\d]+集|眼神|动作|台词|造型|场景|镜头|结尾|开篇|一场戏|"
                        r"互动|反应|发言|举动|片段|现场|情绪|状态|表情|名场面|包袱|笑点|反转|设定|"
                        r"锁喉|贴脸|打嗝|连线")
DISPLAY_NORMALIZATION = "remove_social_markup_v3"
EXCERPT_PREFERRED_MIN = 70
EXCERPT_MAX = 150
FINAL_REVIEW_CHUNK_SIZE = 80
CLUSTER_ROUTING_CACHE_VERSION = 1
AUTO_SEMANTIC_CONTRAST = re.compile(
    r"虽然|尽管|但是|不过|然而|却|反而|可惜|遗憾|问题|争议|质疑|吐槽|批评|失望"
)
AUTO_SEMANTIC_COMPLETE_END = ("。", "！", "？", "!", "?", "”", "’", "）", ")", "】", "]")
DISPLAY_MARKDOWN_LINK = re.compile(r"!?\[[^\]\r\n]{0,80}\]\((?:https?://|//)[^)\s]+\)", re.I)
DISPLAY_RAW_URL = re.compile(r"(?:https?://|www\.)[^\s，。！？；]+", re.I)
DISPLAY_HASHTAG_PAIR = re.compile(r"#[^#\s\r\n]{1,80}#")
DISPLAY_HASHTAG_SINGLE = re.compile(r"#[^\s#，。！？；：、/]{1,40}")
DISPLAY_HASHTAG_TOKEN = re.compile(r"#[^#\s\r\n，。！？；：、/]{1,80}")
DISPLAY_ADJACENT_TRAILING_TAGS = re.compile(
    r"(?P<pair>#[^#\s\r\n]{1,80}#)(?P<tail>[A-Za-z0-9_\-·\u4e00-\u9fff]{2,30})\s*$"
)
DISPLAY_PLATFORM_EMOTE = re.compile(r"\[(?=[^\]\r\n]{1,16}\])[^\]\r\n]*\]")
DISPLAY_MENTION = re.compile(r"@[\w\-\u4e00-\u9fff]{1,40}")
DISPLAY_PROMO_BOILERPLATE = re.compile(
    r"(?:你也)?戳卡片(?:来)?看看[，！!。~～]*|"
    r"(?:每天用智搜看热点[，,]?)?分享抽\d+(?:\.\d+)?元[~～！!。]*"
)
DISPLAY_STRAY_MARKDOWN_TAIL = re.compile(
    r"\s*\[[^\]\r\n]{0,80}(?:的(?:微博|小红书)?视频)?\s*$|\s*\]\([^)]*$",
    re.I,
)
PROMOTION_OPERATIONAL = re.compile(
    r"抽奖|随机抽|加抽|兑奖|礼包|赠票|扫码|二维码|购票|单人票|双人票|票价|报名|"
    r"活动规则|领取福利|点击链接|直播间下单|转发抽|云包场|送礼物|应援|控评|"
    r"(?:正片|每日|话题|任务|互动|参与)打卡|打卡(?:任务|互动)|"
    r"评论区盖楼|全平台分(?:享|xiang)|详细教程|福利任务|主攻.{0,6}站内|全力冲刺|"
    r"免费获得|角色表白|表白活动|动态皮肤|定制票根|热度值加成|签到|解锁|前来打卡|请.*查收"
)
PROMOTION_DIRECT_CTA = re.compile(
    r"关注\s*[+＋和与]?\s*转发|转发|抽\s*\d|扫码|点击|领取|免费获得|请.{0,12}查收|"
    r"锁定.{0,18}(?:平台|频道|开播|播出)|前来打卡|加入.{0,12}(?:旅程|活动)|邀你|一起共赴|"
    r"角色表白|表白活动|热度值加成|签到|解锁|下单|购买|评论区|盖楼|冲刺"
)
DISPLAY_OPERATIONAL_CTA = re.compile(
    r"点击(?:蓝字|链接|图片|阅读原文)|关注(?:我们|公众号|账号)|"
    r"转发.{0,10}(?:抽|参与|赢)|抽\s*\d|扫码|"
    r"(?:立即|即刻|赶快|快来|速来).{0,12}(?:下单|购买|打卡|参与|领取)|"
    r"购买攻略|按需购买|买\s*\d+\s*(?:箱|件|份|套)|售完即止|限量发售|"
    r"领取(?:福利|奖品)|免费获得|评论区盖楼|主攻.{0,8}站内|全力冲刺"
)
UNRELATED_LEADING_TOPIC = re.compile(
    r"闪婚|恋情|绯闻|婚变|离婚|辟谣|造谣|狗仔|婚讯|分手"
)
AI_GENERATED_DISCLOSURE = re.compile(
    r"(?:本文|本篇|该文|文章|内容).{0,12}(?:由|使用|借助|经).{0,10}(?:AI|人工智能|ChatGPT|GPT).{0,16}(?:生成|创作|撰写|改写|润色)",
    re.I,
)
AI_ASSISTANT_IDENTITY = re.compile(
    r"作为(?:一个|一名)?(?:AI|人工智能)(?:语言模型|助手)?[，,:：\s]{0,4}(?:我|无法|不能|可以|将|会)",
    re.I,
)
AI_DRAFT_INSTRUCTION = re.compile(
    r"(?:我先|好的[，,:：]?).{0,45}(?:按原文|根据.{0,20}(?:原文|材料|内容|要求)).{0,60}(?:重写|改写|写成|生成|撰写)|"
    r"(?:不做方法说明.{0,12}只给正文|下面(?:直接)?给出正文|以下是(?:为你)?(?:生成|改写|撰写)|为你(?:生成|整理|撰写|改写))",
    re.I,
)
MEDIA_AUTHORITY_RANK = {"unclassified": 0, "major_mainstream": 1, "central_mainstream": 2}
DEFAULT_MEDIA_SUBJECTS_PATH = Path(__file__).resolve().parents[1] / "assets" / "media_subject_registry.json"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def clean(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return "" if text in {"-", "--", "None", "null"} else text


def _clean_display_excerpt(value: object, *, hashtag_mode: str) -> str:
    """Delete non-semantic platform markup without rewriting the source opinion."""
    text = html.unescape(clean(value)).replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    text = DISPLAY_MARKDOWN_LINK.sub(" ", text)
    text = DISPLAY_RAW_URL.sub(" ", text)
    # Social exports may serialize adjacent tags as ``#节目#人物名``.  The
    # middle ``#`` is the marker that starts the second tag.  Remove the whole
    # terminal tag run when the second token is label-like; keep evaluative
    # prose after a conventional paired tag to avoid over-deletion.
    if hashtag_mode in {"legacy_tail_inference", "adjacent_trailing_tags"}:
        while True:
            match = DISPLAY_ADJACENT_TRAILING_TAGS.search(text)
            if not match:
                break
            tail = match.group("tail")
            if OPINION.search(tail) or JUDGEMENT.search(tail) or DIMENSION.search(tail):
                break
            text = text[:match.start()] + " "
    text = DISPLAY_HASHTAG_PAIR.sub(" ", text)
    text = DISPLAY_HASHTAG_SINGLE.sub(" ", text)
    text = text.replace("#", " ")
    text = DISPLAY_PLATFORM_EMOTE.sub(" ", text)
    text = DISPLAY_MENTION.sub(" ", text)
    text = DISPLAY_PROMO_BOILERPLATE.sub(" ", text)
    text = DISPLAY_STRAY_MARKDOWN_TAIL.sub(" ", text)
    text = "".join(
        " " if (
            0x1F000 <= ord(char) <= 0x1FAFF
            or 0x2600 <= ord(char) <= 0x27BF
            or ord(char) in {0xFE0F, 0x200D}
            or unicodedata.category(char) in {"So", "Sk"}
        ) else char
        for char in text
    )
    text = re.sub(r"(?:网页链接|共创视频|展开全文|收起全文)", " ", text)
    text = re.sub(r"[\u200B-\u200F\u202A-\u202E\u2060\uFEFF]", "", text)
    text = re.sub(r"\s+", " ", text)
    # Platform exports often append an SEO/entity roster after the actual
    # sentence (for example "主演｜作品名｜角色名｜...").  It is metadata, not
    # opinion text.  Remove only long terminal runs with no judgement syntax.
    last_stop = max((text.rfind(mark) for mark in "。！？!?"), default=-1)
    if last_stop >= 0 and last_stop + 1 < len(text):
        suffix = text[last_stop + 1:].strip()
        tokens = [part for part in re.split(r"[\s|｜/·•]+", suffix) if part]
        if (
            len(tokens) >= 5
            and len(suffix) <= 140
            and not OPINION.search(suffix)
            and not JUDGEMENT.search(suffix)
            and not re.search(r"[，；：,.?？!！]", suffix)
        ):
            text = text[:last_stop + 1]
    text = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", text)
    text = re.sub(r"(?:[/|｜·•▪■□◆◇★☆※→←]+\s*)+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n/|｜·•▪■□◆◇★☆※→←，,；;：:")


def clean_display_excerpt_v1(value: object) -> str:
    """Reproduce the earlier display normalization for archived workbenches."""
    return _clean_display_excerpt(value, hashtag_mode="legacy_pair")


def clean_display_excerpt_v2(value: object) -> str:
    """Reproduce the temporary dangling-tail normalization for archived output."""
    return _clean_display_excerpt(value, hashtag_mode="legacy_tail_inference")


def clean_display_excerpt(value: object) -> str:
    """Apply current normalization for paired and adjacent topic tags."""
    return _clean_display_excerpt(value, hashtag_mode="adjacent_trailing_tags")


def display_excerpt_has_markup(value: object) -> bool:
    """Return True when an excerpt still contains platform markup after normalization."""
    text = clean(value)
    if any(pattern.search(text) for pattern in (
        DISPLAY_MARKDOWN_LINK, DISPLAY_RAW_URL, DISPLAY_HASHTAG_PAIR,
        DISPLAY_HASHTAG_SINGLE, DISPLAY_PLATFORM_EMOTE, DISPLAY_MENTION,
        DISPLAY_PROMO_BOILERPLATE,
    )):
        return True
    return any(
        0x1F000 <= ord(char) <= 0x1FAFF
        or 0x2600 <= ord(char) <= 0x27BF
        or ord(char) in {0xFE0F, 0x200D}
        or unicodedata.category(char) in {"So", "Sk"}
        for char in text
    )


def unbalanced_display_quotes(value: object) -> bool:
    text = clean(value)
    return text.count("“") != text.count("”") or text.count("‘") != text.count("’")


def promotion_review_markers(value: object) -> list[str]:
    """Find operational promotion language that requires an explicit opinion check."""
    return sorted(set(PROMOTION_OPERATIONAL.findall(clean(value))))


def promotion_evidence_is_independent(value: object) -> bool:
    """Reject instructions masquerading as viewpoints while keeping real commentary."""
    text = clean(value)
    if not text:
        return False
    if PROMOTION_DIRECT_CTA.search(text):
        return False
    if OPINION.search(text) or JUDGEMENT.search(text) or OBJECTIVE_ANALYSIS.search(text):
        return True
    return bool(
        re.search(
            r"(?:媒体|平台|剧方|官方|网民|网友|观众|粉丝|品牌).{0,24}"
            r"(?:发布|发起|组织|参与|准备|达到|累计|突破|形成|引发|出现|推出|开展|投放|投入|豪掷|包场)",
            text,
        )
    )


def display_operational_promotion_markers(value: object) -> list[str]:
    """Return direct actions that must not remain in a published excerpt."""
    return list(dict.fromkeys(match.group(0) for match in DISPLAY_OPERATIONAL_CTA.finditer(clean(value))))


def normalized(value: object) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", clean(value)).lower()


def ai_draft_leak(value: object) -> tuple[str, str]:
    """Return a high-confidence AI drafting leak label and verbatim evidence."""
    text = clean(value)
    for label, pattern in (
        ("explicit_ai_generation_disclosure", AI_GENERATED_DISCLOSURE),
        ("ai_assistant_identity", AI_ASSISTANT_IDENTITY),
        ("draft_instruction_or_output_meta", AI_DRAFT_INSTRUCTION),
    ):
        match = pattern.search(text)
        if match:
            return label, match.group(0)
    return "", ""


def normalize_media_account(value: object) -> str:
    return normalized(value)


def normalize_media_domain(value: object) -> str:
    host = clean(value).lower().strip(".")
    if "://" in host:
        host = urlparse(host).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host.strip(".")


def media_subject_registry(config: dict) -> dict[str, dict]:
    """Load reviewed parent entities, exact account aliases and first-party domains."""
    if "mainstream_media_aliases" in config:
        raise ValueError("mainstream_media_aliases 已停用；请使用 media_subject_extensions 的主体结构")
    payload = load_json(DEFAULT_MEDIA_SUBJECTS_PATH)
    subjects = payload.get("subjects", [])
    extensions = config.get("media_subject_extensions", [])
    if not isinstance(subjects, list) or not isinstance(extensions, list):
        raise ValueError("媒体主体库和 media_subject_extensions 均须为主体列表")
    aliases_registry: dict[str, dict] = {}
    domains_registry: dict[str, dict] = {}
    certification_registry: dict[str, dict] = {}
    subject_ids = set()
    for subject in [*subjects, *extensions]:
        if not isinstance(subject, dict):
            raise ValueError("媒体主体记录必须是对象")
        subject_id = clean(subject.get("id"))
        canonical_name = clean(subject.get("canonical_name"))
        tier = clean(subject.get("tier"))
        aliases = subject.get("aliases", [])
        domains = subject.get("domains", [])
        if not subject_id or subject_id in subject_ids or not canonical_name:
            raise ValueError("媒体主体缺少唯一 id 或 canonical_name")
        if tier not in {"central_mainstream", "major_mainstream"}:
            raise ValueError(f"媒体主体 {subject_id} 的 tier 无效")
        if not isinstance(aliases, list) or not aliases:
            raise ValueError(f"媒体主体 {subject_id} 缺少 aliases")
        if not isinstance(domains, list):
            raise ValueError(f"媒体主体 {subject_id} 的 domains 必须为列表")
        subject_ids.add(subject_id)
        certification_candidates = [(canonical_name, "certified_parent_entity")]
        for alias in aliases:
            if isinstance(alias, str):
                alias_name, account_type = clean(alias), "unspecified"
            elif isinstance(alias, dict):
                alias_name, account_type = clean(alias.get("name")), clean(alias.get("account_type")) or "unspecified"
            else:
                raise ValueError(f"媒体主体 {subject_id} 含非法 alias")
            key = normalize_media_account(alias_name)
            if not key:
                raise ValueError(f"媒体主体 {subject_id} 含空 alias")
            if key in aliases_registry:
                raise ValueError(f"媒体账号别名重复归属：{alias_name}")
            aliases_registry[key] = {
                "subject_id": subject_id,
                "canonical_name": canonical_name,
                "tier": tier,
                "account_alias": alias_name,
                "account_type": account_type,
            }
            certification_candidates.append((alias_name, account_type))
        for certification_name, account_type in certification_candidates:
            key = normalize_media_account(certification_name)
            if len(key) < 4:
                continue
            existing = certification_registry.get(key)
            if existing and existing["subject_id"] != subject_id:
                certification_registry.pop(key, None)
                continue
            certification_registry[key] = {
                "subject_id": subject_id,
                "canonical_name": canonical_name,
                "tier": tier,
                "account_alias": certification_name,
                "account_type": account_type,
            }
        for domain in domains:
            if isinstance(domain, str):
                domain_name, account_type = normalize_media_domain(domain), "news_website"
            elif isinstance(domain, dict):
                domain_name = normalize_media_domain(domain.get("host"))
                account_type = clean(domain.get("account_type")) or "news_website"
            else:
                raise ValueError(f"媒体主体 {subject_id} 含非法 domain")
            if not domain_name:
                raise ValueError(f"媒体主体 {subject_id} 含空 domain")
            if domain_name in domains_registry:
                raise ValueError(f"媒体官网域名重复归属：{domain_name}")
            domains_registry[domain_name] = {
                "subject_id": subject_id,
                "canonical_name": canonical_name,
                "tier": tier,
                "account_alias": domain_name,
                "account_type": account_type,
            }
    return {"aliases": aliases_registry, "domains": domains_registry, "certifications": certification_registry}


def classify_media_authority(
    author: object,
    registry: dict[str, dict],
    url: object = "",
    certification_type: object = "",
    certification_info: object = "",
) -> dict:
    key = normalize_media_account(author)
    aliases_registry = registry.get("aliases", {})
    domains_registry = registry.get("domains", {})
    match = aliases_registry.get(key)
    basis = "verified_subject_exact_alias" if match else ""
    if not match:
        host = normalize_media_domain(urlparse(clean(url)).hostname or "") if clean(url) else ""
        for domain in sorted(domains_registry, key=len, reverse=True):
            if host == domain or host.endswith("." + domain):
                match = domains_registry[domain]
                basis = "verified_subject_first_party_domain"
                break
    if not match and "媒体" in clean(certification_type):
        info = normalize_media_account(certification_info)
        certification_matches = [
            (token, candidate)
            for token, candidate in registry.get("certifications", {}).items()
            if token in info
        ] if info else []
        subject_ids = {candidate["subject_id"] for _, candidate in certification_matches}
        if len(subject_ids) == 1:
            token, match = max(certification_matches, key=lambda item: len(item[0]))
            match = {**match, "account_alias": clean(author) or match["account_alias"], "account_type": "certified_account"}
            basis = "verified_subject_certification_info"
    tier = match["tier"] if match else "unclassified"
    return {
        "media_subject_id": match["subject_id"] if match else "",
        "media_subject_name": match["canonical_name"] if match else "",
        "media_account_alias": match["account_alias"] if match else "",
        "media_account_type": match["account_type"] if match else "",
        "media_authority_tier": tier,
        "media_authority_rank": MEDIA_AUTHORITY_RANK[tier],
        "media_authority_basis": basis if match else "not_in_media_subject_registry",
    }


def drop_reason_fingerprint(value: object) -> str:
    """Collapse quoted per-row text so template reasons cannot evade reuse checks."""
    text = clean(value).lower()
    text = re.sub(r"[“\"‘][\s\S]*?[”\"’]", "<引文>", text)
    text = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", text)
    text = re.sub(r"\d+", "#", text)
    return re.sub(r"\s+", "", text)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 存在重复键：{key}")
        result[key] = value
    return result


def parse_json(text: str, source: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{source} JSON 解析失败：{exc}") from exc


def load_json(path: Path) -> dict:
    value = parse_json(path.read_text(encoding="utf-8-sig"), str(path))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 顶层必须是对象")
    return value


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, start=1):
            if line.strip():
                value = parse_json(line, f"{path}:{number}")
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{number} 不是对象")
                rows.append(value)
    return rows


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def write_jsonl_chunks(
    directory: Path,
    rows: list[dict],
    *,
    chunk_size: int = 60,
    max_bytes: int = 120_000,
) -> list[Path]:
    """Write bounded AI handoff files while keeping the canonical full JSONL."""
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("chunk-*.jsonl"):
        old.unlink()
    paths = []
    current: list[dict] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        path = directory / f"chunk-{len(paths) + 1:03d}.jsonl"
        write_jsonl(path, current)
        paths.append(path)
        current, current_bytes = [], 0

    for row in rows:
        row_bytes = len((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        if current and (len(current) >= chunk_size or current_bytes + row_bytes > max_bytes):
            flush()
        current.append(row)
        current_bytes += row_bytes
    flush()
    return paths


def excerpt_evidence_segments(value: object) -> dict[str, str]:
    """Split one cleaned excerpt into ordered, lossless evidence choices."""
    text = clean(value)
    if not text:
        return {}
    pieces = [match.group(0) for match in re.finditer(r".*?[，。！？；：,.!?;:]|.+$", text)]
    segments: list[str] = []
    current = ""
    for piece in pieces:
        if not piece:
            continue
        if current and len(current) + len(piece) > 58 and len(normalized(current)) >= 8:
            segments.append(current)
            current = piece
        else:
            current += piece
    if current:
        if segments and len(normalized(current)) < 6:
            segments[-1] += current
        else:
            segments.append(current)
    if not segments:
        segments = [text]
    return {str(index): segment for index, segment in enumerate(segments, start=1)}


def semantic_review_fingerprint(item: dict) -> str:
    """Bind one final review to its own excerpt and cluster contract."""
    payload = {
        key: item.get(key)
        for key in (
            "view_id", "cluster_id", "cluster_title", "cluster_stance",
            "excerpt_segments", "aspect_terms", "aspect_candidate_indexes",
            "promotion_markers", "short_excerpt",
            "target_review_required", "work_consistency_review_required",
            "work_conflict_markers", "target_anchor_terms", "target_evidence_segments",
            "target_context_flags", "display_operational_promotion_markers",
            "irrelevant_leading_segment_indexes",
        )
    }
    # Preserve earlier item fingerprints for unaffected reviews.  New claim
    # fields participate only when the extra review is actually required.
    if item.get("cluster_claim_review_required"):
        payload.update({
            "cluster_claim_review_required": True,
            "cluster_claim_review_reasons": item.get("cluster_claim_review_reasons", []),
            "cluster_claim_candidate_indexes": item.get("cluster_claim_candidate_indexes", []),
        })
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def deterministic_semantic_keep(input_item: dict, excerpt: str) -> dict | None:
    """Auto-approve only mechanically unambiguous, complete excerpts.

    This is deliberately a narrow fast path.  Mixed, short, promotional,
    cross-work, target-ambiguous, or contrastive passages still go to AI.
    """
    if (
        input_item.get("target_review_required")
        or input_item.get("work_consistency_review_required")
        or input_item.get("cluster_claim_review_required")
        or input_item.get("short_excerpt")
        or input_item.get("promotion_markers")
        or input_item.get("display_operational_promotion_markers")
        or input_item.get("irrelevant_leading_segment_indexes")
        or len(excerpt) < EXCERPT_PREFERRED_MIN
        or len(excerpt) > EXCERPT_MAX
        or not excerpt.endswith(AUTO_SEMANTIC_COMPLETE_END)
        or LEADING_FRAGMENT.search(excerpt)
        or AUTO_SEMANTIC_CONTRAST.search(excerpt)
    ):
        return None
    expected_stance = clean(input_item.get("cluster_stance"))
    aspect_terms = [clean(term) for term in input_item.get("aspect_terms", []) if clean(term)]
    if expected_stance not in ALLOWED_CLUSTER_STANCES or not aspect_terms:
        return None
    aspect_candidates = []
    stance_candidates = []
    joint_candidates = []
    concrete_aspect_hits = []
    for index, segment in (input_item.get("excerpt_segments") or {}).items():
        segment_text = clean(segment)
        segment_aspects = [term for term in aspect_terms if term in segment_text]
        if segment_aspects:
            aspect_candidates.append(str(index))
            concrete_aspect_hits.extend(
                term for term in segment_aspects if term not in AMBIGUOUS_AUTO_ASPECT_TERMS
            )
        stance_supported = (
            not stance_evidence_conflicts(expected_stance, segment_text)
            and stance_evidence_supports(expected_stance, segment_text)
        )
        if stance_supported:
            stance_candidates.append(str(index))
        if segment_aspects and stance_supported:
            joint_candidates.append(str(index))
    if not joint_candidates or not concrete_aspect_hits:
        return None
    return {
        "view_id": clean(input_item.get("view_id")),
        "review_fingerprint": clean(input_item.get("review_fingerprint")),
        "decision": "keep",
        "aspect_evidence_candidate_index": int(joint_candidates[0]),
        "stance": expected_stance,
        "stance_evidence_candidate_index": int(joint_candidates[0]),
        "self_contained": True,
        "review_basis": "script_high_confidence_semantic_gate",
    }


def short_excerpt_support_is_specific(excerpt: object, evidence: object) -> bool:
    """Reject generic short praise while preserving concise, concrete evidence."""
    excerpt_text = clean(excerpt)
    evidence_text = clean(evidence)
    if not evidence_text or evidence_text not in excerpt_text or len(normalized(evidence_text)) < 8:
        return False
    if FACT_WARNING.search(evidence_text):
        return True
    if re.search(r"[“\"]([^”\"]{4,})[”\"]", evidence_text):
        return True
    dimensions = set(DIMENSION.findall(evidence_text))
    has_reasoning = bool(re.search(r"因为|在于|通过|例如|比如|其中|从.{0,12}(?:看出|体现)|使得|导致", evidence_text))
    has_concrete_detail = bool(
        DETAIL_CUE.search(evidence_text)
        and re.search(r"推进|拉开|切换|变化|停顿|转身|走路|说话|抬眼|落泪|挥剑|对打|实拍|搭建|复原|还原|层层|逐渐|从.{0,12}到", evidence_text)
    )
    return bool(
        has_concrete_detail
        or (len(dimensions) >= 2 and has_reasoning and (OPINION.search(evidence_text) or JUDGEMENT.search(evidence_text)))
    )


def stance_evidence_conflicts(expected: object, evidence: object) -> bool:
    """Use deterministic polarity only as an opposite-stance safety veto."""
    expected_value = clean(expected)
    inferred = stance(clean(evidence))
    if expected_value == "positive":
        return inferred == "负向"
    if expected_value == "negative":
        return inferred == "正向"
    if expected_value == "objective":
        return inferred != "混合或中性"
    return True


def stance_evidence_supports(expected: object, evidence: object) -> bool:
    """Require the cited sentence to actually express the cluster polarity."""
    expected_value = clean(expected)
    inferred = stance(clean(evidence))
    if expected_value == "positive":
        return inferred == "正向"
    if expected_value == "negative":
        return inferred == "负向"
    if expected_value == "objective":
        return inferred == "混合或中性"
    return False


def indexed_excerpt_evidence(review: dict, input_item: dict, field: str) -> str:
    """Resolve an AI-selected excerpt segment, with exact-text fallback."""
    direct = clean(review.get(field))
    if direct:
        return direct
    index = review.get(f"{field}_candidate_index")
    if isinstance(index, bool):
        return ""
    if isinstance(index, int) or (isinstance(index, str) and index.isdigit()):
        return clean((input_item.get("excerpt_segments") or {}).get(str(index)))
    return ""


def indexed_target_evidence(review: dict, input_item: dict) -> str:
    """Resolve a target-attribution snippet from the script-built evidence menu."""
    direct = clean(review.get("target_evidence"))
    if direct:
        return direct
    index = review.get("target_evidence_candidate_index")
    if isinstance(index, bool):
        return ""
    if isinstance(index, int) or (isinstance(index, str) and index.isdigit()):
        return clean((input_item.get("target_evidence_segments") or {}).get(str(index)))
    return ""


def safe_url(value: object) -> str:
    url = clean(value)
    if not re.match(r"^https?://", url, re.I):
        return ""
    if re.search(r"(^|\.)woa\.com(?=/|:|$)", url, re.I):
        return ""
    return url


def data_value(data: dict, *names: str) -> str:
    for name in names:
        value = clean(data.get(name))
        if value:
            return value
    return ""


def map_record(record: dict) -> dict:
    data = record.get("data") if isinstance(record.get("data"), dict) else record
    post_type = data_value(data, "帖子类型", "post_type") or clean(record.get("post_type")) or "原帖"
    body = data_value(data, "正文内容", "正文", "内容", "文章内容", "body") or clean(record.get("body"))
    asr = data_value(data, "视频ASR", "音转文", "asr") or clean(record.get("asr"))
    title = data_value(data, "标题", "title") or clean(record.get("title"))
    parent_body = data_value(data, "原帖正文", "父帖正文", "parent_body") or clean(record.get("parent_body"))
    source_id = clean(record.get("id"))
    if not source_id:
        seed = "|".join(clean(record.get(key)) for key in ("source_path", "sheet", "row"))
        source_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return {
        "id": source_id,
        "batch": clean(record.get("batch")),
        "channel": clean(record.get("channel")) or data_value(data, "站点名称", "channel"),
        "post_type": post_type,
        "published": data_value(data, "发表时间", "published", "published_at"),
        "title": title,
        "body": body,
        "asr": asr,
        "parent_body": parent_body,
        "author": data_value(data, "用户名", "昵称", "账号昵称", "帐号昵称", "公众号名称", "作者", "author") or clean(record.get("author")) or "未署名",
        "certification_type": data_value(data, "认证类型（名人、媒体、企业等）", "认证类型", "certification_type"),
        "certification_info": data_value(data, "认证信息（认证主体）", "认证信息", "认证主体", "certification_info"),
        "url": safe_url(data_value(data, "发文链接", "链接", "文章链接", "原贴url", "url") or record.get("url")),
        "source_file": clean(record.get("source_file")),
        "source_path": clean(record.get("source_path")),
        "sheet": clean(record.get("sheet")),
        "row": record.get("row", ""),
    }


def parse_datetime(value: object) -> datetime | None:
    text = clean(value)
    if not text:
        return None
    for candidate in (text, text.replace("/", "-")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    return None


def parse_period_boundary(value: object, *, end: bool = False) -> datetime | None:
    """Parse an explicit period boundary, treating a date-only end as inclusive."""
    text = clean(value)
    parsed = parse_datetime(text)
    if parsed and end and re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", text):
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def filename_period(name: str) -> tuple[datetime | None, datetime | None]:
    match = re.search(r"(\d{4}\.\d{2}\.\d{2}) (\d{2})_(\d{2})至(\d{4}\.\d{2}\.\d{2}) (\d{2})_(\d{2})", name)
    if match:
        start = datetime.strptime(" ".join(match.group(1, 2, 3)), "%Y.%m.%d %H %M")
        end = datetime.strptime(" ".join(match.group(4, 5, 6)), "%Y.%m.%d %H %M").replace(second=59)
        return start, end
    date_only = re.search(r"(\d{4}[.-]\d{2}[.-]\d{2})至(\d{4}[.-]\d{2}[.-]\d{2})", name)
    if date_only:
        start = datetime.strptime(date_only.group(1).replace("-", "."), "%Y.%m.%d")
        end = datetime.strptime(date_only.group(2).replace("-", "."), "%Y.%m.%d").replace(
            hour=23, minute=59, second=59
        )
        return start, end
    return None, None


def period_state(row: dict, config: dict) -> tuple[str, str]:
    batch_window = config.get("period_windows", {}).get(row["batch"], {})
    if batch_window:
        start = parse_period_boundary(batch_window.get("start"))
        end = parse_period_boundary(batch_window.get("end"), end=True)
    else:
        start, end = filename_period(row.get("source_file", ""))
    moment = parse_datetime(row.get("published"))
    if start and end and moment:
        return ("in_period", f"{start.isoformat(sep=' ')}..{end.isoformat(sep=' ')}") if start <= moment <= end else ("out_of_period", f"{start.isoformat(sep=' ')}..{end.isoformat(sep=' ')}")
    if config.get("assume_all_in_period") is True:
        return "in_period", "assume_all_in_period"
    return "unconfigured", "缺少可解析的文件名周期或 period_windows"


def source_text(row: dict) -> str:
    own = clean(row.get("body")) or clean(row.get("asr")) or clean(row.get("title"))
    if row.get("post_type") in {"评论", "转帖"}:
        return own
    title = clean(row.get("title"))
    return clean((title + " " + own) if title and normalized(title) not in normalized(own) else own)


def suspicious_unconfigured_entities(text: str, auxiliary_terms: list[str]) -> list[str]:
    """Find role-linked names that are absent from the configured work entities.

    This is deliberately a review router, not an exclusion rule.  It catches the
    common wrong-work pattern where one target title is pasted onto a synopsis
    about several characters from another work.
    """
    configured = {normalized(term) for term in auxiliary_terms if len(normalized(term)) >= 2}
    generic = {
        "角色", "人物", "观众", "演员", "部分", "传统", "这种", "男女", "男女主角",
        "男主", "女主", "剧情", "感情", "关系", "其他配角", "重要男性角色",
    }
    found = set()
    for pattern in ENTITY_MENTION_PATTERNS:
        for match in pattern.findall(text):
            values = match if isinstance(match, tuple) else (match,)
            for value in values:
                candidate = re.sub(r"^(?:尽管|虽然|但是|只是|但)", "", clean(value))
                key = normalized(candidate)
                if candidate in generic or len(key) < 2:
                    continue
                if any(key == known or key in known or known in key for known in configured):
                    continue
                found.add(candidate)
    return sorted(found)


def target_layers(target: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return explicit work anchors, ambiguous aliases, people/role helpers and comparison works."""
    strong = [clean(value) for value in target.get("strong_terms", []) if clean(value)]
    weak = [clean(value) for value in target.get("weak_terms", []) if clean(value)]
    auxiliary = [clean(value) for value in target.get("auxiliary_terms", []) if clean(value)]
    comparisons = [clean(value) for value in target.get("comparison_terms", []) if clean(value)]
    return strong, weak, auxiliary, comparisons


def other_work_titles(text: object, target: dict) -> list[str]:
    """Find bracketed work titles that are not aliases of the current target."""
    strong, weak, _, comparisons = target_layers(target)
    target_keys = [normalized(value) for value in strong + weak if normalized(value)]
    output = []
    source = clean(text)
    for match in re.finditer(r"《([^》]{1,40})》", source):
        title = match.group(1)
        key = normalized(title)
        if not key or any(key == target_key or key in target_key or target_key in key for target_key in target_keys):
            continue
        context = source[max(0, match.start() - 28):min(len(source), match.end() + 28)]
        is_screen_or_book_work = bool(re.search(
            r"电视剧|网剧|短剧|电影|影片|综艺|节目|剧集|新剧|该剧|本剧|主演|导演|"
            r"定档|开播|播出|上线|上映|接档|改编|原著|小说|第[一二三四五六七八九十\d]+季",
            context,
        ))
        if (is_screen_or_book_work or any(key == normalized(value) for value in comparisons)) and title not in output:
            output.append(title)
    for value in comparisons:
        if clean(value) in source and clean(value) not in output:
            output.append(clean(value))
    return output


def role_related_other_work_titles(text: object, target: dict) -> list[str]:
    """Route explicit other-work cast or character clauses to attribution review."""
    strong, weak, _, _ = target_layers(target)
    target_keys = [normalized(value) for value in strong + weak if normalized(value)]
    source = clean(text)
    output = []
    for match in re.finditer(r"《([^》]{1,40})》", source):
        title = match.group(1)
        key = normalized(title)
        if not key or any(key == target_key or key in target_key or target_key in key for target_key in target_keys):
            continue
        clause = source[match.end():min(len(source), match.end() + 90)]
        if re.search(r"(?:汇聚|集结|领衔|主演|饰演|扮演).{0,60}(?:演员|角色|男主|女主|人物|实力派)?", clause):
            output.append(title)
    return list(dict.fromkeys(output))


def term_hits(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if len(term) >= 2 and term in text]


def derive_period_context_terms(rows: list[dict], target: dict) -> list[dict]:
    """Learn period-specific topic hashtags only from sources that explicitly name the work.

    These anchors can route target-omitting event comments to semantic review, but can never
    admit a source automatically.
    """
    strong, _, _, _ = target_layers(target)
    supporting_sources: dict[str, set[str]] = defaultdict(set)
    all_period_sources: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        text = source_text(row)
        has_strong = bool(term_hits(text, strong))
        for raw in HASHTAG.findall(text):
            term = clean(raw).strip("# ")
            compact = normalized(term)
            if not (4 <= len(compact) <= 36) or GENERIC_CONTEXT_TAG.fullmatch(term):
                continue
            if any(normalized(anchor) in compact or compact in normalized(anchor) for anchor in strong):
                continue
            all_period_sources[term].add(row["id"])
            if has_strong:
                supporting_sources[term].add(row["id"])
    ranked = sorted(
        (
            {
                "term": term,
                "explicit_target_supporting_sources": len(supporting_sources.get(term, set())),
                "period_supporting_sources": len(ids),
                "basis": "explicit_target_cooccurrence" if len(supporting_sources.get(term, set())) >= 2 else "recurrent_period_topic",
            }
            for term, ids in all_period_sources.items()
            if len(supporting_sources.get(term, set())) >= 2 or len(ids) >= 3
        ),
        key=lambda item: (
            item["basis"] == "explicit_target_cooccurrence",
            item["explicit_target_supporting_sources"],
            item["period_supporting_sources"],
            len(normalized(item["term"])),
            item["term"],
        ),
        reverse=True,
    )
    return ranked[:40]


def local_target_candidates(text: str, target: dict) -> list[dict]:
    """Find exact source spans where a work anchor and an opinion can be read together."""
    strong, weak, auxiliary, comparisons = target_layers(target)
    derived = [clean(value) for value in target.get("_derived_context_terms", []) if clean(value)]
    spans = sentence_spans(text)
    candidates = []
    for start_index in range(len(spans)):
        for end_index in range(start_index, min(len(spans), start_index + 3)):
            start, end = spans[start_index]["start"], spans[end_index]["end"]
            fragment = text[start:end]
            strong_hits = term_hits(fragment, strong)
            weak_hits = term_hits(fragment, weak)
            auxiliary_hits = term_hits(fragment, auxiliary)
            comparison_hits = term_hits(fragment, comparisons)
            derived_hits = term_hits(fragment, derived)
            anchor_offsets = sorted({
                match.start()
                for term in strong + weak + derived
                for match in re.finditer(re.escape(term), fragment)
            })
            if anchor_offsets:
                neighborhoods = [fragment[max(0, offset - 220):min(len(fragment), offset + 220)] for offset in anchor_offsets]
                local_evidence = " ".join(neighborhoods)
            else:
                local_evidence = fragment
            opinion = len(OPINION.findall(local_evidence))
            judgement = len(JUDGEMENT.findall(local_evidence))
            risk = len(RISK.findall(local_evidence))
            analysis = len(OBJECTIVE_ANALYSIS.findall(local_evidence))
            dimensions = len(set(DIMENSION.findall(local_evidence)))
            detail_hits = len(set(DETAIL_CUE.findall(local_evidence)))
            viewpoint = bool(opinion or judgement or risk or analysis >= 2)
            supported = bool(dimensions or detail_hits or risk or analysis >= 2)
            anchor_class = "strong" if strong_hits else "weak" if weak_hits else "derived" if derived_hits else ""
            if not anchor_class:
                continue
            if not (viewpoint and supported):
                continue
            score = (
                len(strong_hits) * 20 + len(weak_hits) * 8 + len(derived_hits) * 5 + len(auxiliary_hits) * 2
                + opinion * 3 + judgement * 2 + dimensions * 2 + detail_hits * 2 + risk * 3 + analysis
                - len(comparison_hits) * 8
            )
            candidates.append({
                "start": start,
                "end": end,
                "text": fragment,
                "anchor_class": anchor_class,
                "strong_hits": strong_hits,
                "weak_hits": weak_hits,
                "auxiliary_hits": auxiliary_hits,
                "comparison_hits": comparison_hits,
                "derived_hits": derived_hits,
                "opinion_hits": opinion,
                "judgement_hits": judgement,
                "analysis_hits": analysis,
                "dimension_hits": dimensions,
                "detail_hits": detail_hits,
                "local_evidence": local_evidence,
                "score": score,
            })
    candidates.sort(key=lambda item: (item["anchor_class"] == "strong", item["anchor_class"] == "weak", not item["comparison_hits"], item["score"], -item["start"]), reverse=True)
    deduplicated = []
    for candidate in candidates:
        if any(candidate["start"] >= old["start"] and candidate["end"] <= old["end"] for old in deduplicated):
            continue
        deduplicated.append(candidate)
    return deduplicated[:3]


def stance(text: str) -> str:
    positive_pattern = re.compile(
        r"好看|出彩|惊喜|真实|细腻|精彩|过瘾|带感|质感|高级|贴合|适配|还原|亮点|加分|成功|喜欢|认可|合理|自然|生动|可信|"
        r"好笑|笑疯|笑死|笑出声|笑一年|哈哈|太有梗|有梗|可爱|舒服|绝了|封神|拉满|冲击力|张力|破功|亮眼|投入|用心|成长|鼓励|好机会|"
        r"鲜活|新鲜|创新|突破|值得|厉害|太会|稳了|共鸣|欢呼|好感|幽默|有趣|反差|进步|魅力|折服|热忱|有爱|心满意足|沉浸|走心|戳心|不可替代|灵魂|"
        r"期待|扎实|豪华|强大|精湛|匠心|诚意|行云流水|吊足胃口|硬核|利落|震撼|热血|燃|太顶|太牛"
    )
    negative_pattern = re.compile(
        r"难看|失望|拉胯|油腻|尴尬|出戏|违和|不合理|拖沓|注水|悬浮|差评|毁|魔改|弃剧|劝退|翻车|用力过猛|"
        r"过度|审美疲劳|消耗|消费|不适|没分寸|套路|生硬|低质|担忧|质疑|吐槽|看衰|不满|担心|短板|糟糕|塑料感|老套|僵硬|呆板"
    )
    if re.search(r"哈哈|笑一年|笑疯|笑死|笑出声|有梗|破功|综艺效果|可爱", text) and not re.search(
        r"批评|质疑|问题|不适|没分寸|过度|审美疲劳|尴尬|难看|拉胯|油腻|出戏|违和|拖沓|注水|悬浮|差评|劝退|翻车|用力过猛",
        text,
    ):
        return "正向"
    positive = len(positive_pattern.findall(text))
    negative = len(negative_pattern.findall(text))
    contrast_tail = re.split(r"但是|不过|然而|但也|但|可惜|只是", text)[-1]
    tail_positive = len(positive_pattern.findall(contrast_tail))
    tail_negative = len(negative_pattern.findall(contrast_tail))
    if tail_positive > tail_negative and tail_positive:
        return "正向"
    if tail_negative > tail_positive and tail_negative:
        return "负向"
    return "正向" if positive > negative else "负向" if negative > positive else "混合或中性"


def length_score(length: int) -> float:
    if length < 20:
        return -5.0
    if length < 50:
        return -3.0
    if length < 100:
        return -1.5
    if length < 200:
        return 0.0
    if length < 300:
        return 1.0
    if length < 600:
        return 2.0
    if length < 1000:
        return 3.0
    return 4.0


def evaluate(row: dict, target: dict, config: dict) -> dict:
    text = source_text(row)
    ai_leak_label, ai_leak_evidence = ai_draft_leak(text)
    own_and_context = " ".join(filter(None, [row.get("title", ""), row.get("body", ""), row.get("asr", ""), row.get("parent_body", "") if row.get("post_type") in {"评论", "转帖"} else ""]))
    strong_terms, weak_terms, auxiliary_terms, comparison_terms = target_layers(target)
    derived_terms = [clean(value) for value in target.get("_derived_context_terms", []) if clean(value)]
    strong_hits = term_hits(text, strong_terms)
    weak_hits = term_hits(text, weak_terms)
    auxiliary_hits = term_hits(text, auxiliary_terms)
    derived_hits = term_hits(text, derived_terms)
    context_strong_hits = term_hits(own_and_context, strong_terms)
    context_weak_hits = term_hits(own_and_context, weak_terms)
    title_strong_hits = term_hits(clean(row.get("title")), strong_terms)
    raw_strong_spans = sorted({(match.start(), match.end()) for term in strong_terms for match in re.finditer(re.escape(term), text)})
    merged_strong_spans = []
    for start, end in raw_strong_spans:
        if merged_strong_spans and start < merged_strong_spans[-1][1]:
            merged_strong_spans[-1][1] = max(merged_strong_spans[-1][1], end)
        else:
            merged_strong_spans.append([start, end])
    strong_positions = [start for start, _ in merged_strong_spans]
    first_strong_position = strong_positions[0] if strong_positions else -1
    target_anchor_density = min(1.0, len(strong_positions) * 120 / max(1, len(text)))
    candidates = local_target_candidates(text, target)
    strong_candidates = [item for item in candidates if item["anchor_class"] == "strong"]
    weak_candidates = [item for item in candidates if item["anchor_class"] == "weak"]
    derived_candidates = [item for item in candidates if item["anchor_class"] == "derived"]
    clean_strong_candidates = [item for item in strong_candidates if not item["comparison_hits"]]
    best_local = (clean_strong_candidates or strong_candidates or weak_candidates or derived_candidates or candidates or [None])[0]
    local_strong_viewpoint = bool(strong_candidates)
    local_weak_viewpoint = bool(weak_candidates)
    local_derived_viewpoint = bool(derived_candidates)
    comparison_near_target = bool(best_local and best_local["comparison_hits"])
    target_mentions = sum(text.count(term) for term in strong_terms + weak_terms if len(term) >= 2)
    unknown_entities = suspicious_unconfigured_entities(text, auxiliary_terms)
    entity_consistency_risk = bool(
        target_mentions <= 2
        and len(normalized(text)) >= 100
        and not auxiliary_hits
        and len(unknown_entities) >= 2
    )
    abusive_review_risk = bool(ABUSIVE_REVIEW_RISK.search(text))
    target_is_late_minor_section = bool(
        strong_positions
        and not title_strong_hits
        and first_strong_position > 320
        and target_anchor_density < 0.35
    )
    target_is_sparse_secondary_section = bool(
        strong_positions
        and not title_strong_hits
        and first_strong_position > 80
        and target_mentions <= 2
        and target_anchor_density < 0.08
    )
    context_only_target = bool((context_strong_hits or context_weak_hits) and not (strong_hits or weak_hits))
    semantic = normalized(text)
    length = len(semantic)
    opinion = len(OPINION.findall(text))
    judgement = len(JUDGEMENT.findall(text))
    dimensions = len(set(DIMENSION.findall(text)))
    details = target_mentions + len(set(DETAIL_CUE.findall(text)))
    specific_details = max(0, details - target_mentions)
    quoted_evidence = bool(re.search(r"(?:称|说|表示|吐槽|评价|直言|认为|辣评)[：:]?\s*[“\"]", text))
    watched = len(WATCHED.findall(text))
    preview = len(PREVIEW.findall(text))
    cta = len(CTA.findall(text))
    fan = len(FAN.findall(text))
    risk = len(RISK.findall(text))
    score = CHANNEL_BASE.get(row.get("channel", ""), 0.0) + POST_BASE.get(row.get("post_type", ""), 0.0) + length_score(length)
    score += min(5.0, opinion * 0.55) + min(4.0, dimensions * 0.7) + min(4.0, details * 0.45)
    score += min(2.0, watched * 0.5) + min(2.0, risk * 0.55)
    score -= min(5.0, cta * 1.35) + min(5.0, fan * 1.5)
    if row.get("post_type") in {"评论", "转帖"} and length < 100:
        score -= 2.0
    floor_key = "social" if row.get("channel") in SOCIAL_CHANNELS else "long"
    floor = float(config.get("quality_floor", {}).get(floor_key, 9.5 if floor_key == "social" else 12.5))

    decision = "retain_core"
    reason = "原文含有明确评价对象、判断和可复核依据"
    lead = clean(row.get("title") + " " + text[:180])
    if ai_leak_label:
        decision, reason = "exclude", "正文明确包含AI生成声明、写稿指令或模型元话语"
    elif not text:
        decision, reason = "exclude", "无正文、标题或音转文"
    elif row.get("channel") == "抖音" and not clean(row.get("asr")):
        decision, reason = "exclude", "短视频没有可用音转文"
    elif PIRACY.search(text):
        decision, reason = "exclude", "网盘、盗版或资源下载信息"
    elif not (context_strong_hits or context_weak_hits or derived_hits):
        decision, reason = "exclude", "标题、正文、音转文及必要上下文未确认目标作品"
    elif derived_hits and not (strong_hits or weak_hits):
        decision, reason = "exclude", "仅命中本期自动发现的关联话题，需确认其确属目标作品内容"
    elif context_only_target:
        decision, reason = "exclude", "目标作品只出现在父帖上下文，需确认当前评论自身观点归属"
    elif not strong_hits and weak_hits:
        decision, reason = "exclude", "仅命中可能泛指其他内容的弱目标名，需结合全文确认作品归属"
    elif not local_strong_viewpoint:
        decision, reason = "exclude", "目标名称与评价判断未在同一局部语境出现"
    elif target_is_sparse_secondary_section:
        decision, reason = "exclude", "目标作品在无目标标题的长文中占比较低，需确认摘出片段是否确实评价该作品"
    elif target_is_late_minor_section:
        decision, reason = "exclude", "目标作品只在长文后部占较小篇幅，需确认是否为独立有效观点"
    elif comparison_near_target:
        decision, reason = "exclude", "同一评价片段涉及多部作品，需确认判断归属"
    elif GOSSIP.search(lead):
        decision, reason = "exclude", "演员私生活或八卦内容占主体"
    elif ROUNDUP.search(lead):
        decision, reason = "exclude", "多剧片单、排播合集或顺带提及"
    elif EPISODE_RECAP.search(lead):
        decision, reason = "exclude", "分集剧情或剧情解说以复述为主"
    elif HARD_FAN.search(lead):
        decision, reason = "exclude", "饭圈图片、修图、穿搭或同款物料"
    elif GENERIC_ONLY.fullmatch(clean(text)):
        decision, reason = "exclude", "只有好坏、支持或期待等空泛态度"
    elif row.get("post_type") in {"评论", "转帖"} and length < 18:
        decision, reason = "exclude", "评论自身信息过短，不能借父帖补足观点"
    elif QUESTION.search(text) and opinion < 2 and dimensions < 2:
        decision, reason = "exclude", "主要询问他人是否值得观看，自身判断不足"
    elif fan and not watched and dimensions < 2 and details < 2:
        decision, reason = "exclude", "粉丝互动或明星物料为主"
    elif cta and not watched and opinion < 2 and dimensions < 2:
        decision, reason = "exclude", "宣发号召显著，缺少独立评价"
    elif opinion == 0 and risk == 0:
        decision, reason = "exclude", "只有剧情、阵容或播出信息，缺少评价判断"
    elif length < 28 and dimensions == 0 and details == 0 and risk == 0:
        decision, reason = "exclude", "观点过短且没有具体分析维度"
    elif score < floor:
        low_score_evidence = (
            dimensions >= 1
            or specific_details >= 1
            or (target_mentions >= 1 and (opinion >= 2 or quoted_evidence))
        )
        if (opinion >= 1 or risk >= 1) and low_score_evidence:
            decision, reason = "retain_consensus", f"已通过语义门槛；内容优先级分{score:.3f}低于核心层门槛{floor:g}，进入补充候选层"
        else:
            decision, reason = "exclude", "虽有态度词，但缺少能支撑目标观点的具体依据"

    substantial = length >= 600 and dimensions >= 4 and details >= 4 and target_mentions >= 5 and local_strong_viewpoint
    if decision == "exclude" and substantial and reason in {"宣发号召显著，缺少独立评价", "多剧片单、排播合集或顺带提及"}:
        decision, reason = "retain_core", "长文主体包含多项目标剧具体判断，模板或顺带内容不占主体"

    # 来源级队列只处理“脚本准备排除、但正文仍可能含有效评价”的语义边界。
    # 已保留来源的混合立场、低分和风险表达交给后续片段级归簇审查，避免在入口重复排队。
    clear_exclusion_reasons = {
        "正文明确包含AI生成声明、写稿指令或模型元话语",
        "无正文、标题或音转文",
        "短视频没有可用音转文",
        "网盘、盗版或资源下载信息",
        "标题、正文、音转文及必要上下文未确认目标作品",
        "只有好坏、支持或期待等空泛态度",
        "评论自身信息过短，不能借父帖补足观点",
        "虽有态度词，但缺少能支撑目标观点的具体依据",
    }
    possible_viewpoint = (
        opinion >= 1
        or risk >= 1
        or judgement >= 1
    )
    head_target_review = bool(
        strong_positions
        and first_strong_position <= 80
        and possible_viewpoint
        and (dimensions or specific_details or risk)
    )
    reviewable_target_context = (
        local_strong_viewpoint
        or local_weak_viewpoint
        or local_derived_viewpoint
        or head_target_review
        or (context_only_target and possible_viewpoint and bool(dimensions or specific_details or risk))
    )
    needs_review = (
        decision == "exclude"
        and reason not in clear_exclusion_reasons
        and length >= 12
        and reviewable_target_context
        and possible_viewpoint
    )
    # These two signals never delete automatically.  They force a full-text AI
    # check so a pasted work title, wrong character set, or abusive aside cannot
    # silently pass the deterministic gate.
    if entity_consistency_risk or abusive_review_risk:
        needs_review = True
    if clean(target.get("content_mode")) == "episodic_variety" and text and reason not in clear_exclusion_reasons:
        # 周更综艺必须先判断期次。脚本只控制监测日期，不能凭人物名或话题词可靠区分最新一期与往期。
        needs_review = True
    review_candidates = [
        {key: item[key] for key in ("start", "end", "text", "anchor_class", "strong_hits", "weak_hits", "comparison_hits")}
        for item in candidates
    ]
    if needs_review and not review_candidates and head_target_review:
        # 为“篇首已点明作品、后文判断距离较远”的边界项提供可定位的连续原文，
        # 方便 AI/人工确认归属；该候选本身不自动改变排除决定。
        end = min(len(text), 700)
        review_candidates.append({
            "start": 0,
            "end": end,
            "text": text[:end],
            "anchor_class": "strong_head_review",
            "strong_hits": term_hits(text[:end], strong_terms),
            "weak_hits": term_hits(text[:end], weak_terms),
            "comparison_hits": term_hits(text[:end], comparison_terms),
        })
    return {
        "auto_decision": decision,
        "auto_reason": reason,
        "quality": round(score, 3),
        "quality_floor": floor,
        "length": length,
        "opinion_hits": opinion,
        "judgement_hits": judgement,
        "dimension_hits": dimensions,
        "detail_hits": details,
        "specific_detail_hits": specific_details,
        "target_mentions": target_mentions,
        "strong_target_hits": strong_hits,
        "weak_target_hits": weak_hits,
        "auxiliary_target_hits": auxiliary_hits,
        "derived_context_hits": derived_hits,
        "local_target_viewpoint": local_strong_viewpoint,
        "local_weak_target_viewpoint": local_weak_viewpoint,
        "local_derived_target_viewpoint": local_derived_viewpoint,
        "local_target_anchor_class": clean(best_local.get("anchor_class")) if best_local else "none",
        "first_strong_target_position": first_strong_position,
        "target_anchor_density": round(target_anchor_density, 4),
        "target_is_late_minor_section": target_is_late_minor_section,
        "target_is_sparse_secondary_section": target_is_sparse_secondary_section,
        "comparison_near_target": comparison_near_target,
        "entity_consistency_risk": entity_consistency_risk,
        "unconfigured_role_entities": unknown_entities,
        "abusive_content_review_risk": abusive_review_risk,
        "review_evidence_candidates": review_candidates,
        "quoted_evidence": quoted_evidence,
        "stance": stance(text),
        "fact_warning": "含事实或数字主张，成文前核验" if FACT_WARNING.search(text) else "",
        "needs_ai_review": needs_review,
        "hard_exclusion": bool(ai_leak_label),
        "hard_exclusion_type": ai_leak_label,
        "hard_exclusion_evidence": ai_leak_evidence,
    }


def validate_config(config: dict, batches: set[str]) -> None:
    retired_quantity_fields = {
        "cluster_min_independent_sources",
        "cluster_max_sources_before_review",
        "min_samples_per_cluster",
        "max_samples_per_cluster",
        "sample_budget",
        "reading_budget",
        "cluster_target_count",
    }
    present_retired = sorted(retired_quantity_fields & set(config))
    if present_retired:
        raise ValueError(
            "period_config.json 含已停用的数量或阅读预算字段："
            f"{present_retired}。请删除；现行流程保留全部合格独立表达，9—16仅用于观点簇集合质量复查"
        )
    order = config.get("batch_order")
    targets = config.get("targets")
    if not isinstance(order, list) or not order:
        raise ValueError("period_config.json 缺少 batch_order")
    if not isinstance(targets, dict):
        raise ValueError("period_config.json 缺少 targets")
    missing = batches - set(targets)
    if missing:
        raise ValueError(f"targets 缺少批次：{sorted(missing)}")
    for batch in batches:
        target = targets[batch]
        if target.get("terms") or target.get("regex"):
            raise ValueError(
                f"{batch} 仍使用旧版 terms/regex；请按语义拆为 strong_terms、weak_terms、auxiliary_terms"
            )
        strong, weak, auxiliary, comparisons = target_layers(target)
        if not strong:
            raise ValueError(f"{batch} 缺少 strong_terms；至少配置作品全名或官方话题")
        layers = strong + weak + auxiliary
        if len(layers) != len(set(layers)):
            raise ValueError(f"{batch} 的强、弱、辅助目标词存在重复")
        if set(layers) & set(comparisons):
            raise ValueError(f"{batch} 的目标词与对比作品词重复")
        content_mode = clean(target.get("content_mode") or "serial_drama")
        if content_mode not in {"serial_drama", "episodic_variety"}:
            raise ValueError(f"{batch} 的 content_mode 必须为 serial_drama 或 episodic_variety")


def command_prepare(args: argparse.Namespace) -> None:
    records_path, config_path, run_dir = args.records.resolve(), args.config.resolve(), args.run.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_records = load_jsonl(records_path)
    all_sources = [map_record(record) for record in raw_records]
    config = load_json(config_path)
    period_results = [(row, *period_state(row, config)) for row in all_sources]
    unconfigured = [row for row, state, _ in period_results if state == "unconfigured"]
    if unconfigured:
        examples = [f"{row['batch']} / {row['source_file']} / {row['published']}" for row in unconfigured[:5]]
        raise ValueError("存在无法判断期内口径的来源；请配置 period_windows 或明确 assume_all_in_period=true：" + "；".join(examples))
    sources = [row for row, state, _ in period_results if state == "in_period"]
    out_of_period = [{**row, "period_state": state, "period_basis": basis} for row, state, basis in period_results if state != "in_period"]
    ids = [row["id"] for row in sources]
    if len(ids) != len(set(ids)):
        raise RuntimeError("来源ID不唯一")
    validate_config(config, {row["batch"] for row in sources})
    media_registry = media_subject_registry(config)
    for row in sources:
        row.update(classify_media_authority(
            row.get("author"),
            media_registry,
            row.get("url"),
            row.get("certification_type"),
            row.get("certification_info"),
        ))
    runtime_targets = {}
    derived_context_audit = {}
    for batch in config["batch_order"]:
        batch_rows = [row for row in sources if row["batch"] == batch]
        target = dict(config["targets"][batch])
        discovered = derive_period_context_terms(batch_rows, target)
        target["_derived_context_terms"] = [item["term"] for item in discovered]
        runtime_targets[batch] = target
        derived_context_audit[batch] = discovered
    decisions = []
    review_queue_all = []
    for row in sources:
        target = runtime_targets[row["batch"]]
        result = evaluate(row, target, config)
        decision = {"source_id": row["id"], "batch": row["batch"], "channel": row["channel"], **result}
        decisions.append(decision)
        if result["needs_ai_review"]:
            candidates = [
                {"candidate_index": index, **candidate}
                for index, candidate in enumerate(result.get("review_evidence_candidates", []), start=1)
            ]
            review_queue_all.append({
                **row,
                **result,
                "review_evidence_candidates": candidates,
                "evidence_position_basis": "evidence_source_text",
                "evidence_source_text": source_text(row),
                "episode_review_instruction": (
                    "周更综艺：标记 latest_episode、previous_episode_prominent、program_level_current 或 out_of_scope；"
                    "原则上只保留最新一期。前一期仅在本监测周形成新出现且明显突出的独立话题时保留，并记录突出依据。"
                    if clean(target.get("content_mode")) == "episodic_variety" else ""
                ),
            })
    # Exact copies need one source-level semantic judgment.  `select` later
    # propagates that judgment across the exact-copy family and still audits
    # every original source ID.  Similar opinions with different wording are
    # untouched and remain independent review items.
    exact_review_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in review_queue_all:
        exact_review_groups[(item["batch"], normalized(source_text(item)))].append(item)
    review_queue = []
    for group in exact_review_groups.values():
        representative = max(group, key=lambda item: (
            int(item.get("media_authority_rank", 0)),
            CHANNEL_PRIORITY.get(item.get("channel", ""), 0),
            float(item.get("quality", 0)),
            len(normalized(source_text(item))),
            item["id"],
        ))
        # The normalized source remains the authoritative full record.  The AI
        # handoff contains only compact verbatim candidates; workflow_status
        # provides the one shared full-source lookup path when needed.
        compact_candidates = [
            {
                key: candidate[key]
                for key in ("candidate_index", "start", "end", "text", "anchor_class")
                if key in candidate and candidate[key] not in ("", None, [], {})
            }
            for candidate in representative.get("review_evidence_candidates", [])
        ]
        compact_item = {
            "id": representative["id"],
            "batch": representative["batch"],
            "channel": representative.get("channel", ""),
            "post_type": representative.get("post_type", ""),
            "published": representative.get("published", ""),
            "title": representative.get("title", ""),
            "author": representative.get("author", ""),
            "auto_decision": representative.get("auto_decision", ""),
            "auto_reason": representative.get("auto_reason", ""),
            "review_evidence_candidates": compact_candidates,
        }
        parent_context = clean(representative.get("parent_body")) if representative.get("post_type") in {"评论", "转帖"} else ""
        if parent_context:
            compact_item["parent_context"] = parent_context
        if clean(representative.get("episode_review_instruction")):
            compact_item["episode_review_required"] = True
        if len(group) > 1:
            compact_item["copy_group_size"] = len(group)
        review_queue.append(compact_item)
    review_queue.sort(key=lambda item: (item["batch"], item["id"]))
    write_jsonl(run_dir / "normalized_sources.jsonl", sources)
    write_jsonl(run_dir / "out_of_period_sources.jsonl", out_of_period)
    write_jsonl(run_dir / "source_decisions.auto.jsonl", decisions)
    write_jsonl(run_dir / "source_review_queue.jsonl", review_queue)
    source_review_chunks = write_jsonl_chunks(run_dir / "source_review_chunks", review_queue)
    write_json(run_dir / "period_config.json", config)
    write_json(run_dir / "derived_context_terms.json", derived_context_audit)
    summary = {
        "status": "PREPARED",
        "raw_records": len(all_sources),
        "records": len(sources),
        "out_of_period": len(out_of_period),
        "batches": dict(Counter(row["batch"] for row in sources)),
        "auto_decisions": dict(Counter(row["auto_decision"] for row in decisions)),
        "ai_review_queue": len(review_queue),
        "ai_review_queue_before_exact_copy_collapse": len(review_queue_all),
        "exact_copy_review_items_saved": len(review_queue_all) - len(review_queue),
        "source_review_chunks": len(source_review_chunks),
        "card_files_read": 0,
        "historical_report_fields_read": 0,
        "input_sha256": sha256(records_path),
        "forbidden_fields_in_normalized_output": 0,
        "derived_context_terms": {batch: len(items) for batch, items in derived_context_audit.items()},
        "mainstream_media_samples": sum(row.get("media_authority_rank", 0) > 0 for row in sources),
        "ai_draft_hard_exclusions": sum(item.get("hard_exclusion") is True for item in decisions),
    }
    write_json(run_dir / "prepare_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def review_map(
    path: Path | None,
    scope: str,
    list_key: str = "reviews",
    *,
    key_field: str = "source_id",
) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("scope") != scope:
        raise ValueError(f"{path.name} scope 应为 {scope}")
    rows = payload.get(list_key, [])
    if isinstance(rows, dict):
        return {clean(key): value for key, value in rows.items()}
    result = {}
    for item in rows:
        review_id = clean(item.get(key_field))
        if not review_id or review_id in result:
            raise ValueError(f"{path.name} 存在空或重复 {key_field}")
        result[review_id] = item
    return result


def grams(value: object, n: int = 3) -> set[str]:
    value = normalized(value)
    return {value[index:index+n] for index in range(max(0, len(value) - n + 1))}


def similarity(left: set[str], right: set[str]) -> tuple[float, float]:
    if not left or not right:
        return 0.0, 0.0
    overlap = len(left & right)
    return overlap / len(left | right), overlap / min(len(left), len(right))


def row_rank(row: dict) -> tuple[int, int, int, float, int, str]:
    return (
        int(row.get("media_authority_rank", 0)),
        CHANNEL_PRIORITY.get(row.get("channel", ""), 0),
        int(row.get("decision") == "retain_core"),
        float(row.get("quality", 0)),
        len(normalized(source_text(row))),
        row["id"],
    )


def load_dedup_reviews(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("scope") != "current_period_copy_reviews":
        raise ValueError("dedup_reviews scope 错误")
    result = {}
    for item in payload.get("reviews", []):
        pair = tuple(sorted((clean(item.get("left_id")), clean(item.get("right_id")))))
        if not all(pair) or item.get("decision") not in {"same_copy", "independent"}:
            raise ValueError("dedup_reviews 项不合法")
        if not clean(item.get("reason")):
            raise ValueError(f"dedup_reviews 缺少逐对理由：{pair[0]}, {pair[1]}")
        result[pair] = item["decision"]
    return result


def copy_components(sources: dict[str, dict], reviews: dict[tuple[str, str], str]) -> list[list[str]]:
    parent = {source_id: source_id for source_id in sources}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    exact_groups: dict[str, list[str]] = defaultdict(list)
    for source_id, row in sources.items():
        fingerprint = normalized(source_text(row))
        if fingerprint:
            exact_groups[fingerprint].append(source_id)
    for ids in exact_groups.values():
        if len(ids) > 1:
            anchor = ids[0]
            for source_id in ids[1:]:
                pair = tuple(sorted((anchor, source_id)))
                if reviews.get(pair) != "independent":
                    union(anchor, source_id)
    for pair, decision in reviews.items():
        if decision != "same_copy":
            continue
        left, right = pair
        if left not in sources or right not in sources:
            raise ValueError(f"dedup_reviews 含未知来源：{left}, {right}")
        union(left, right)
    grouped: dict[str, list[str]] = defaultdict(list)
    for source_id in sources:
        grouped[find(source_id)].append(source_id)
    return [sorted(ids) for ids in grouped.values() if len(ids) > 1]


def resolve_source_review_evidence(
    review: dict,
    row: dict,
    queue_item: dict | None = None,
) -> tuple[str, list[int] | None]:
    """Resolve review evidence from a generated candidate or exact source text."""
    source = source_text(row)
    candidate_index = review.get("evidence_candidate_index")
    position = review.get("evidence_position")
    supplied = clean(review.get("evidence"))
    if candidate_index is not None:
        if not isinstance(candidate_index, int) or isinstance(candidate_index, bool) or candidate_index < 1:
            raise ValueError(f"AI复核 evidence_candidate_index 必须是从1开始的整数：{row['id']}")
        candidates = (queue_item or {}).get("review_evidence_candidates", [])
        selected = next(
            (
                item for item in candidates
                if isinstance(item, dict) and int(item.get("candidate_index", 0) or 0) == candidate_index
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"AI复核 evidence_candidate_index 不在本来源候选范围内：{row['id']}")
        start, end = selected.get("start"), selected.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(source):
            raise ValueError(f"来源候选证据位置无效：{row['id']}")
        extracted = source[start:end]
        if supplied and supplied != clean(extracted):
            raise ValueError(f"AI复核 evidence 与候选证据不一致：{row['id']}")
        if position is not None and position != [start, end]:
            raise ValueError(f"AI复核 evidence_position 与候选证据不一致：{row['id']}")
        return extracted, [start, end]
    if position is not None:
        if not isinstance(position, list) or len(position) != 2 or not all(isinstance(value, int) for value in position):
            raise ValueError(f"AI复核 evidence_position 必须是 [start,end]：{row['id']}")
        start, end = position
        if start < 0 or end <= start or end > len(source):
            raise ValueError(f"AI复核 evidence_position 越界：{row['id']}")
        extracted = source[start:end]
        if supplied and supplied != clean(extracted):
            raise ValueError(f"AI复核 evidence 与指定原文位置不一致：{row['id']}")
        return extracted, [start, end]
    if supplied:
        start = source.find(supplied)
        if start < 0:
            raise ValueError(f"AI复核依据无法在本来源原文定位：{row['id']}")
        return supplied, [start, start + len(supplied)]
    return "", None


def source_review_validation_issues(
    sources: dict[str, dict],
    queue: list[dict],
    reviews: dict[str, dict],
    period_config: dict,
) -> list[dict]:
    """Collect every source-review contract error in one fast preflight."""
    issues: list[dict] = []
    queue_by_id = {clean(row.get("id")): row for row in queue}
    queue_ids = set(queue_by_id)
    for source_id in sorted(queue_ids - set(reviews)):
        issues.append({"source_id": source_id, "issue": "missing_review"})
    for source_id in sorted(set(reviews) - set(sources)):
        issues.append({"source_id": source_id, "issue": "unknown_source"})
    for source_id, review in reviews.items():
        row = sources.get(source_id)
        if row is None:
            continue
        decision = clean(review.get("decision"))
        if decision not in ALLOWED_DECISIONS:
            issues.append({"source_id": source_id, "issue": "invalid_decision", "value": decision})
        if decision == "exclude" and not clean(review.get("reason")):
            issues.append({"source_id": source_id, "issue": "missing_reason"})
        try:
            evidence, _ = resolve_source_review_evidence(review, row, queue_by_id.get(source_id))
            if not evidence:
                issues.append({"source_id": source_id, "issue": "missing_verbatim_evidence"})
        except ValueError as exc:
            candidates = queue_by_id.get(source_id, {}).get("review_evidence_candidates", [])
            issues.append({
                "source_id": source_id,
                "issue": "invalid_evidence",
                "detail": str(exc),
                "repair": "从本来源 review_evidence_candidates 选择 evidence_candidate_index；只有候选均不合适时才逐字复制新证据",
                "available_candidate_indexes": [
                    item.get("candidate_index") for item in candidates if isinstance(item, dict)
                ],
            })
        target_config = period_config.get("targets", {}).get(row["batch"], {})
        if clean(target_config.get("content_mode")) == "episodic_variety":
            episode_scope = clean(review.get("episode_scope"))
            allowed_scopes = {"latest_episode", "previous_episode_prominent", "program_level_current", "out_of_scope"}
            if episode_scope not in allowed_scopes:
                issues.append({"source_id": source_id, "issue": "invalid_or_missing_episode_scope"})
            if not clean(review.get("episode_evidence")):
                issues.append({"source_id": source_id, "issue": "missing_episode_evidence"})
            if episode_scope == "previous_episode_prominent" and not clean(review.get("prominence_basis")):
                issues.append({"source_id": source_id, "issue": "missing_prominence_basis"})
            if episode_scope == "out_of_scope" and decision != "exclude":
                issues.append({"source_id": source_id, "issue": "out_of_scope_must_exclude"})
    return issues


def command_validate_source_reviews(args: argparse.Namespace) -> None:
    run_dir = args.run.resolve()
    sources = {row["id"]: row for row in load_jsonl(run_dir / "normalized_sources.jsonl")}
    queue = load_jsonl(run_dir / "source_review_queue.jsonl")
    config = load_json(run_dir / "period_config.json")
    reviews = review_map(args.reviews, "current_period_source_fulltext_reviews") if args.reviews else {}
    issues = source_review_validation_issues(sources, queue, reviews, config)
    report = {
        "status": "PASS" if not issues else "REVIEW_REQUIRED",
        "reviewed": len(reviews),
        "required": len({clean(row.get('id')) for row in queue}),
        "issue_count": len(issues),
        "affected_source_ids": sorted({clean(item.get("source_id")) for item in issues}),
        "issues": issues,
    }
    write_json(run_dir / "source_review_validation.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if issues:
        raise SystemExit(1)

    # Link checks are useful only for sources that can still reach the retained
    # pool. Resolve a reviewed representative across exact-copy families here,
    # so the network stage skips rows already excluded by content screening.
    # `select` independently recomputes the decisions and verifies URL coverage.
    auto = {row["source_id"]: row for row in load_jsonl(run_dir / "source_decisions.auto.jsonl")}
    decisions = {
        source_id: clean(auto[source_id].get("auto_decision"))
        for source_id in sources
    }
    for source_id, review in reviews.items():
        decisions[source_id] = clean(review.get("decision"))
    for family in copy_components(sources, {}):
        reviewed = [source_id for source_id in family if source_id in reviews]
        if reviewed:
            propagated = decisions[reviewed[0]]
            for source_id in family:
                decisions[source_id] = propagated
    candidates = [
        {
            "id": source_id,
            "batch": row.get("batch", ""),
            "channel": row.get("channel", ""),
            "author": row.get("author", ""),
            "url": row.get("url", ""),
        }
        for source_id, row in sources.items()
        if decisions.get(source_id) in {"retain_core", "retain_consensus"}
        and clean(row.get("url"))
        and auto[source_id].get("hard_exclusion") is not True
    ]
    write_jsonl(run_dir / "source_link_candidates.jsonl", candidates)


def cluster_discovery_record(row: dict, auto_result: dict) -> dict:
    """Build a compact, verbatim-first record for initial viewpoint discovery."""
    source = source_text(row)
    passages: list[dict] = []
    seen: set[tuple[int, int]] = set()

    review_position = row.get("review_evidence_position")
    if (
        isinstance(review_position, list)
        and len(review_position) == 2
        and all(isinstance(value, int) for value in review_position)
    ):
        start, end = review_position
        if 0 <= start < end <= len(source):
            passages.append({
                "start": start,
                "end": end,
                "text": source[start:end],
                "basis": "source_review_evidence",
            })
            seen.add((start, end))

    for candidate in ([] if passages else auto_result.get("review_evidence_candidates", [])):
        if not isinstance(candidate, dict):
            continue
        start, end = candidate.get("start"), candidate.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(source)):
            continue
        if (start, end) in seen:
            continue
        passages.append({
            "start": start,
            "end": end,
            "text": source[start:end],
            "basis": "deterministic_viewpoint_candidate",
        })
        seen.add((start, end))
        if len(passages) >= 1:
            break

    if not passages and source:
        end = min(len(source), 700)
        passages.append({
            "start": 0,
            "end": end,
            "text": source[:end],
            "basis": "source_opening_fallback",
        })

    return {
        "source_id": row["id"],
        "batch": row["batch"],
        "channel": row.get("channel", ""),
        "author": row.get("author", ""),
        "title": row.get("title", ""),
        "source_stance": row.get("stance", ""),
        "discovery_passages": passages,
        "full_text_lookup": {
            "file": "retained_sources.jsonl",
            "source_id": row["id"],
            "instruction": "仅在片段语义不清或需要确认跨作品归属时按 source_id 回查全文",
        },
    }


def discovery_round_robin(rows: list[dict], limit: int) -> list[dict]:
    """Prefer high-quality, cross-channel examples without losing rare stances."""
    if len(rows) <= limit:
        return sorted(rows, key=lambda row: row["source_id"])
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_channel[clean(row.get("channel"))].append(row)
    for values in by_channel.values():
        values.sort(key=lambda row: (
            -int(row.get("_media_rank", 0)),
            -float(row.get("_quality", 0)),
            row["source_id"],
        ))
    ordered_channels = sorted(
        by_channel,
        key=lambda channel: (-CHANNEL_PRIORITY.get(channel, 0), channel),
    )
    selected: list[dict] = []
    while len(selected) < limit and any(by_channel.values()):
        for channel in ordered_channels:
            if by_channel[channel] and len(selected) < limit:
                selected.append(by_channel[channel].pop(0))
    return selected


def discovery_seed(records: list[dict], source_rows: list[dict], limit_per_batch: int = 180) -> list[dict]:
    """Build a bounded discovery set; assignment later still covers every retained source."""
    source_by_id = {row["id"]: row for row in source_rows}
    output: list[dict] = []
    by_batch: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        source = source_by_id[record["source_id"]]
        by_batch[record["batch"]].append({
            **record,
            "_quality": source.get("quality", 0),
            "_media_rank": source.get("media_authority_rank", 0),
        })
    for batch in sorted(by_batch):
        batch_rows = by_batch[batch]
        if len(batch_rows) <= limit_per_batch:
            chosen = batch_rows
        else:
            stance_groups: dict[str, list[dict]] = defaultdict(list)
            for row in batch_rows:
                stance_groups[clean(row.get("source_stance")) or "混合或中性"].append(row)
            quotas = {stance: min(len(values), 20) for stance, values in stance_groups.items()}
            remaining = limit_per_batch - sum(quotas.values())
            while remaining > 0:
                candidates = [
                    stance for stance, values in stance_groups.items()
                    if quotas[stance] < len(values)
                ]
                if not candidates:
                    break
                stance = max(
                    candidates,
                    key=lambda value: ((len(stance_groups[value]) ** 0.5) / (quotas[value] + 1), value),
                )
                quotas[stance] += 1
                remaining -= 1
            chosen = []
            for stance in sorted(stance_groups):
                chosen.extend(discovery_round_robin(stance_groups[stance], quotas[stance]))
        for row in chosen:
            row.pop("_quality", None)
            row.pop("_media_rank", None)
        output.extend(chosen)
    return sorted(output, key=lambda row: (row["batch"], row["source_id"]))


def compact_discovery_handoff(records: list[dict]) -> list[dict]:
    """Remove repeated lookup metadata while preserving every discovery passage."""
    output = []
    for row in records:
        passages = [clean(item.get("text")) for item in row.get("discovery_passages", []) if clean(item.get("text"))]
        output.append({
            "source_id": clean(row.get("source_id")),
            "batch": clean(row.get("batch")),
            "channel": clean(row.get("channel")),
            "title": clean(row.get("title"))[:160],
            "source_stance": clean(row.get("source_stance")),
            "passage": " ".join(passages),
        })
    return output


def load_link_health(path: Path) -> dict[str, dict]:
    payload = load_json(path.resolve())
    if payload.get("scope") != "current_period_source_link_health":
        raise ValueError("link_health scope 错误")
    result = {}
    for item in payload.get("results", []):
        url = clean(item.get("url"))
        classification = clean(item.get("classification"))
        if not url or url in result:
            raise ValueError("link_health 含空或重复 URL")
        if classification not in {"reachable", "confirmed_dead", "indeterminate", "blocked", "invalid"}:
            raise ValueError(f"link_health 含未知 classification：{classification}")
        if classification == "confirmed_dead" and item.get("status_code") not in {404, 410}:
            raise ValueError(f"confirmed_dead 必须由 HTTP 404/410 支撑：{url}")
        result[url] = item
    return result


def command_select(args: argparse.Namespace) -> None:
    run_dir = args.run.resolve()
    period_config = load_json(run_dir / "period_config.json")
    sources = {row["id"]: row for row in load_jsonl(run_dir / "normalized_sources.jsonl")}
    auto = {row["source_id"]: row for row in load_jsonl(run_dir / "source_decisions.auto.jsonl")}
    reviews = review_map(args.reviews, "current_period_source_fulltext_reviews")
    review_queue = load_jsonl(run_dir / "source_review_queue.jsonl")
    review_issues = source_review_validation_issues(sources, review_queue, reviews, period_config)
    if review_issues:
        write_json(run_dir / "source_review_validation.json", {
            "status": "REVIEW_REQUIRED",
            "issue_count": len(review_issues),
            "affected_source_ids": sorted({clean(item.get("source_id")) for item in review_issues}),
            "issues": review_issues,
        })
        raise ValueError(
            f"source_reviews 共有 {len(review_issues)} 项契约错误，详见 "
            f"{run_dir / 'source_review_validation.json'}"
        )
    link_health = load_link_health(args.link_health)
    queue_by_id = {clean(row.get("id")): row for row in review_queue}
    pending_reviews = [row for row in review_queue if row["id"] not in reviews]
    write_jsonl(run_dir / "source_review_queue.unresolved.jsonl", pending_reviews)
    unknown = set(reviews) - set(sources)
    if unknown:
        raise ValueError(f"source_reviews 含未知来源：{sorted(unknown)[:5]}")
    decided = []
    for source_id, row in sources.items():
        base = auto[source_id]
        review = reviews.get(source_id)
        decision = clean(review.get("decision")) if review else base["auto_decision"]
        reason = clean(review.get("reason")) if review else base["auto_reason"]
        if review and decision in {"retain_core", "retain_consensus"} and not reason:
            reason = "AI复核确认来源含目标作品的可用观点"
        evidence, evidence_position = (
            resolve_source_review_evidence(review, row, queue_by_id.get(source_id))
            if review else ("", None)
        )
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"非法来源决定：{source_id} {decision}")
        if review and decision == "exclude" and not reason:
            raise ValueError(f"AI复核缺少理由：{source_id}")
        if review and not evidence:
            raise ValueError(f"AI复核缺少 evidence_candidate_index、逐字 evidence 或 evidence_position：{source_id}")
        target_config = period_config.get("targets", {}).get(row["batch"], {})
        if review and clean(target_config.get("content_mode")) == "episodic_variety":
            episode_scope = clean(review.get("episode_scope"))
            allowed_scopes = {"latest_episode", "previous_episode_prominent", "program_level_current", "out_of_scope"}
            if episode_scope not in allowed_scopes:
                raise ValueError(f"周更综艺复核缺少合法 episode_scope：{source_id}")
            if not clean(review.get("episode_evidence")):
                raise ValueError(f"周更综艺复核缺少 episode_evidence：{source_id}")
            if episode_scope == "previous_episode_prominent" and not clean(review.get("prominence_basis")):
                raise ValueError(f"前一期突出话题复核缺少 prominence_basis：{source_id}")
            if episode_scope == "out_of_scope" and decision != "exclude":
                raise ValueError(f"out_of_scope 的综艺来源必须 exclude：{source_id}")
        decided.append({
            **row,
            **{key: base[key] for key in ("quality", "stance", "fact_warning")},
            "decision": decision,
            "decision_reason": reason,
            "decision_basis": "ai_fulltext_review" if review else "deterministic_gate",
            "review_evidence": evidence,
            "review_evidence_position": evidence_position,
        })

    dedup_reviews = load_dedup_reviews(args.dedup_reviews)
    decided_by_id = {row["id"]: row for row in decided}
    families = copy_components(sources, dedup_reviews)
    family_audit = []
    for family in families:
        reviewed = [decided_by_id[source_id] for source_id in family if source_id in reviews]
        reviewed_states = {row["decision"] for row in reviewed}
        if len(reviewed_states) > 1:
            raise ValueError(
                f"同一 copy family 存在互相冲突的人工决定 {sorted(reviewed_states)}：{family[:6]}"
            )
        propagated = "none"
        if reviewed:
            source = reviewed[0]
            for source_id in family:
                target = decided_by_id[source_id]
                target["decision"] = source["decision"]
                target["decision_reason"] = f"同稿组沿用人工判断：{source['decision_reason']}"
                target["decision_basis"] = "copy_family_propagation"
            propagated = source["decision"]
        family_audit.append({"members": family, "propagated_decision": propagated})

    expected_urls = {
        clean(row.get("url"))
        for row in decided
        if row["decision"] in {"retain_core", "retain_consensus"}
        and clean(row.get("url"))
        and auto[row["id"]].get("hard_exclusion") is not True
    }
    missing_link_checks = sorted(expected_urls - set(link_health))
    if missing_link_checks:
        raise ValueError(
            f"link_health 未覆盖 {len(missing_link_checks)} 个仍可能保留的来源 URL，例如："
            f"{missing_link_checks[:3]}"
        )

    hard_exclusions = []
    for row in decided:
        base = auto[row["id"]]
        link_result = link_health.get(clean(row.get("url")), {})
        row["link_health"] = {
            "classification": clean(link_result.get("classification")),
            "status_code": link_result.get("status_code"),
            "checked_at": clean(link_result.get("checked_at")),
            "reason": clean(link_result.get("reason")),
        } if link_result else {}
        if base.get("hard_exclusion") is True:
            row["decision"] = "exclude"
            row["decision_reason"] = base["auto_reason"]
            row["decision_basis"] = "hard_ai_draft_leak_gate"
            row["review_evidence"] = clean(base.get("hard_exclusion_evidence"))
            hard_exclusions.append({"source_id": row["id"], "type": clean(base.get("hard_exclusion_type")), "reason": row["decision_reason"], "evidence": row["review_evidence"]})
        elif link_result.get("classification") == "confirmed_dead":
            row["decision"] = "exclude"
            row["decision_reason"] = f"来源链接经在线核验明确返回 HTTP {link_result.get('status_code')}"
            row["decision_basis"] = "confirmed_dead_link_gate"
            hard_exclusions.append({"source_id": row["id"], "type": "confirmed_dead_link", "reason": row["decision_reason"], "url": row.get("url")})

    admitted = [row for row in decided if row["decision"] in {"retain_core", "retain_consensus"}]
    kept: list[dict] = []
    by_batch: dict[str, list[tuple[dict, set[str]]]] = defaultdict(list)
    duplicates, candidates = [], []
    for row in sorted(admitted, key=row_rank, reverse=True):
        row_grams = grams(source_text(row))
        duplicate = None
        for old, old_grams in by_batch[row["batch"]]:
            jaccard, containment = similarity(row_grams, old_grams)
            pair = tuple(sorted((row["id"], old["id"])))
            reviewed = dedup_reviews.get(pair)
            hard = jaccard >= 0.84 or containment >= 0.94
            candidate = jaccard >= 0.28 and containment >= 0.58
            if reviewed == "same_copy" or (hard and reviewed != "independent"):
                duplicate = (old, jaccard, containment, "ai_review" if reviewed else "hard_text_threshold")
                break
            if candidate:
                candidates.append({"left_id": row["id"], "right_id": old["id"], "batch": row["batch"], "jaccard": round(jaccard, 4), "containment": round(containment, 4), "review": reviewed or "pending_independent_by_default"})
        if duplicate:
            old, jaccard, containment, basis = duplicate
            duplicates.append({"excluded_id": row["id"], "kept_id": old["id"], "batch": row["batch"], "jaccard": round(jaccard, 4), "containment": round(containment, 4), "basis": basis})
        else:
            kept.append(row)
            by_batch[row["batch"]].append((row, row_grams))

    kept.sort(key=lambda row: (row["batch"], -int(row.get("media_authority_rank", 0)), -CHANNEL_PRIORITY.get(row["channel"], 0), -float(row["quality"]), row["id"]))
    write_jsonl(run_dir / "retained_sources.jsonl", kept)
    discovery_records = [cluster_discovery_record(row, auto[row["id"]]) for row in kept]
    write_jsonl(run_dir / "cluster_discovery_input.jsonl", discovery_records)
    discovery_seed_records = discovery_seed(discovery_records, kept)
    write_jsonl(run_dir / "cluster_discovery_seed_input.jsonl", discovery_seed_records)
    discovery_chunks = write_jsonl_chunks(
        run_dir / "cluster_discovery_chunks",
        compact_discovery_handoff(discovery_seed_records),
        chunk_size=180,
        max_bytes=150_000,
    )
    write_json(run_dir / "source_decisions.final.json", {"decisions": [{"source_id": row["id"], "decision": row["decision"], "reason": row["decision_reason"], "basis": row["decision_basis"], "evidence": row.get("review_evidence", ""), "evidence_position": row.get("review_evidence_position")} for row in decided]})
    write_json(run_dir / "dedup_audit.json", {"copy_families": family_audit, "duplicates": duplicates, "medium_similarity_candidates": candidates})
    write_json(run_dir / "hard_exclusion_audit.json", {"exclusions": hard_exclusions})
    write_json(run_dir / "source_link_health.applied.json", {"scope": "current_period_source_link_health_applied", "checked_urls": len(link_health), "confirmed_dead_urls": sum(item.get("classification") == "confirmed_dead" for item in link_health.values()), "source_exclusions": sum(item.get("type") == "confirmed_dead_link" for item in hard_exclusions), "input": str(args.link_health.resolve())})
    summary = {
        "status": "SELECTED",
        "input": len(sources),
        "admitted_before_dedup": len(admitted),
        "hard_or_reviewed_duplicates": len(duplicates),
        "retained_after_dedup": len(kept),
        "cluster_discovery_seed": len(discovery_seed_records),
        "cluster_discovery_chunks": len(discovery_chunks),
        "medium_similarity_candidates_kept_pending_review": sum(item["review"] == "pending_independent_by_default" for item in candidates),
        "source_review_queue": len(review_queue),
        "unreviewed_source_queue": len(pending_reviews),
        "mainstream_media_retained": sum(row.get("media_authority_rank", 0) > 0 for row in kept),
        "hard_ai_draft_exclusions": sum(item.get("type") != "confirmed_dead_link" for item in hard_exclusions),
        "confirmed_dead_link_exclusions": sum(item.get("type") == "confirmed_dead_link" for item in hard_exclusions),
        "batches": dict(Counter(row["batch"] for row in kept)),
    }
    write_json(run_dir / "selection_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def sentence_spans(body: str) -> list[dict]:
    spans = []
    for match in re.finditer(r"[^。！？!?；;\r\n]+[。！？!?；;]?", body):
        raw = match.group(0)
        if len(normalized(raw)) < 8:
            continue
        if len(raw) <= EXCERPT_MAX:
            spans.append({"start": match.start(), "end": match.end(), "text": raw})
            continue
        # 无句号长文先按逗号、冒号、项目符号、话题标签和转折词切成可复核的原文块。
        boundaries = [0]
        for hint in re.finditer(r"[，,、：:]|(?=\s*[①②③④⑤⑥⑦⑧⑨⑩])|(?=\s*#.{1,32}?#)|(?=\s*(?:但是|不过|然而|同时|另外|其次|最后|再说|更重要的是))", raw):
            point = hint.end() if hint.group(0) and hint.group(0)[-1:] in "，,、：:" else hint.start()
            if point - boundaries[-1] >= 45:
                boundaries.append(point)
        if len(raw) - boundaries[-1] >= 8:
            boundaries.append(len(raw))
        if len(boundaries) <= 2:
            boundaries = list(range(0, len(raw), 140)) + [len(raw)]
        for start, end in zip(boundaries, boundaries[1:]):
            absolute_start, absolute_end = match.start() + start, match.start() + end
            piece = body[absolute_start:absolute_end]
            if len(normalized(piece)) >= 8:
                spans.append({"start": absolute_start, "end": absolute_end, "text": piece})
    return spans or ([{"start": 0, "end": len(body), "text": body}] if body else [])


def target_evidence_segments(row: dict, body: str, target: dict, local_context: str) -> dict[str, str]:
    """Build a small exact-text menu for target attribution without sending the full source."""
    strong, weak, auxiliary, _ = target_layers(target)
    anchors = [term for term in strong + weak + auxiliary if term]
    candidates: list[str] = []

    def add(value: object) -> None:
        text = clean(value)
        if not text or not any(term in text for term in anchors):
            return
        if len(text) > 120:
            hit_positions = [text.find(term) for term in anchors if term in text]
            center = min(position for position in hit_positions if position >= 0)
            start = max(0, center - 45)
            text = text[start:start + 120]
        key = normalized(text)
        if not key:
            return
        for index, existing in enumerate(candidates):
            existing_key = normalized(existing)
            if key in existing_key or existing_key in key:
                if len(text) < len(existing):
                    candidates[index] = text
                return
        candidates.append(text)

    add(row.get("title"))
    for span in sentence_spans(local_context):
        add(span.get("text"))
    if len(candidates) < 2:
        for span in sentence_spans(body):
            add(span.get("text"))
            if len(candidates) >= 2:
                break
    return {str(index): value for index, value in enumerate(candidates[:2], start=1)}


def unrelated_leading_segment_indexes(
    segments: dict[str, str], target_terms: list[str], aspect_terms: list[str]
) -> list[int]:
    """Find clearly unrelated gossip before the first target/aspect-bearing segment."""
    ordered = [(int(index), clean(value)) for index, value in segments.items() if str(index).isdigit()]
    relevant = [
        index for index, text in ordered
        if any(term and term in text for term in target_terms + aspect_terms)
    ]
    if not relevant:
        return []
    first_relevant = min(relevant)
    return [
        index for index, text in ordered
        if index < first_relevant
        and UNRELATED_LEADING_TOPIC.search(text)
        and not any(term and term in text for term in target_terms + aspect_terms)
    ]


def keyword_pairs(definition: dict) -> list[tuple[str, float]]:
    result = []
    for item in definition.get("keywords", []):
        if isinstance(item, str):
            result.append((item, 1.0))
        elif isinstance(item, list) and len(item) >= 2:
            result.append((clean(item[0]), float(item[1])))
    return [(term, weight) for term, weight in result if term]


def effective_required_terms(definition: dict, target_config: dict | None = None) -> list[str]:
    """Return aspect-bearing routing terms without letting work or person names become the gate."""
    explicit = [clean(term) for term in definition.get("required_any", []) if clean(term)]
    strong, weak, auxiliary, comparisons = target_layers(target_config or {})
    entities = {normalized(term) for term in strong + weak + auxiliary + comparisons}
    candidates = []
    weighted = (
        [(term, 1.0) for term in explicit]
        if explicit else sorted(keyword_pairs(definition), key=lambda item: item[1], reverse=True)
    )
    for term, weight in weighted:
        key = normalized(term)
        if len(key) < 2 or key in entities or term in GENERIC_ROUTING_TERMS:
            continue
        candidates.append(term)
    return list(dict.fromkeys(candidates))[:4]


def validate_clusters(payload: dict, batches: set[str], config: dict | None = None) -> dict[str, list[dict]]:
    if payload.get("scope") not in {None, "current_period_data_derived_clusters"}:
        raise ValueError("cluster_definitions scope 错误")
    definitions = payload.get("batches")
    if not isinstance(definitions, dict):
        raise ValueError("cluster_definitions 缺少 batches")
    if batches - set(definitions):
        raise ValueError(f"cluster_definitions 缺少批次：{sorted(batches - set(definitions))}")
    for batch in batches:
        ids = [clean(item.get("id")) for item in definitions[batch]]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"{batch} 簇ID为空或重复")
        for item in definitions[batch]:
            title = clean(item.get("title"))
            if not title or GENERIC_CLUSTER_TITLE.search(title):
                raise ValueError(f"{batch} 存在空标题或兜底簇")
            if len(normalized(title)) < 16 or not CLUSTER_TITLE_PREDICATE.search(title):
                raise ValueError(
                    f"{batch}/{item.get('id')} 的标题不是可直接理解的报告体观点句：{title}"
                )
            if clean(item.get("stance")) not in ALLOWED_CLUSTER_STANCES:
                raise ValueError(f"{batch}/{item.get('id')} 缺少合法 stance")
            if not keyword_pairs(item) and not item.get("background"):
                raise ValueError(f"{batch}/{item.get('id')} 缺少关键词")
            target_config = (config or {}).get("targets", {}).get(batch, {})
            if not item.get("background") and not effective_required_terms(item, target_config):
                raise ValueError(
                    f"{batch}/{item.get('id')} 缺少可用于归簇的方面词；"
                    "关键词不能只由作品名、演员名或影视通用词构成"
                )
    return definitions


def cluster_score(
    fragment: str,
    title: str,
    definition: dict,
    target_config: dict | None = None,
) -> tuple[float, list[str]]:
    score, hits = 0.0, []
    strong, weak, auxiliary, comparisons = target_layers(target_config or {})
    target_keys = {normalized(term) for term in strong + weak}
    auxiliary_keys = {normalized(term) for term in auxiliary}
    comparison_keys = {normalized(term) for term in comparisons}
    for term, weight in keyword_pairs(definition):
        body_count = min(fragment.count(term), 3)
        title_count = min(title.count(term), 2)
        if body_count or title_count:
            hits.append(term)
            term_key = normalized(term)
            if term_key in target_keys:
                effective_weight = 0.0
            elif term_key in auxiliary_keys:
                effective_weight = min(float(weight), 0.5)
            elif term_key in comparison_keys or term in GENERIC_ROUTING_TERMS:
                effective_weight = min(float(weight), 1.0)
            else:
                effective_weight = min(max(float(weight), 0.5), 5.0)
            score += body_count * effective_weight * 2.2 + title_count * effective_weight * 0.6
    score += min(5.0, len(OPINION.findall(fragment)) * 0.7)
    required = effective_required_terms(definition, target_config)
    if required and not any(term in fragment for term in required):
        score -= 30.0
    negative = [clean(term) for term in definition.get("negative_cues", []) if clean(term)]
    if negative and not any(term in fragment for term in negative):
        score -= 20.0
    local_stance = stance(fragment)
    expected_stance = clean(definition.get("stance"))
    if expected_stance == "positive" and local_stance == "负向":
        score -= 30.0
    elif expected_stance == "positive" and local_stance == "混合或中性":
        score -= 8.0
    elif expected_stance == "negative" and local_stance == "正向":
        score -= 30.0
    elif expected_stance == "negative" and local_stance == "混合或中性":
        score -= 8.0
    elif expected_stance == "objective" and local_stance != "混合或中性" and not definition.get("objective_meta"):
        score -= 12.0
    return score, hits


def best_window(row: dict, definition: dict, anchor_terms: list[str] | None = None, target_config: dict | None = None) -> dict:
    body = source_text(row)
    spans = sentence_spans(body)
    candidates = []
    strong_terms, weak_terms, _, comparisons = target_layers(target_config or {})
    title_strong = term_hits(clean(row.get("title")), strong_terms)
    title_comparisons = term_hits(clean(row.get("title")), comparisons)
    source_target_hits = term_hits(body, strong_terms + weak_terms)
    normalized_anchors = [clean(term) for term in (anchor_terms or []) if clean(term)]
    for start in range(len(spans)):
        for end in range(start, min(len(spans), start + 2)):
            fragment = body[spans[start]["start"]:spans[end]["end"]]
            score, hits = cluster_score(fragment, row.get("title", ""), definition, target_config)
            strong_hits = term_hits(fragment, strong_terms)
            weak_hits = term_hits(fragment, weak_terms)
            comparison_hits = term_hits(fragment, comparisons)
            if strong_hits:
                score += 16.0 + 3.0 * len(strong_hits)
            elif weak_hits:
                score += 6.0 + 1.5 * len(weak_hits)
            elif title_strong and not title_comparisons:
                score += 2.0
            elif source_target_hits:
                # The source already establishes the work.  A later pronoun or
                # character-led evaluative passage should not lose to an empty
                # sentence merely because it does not repeat the title.
                score -= 6.0
            else:
                score -= 12.0
            score -= 10.0 * len(comparison_hits)
            anchor_hits = [term for term in normalized_anchors if term in fragment]
            if normalized_anchors:
                score += 44.0 + 16.0 * len(anchor_hits) if anchor_hits else -24.0
            candidates.append({"start": spans[start]["start"], "end": spans[end]["end"], "text": fragment, "score": round(score, 3), "hits": hits, "anchor_hits": anchor_hits, "strong_target_hits": strong_hits, "weak_target_hits": weak_hits, "comparison_hits": comparison_hits})
    required = effective_required_terms(definition, target_config)
    return max(
        candidates,
        key=lambda item: (
            bool(item.get("anchor_hits")) if normalized_anchors else True,
            len(item.get("anchor_hits", [])),
            bool(item.get("strong_target_hits")) and (
                not required or any(term in item.get("text", "") for term in required)
            ),
            bool(item.get("strong_target_hits")),
            item["score"],
            len(item["hits"]),
            -item["start"],
        ),
    ) if candidates else {"start": 0, "end": len(body), "text": body, "score": 0.0, "hits": []}


def target_evidence_candidates(row: dict, target_config: dict, limit: int = 3) -> list[dict]:
    """Return short verbatim source spans that explicitly contain the target work."""
    body = source_text(row)
    strong, weak, _, _ = target_layers(target_config)
    terms = strong + weak
    candidates = []
    for span in sentence_spans(body):
        hits = term_hits(span["text"], terms)
        if not hits:
            continue
        text = span["text"]
        if len(text) > 260:
            first = min(text.find(term) for term in hits if term in text)
            left = max(0, first - 90)
            right = min(len(text), first + 170)
            text = text[left:right]
            start, end = span["start"] + left, span["start"] + right
        else:
            start, end = span["start"], span["end"]
        candidates.append({
            "candidate_index": len(candidates) + 1,
            "text": text,
            "start": start,
            "end": end,
            "target_hits": hits,
        })
        if len(candidates) >= limit:
            break
    return candidates


def reviewed_window(
    row: dict,
    definition: dict,
    override: dict,
    target_config: dict,
    secondary: bool = False,
) -> dict:
    """Return an AI-selected one/two-span passage, or the scored default window.

    Cluster-set and member review happen before the final excerpt stage, so a
    non-contiguous but coherent source argument must already be representable
    here.  Positions are authoritative and prevent a model from silently
    joining rewritten text or skipping source order.
    """
    prefix = "secondary_" if secondary else ""
    fragment_key = f"{prefix}passage_fragments"
    position_key = f"{prefix}passage_positions"
    anchor_key = f"{prefix}anchor_terms"
    fragments_value = override.get(fragment_key)
    positions_value = override.get(position_key)
    anchors = [clean(term) for term in override.get(anchor_key, []) if clean(term)]
    if fragments_value is None and positions_value is None:
        return best_window(row, definition, anchors, target_config)
    if not isinstance(fragments_value, list):
        raise ValueError(f"归簇片段必须填写 {fragment_key} 数组")
    fragments = [str(value) for value in fragments_value]
    body = source_text(row)
    if positions_value is None:
        positions = locate_fragments(body, fragments)
    elif isinstance(positions_value, list):
        positions = validate_fragment_positions(body, fragments, positions_value)
    else:
        raise ValueError(f"{position_key} 必须为位置数组；也可省略并由脚本按原文顺序定位")
    text = clean(" ".join(fragment.strip() for fragment in fragments))
    score, hits = cluster_score(text, row.get("title", ""), definition, target_config)
    strong_terms, weak_terms, _, comparisons = target_layers(target_config)
    strong_hits = term_hits(text, strong_terms)
    weak_hits = term_hits(text, weak_terms)
    comparison_hits = term_hits(text, comparisons)
    source_target_hits = term_hits(body, strong_terms + weak_terms)
    title_strong = term_hits(clean(row.get("title")), strong_terms)
    title_comparisons = term_hits(clean(row.get("title")), comparisons)
    if strong_hits:
        score += 16.0 + 3.0 * len(strong_hits)
    elif weak_hits:
        score += 6.0 + 1.5 * len(weak_hits)
    elif title_strong and not title_comparisons:
        score += 2.0
    elif source_target_hits:
        score -= 6.0
    else:
        score -= 12.0
    score -= 10.0 * len(comparison_hits)
    anchor_hits = [term for term in anchors if term in text]
    if anchors:
        score += 44.0 + 16.0 * len(anchor_hits) if anchor_hits else -24.0
    return {
        "start": positions[0][0],
        "end": positions[-1][1],
        "text": text,
        "score": round(score, 3),
        "hits": hits,
        "anchor_hits": anchor_hits,
        "strong_target_hits": strong_hits,
        "weak_target_hits": weak_hits,
        "comparison_hits": comparison_hits,
        "fragments": fragments,
        "positions": positions,
        "reviewed_fragments": True,
    }


def compact_payload_fingerprint(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def cluster_routing_cache_rows(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        rows = load_jsonl(path)
    except (OSError, ValueError):
        return {}
    return {
        clean(row.get("source_id")): row
        for row in rows
        if int(row.get("cache_version", 0)) == CLUSTER_ROUTING_CACHE_VERSION
        and clean(row.get("source_id"))
    }


def windows_overlap(left: dict, right: dict) -> bool:
    left_positions = left.get("positions") or [[left["start"], left["end"]]]
    right_positions = right.get("positions") or [[right["start"], right["end"]]]
    return any(max(a[0], b[0]) < min(a[1], b[1]) for a in left_positions for b in right_positions)


def passage_alignment(
    row: dict,
    window: dict,
    definition: dict,
    target_config: dict,
    override: dict,
    secondary: bool = False,
    allow_title_fallback: bool = True,
) -> dict:
    text = clean(window.get("text"))
    strong_terms, weak_terms, _, comparisons = target_layers(target_config)
    strong_hits = term_hits(text, strong_terms)
    weak_hits = term_hits(text, weak_terms)
    target_hits = strong_hits + weak_hits
    comparison_hits = term_hits(text, comparisons)
    source = source_text(row)
    evidence_key = "secondary_target_evidence" if secondary else "target_evidence"
    stance_key = "secondary_passage_stance" if secondary else "passage_stance"
    evidence = clean(override.get(evidence_key))
    reviewed_stance = clean(override.get(stance_key))
    evidence_valid = bool(evidence and evidence in source and any(term in evidence for term in strong_terms + weak_terms))
    review_position = row.get("review_evidence_position")
    reviewed_source_overlap = bool(
        row.get("decision") in {"retain_core", "retain_consensus"}
        and isinstance(review_position, list)
        and len(review_position) == 2
        and int(review_position[0]) < int(window.get("end", 0))
        and int(window.get("start", 0)) < int(review_position[1])
    )

    if strong_hits and not comparison_hits:
        target_passed, target_basis = True, "passage_target_anchor"
    elif strong_hits and comparison_hits and (evidence_valid or reviewed_source_overlap):
        target_passed, target_basis = True, "ai_reviewed_multi_work_attribution"
    elif strong_hits and comparison_hits:
        target_passed, target_basis = False, "multi_work_passage_requires_review"
    elif weak_hits and evidence_valid:
        target_passed, target_basis = True, "ai_reviewed_weak_target_attribution"
    elif weak_hits:
        target_passed, target_basis = False, "weak_passage_target_requires_review"
    elif evidence_valid:
        target_passed, target_basis = True, "ai_reviewed_source_target_evidence"
    elif reviewed_source_overlap:
        target_passed, target_basis = True, "reviewed_source_passage_overlap"
    elif allow_title_fallback:
        title_hits = term_hits(clean(row.get("title")), strong_terms)
        source_comparisons = term_hits(source, comparisons)
        target_passed = bool(title_hits and not source_comparisons)
        target_basis = "single_work_title_anchor" if target_passed else "no_passage_target_anchor"
    else:
        target_passed, target_basis = False, "no_passage_target_anchor"

    required = effective_required_terms(definition, target_config)
    aspect_hits = [term for term in required if term in text]
    # required_any is a routing vocabulary, not an exhaustive semantic list.
    # A reviewed verbatim anchor may express the same aspect in different words;
    # the final excerpt review remains the item-level semantic release gate.
    reviewed_anchor_hits = [clean(term) for term in window.get("anchor_hits", []) if clean(term)]
    aspect_passed = not required or bool(aspect_hits) or bool(override and reviewed_anchor_hits)
    aspect_basis = (
        "required_any_hit" if aspect_hits
        else "ai_reviewed_anchor" if override and reviewed_anchor_hits
        else "no_required_terms" if not required
        else "missing_aspect_evidence"
    )

    expected = clean(definition.get("stance"))
    inferred = stance(text)
    if reviewed_stance:
        if reviewed_stance != expected:
            raise ValueError(f"{row['id']} 的 {stance_key} 与目标簇立场不一致")
        stance_passed, stance_basis = True, "ai_semantic_review"
    elif expected == "positive":
        stance_passed = inferred != "负向"
        stance_basis = "window_stance" if inferred == "正向" else "deferred_to_independent_member_review"
    elif expected == "negative":
        stance_passed = inferred != "正向"
        stance_basis = "window_stance" if inferred == "负向" else "deferred_to_independent_member_review"
    else:
        stance_passed = inferred == "混合或中性" or bool(definition.get("objective_meta"))
        stance_basis = "objective_meta" if definition.get("objective_meta") else "window_stance"

    return {
        "target": {"passed": target_passed, "basis": target_basis, "target_hits": target_hits, "strong_hits": strong_hits, "weak_hits": weak_hits, "comparison_hits": comparison_hits, "review_evidence": evidence},
        "aspect": {
            "passed": aspect_passed,
            "basis": aspect_basis,
            "hits": aspect_hits,
            "reviewed_anchor_hits": reviewed_anchor_hits,
        },
        "stance": {"passed": stance_passed, "expected": expected, "inferred": inferred, "basis": stance_basis},
    }


def alignment_issue(alignment: dict) -> str:
    if not alignment["target"]["passed"]:
        return alignment["target"]["basis"]
    if not alignment["aspect"]["passed"]:
        return "passage_aspect_mismatch"
    if not alignment["stance"]["passed"]:
        return "passage_stance_conflict"
    return ""


def flatten_overrides(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("scope") not in {None, "current_period_source_cluster_reviews"} and payload.get("schema_version") is None:
        raise ValueError("cluster_overrides scope 错误")
    raw = payload.get("overrides", {})
    result = {}
    for key, value in raw.items():
        if isinstance(value, dict) and "cluster" in value:
            result[clean(key)] = value
        elif isinstance(value, dict):
            for source_id, item in value.items():
                result[clean(source_id)] = item
    return result


def order_for_workbench(items: list[dict]) -> list[dict]:
    """Order every aligned source; ranking never determines admission."""
    return sorted(items, key=lambda item: row_rank(item["source"]), reverse=True)


def representative_cluster_members(items: list[dict], limit: int = 8) -> list[dict]:
    """Expose both strongest and weakest assignments so a broad cluster cannot hide in its top rows."""
    if len(items) <= limit:
        return order_for_workbench(items)
    ranked = order_for_workbench(items)
    by_alignment = sorted(
        items,
        key=lambda item: (
            float(item.get("score", 0)),
            float(item.get("margin", 0)),
            item.get("source_id", ""),
        ),
    )
    selected: list[dict] = []
    seen: set[str] = set()
    for candidate in [*ranked[:4], *by_alignment[:2]]:
        source_id = clean(candidate.get("source_id"))
        if source_id and source_id not in seen:
            selected.append(candidate)
            seen.add(source_id)
    remaining = [item for item in ranked if clean(item.get("source_id")) not in seen]
    while remaining and len(selected) < limit:
        index = round((len(remaining) - 1) * (len(selected) - 5) / max(1, limit - 6)) if len(selected) >= 6 else 0
        candidate = remaining.pop(max(0, min(index, len(remaining) - 1)))
        selected.append(candidate)
    return selected[:limit]


def cluster_is_schedule_note(definition: dict) -> bool:
    if clean(definition.get("stance")) != "objective":
        return False
    text = " ".join(clean(definition.get(key)) for key in ("title", "summary"))
    return bool(
        re.search(r"定档|开播|排播|接档|同步更新|播出时间|追剧日历", text)
        and not re.search(r"热度|口碑|反响|评价|争议|竞争|市场|影响|价值|看好|质疑|批评|担忧", text)
    )


def data_note_fact_facets(value: object) -> set[str]:
    """Return the small set of background facts that a schedule passage can evidence."""
    text = clean(value)
    facets = {"schedule_announcement"}
    if re.search(r"\d{1,2}月\d{1,2}日|\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}", text):
        facets.add("airing_date")
    if re.search(r"\d{1,2}[：:]\d{2}|晚间|黄金档", text):
        facets.add("airing_time")
    if re.search(r"腾讯视频|爱奇艺|优酷|芒果TV|CCTV-?\d+|央视|卫视|双平台|多平台", text, re.I):
        facets.add("airing_platform")
    if re.search(r"每日|每周[一二三四五六日天]|日更|连更|首更|更新\d+集|会员|VIP", text, re.I):
        facets.add("update_cadence")
    return facets


def deduplicate_data_note_members(items: list[dict]) -> list[dict]:
    """Cover each schedule-fact type per channel without retaining every rewrite."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in items:
        channel = clean(item["source"].get("channel")) or "未知渠道"
        for facet in data_note_fact_facets(item.get("window", {}).get("text")):
            groups[(channel, facet)].append(item)
    chosen_by_id = {}
    for group in groups.values():
        best = max(group, key=lambda candidate: row_rank(candidate["source"]))
        chosen_by_id[best["source_id"]] = best
    return order_for_workbench(list(chosen_by_id.values()))


def cluster_set_review_fingerprint(batch: str, definition: dict, members: list[dict]) -> str:
    payload = {
        "batch": batch,
        "cluster_id": str(definition["id"]),
        "title": clean(definition.get("title")),
        "stance": clean(definition.get("stance")),
        "members": sorted(
            (
                item["source_id"],
                clean(item.get("window", {}).get("text")),
                item.get("window", {}).get("positions", []),
            )
            for item in members
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def post_excerpt_count_fingerprint(batch: str, clusters: list[dict]) -> str:
    payload = {
        "batch": batch,
        "clusters": [
            {
                "id": str(cluster.get("id")),
                "title": clean(cluster.get("title")),
                "stance": clean(cluster.get("stance")),
                "view_ids": sorted(clean(item.get("viewId")) for item in cluster.get("items", [])),
            }
            for cluster in clusters
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def post_excerpt_count_review_map(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("scope") != "current_period_post_excerpt_cluster_count_reviews":
        raise ValueError("post_excerpt_count_reviews scope 错误")
    reviews = payload.get("reviews", {})
    if not isinstance(reviews, dict):
        raise ValueError("post_excerpt_count_reviews.reviews 必须是对象")
    return {clean(key): value for key, value in reviews.items() if isinstance(value, dict)}


def post_excerpt_count_review_issues(batch: str, clusters: list[dict], review: dict | None) -> tuple[list[str], dict]:
    cluster_count = len(clusters)
    fingerprint = post_excerpt_count_fingerprint(batch, clusters)
    range_status = "within_range" if 9 <= cluster_count <= 16 else "below_range" if cluster_count < 9 else "above_range"
    audit = {"fingerprint": fingerprint, "cluster_count": cluster_count, "range_status": range_status}
    if review is None:
        return ["missing_review"], audit
    issues = []
    if clean(review.get("fingerprint")) != fingerprint:
        issues.append("fingerprint_stale")
    if clean(review.get("decision")) != "pass":
        issues.append("decision_not_pass")
    declared_issues = review.get("issues")
    if not isinstance(declared_issues, list):
        issues.append("issues_must_be_list")
    elif declared_issues:
        issues.append("review_declares_unresolved_issues")
    if len(normalized(review.get("reason"))) < 12:
        issues.append("reason_missing_or_too_short")
    if range_status != "within_range":
        if len(normalized(review.get("exception_reason"))) < 12:
            issues.append("exception_reason_missing_or_too_short")
    return issues, audit


def cluster_set_review_map(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("scope") != "current_period_cluster_set_reviews":
        raise ValueError("cluster_set_reviews scope 错误")
    reviews = payload.get("reviews")
    if not isinstance(reviews, dict):
        raise ValueError("cluster_set_reviews 缺少 reviews 对象")
    return {str(key).strip(): value for key, value in reviews.items() if isinstance(value, dict)}


def cluster_count_review_map(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("scope") != "current_period_cluster_set_reviews":
        raise ValueError("cluster_set_reviews scope 错误")
    reviews = payload.get("count_reviews", {})
    if not isinstance(reviews, dict):
        raise ValueError("cluster_set_reviews 的 count_reviews 必须为对象")
    return {str(key).strip(): value for key, value in reviews.items() if isinstance(value, dict)}


def cluster_count_review_fingerprint(batch: str, definitions: list[dict], grouped: dict) -> str:
    payload = {
        "batch": batch,
        "clusters": [
            {
                "cluster_id": str(definition["id"]),
                "cluster_title": clean(definition.get("title")),
                "stance": clean(definition.get("stance")),
                "member_ids": sorted(
                    item["source_id"]
                    for item in grouped.get((batch, str(definition["id"])), [])
                ),
            }
            for definition in definitions
            if grouped.get((batch, str(definition["id"])), [])
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_cluster_set_review(
    batch: str,
    definition: dict,
    members: list[dict],
    review: dict | None,
    content_mode: str,
) -> tuple[list[str], dict]:
    cluster_id = str(definition["id"])
    fingerprint = cluster_set_review_fingerprint(batch, definition, members)
    member_ids = {item["source_id"] for item in members}
    issues = []
    review = review or {}
    if clean(review.get("fingerprint")) != fingerprint:
        issues.append("review_fingerprint_missing_or_stale")
    if clean(review.get("decision")) != "pass":
        issues.append("review_decision_not_pass")
    declared_issues = review.get("issues")
    if not isinstance(declared_issues, list):
        issues.append("issues_must_be_list")
    elif declared_issues:
        issues.append("review_declares_unresolved_issues")
    if not clean(review.get("reason")):
        issues.append("reason_missing")

    report_role = clean(review.get("report_role"))
    if report_role not in ALLOWED_CLUSTER_REPORT_ROLES:
        issues.append("report_role_invalid")
    if clean(definition.get("title")).startswith("记录") and report_role != "data_note":
        issues.append("record_title_requires_data_note_role")
    if cluster_is_schedule_note(definition) and report_role != "data_note":
        issues.append("schedule_or_airing_fact_requires_data_note_role")
    if report_role == "data_note" and clean(definition.get("stance")) != "objective":
        issues.append("data_note_requires_objective_stance")
    if any(
        key in review
        for key in (
            "group_id", "group_title", "group_title_claims_passed", "group_title_claims",
            "direction_id", "direction_title", "direction_title_claims_passed", "direction_title_claims",
        )
    ):
        issues.append("legacy_hierarchy_fields_not_allowed")
    objective_subtype = clean(review.get("objective_subtype"))
    if clean(definition.get("stance")) == "objective":
        if objective_subtype not in ALLOWED_OBJECTIVE_SUBTYPES:
            issues.append("objective_subtype_invalid")

    scope_type = clean(review.get("scope_type"))
    allowed_scopes = VARIETY_CLUSTER_SCOPES if content_mode == "episodic_variety" else DRAMA_CLUSTER_SCOPES
    if scope_type not in allowed_scopes:
        issues.append("scope_type_invalid")

    enriched = {
        "fingerprint": fingerprint,
        "report_role": report_role,
        "objective_subtype": objective_subtype,
        "scope_type": scope_type,
        "review_reason": clean(review.get("reason")),
    }
    return issues, enriched


def validate_cluster_count_review(
    batch: str,
    definitions: list[dict],
    grouped: dict,
    review: dict | None,
) -> tuple[list[str], dict]:
    active = [
        definition
        for definition in definitions
        if grouped.get((batch, str(definition["id"])), [])
    ]
    fingerprint = cluster_count_review_fingerprint(batch, active, grouped)
    issues: list[str] = []
    cluster_count = len(active)
    expected_range_status = (
        "within_range" if 9 <= cluster_count <= 16
        else "below_range" if cluster_count < 9
        else "above_range"
    )
    review = review or {}
    if clean(review.get("fingerprint")) != fingerprint:
        issues.append("review_fingerprint_missing_or_stale")
    if clean(review.get("decision")) != "pass":
        issues.append("review_decision_not_pass")
    declared_issues = review.get("issues")
    if not isinstance(declared_issues, list):
        issues.append("issues_must_be_list")
    elif declared_issues:
        issues.append("review_declares_unresolved_issues")
    reason = clean(review.get("reason"))
    if len(normalized(reason)) < 8:
        issues.append("reason_missing_or_too_short")
    exception_required = expected_range_status != "within_range"
    if exception_required and len(normalized(clean(review.get("exception_reason")))) < 12:
        issues.append("exception_reason_missing_or_too_short")

    return issues, {
        "fingerprint": fingerprint,
        "cluster_count": cluster_count,
        "range_status": expected_range_status,
        "reason": reason,
        "exception_approved": exception_required,
    }


def command_cluster(args: argparse.Namespace) -> None:
    run_dir = args.run.resolve()
    sources = load_jsonl(run_dir / "retained_sources.jsonl")
    config = load_json(run_dir / "period_config.json")
    definitions = validate_clusters(
        load_json(args.clusters.resolve()),
        {row["batch"] for row in sources},
        config,
    )
    overrides = flatten_overrides(args.overrides)
    set_reviews = cluster_set_review_map(args.set_reviews)
    count_reviews = cluster_count_review_map(args.set_reviews)
    by_id = {batch: {str(item["id"]): item for item in values} for batch, values in definitions.items()}
    routing_cache_path = run_dir / "cluster_routing_cache.jsonl"
    routing_cache = cluster_routing_cache_rows(routing_cache_path)
    batch_contracts = {
        batch: compact_payload_fingerprint({
            "definitions": values,
            "target": config["targets"][batch],
        })
        for batch, values in definitions.items()
    }
    updated_routing_cache = []
    routing_cache_hits = 0
    routing_cache_misses = 0
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    low_confidence = []
    assignment_audit = []
    cluster_exclusions = []
    for row in sources:
        batch_defs = definitions[row["batch"]]
        target_config = config["targets"][row["batch"]]
        row_fingerprint = compact_payload_fingerprint({
            "batch": row["batch"],
            "title": row.get("title", ""),
            "source_text": source_text(row),
        })
        cached = routing_cache.get(row["id"], {})
        cached_scores = cached.get("scores") if (
            clean(cached.get("row_fingerprint")) == row_fingerprint
            and clean(cached.get("batch_contract")) == batch_contracts[row["batch"]]
            and isinstance(cached.get("scores"), list)
        ) else None
        scores = []
        if cached_scores:
            try:
                scores = [
                    (
                        float(item["score"]),
                        by_id[row["batch"]][str(item["cluster_id"])],
                        item["window"],
                    )
                    for item in cached_scores
                ]
                routing_cache_hits += 1
            except (KeyError, TypeError, ValueError):
                scores = []
        if not scores:
            for definition in batch_defs:
                window = best_window(row, definition, target_config=target_config)
                scores.append((window["score"], definition, window))
            routing_cache_misses += 1
        scores.sort(key=lambda item: item[0], reverse=True)
        updated_routing_cache.append({
            "cache_version": CLUSTER_ROUTING_CACHE_VERSION,
            "source_id": row["id"],
            "row_fingerprint": row_fingerprint,
            "batch_contract": batch_contracts[row["batch"]],
            "scores": [
                {"cluster_id": str(definition["id"]), "score": score, "window": window}
                for score, definition, window in scores
            ],
        })
        override = overrides.get(row["id"], {})
        if override and not clean(override.get("reason")):
            raise ValueError(f"归簇修正缺少 reason：{row['id']}")
        primary_id = clean(override.get("cluster")) or str(scores[0][1]["id"])
        if primary_id == "__exclude__":
            reason_code = clean(override.get("exclude_reason_code"))
            evidence = clean(override.get("exclusion_evidence") or override.get("evidence"))
            if reason_code not in ALLOWED_CLUSTER_EXCLUSION_REASONS:
                raise ValueError(
                    f"归簇排除缺少合法 exclude_reason_code：{row['id']}；"
                    f"允许值={sorted(ALLOWED_CLUSTER_EXCLUSION_REASONS)}"
                )
            if not evidence or evidence not in source_text(row):
                raise ValueError(f"归簇排除必须提供本来源全文中的逐字 exclusion_evidence：{row['id']}")
            exclusion = {
                "source_id": row["id"],
                "batch": row["batch"],
                "reason_code": reason_code,
                "reason": clean(override.get("reason")),
                "evidence": evidence,
                "source_decision": row.get("decision", ""),
            }
            cluster_exclusions.append(exclusion)
            assignment_audit.append({
                "source_id": row["id"], "batch": row["batch"],
                "cluster_id": "__exclude__", "basis": "ai_reviewed_display_exclusion",
                "reason_code": reason_code,
            })
            continue
        if primary_id not in by_id[row["batch"]]:
            raise ValueError(f"未知簇修正：{row['id']} -> {primary_id}")
        primary_def = by_id[row["batch"]][primary_id]
        cached_primary_window = next(
            (window for _score, definition, window in scores if str(definition["id"]) == primary_id),
            None,
        )
        primary_window = (
            cached_primary_window
            if not override
            else reviewed_window(row, primary_def, override, target_config)
        )
        primary_alignment = passage_alignment(
            row, primary_window, primary_def, config["targets"][row["batch"]], override
        )
        second_score = next((score for score, definition, _ in scores if str(definition["id"]) != primary_id), 0.0)
        margin = primary_window["score"] - second_score
        required_negative = [clean(term) for term in primary_def.get("negative_cues", []) if clean(term)]
        issue = alignment_issue(primary_alignment)
        if not issue and primary_window["score"] <= 0:
            issue = "no_positive_cluster_evidence"
        elif (
            not issue
            and required_negative
            and not clean(override.get("passage_stance"))
            and not any(term in primary_window["text"] for term in required_negative)
        ):
            issue = "negative_cluster_missing_negative_cue"
        elif not issue and not override and primary_window["score"] < 5:
            issue = "low_cluster_evidence"
        # A small margin means two nearby viewpoints are both plausible.  The
        # deterministic winner is safe to keep and the representative
        # cluster-set review can still refine an over-broad definition.  Do
        # not turn this ordinary ambiguity into hundreds of item-level tasks.
        assignment = {
            "source_id": row["id"], "batch": row["batch"], "cluster_id": primary_id,
            "window": primary_window, "score": primary_window["score"], "margin": round(margin, 3),
            "source": row, "basis": "ai_override" if override else "script_scoring",
            "alignment": primary_alignment,
            "passage_review": {
                key: override[key] for key in ("target_evidence", "passage_stance") if key in override
            },
        }
        grouped[(row["batch"], primary_id)].append(assignment)
        assignment_audit.append({key: assignment[key] for key in ("source_id", "batch", "cluster_id", "score", "margin", "basis", "alignment")})
        if issue:
            top_candidates = [
                {
                    "cluster": str(definition["id"]),
                    "title": definition["title"],
                    "score": score,
                    "passage": clean(candidate_window.get("text")),
                    "required_terms": effective_required_terms(definition, target_config),
                    "target_hits": candidate_window.get("strong_target_hits", []) + candidate_window.get("weak_target_hits", []),
                }
                for score, definition, candidate_window in scores[:3]
            ]
            low_confidence.append({
                "source_id": row["id"], "batch": row["batch"], "provisional_cluster": primary_id,
                "issue": issue, "issue_guidance": CLUSTER_ASSIGNMENT_ISSUE_GUIDANCE.get(issue, "阅读全文后修正归簇并提供逐字证据"),
                "current_passage": clean(primary_window.get("text")), "current_alignment": primary_alignment,
                "override_applied": bool(override), "title": row["title"],
                "target_evidence_candidates": target_evidence_candidates(row, target_config),
                "top_cluster_candidates": top_candidates,
                "full_source_lookup": {"file": "retained_sources.jsonl", "source_id": row["id"]},
            })
        secondary_id = clean(override.get("secondary_cluster"))
        if secondary_id:
            if secondary_id == primary_id or secondary_id not in by_id[row["batch"]]:
                raise ValueError(f"第二簇修正不合法：{row['id']} -> {secondary_id}")
            secondary_def = by_id[row["batch"]][secondary_id]
            secondary_window = reviewed_window(row, secondary_def, override, target_config, secondary=True)
            if windows_overlap(primary_window, secondary_window):
                raise ValueError(f"第二观点片段与主观点重叠：{row['id']}")
            secondary_alignment = passage_alignment(
                row, secondary_window, secondary_def, config["targets"][row["batch"]], override, secondary=True
            )
            secondary_review = {}
            if "secondary_target_evidence" in override:
                secondary_review["target_evidence"] = override["secondary_target_evidence"]
            if "secondary_passage_stance" in override:
                secondary_review["passage_stance"] = override["secondary_passage_stance"]
            second = {
                "source_id": row["id"], "batch": row["batch"], "cluster_id": secondary_id,
                "window": secondary_window, "score": secondary_window["score"], "margin": 0.0,
                "source": row, "basis": "ai_secondary_override", "alignment": secondary_alignment,
                "passage_review": secondary_review,
            }
            grouped[(row["batch"], secondary_id)].append(second)
            assignment_audit.append({key: second[key] for key in ("source_id", "batch", "cluster_id", "score", "margin", "basis", "alignment")})
            secondary_issue = alignment_issue(secondary_alignment)
            if secondary_issue:
                low_confidence.append({
                    "source_id": row["id"], "batch": row["batch"], "provisional_cluster": secondary_id,
                    "issue": secondary_issue, "issue_guidance": CLUSTER_ASSIGNMENT_ISSUE_GUIDANCE.get(secondary_issue, "阅读全文后修正第二归簇并提供逐字证据"),
                    "current_passage": clean(secondary_window.get("text")), "current_alignment": secondary_alignment,
                    "override_applied": True, "title": row["title"],
                    "target_evidence_candidates": target_evidence_candidates(row, target_config),
                    "full_source_lookup": {"file": "retained_sources.jsonl", "source_id": row["id"]},
                })

    set_review_input = []
    set_review_queue = []
    for batch, batch_defs in definitions.items():
        for definition in batch_defs:
            members = grouped.get((batch, str(definition["id"])), [])
            if not members:
                continue
            size = len({item["source_id"] for item in members})
            review_key = f"{batch}\t{definition['id']}"
            fingerprint = cluster_set_review_fingerprint(batch, definition, members)
            author_counts = Counter(clean(item["source"].get("author")) or "未知作者" for item in members)
            scope_counts = Counter(clean(item["source"].get("episode_scope")) or "未标注" for item in members)
            representative_members = representative_cluster_members(members)
            batch_size = len({
                item["source_id"]
                for batch_definition in batch_defs
                for item in grouped.get((batch, str(batch_definition["id"])), [])
            })
            member_share = round(size / batch_size, 4) if batch_size else 0
            warnings = []
            if size >= 80 and member_share >= 0.25:
                warnings.append("dominant_cluster_check_for_catch_all")
            if cluster_is_schedule_note(definition):
                warnings.append("schedule_or_airing_background_must_use_data_note")
            input_item = {
                "review_key": review_key,
                "fingerprint": fingerprint,
                "batch": batch,
                "cluster_id": str(definition["id"]),
                "title": definition["title"],
                "stance": definition["stance"],
                "content_mode": clean(config["targets"][batch].get("content_mode")) or "serial_drama",
                "independent_sources": size,
                "member_share": member_share,
                "warnings": warnings,
                "representative_sample_count": len(representative_members),
                "unique_authors": len(author_counts),
                "largest_author_share": round(max(author_counts.values()) / size, 4) if size else 0,
                "episode_scope_counts": dict(scope_counts),
                "samples": [
                    {
                        "source_id": item["source_id"],
                        "channel": item["source"].get("channel", ""),
                        "author": item["source"].get("author", ""),
                        "published": item["source"].get("published", ""),
                        "episode_scope": item["source"].get("episode_scope", ""),
                        "passage": clean(item["window"].get("text")),
                        "passage_fragments": item["window"].get("fragments", []),
                        "passage_positions": item["window"].get("positions", []),
                    }
                    for item in representative_members
                ],
                "instruction": "只做簇级标题、立场、范围和颗粒度检查；给出通过或返修结论及一句理由，不抄写样本和证据。逐样本对齐由最终摘录语义复核统一完成。",
            }
            set_review_input.append(input_item)
            issues, enriched = validate_cluster_set_review(
                batch,
                definition,
                members,
                set_reviews.get(review_key),
                input_item["content_mode"],
            )
            if issues:
                set_review_queue.append({**input_item, "issues": issues})
            else:
                definition["report_role"] = enriched["report_role"]
                definition["objective_subtype"] = enriched["objective_subtype"]
                definition["scope_type"] = enriched["scope_type"]
                definition["cluster_set_review_fingerprint"] = enriched["fingerprint"]
                definition["cluster_set_review_reason"] = enriched["review_reason"]
                definition["rare_signal"] = enriched["report_role"] == "rare_signal"
                if definition["rare_signal"]:
                    definition["rare_signal_reason"] = enriched["review_reason"]


    count_review_input = []
    count_review_queue = []
    count_review_audit = {}
    for batch, batch_defs in definitions.items():
        issues, audit = validate_cluster_count_review(
            batch,
            batch_defs,
            grouped,
            count_reviews.get(batch),
        )
        input_item = {
            "review_key": batch,
            "fingerprint": audit["fingerprint"],
            "batch": batch,
            "cluster_count": audit["cluster_count"],
            "normal_review_range": {"min": 9, "max": 16},
            "expected_range_status": audit["range_status"],
            "clusters": [
                {
                    "id": str(definition["id"]),
                    "title": clean(definition.get("title")),
                    "stance": clean(definition.get("stance")),
                    "member_count": len(grouped.get((batch, str(definition["id"])), [])),
                }
                for definition in batch_defs
                if grouped.get((batch, str(definition["id"])), [])
            ],
            "instruction": "9—16仅触发簇数质量复查；只在全部成员支持同一命题时重组，不得删样、造点或强并。",
        }
        count_review_input.append(input_item)
        count_review_audit[batch] = {
            **audit,
            "status": "PASS" if not issues else "REVIEW_REQUIRED",
            "issues": issues,
        }
        if issues:
            count_review_queue.append({**input_item, "issues": issues})

    selected = []
    order_audit = []
    cluster_members = {}
    for batch, batch_defs in definitions.items():
        for definition in batch_defs:
            key = (batch, str(definition["id"]))
            members = grouped.get(key, [])
            if not members:
                continue
            chosen_pool = (
                deduplicate_data_note_members(members)
                if definition.get("report_role") == "data_note" and cluster_is_schedule_note(definition)
                else members
            )
            chosen = order_for_workbench(chosen_pool)
            selected.extend(chosen)
            order_audit.append({
                "batch": batch,
                "cluster_id": str(definition["id"]),
                "policy": [
                    "media_authority_rank_desc",
                    "channel_priority_desc",
                    "retain_core_before_retain_consensus",
                    "quality_desc",
                    "normalized_source_text_length_desc",
                    "stable_source_id_desc",
                ],
                "ordered_sources": [
                    {
                        "position": index,
                        "source_id": item["source_id"],
                        "media_authority_rank": int(item["source"].get("media_authority_rank", 0)),
                        "channel": item["source"].get("channel", ""),
                        "channel_priority": CHANNEL_PRIORITY.get(item["source"].get("channel", ""), 0),
                        "decision": item["source"].get("decision", ""),
                        "quality": float(item["source"].get("quality", 0)),
                        "normalized_source_text_length": len(normalized(source_text(item["source"]))),
                    }
                    for index, item in enumerate(chosen, start=1)
                ],
            })
            cluster_members[f"{batch}\t{definition['id']}"] = {
                "aligned_sources": len(members),
                "workbench_sources": len(chosen),
            }
    selected_keys = {(item["source_id"], item["cluster_id"]) for item in selected}
    low_confidence = [
        item for item in low_confidence
        if (item["source_id"], item["provisional_cluster"]) in selected_keys
    ]
    write_jsonl(routing_cache_path, updated_routing_cache)
    write_json(run_dir / "cluster_routing_cache_audit.json", {
        "cache_version": CLUSTER_ROUTING_CACHE_VERSION,
        "sources": len(sources),
        "hits": routing_cache_hits,
        "misses": routing_cache_misses,
        "hit_rate": round(routing_cache_hits / len(sources), 4) if sources else 0.0,
    })
    write_json(run_dir / "clustered_items.json", {"definitions": definitions, "items": selected})
    write_json(run_dir / "workbench_order_audit.json", {"random": False, "clusters": order_audit})
    write_json(run_dir / "cluster_assignment_audit.json", {"assignments": assignment_audit, "cluster_members": cluster_members})
    write_jsonl(run_dir / "cluster_review_queue.jsonl", low_confidence)
    write_jsonl(run_dir / "cluster_exclusions.jsonl", cluster_exclusions)
    write_json(run_dir / "cluster_set_review_input.json", {"scope": "current_period_cluster_set_review_input", "clusters": set_review_input})
    write_jsonl(run_dir / "cluster_set_review_queue.unresolved.jsonl", set_review_queue)
    write_json(run_dir / "cluster_count_review_input.json", {"scope": "current_period_cluster_count_review_input", "batches": count_review_input})
    write_jsonl(run_dir / "cluster_count_review_queue.unresolved.jsonl", count_review_queue)
    write_json(run_dir / "cluster_count_review_audit.json", count_review_audit)
    summary = {"status": "CLUSTERED", "retained_sources": len(sources), "assignments": len(assignment_audit), "display_exclusions": len(cluster_exclusions), "workbench_items": len(selected), "unique_workbench_sources": len({item["source_id"] for item in selected}), "low_confidence_unresolved": len(low_confidence), "cluster_set_review_unresolved": len(set_review_queue), "cluster_count_review_unresolved": len(count_review_queue), "routing_cache_hits": routing_cache_hits, "routing_cache_misses": routing_cache_misses, "batches": dict(Counter(item["batch"] for item in selected))}
    write_json(run_dir / "cluster_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def excerpt_candidates(body: str, window: dict, definition: dict) -> list[dict]:
    spans = sentence_spans(body)
    window_positions = window.get("positions") or [[window["start"], window["end"]]]
    anchored = [
        index for index, span in enumerate(spans)
        if any(span["start"] < end and span["end"] > start for start, end in window_positions)
    ]
    if not anchored and spans:
        anchored = [min(range(len(spans)), key=lambda index: abs(spans[index]["start"] - window["start"]))]
    indexes = sorted(set(index for anchor in anchored for index in (anchor - 1, anchor, anchor + 1) if 0 <= index < len(spans)))
    candidates = []
    if window.get("reviewed_fragments"):
        reviewed_fragments = [str(value) for value in window.get("fragments", [])]
        reviewed_positions = window.get("positions", [])
        reviewed_excerpt = " ".join(fragment.strip() for fragment in reviewed_fragments)
        reviewed_display_length = len(clean_display_excerpt(reviewed_excerpt))
        if (
            1 <= len(reviewed_fragments) <= 2
            and len(normalized(reviewed_excerpt)) >= 18
            and reviewed_display_length <= EXCERPT_MAX
            and not LEADING_FRAGMENT.search(reviewed_excerpt)
        ):
            validate_fragment_positions(body, reviewed_fragments, reviewed_positions)
            cluster_value, _ = cluster_score(reviewed_excerpt, "", definition)
            candidates.append({
                "fragments": reviewed_fragments,
                "positions": reviewed_positions,
                "excerpt": reviewed_excerpt,
                "score": round(cluster_value, 3),
                "reviewed_passage": True,
                "display_length": reviewed_display_length,
                "display_operational_promotion": bool(display_operational_promotion_markers(reviewed_excerpt)),
                "protect_reviewed_selection": bool(
                    len(reviewed_positions) == 2 and reviewed_positions[0][1] < reviewed_positions[1][0]
                ),
            })
    for start in indexes:
        for end in range(start, min(len(spans), start + 3)):
            if not any(index in anchored for index in range(start, end + 1)):
                continue
            if end - start + 1 > 2:
                raw = [body[spans[start]["start"]:spans[end]["end"]]]
                raw_positions = [[spans[start]["start"], spans[end]["end"]]]
            else:
                raw = [body[spans[index]["start"]:spans[index]["end"]] for index in range(start, end + 1)]
                raw_positions = [[spans[index]["start"], spans[index]["end"]] for index in range(start, end + 1)]
            visible = " ".join(part.strip() for part in raw)
            display_length = len(clean_display_excerpt(visible))
            if len(normalized(visible)) < 18 or display_length > EXCERPT_MAX or LEADING_FRAGMENT.search(visible):
                continue
            cluster_value, _ = cluster_score(visible, "", definition)
            voice = len(JUDGEMENT.findall(visible)) * 2.2 + len(OPINION.findall(visible)) * 0.6
            length_bonus = min(display_length, EXCERPT_PREFERRED_MIN) / 20
            if display_length < EXCERPT_PREFERRED_MIN:
                length_bonus -= (EXCERPT_PREFERRED_MIN - display_length) / 12
            candidates.append({
                "fragments": raw,
                "positions": raw_positions,
                "excerpt": visible,
                "score": round(cluster_value * 1.4 + voice + length_bonus, 3),
                "display_length": display_length,
                "display_operational_promotion": bool(display_operational_promotion_markers(visible)),
            })
    if not candidates:
        raw = body[window["start"]:window["end"]]
        if len(raw) > EXCERPT_MAX:
            cut = max((raw.rfind(mark, EXCERPT_PREFERRED_MIN, EXCERPT_MAX + 1) + 1 for mark in "。！？；!?;"), default=0)
            raw = raw[:cut or EXCERPT_MAX]
        return [{"fragments": [raw], "positions": [[window["start"], window["start"] + len(raw)]], "excerpt": raw.strip(), "score": 0.0}]
    candidates.sort(key=lambda item: (
        item.get("display_operational_promotion", False),
        not item.get("protect_reviewed_selection", False),
        int(item.get("display_length", 0) < EXCERPT_PREFERRED_MIN),
        not item.get("reviewed_passage", False),
        -item["score"],
        item["positions"][0][0],
    ))
    return candidates


def choose_valid_display_candidate(
    body: str,
    window: dict,
    definition: dict,
    review: dict | None,
) -> dict:
    """Prefer a valid reviewed excerpt, then fall back through script candidates."""
    candidates = []
    errors = []
    if review:
        try:
            fragments = [str(value) for value in review.get("fragments", [])]
            supplied_positions = review.get("positions")
            positions = (
                validate_fragment_positions(body, fragments, supplied_positions)
                if supplied_positions else locate_fragments(body, fragments)
            )
            candidates.append({
                "fragments": fragments,
                "positions": positions,
                "excerpt": " ".join(fragment.strip() for fragment in fragments),
                "basis": "ai_verbatim_review",
            })
        except ValueError as exc:
            errors.append(str(exc))
    for candidate in excerpt_candidates(body, window, definition):
        candidates.append({**candidate, "basis": "deterministic_extractive_candidate"})
    seen = set()
    for candidate in candidates:
        try:
            fragments = [str(value) for value in candidate.get("fragments", [])]
            positions = validate_fragment_positions(body, fragments, candidate.get("positions", []))
            raw_excerpt = " ".join(fragment.strip() for fragment in fragments)
            key = tuple((position[0], position[1]) for position in positions)
            if key in seen:
                continue
            seen.add(key)
            excerpt = clean_display_excerpt(raw_excerpt)
            if not excerpt:
                raise ValueError("展示片段清除话题标签、平台表情、链接和账号标记后为空")
            if display_excerpt_has_markup(excerpt):
                raise ValueError("展示片段清理后仍含话题标签、平台表情、链接或账号标记")
            if len(excerpt) > EXCERPT_MAX:
                raise ValueError(f"展示片段超过 {EXCERPT_MAX} 字")
            if excerpt.startswith(BAD_EXCERPT_START) or excerpt.endswith(BAD_EXCERPT_END):
                raise ValueError("展示片段边界不完整")
            if unbalanced_display_quotes(excerpt):
                raise ValueError("展示片段引号不成对")
            return {
                "fragments": fragments,
                "positions": positions,
                "raw_excerpt": raw_excerpt,
                "excerpt": excerpt,
                "basis": candidate.get("basis", "deterministic_extractive_candidate"),
                "fallback_from_invalid_review": bool(review) and candidate.get("basis") != "ai_verbatim_review",
            }
        except (ValueError, IndexError) as exc:
            errors.append(str(exc))
    raise ValueError("；".join(dict.fromkeys(errors)) or "没有可用的逐字展示片段")


def locate_fragments(body: str, fragments: list[str]) -> list[list[int]]:
    if not 1 <= len(fragments) <= 2:
        raise ValueError("展示片段必须为一至两段")
    positions, cursor = [], 0
    for fragment in fragments:
        if not fragment:
            raise ValueError("展示片段不能为空")
        start = body.find(fragment, cursor)
        if start < 0:
            raise ValueError("展示片段无法在原文中按顺序定位")
        end = start + len(fragment)
        positions.append([start, end])
        cursor = end
    return positions


def validate_fragment_positions(body: str, fragments: list[str], positions: list[list[int]]) -> list[list[int]]:
    if len(fragments) != len(positions) or not 1 <= len(fragments) <= 2:
        raise ValueError("展示片段与位置数量不一致")
    previous_end = 0
    normalized_positions = []
    for fragment, position in zip(fragments, positions):
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError("展示片段位置必须是 [start, end]")
        start, end = int(position[0]), int(position[1])
        if start < previous_end or start < 0 or end < start or end > len(body):
            raise ValueError("展示片段位置越界、乱序或重叠")
        if body[start:end] != fragment:
            raise ValueError("展示片段与指定原文位置不一致")
        normalized_positions.append([start, end])
        previous_end = end
    return normalized_positions


def fragments_extract_hashtag_interior(body: str, positions: list[list[int]]) -> bool:
    """Reject evidence created by stripping the # delimiters off a hashtag.

    Full source spans may contain a hashtag because display normalization removes
    it.  What is forbidden is selecting only the text inside ``#...#`` so it
    survives as apparently ordinary prose.
    """
    hashtag_spans = [(match.start(), match.end()) for match in DISPLAY_HASHTAG_PAIR.finditer(body)]
    hashtag_spans.extend(
        (match.start(), match.end())
        for match in DISPLAY_ADJACENT_TRAILING_TAGS.finditer(body)
    )
    return any(
        (tag_start < start < tag_end)
        or (tag_start < end < tag_end)
        for start, end in positions
        for tag_start, tag_end in hashtag_spans
    )


def render_html(dataset: dict) -> str:
    """Render the verified dataset in the established film-workbench visual shell."""
    data_json = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template_path = Path(__file__).resolve().parent.parent / "templates" / "workbench.html"
    template = template_path.read_text(encoding="utf-8")
    placeholder = "@@FILM_BRIEF_DATA@@"
    if template.count(placeholder) != 1:
        raise ValueError(f"工作台模板数据占位符异常：{template_path}")
    return template.replace(placeholder, data_json)

def dataset_from_html(path: Path) -> tuple[dict, str]:
    """Read the embedded dataset from a workbench generated by this pipeline."""
    page = path.read_text(encoding="utf-8")
    marker = "const DATA="
    start = page.find(marker)
    if start < 0:
        raise ValueError(f"工作台缺少 DATA 数据块：{path}")
    start += len(marker)
    try:
        dataset, consumed = json.JSONDecoder().raw_decode(page[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"工作台 DATA 数据块无法解析：{path}：{exc}") from exc
    suffix = page[start + consumed:].lstrip()
    if not (suffix.startswith(",STANCES=") or re.match(r"^;\s*const\s+STANCES=", suffix)):
        raise ValueError(f"工作台 DATA 数据块边界异常：{path}")
    return dataset, page


def html_with_dataset(page: str, dataset: dict) -> str:
    marker = "const DATA="
    start = page.find(marker)
    if start < 0:
        raise ValueError("基础工作台缺少 DATA 数据块")
    start += len(marker)
    try:
        _, consumed = json.JSONDecoder().raw_decode(page[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"基础工作台 DATA 数据块无法解析：{exc}") from exc
    data_json = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return page[:start] + data_json + page[start + consumed:]


def embedded_schema_counts(dataset: dict) -> dict[str, int]:
    counts = {"current": 0, "legacy_reviewed": 0, "legacy_aligned": 0, "unknown": 0}
    for batch in dataset.get("batches", []):
        for cluster in batch.get("clusters", []):
            for item in cluster.get("items", []):
                if all(isinstance(item.get(key), dict) for key in ("semanticReview", "excerptProvenance", "alignment")):
                    counts["current"] += 1
                elif isinstance(item.get("semanticExcerptExperiment"), dict) and isinstance(item.get("alignmentReview"), dict):
                    counts["legacy_reviewed"] += 1
                elif isinstance(item.get("excerptProvenance"), dict) and isinstance(item.get("alignment"), dict):
                    counts["legacy_aligned"] += 1
                else:
                    counts["unknown"] += 1
    return counts


def validate_embedded_dataset(dataset: dict, source: Path, *, allow_legacy: bool = False) -> list[str]:
    """Validate embedded workbench data, with explicit compatibility for a legacy base."""
    failures: list[str] = []
    batches = dataset.get("batches")
    if not isinstance(batches, list) or not batches:
        return [f"工作台没有批次：{source}"]
    batch_names: list[str] = []
    view_ids: list[str] = []
    calculated_total = 0
    for batch in batches:
        name = clean(batch.get("name"))
        batch_names.append(name)
        clusters = batch.get("clusters")
        if not name or not isinstance(clusters, list) or not clusters:
            failures.append(f"批次名称或观点簇为空：{source} / {name or '未命名'}")
            continue
        if int(batch.get("clusterCount", -1)) != len(clusters):
            failures.append(f"观点簇计数不一致：{source} / {name}")
        cluster_ids: list[str] = []
        group_contracts: dict[str, tuple[str, str]] = {}
        direction_contracts: dict[str, tuple[str, str]] = {}
        clusters_with_directions = 0
        batch_count = 0
        for cluster in clusters:
            cluster_id = clean(cluster.get("id"))
            cluster_ids.append(cluster_id)
            cluster_title = clean(cluster.get("title"))
            stance_value = clean(cluster.get("stance"))
            report_role = clean(cluster.get("reportRole"))
            direction_id = clean(cluster.get("directionId"))
            direction_title = clean(cluster.get("directionTitle"))
            hierarchy_present = any(
                clean(cluster.get(key))
                for key in ("groupId", "groupTitle", "directionId", "directionTitle")
            )
            if not allow_legacy and hierarchy_present:
                failures.append(f"当前工作台不得包含父子观点层级：{source} / {name} / {cluster_id}")
            if allow_legacy and (direction_id or direction_title):
                clusters_with_directions += 1
                if not direction_id or not direction_title:
                    failures.append(f"观点方向ID或标题缺失：{source} / {name} / {cluster_id}")
                elif len(normalized(direction_title)) < 12 or not CLUSTER_TITLE_PREDICATE.search(direction_title):
                    failures.append(f"观点方向不是完整报告体观点句：{source} / {name} / {cluster_id}")
                else:
                    contract = (direction_title, stance_value)
                    previous_contract = direction_contracts.setdefault(direction_id, contract)
                    if previous_contract != contract:
                        failures.append(f"同一观点方向标题或立场不一致：{source} / {name} / {direction_id}")
            minimum_title_length = 10 if report_role in {"data_note", "rare_signal"} else 16
            items = cluster.get("items")
            if not cluster_title or GENERIC_CLUSTER_TITLE.search(cluster_title):
                failures.append(f"观点簇标题为空或为兜底簇：{source} / {name} / {cluster_id}")
            elif not allow_legacy and (
                len(normalized(cluster_title)) < minimum_title_length or not CLUSTER_TITLE_PREDICATE.search(cluster_title)
            ):
                failures.append(f"观点簇标题不是完整报告体观点句：{source} / {name} / {cluster_id}")
            if stance_value not in ALLOWED_CLUSTER_STANCES:
                failures.append(f"观点簇立场无效：{source} / {name} / {cluster_id}")
            if report_role:
                if report_role not in ALLOWED_CLUSTER_REPORT_ROLES:
                    failures.append(f"观点簇报告角色无效：{source} / {name} / {cluster_id}")
                if allow_legacy and report_role == "subtopic" and (
                    not clean(cluster.get("groupId")) or not clean(cluster.get("groupTitle"))
                ):
                    failures.append(f"子主题缺少上位观点：{source} / {name} / {cluster_id}")
                group_title = clean(cluster.get("groupTitle"))
                if allow_legacy and group_title and (
                    len(normalized(group_title)) < 12 or not CLUSTER_TITLE_PREDICATE.search(group_title)
                ):
                    failures.append(f"上位观点不是完整报告体观点句：{source} / {name} / {cluster_id}")
                group_id = clean(cluster.get("groupId"))
                if allow_legacy and group_id:
                    contract = (group_title, stance_value)
                    previous_contract = group_contracts.setdefault(group_id, contract)
                    if previous_contract[0] != group_title:
                        failures.append(f"同一上位观点标题不一致：{source} / {name} / {group_id}")
                    if previous_contract[1] != stance_value:
                        failures.append(f"同一上位观点混入不同立场：{source} / {name} / {group_id}")
                if stance_value == "objective" and clean(cluster.get("objectiveSubtype")) not in ALLOWED_OBJECTIVE_SUBTYPES:
                    failures.append(f"客观簇缺少合法客观子类型：{source} / {name} / {cluster_id}")
                if clean(cluster.get("scopeType")) not in DRAMA_CLUSTER_SCOPES | VARIETY_CLUSTER_SCOPES:
                    failures.append(f"观点簇缺少合法时间或期次范围：{source} / {name} / {cluster_id}")
            if not isinstance(items, list) or not items:
                failures.append(f"观点簇没有样本：{source} / {name} / {cluster_id}")
                continue
            if int(cluster.get("count", -1)) != len(items):
                failures.append(f"观点簇样本计数不一致：{source} / {name} / {cluster_id}")
            batch_count += len(items)
            for item in items:
                view_id = clean(item.get("viewId"))
                view_ids.append(view_id)
                if not view_id:
                    failures.append(f"样本 viewId 为空：{source} / {name} / {cluster_id}")
                item_cluster_stance = clean(item.get("clusterStance"))
                if item_cluster_stance and item_cluster_stance != stance_value:
                    failures.append(f"样本与观点簇立场不一致：{source} / {view_id}")
                if not item_cluster_stance and not allow_legacy:
                    failures.append(f"样本缺少观点簇立场：{source} / {view_id}")
                if clean(batch.get("contentMode")) == "episodic_variety":
                    item_scope = clean(item.get("episodeScope"))
                    cluster_scope = clean(cluster.get("scopeType"))
                    if not item_scope:
                        failures.append(f"综艺样本缺少原始期次范围：{source} / {view_id}")
                    elif cluster_scope and cluster_scope != "mixed_scope_explicit" and item_scope != cluster_scope:
                        failures.append(
                            f"综艺样本与观点簇期次不一致：{source} / {view_id} / "
                            f"样本={item_scope} / 观点簇={cluster_scope}"
                        )
                body = str(item.get("body") or "")
                excerpt = clean(item.get("excerpt"))
                if not body or not excerpt:
                    failures.append(f"样本正文或展示文字为空：{source} / {view_id}")
                excerpt_length = len(excerpt)
                length_review = item.get("excerptLengthReview")
                if excerpt_length > EXCERPT_MAX:
                    failures.append(f"展示文字超过{EXCERPT_MAX}字：{source} / {view_id}")
                if excerpt_length < EXCERPT_PREFERRED_MIN:
                    if not isinstance(length_review, dict):
                        failures.append(f"短摘录缺少逐条例外复核：{source} / {view_id}")
                    elif length_review.get("exception") is not True or not clean(length_review.get("reason")):
                        failures.append(f"短摘录缺少有效例外理由：{source} / {view_id}")
                    else:
                        support_evidence = clean(length_review.get("specificSupportEvidence"))
                        if length_review.get("specificSupportPassed") is not True:
                            failures.append(f"短摘录未确认含有具体依据：{source} / {view_id}")
                        if not support_evidence or support_evidence not in excerpt:
                            failures.append(f"短摘录缺少逐字具体依据：{source} / {view_id}")
                        elif not short_excerpt_support_is_specific(excerpt, support_evidence):
                            failures.append(f"短摘录具体依据仍是泛泛态度：{source} / {view_id}")
                if isinstance(length_review, dict):
                    if int(length_review.get("length", -1)) != excerpt_length:
                        failures.append(f"摘录长度记录与展示文字不一致：{source} / {view_id}")
                    if int(length_review.get("preferredMin", -1)) != EXCERPT_PREFERRED_MIN or int(length_review.get("max", -1)) != EXCERPT_MAX:
                        failures.append(f"摘录长度规则版本不一致：{source} / {view_id}")
                    if excerpt_length >= EXCERPT_PREFERRED_MIN and length_review.get("exception") is True:
                        failures.append(f"合格长度摘录被误标为短例外：{source} / {view_id}")
                semantic = item.get("semanticReview")
                provenance = item.get("excerptProvenance")
                alignment = item.get("alignment")
                if isinstance(semantic, dict) and isinstance(provenance, dict) and isinstance(alignment, dict):
                    if clean(semantic.get("decision")) != "keep" or semantic.get("self_contained") is not True:
                        failures.append(f"样本缺少已通过的最终语义复核：{source} / {view_id}")
                    if not clean(semantic.get("aspect_evidence")) or not clean(semantic.get("stance_evidence")):
                        failures.append(f"样本缺少脚本回填的最终语义证据：{source} / {view_id}")
                    if any(alignment.get(key, {}).get("passed") is not True for key in ("target", "aspect", "stance")):
                        failures.append(f"样本四项对齐未通过：{source} / {view_id}")
                    fragments = provenance.get("fragments", [])
                    positions = provenance.get("positions", [])
                    try:
                        positions = validate_fragment_positions(body, fragments, positions)
                    except ValueError as exc:
                        failures.append(f"样本原文定位失败：{source} / {view_id} / {exc}")
                        continue
                    if fragments_extract_hashtag_interior(body, positions):
                        failures.append(f"样本摘录从话题标签内部抽词：{source} / {view_id}")
                    raw_joined = clean(" ".join(str(fragment) for fragment in fragments))
                    normalization_version = clean(provenance.get("displayNormalization"))
                    if normalization_version == DISPLAY_NORMALIZATION:
                        expected_excerpt = clean_display_excerpt(raw_joined)
                    elif normalization_version == "remove_social_markup_v2":
                        expected_excerpt = clean_display_excerpt_v2(raw_joined)
                    elif normalization_version == "remove_social_markup_v1":
                        expected_excerpt = clean_display_excerpt_v1(raw_joined)
                    else:
                        expected_excerpt = raw_joined
                    if excerpt != expected_excerpt:
                        failures.append(f"展示文字与原文片段不一致：{source} / {view_id}")
                    if display_excerpt_has_markup(excerpt):
                        failures.append(f"展示文字仍含话题标签、平台表情、链接或账号标记：{source} / {view_id}")
                elif allow_legacy:
                    legacy_alignment = item.get("alignmentReview") or item.get("alignment") or {}
                    if any(legacy_alignment.get(key, {}).get("passed") is not True for key in ("target", "aspect", "stance")):
                        failures.append(f"旧版样本对齐复核未通过：{source} / {view_id}")
                    if isinstance(provenance, dict):
                        fragments = provenance.get("fragments", [])
                        positions = provenance.get("positions", [])
                        try:
                            validate_fragment_positions(body, fragments, positions)
                        except ValueError as exc:
                            failures.append(f"旧版样本原文定位失败：{source} / {view_id} / {exc}")
                            continue
                        raw_joined = clean(" ".join(str(fragment) for fragment in fragments))
                        expected_excerpt = clean_display_excerpt(raw_joined) if provenance.get("displayNormalization") == DISPLAY_NORMALIZATION else raw_joined
                        if excerpt != expected_excerpt:
                            failures.append(f"旧版展示文字与原文片段不一致：{source} / {view_id}")
                        if display_excerpt_has_markup(excerpt):
                            failures.append(f"旧版展示文字仍含话题标签、平台表情、链接或账号标记：{source} / {view_id}")
                    else:
                        legacy_excerpt = item.get("semanticExcerptExperiment") or {}
                        fragments = legacy_excerpt.get("rawFragments", [])
                        if item.get("excerptVerified") is not True or not fragments:
                            failures.append(f"旧版样本缺少原文复核证据：{source} / {view_id}")
                        elif any(str(fragment) not in body for fragment in fragments):
                            failures.append(f"旧版样本原文片段不存在于正文：{source} / {view_id}")
                else:
                    failures.append(f"输入工作台不是当前可合并数据格式：{source} / {view_id}")
                if "woa.com" in clean(item.get("url")).lower():
                    failures.append(f"工作台包含禁止的内网链接：{source} / {view_id}")
        if len(cluster_ids) != len(set(cluster_ids)):
            failures.append(f"批次内观点簇ID重复：{source} / {name}")
        if allow_legacy and clusters_with_directions not in {0, len(clusters)}:
            failures.append(f"观点方向未覆盖全部细分主题：{source} / {name}")
        expected_direction_count = len(direction_contracts) if direction_contracts else len(clusters)
        if not allow_legacy and "directionCount" in batch:
            failures.append(f"当前工作台不得包含观点方向计数：{source} / {name}")
        elif allow_legacy and "directionCount" in batch and int(batch.get("directionCount", -1)) != expected_direction_count:
            failures.append(f"观点方向计数不一致：{source} / {name}")
        if int(batch.get("count", -1)) != batch_count:
            failures.append(f"批次样本计数不一致：{source} / {name}")
        calculated_total += batch_count
    if len(batch_names) != len(set(batch_names)):
        failures.append(f"工作台批次名称重复：{source}")
    if len(view_ids) != len(set(view_ids)):
        failures.append(f"工作台 viewId 重复：{source}")
    if int(dataset.get("total", -1)) != calculated_total:
        failures.append(f"工作台总样本计数不一致：{source}")
    return failures


def command_merge(args: argparse.Namespace) -> None:
    """Merge workbench HTML files, replacing batches with the same name in place."""
    input_paths = [path.resolve() for path in args.inputs]
    output = args.output.resolve()
    if len(input_paths) < 2:
        raise ValueError("合并工作台至少需要两个输入HTML")
    merged_batches: list[dict] = []
    batch_indexes: dict[str, int] = {}
    batch_sources: dict[str, str] = {}
    input_audit = []
    failures: list[str] = []
    template_page = ""
    for path in input_paths:
        if not path.is_file():
            failures.append(f"输入工作台不存在：{path}")
            continue
        try:
            dataset, page = dataset_from_html(path)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        input_failures = validate_embedded_dataset(dataset, path, allow_legacy=(path == input_paths[0]))
        if re.search(r"<(?:script|img)[^>]+src=[\"']https?://|<link[^>]+href=[\"']https?://", page, re.I):
            input_failures.append(f"工作台包含远程加载资源：{path}")
        failures.extend(input_failures)
        input_audit.append({
            "path": str(path), "sha256": sha256(path), "batches": len(dataset.get("batches", [])),
            "items": int(dataset.get("total", 0)), "validation_failures": len(input_failures),
            "validation_mode": "legacy_base_compatible" if path == input_paths[0] else "current_only",
            "schemas": embedded_schema_counts(dataset),
        })
        if input_failures:
            continue
        if not template_page:
            template_page = page
        for batch in dataset["batches"]:
            name = clean(batch["name"])
            if name in batch_indexes:
                merged_batches[batch_indexes[name]] = batch
            else:
                batch_indexes[name] = len(merged_batches)
                merged_batches.append(batch)
            batch_sources[name] = str(path)
    report = (args.report or output.with_suffix(".merge-verification.json")).resolve()
    if failures:
        result = {"status": "FAIL", "failures": failures, "inputs": input_audit, "output_not_replaced": str(output)}
        write_json(report, result)
        console_result = dict(result)
        console_result["failures"] = failures[:20]
        console_result["failure_count"] = len(failures)
        print(json.dumps(console_result, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    merged = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "batches": merged_batches,
        "total": sum(int(batch["count"]) for batch in merged_batches),
    }
    merged_failures = validate_embedded_dataset(merged, output, allow_legacy=True)
    if merged_failures:
        raise ValueError("合并后数据校验失败：" + "；".join(merged_failures))
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if output.exists():
        backup_dir = (args.backup_dir or output.parent / "_workbench_backups").resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{output.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{output.suffix}"
        shutil.copy2(output, backup)
    temporary = output.with_name(f".{output.name}.merging")
    temporary.write_text(html_with_dataset(template_page, merged), encoding="utf-8")
    parsed, page = dataset_from_html(temporary)
    final_failures = validate_embedded_dataset(parsed, temporary, allow_legacy=True)
    if re.search(r"<(?:script|img)[^>]+src=[\"']https?://|<link[^>]+href=[\"']https?://", page, re.I):
        final_failures.append("合并工作台包含远程加载资源")
    if final_failures:
        temporary.unlink(missing_ok=True)
        raise ValueError("合并HTML复核失败：" + "；".join(final_failures))
    os.replace(temporary, output)
    result = {
        "status": "PASS", "output": str(output), "backup": str(backup) if backup else None,
        "inputs": input_audit, "batches": len(merged_batches), "items": merged["total"],
        "batch_sources": batch_sources, "remote_assets": 0, "failures": [],
    }
    write_json(report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_render(args: argparse.Namespace) -> None:
    run_dir = args.run.resolve()
    cluster_set_unresolved = load_jsonl(run_dir / "cluster_set_review_queue.unresolved.jsonl") if (run_dir / "cluster_set_review_queue.unresolved.jsonl").exists() else []
    if cluster_set_unresolved:
        summary = {
            "status": "REVIEW_REQUIRED",
            "stage": "cluster_set_review",
            "unreviewed_clusters": len(cluster_set_unresolved),
            "output_not_replaced": str(args.output.resolve()),
        }
        write_json(run_dir / "render_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    cluster_count_unresolved = load_jsonl(run_dir / "cluster_count_review_queue.unresolved.jsonl") if (run_dir / "cluster_count_review_queue.unresolved.jsonl").exists() else []
    if cluster_count_unresolved:
        summary = {
            "status": "REVIEW_REQUIRED",
            "stage": "cluster_count_review",
            "unreviewed_batches": len(cluster_count_unresolved),
            "output_not_replaced": str(args.output.resolve()),
        }
        write_json(run_dir / "render_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    count_audit_path = run_dir / "cluster_count_review_audit.json"
    if not count_audit_path.exists():
        summary = {
            "status": "REVIEW_REQUIRED",
            "stage": "cluster_count_review",
            "issue": "缺少整期观点簇数量审查记录，需重新运行 cluster 并完成 count_reviews",
            "output_not_replaced": str(args.output.resolve()),
        }
        write_json(run_dir / "render_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    cluster_count_audit = load_json(count_audit_path)
    post_count_reviews = post_excerpt_count_review_map(args.post_count_reviews)
    payload = load_json(run_dir / "clustered_items.json")
    definitions = payload["definitions"]
    items = payload["items"]
    excerpt_reviews = review_map(args.excerpt_reviews, "current_period_verbatim_excerpt_reviews")
    semantic_reviews = review_map(
        args.semantic_reviews,
        "current_period_final_excerpt_semantic_reviews",
        key_field="view_id",
    )
    period_config = load_json(run_dir / "period_config.json")
    dataset = {"generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "batches": [], "total": 0}
    staged_dataset = {"generatedAt": dataset["generatedAt"], "batches": [], "total": 0}
    provenance = []
    semantic_inputs = []
    semantic_pending = []
    semantic_rejected = []
    semantic_auto_accepted = []
    failures = []
    preflight_failures = []
    drop_counts: Counter[tuple[str, str]] = Counter()
    cluster_display_audit = []
    post_count_review_input = []
    post_count_review_audit = {}
    for batch in load_json(run_dir / "period_config.json")["batch_order"]:
        clusters = []
        staged_clusters = []
        batch_display_audit = []
        for definition in definitions.get(batch, []):
            members = [item for item in items if item["batch"] == batch and item["cluster_id"] == str(definition["id"])]
            display_items = []
            staged_items = []
            for item in members:
                row = item["source"]
                body = source_text(row)
                view_id = f"{row['id']}::{definition['id']}"
                raw_excerpt = ""
                excerpt = ""
                try:
                    review = excerpt_reviews.get(view_id)
                    chosen_excerpt = choose_valid_display_candidate(
                        body, item["window"], definition, review,
                    )
                    fragments = chosen_excerpt["fragments"]
                    positions = chosen_excerpt["positions"]
                    raw_excerpt = chosen_excerpt["raw_excerpt"]
                    excerpt = chosen_excerpt["excerpt"]
                    basis = chosen_excerpt["basis"]
                except (ValueError, IndexError) as exc:
                    failure = {
                        "view_id": view_id,
                        "source_id": row["id"],
                        "batch": batch,
                        "cluster_id": str(definition["id"]),
                        "cluster_title": definition["title"],
                        "stage": "verbatim",
                        "issue": str(exc),
                        "current_excerpt": excerpt or clean_display_excerpt(raw_excerpt),
                    }
                    failures.append(failure)
                    preflight_failures.append(failure)
                    continue

                # 最终立场不再复用归簇阶段的 passage_stance；它由独立摘录语义复核给出。
                target_config = period_config["targets"][batch]
                source_other_works = other_work_titles(body, target_config)
                final_alignment = passage_alignment(
                    row, {"text": raw_excerpt}, definition,
                    target_config, {}, allow_title_fallback=not source_other_works,
                )
                context_start = max(0, positions[0][0] - 60)
                context_end = min(len(body), positions[-1][1] + 60)
                promotion_markers = promotion_review_markers(excerpt)
                short_excerpt = len(excerpt) < EXCERPT_PREFERRED_MIN
                comparison_terms = target_layers(target_config)[3]
                work_conflict_markers = list(dict.fromkeys([
                    *[term for term in comparison_terms if term and term in raw_excerpt],
                    *[f"《{term}》" for term in other_work_titles(raw_excerpt, target_config)],
                    *[f"《{term}》" for term in role_related_other_work_titles(raw_excerpt, target_config)],
                ]))
                target_review_required = final_alignment.get("target", {}).get("passed") is not True
                excerpt_segments = excerpt_evidence_segments(excerpt)
                strong_terms, weak_terms, auxiliary_terms, comparison_terms = target_layers(target_config)
                entity_terms = {normalized(term) for term in strong_terms + weak_terms + auxiliary_terms + comparison_terms}
                aspect_terms = list(effective_required_terms(definition, target_config))
                for term, _weight in sorted(keyword_pairs(definition), key=lambda pair: pair[1], reverse=True):
                    if (
                        term not in aspect_terms
                        and normalized(term) not in entity_terms
                        and term not in GENERIC_ROUTING_TERMS
                    ):
                        aspect_terms.append(term)
                    if len(aspect_terms) >= 10:
                        break
                aspect_candidate_indexes = [
                    int(index) for index, segment in excerpt_segments.items()
                    if any(term in segment for term in aspect_terms)
                ]
                matched_aspect_terms = list(dict.fromkeys(
                    term for term in aspect_terms if term and term in excerpt
                ))
                concrete_aspect_terms = [
                    term for term in matched_aspect_terms
                    if term not in AMBIGUOUS_AUTO_ASPECT_TERMS
                ]
                cluster_claim_review_reasons = []
                if matched_aspect_terms and not concrete_aspect_terms:
                    cluster_claim_review_reasons.append("only_ambiguous_routing_terms")
                if len(members) <= 2:
                    cluster_claim_review_reasons.append("rare_cluster_requires_direct_support")
                cluster_claim_review_required = bool(cluster_claim_review_reasons)
                target_anchor_terms = list(dict.fromkeys(strong_terms + weak_terms + auxiliary_terms))
                target_segments = target_evidence_segments(
                    row,
                    body,
                    target_config,
                    " ".join(filter(None, [
                        body[context_start:positions[0][0]], raw_excerpt, body[positions[-1][1]:context_end],
                    ])),
                )
                display_promotion_markers = display_operational_promotion_markers(excerpt)
                irrelevant_leading_indexes = unrelated_leading_segment_indexes(
                    excerpt_segments, strong_terms + weak_terms, aspect_terms,
                )
                target_context_flags = []
                if target_review_required:
                    target_keys = {normalized(term) for term in strong_terms + weak_terms if normalized(term)}
                    other_bracketed_titles = {
                        match.group(1) for match in re.finditer(r"《([^》]{1,40})》", body)
                        if normalized(match.group(1))
                        and not any(
                            normalized(match.group(1)) == key
                            or normalized(match.group(1)) in key
                            or key in normalized(match.group(1))
                            for key in target_keys
                        )
                    }
                    if len(other_bracketed_titles) >= 3:
                        target_context_flags.append("multi_work_roundup")
                semantic_input = {
                    "view_id": view_id, "source_id": row["id"], "batch": batch, "cluster_id": str(definition["id"]),
                    "cluster_title": definition["title"], "cluster_stance": definition["stance"],
                    "excerpt_segments": excerpt_segments,
                    "aspect_terms": aspect_terms,
                    "aspect_candidate_indexes": aspect_candidate_indexes,
                    "matched_aspect_terms": matched_aspect_terms,
                    "promotion_markers": promotion_markers,
                    "excerpt_length": len(excerpt),
                    "short_excerpt": short_excerpt,
                    "target_review_required": target_review_required,
                    "work_consistency_review_required": bool(work_conflict_markers),
                    "cluster_claim_review_required": cluster_claim_review_required,
                    **({"cluster_claim_review_reasons": cluster_claim_review_reasons} if cluster_claim_review_required else {}),
                    **({"cluster_claim_candidate_indexes": aspect_candidate_indexes} if cluster_claim_review_required else {}),
                    **({"target_anchor_terms": target_anchor_terms} if target_review_required else {}),
                    **({"target_evidence_segments": target_segments} if target_review_required else {}),
                    **({"target_context_flags": target_context_flags} if target_context_flags else {}),
                    **({"display_operational_promotion_markers": display_promotion_markers} if display_promotion_markers else {}),
                    **({"irrelevant_leading_segment_indexes": irrelevant_leading_indexes} if irrelevant_leading_indexes else {}),
                    **({"work_conflict_markers": work_conflict_markers} if work_conflict_markers else {}),
                    **({"raw_excerpt": raw_excerpt} if raw_excerpt != excerpt and work_conflict_markers else {}),
                }
                if short_excerpt or work_conflict_markers or promotion_markers or display_promotion_markers or irrelevant_leading_indexes:
                    semantic_input["context_before"] = body[context_start:positions[0][0]]
                    semantic_input["context_after"] = body[positions[-1][1]:context_end]
                semantic_input["review_fingerprint"] = semantic_review_fingerprint(semantic_input)
                semantic = semantic_reviews.get(view_id)
                if semantic is None:
                    semantic = deterministic_semantic_keep(semantic_input, excerpt)
                    if semantic is not None:
                        semantic_auto_accepted.append({
                            "view_id": view_id,
                            "source_id": row["id"],
                            "batch": batch,
                            "cluster_id": str(definition["id"]),
                            "review_fingerprint": semantic_input["review_fingerprint"],
                            "basis": semantic["review_basis"],
                        })
                if semantic is None or clean(semantic.get("review_basis")) != "script_high_confidence_semantic_gate":
                    semantic_inputs.append(semantic_input)
                item_failures = []
                if semantic is None:
                    semantic_pending.append(semantic_input)
                    item_failures.append("缺少独立的最终摘录语义复核")
                else:
                    semantic_decision = clean(semantic.get("decision"))
                    if clean(semantic.get("review_fingerprint")) != semantic_input["review_fingerprint"]:
                        item_failures.append("最终摘录语义复核与当前条目指纹不一致")
                    if semantic_decision not in {"keep", "drop"}:
                        item_failures.append("最终摘录语义复核 decision 必须为 keep 或 drop")
                    if semantic_decision == "keep":
                        aspect_evidence = indexed_excerpt_evidence(semantic, semantic_input, "aspect_evidence")
                        stance_evidence = indexed_excerpt_evidence(semantic, semantic_input, "stance_evidence")
                        reviewed_stance = clean(semantic.get("stance"))
                        if display_promotion_markers:
                            item_failures.append("展示摘录仍含购买、抽奖、关注或参与指令；须重截为纯观点片段后再保留")
                        if irrelevant_leading_indexes:
                            item_failures.append(
                                f"展示摘录第 {irrelevant_leading_indexes} 段为目标观点前的无关八卦；须从有效观点处重截"
                            )
                        if reviewed_stance != clean(definition["stance"]):
                            item_failures.append(f"摘录立场 {reviewed_stance or '空'} 与观点簇立场不一致")
                        if semantic.get("self_contained") is not True:
                            item_failures.append("片段自足性未通过独立语义复核")
                        for evidence, label in ((aspect_evidence, "方面证据"), (stance_evidence, "立场证据")):
                            if not evidence or evidence not in excerpt:
                                item_failures.append(f"{label}候选编号无效，且未提供最终摘录中的逐字证据")
                        if stance_evidence and stance_evidence_conflicts(definition["stance"], stance_evidence):
                            item_failures.append("立场证据自身呈现的立场与观点簇不一致")
                        elif stance_evidence and not stance_evidence_supports(definition["stance"], stance_evidence):
                            item_failures.append("立场证据没有实际表达观点簇要求的正面、客观或负面立场")
                        if aspect_terms and not any(term in aspect_evidence for term in aspect_terms):
                            item_failures.append("方面证据未命中该观点簇的任何具体方面词，需重截或重新归簇")
                        if cluster_claim_review_required:
                            claim_evidence = indexed_excerpt_evidence(semantic, semantic_input, "cluster_claim_evidence")
                            if semantic.get("cluster_claim_passed") is not True:
                                item_failures.append("宽泛路由词或少量观点簇须确认摘录能直接支持观点标题，无需分析者补充推理")
                            if not claim_evidence or claim_evidence not in excerpt:
                                item_failures.append("观点标题支持证据候选编号无效，且未提供最终摘录中的逐字证据")
                        if target_review_required:
                            target_evidence = indexed_target_evidence(semantic, semantic_input)
                            target_scope = " ".join(target_segments.values())
                            if semantic.get("target_relation_passed") is not True:
                                item_failures.append("脚本无法确认目标作品，AI未确认目标锚点与当前观点属于同一语义关系")
                            if not target_evidence or target_evidence not in target_scope:
                                item_failures.append("目标证据候选编号无效，且未提供脚本候选中的逐字证据")
                            elif not any(term in target_evidence for term in target_anchor_terms):
                                item_failures.append("目标证据未包含作品名、节目名、演员或角色锚点")
                        if work_conflict_markers:
                            consistency_evidence = clean(semantic.get("work_consistency_evidence"))
                            if semantic.get("work_consistency_passed") is not True:
                                item_failures.append("摘录含对比作品，未确认目标作品内部信息一致性")
                            if not consistency_evidence or consistency_evidence not in raw_excerpt:
                                item_failures.append("作品一致性证据不是后台逐字原文片段中的子串")
                        if promotion_markers:
                            opinion_evidence = indexed_excerpt_evidence(semantic, semantic_input, "opinion_evidence")
                            if semantic.get("independent_opinion_passed") is not True:
                                item_failures.append("含促销操作词的摘录未确认存在独立观点")
                            if not opinion_evidence or opinion_evidence not in excerpt:
                                item_failures.append("含促销操作词的摘录缺少最终摘录中的逐字观点证据")
                            elif not promotion_evidence_is_independent(opinion_evidence):
                                item_failures.append("促销证据只有应援、送礼、抽奖或操作指令，没有独立评价或第三方事实观察")
                        if short_excerpt:
                            support_evidence = (
                                aspect_evidence
                                if short_excerpt_support_is_specific(excerpt, aspect_evidence)
                                else stance_evidence
                            )
                            if not short_excerpt_support_is_specific(excerpt, support_evidence):
                                item_failures.append("不足70字的摘录只有泛泛态度，具体依据未包含动作、台词、场景、数据或因果分析")
                    elif semantic_decision == "drop" and not item_failures:
                        drop_reason = clean(semantic.get("reason"))
                        if not drop_reason:
                            item_failures.append("drop 缺少具体理由")
                        failed_checks = semantic.get("failed_checks")
                        if not isinstance(failed_checks, list) or not failed_checks:
                            item_failures.append("drop 必须填写至少一项 failed_checks")
                        else:
                            invalid_checks = sorted({clean(value) for value in failed_checks} - ALLOWED_DROP_FAILURE_CHECKS)
                            if invalid_checks:
                                item_failures.append(f"drop 含未知 failed_checks：{invalid_checks}")
                        if semantic.get("reexcerpt_attempted") is not True:
                            item_failures.append("drop 前必须回看全文并确认 reexcerpt_attempted=true")
                        if isinstance(failed_checks, list) and ({"aspect", "stance", "cluster_claim"} & set(failed_checks)):
                            if semantic.get("reassignment_attempted") is not True:
                                item_failures.append("方面或立场不匹配时，drop 前必须确认已尝试重新归簇")
                            if not clean(semantic.get("reassignment_reason")):
                                item_failures.append("方面或立场不匹配时，drop 必须说明为何无法转入其他现有簇或形成新簇")
                        if isinstance(failed_checks, list) and "work_consistency" in failed_checks:
                            conflict_evidence = clean(semantic.get("conflict_evidence"))
                            if not conflict_evidence or conflict_evidence not in body:
                                item_failures.append("作品信息冲突必须提供全文中的逐字 conflict_evidence")
                        if not item_failures:
                            reason_key = drop_reason_fingerprint(drop_reason)
                            drop_counts[(batch, str(definition["id"]))] += 1
                            semantic_rejected.append({
                                "view_id": view_id,
                                "source_id": row["id"],
                                "cluster_id": str(definition["id"]),
                                "reason": drop_reason,
                                "reason_fingerprint": reason_key,
                                "failed_checks": failed_checks,
                                "reexcerpt_attempted": True,
                                "reassignment_attempted": semantic.get("reassignment_attempted") is True,
                                "reassignment_reason": clean(semantic.get("reassignment_reason")),
                            })
                            continue
                aspect_evidence = ""
                stance_evidence = ""
                specific_support_evidence = ""
                opinion_evidence = ""
                semantic_effective = dict(semantic or {})
                if semantic:
                    aspect_evidence = indexed_excerpt_evidence(semantic, semantic_input, "aspect_evidence")
                    stance_evidence = indexed_excerpt_evidence(semantic, semantic_input, "stance_evidence")
                    if short_excerpt:
                        specific_support_evidence = (
                            aspect_evidence
                            if short_excerpt_support_is_specific(excerpt, aspect_evidence)
                            else stance_evidence
                        )
                    opinion_evidence = indexed_excerpt_evidence(semantic, semantic_input, "opinion_evidence")
                    if aspect_evidence:
                        semantic_effective["aspect_evidence"] = aspect_evidence
                    if stance_evidence:
                        semantic_effective["stance_evidence"] = stance_evidence
                    if specific_support_evidence:
                        semantic_effective["specific_support_evidence"] = specific_support_evidence
                    if opinion_evidence:
                        semantic_effective["opinion_evidence"] = opinion_evidence
                    if target_review_required:
                        resolved_target_evidence = indexed_target_evidence(semantic, semantic_input)
                        final_alignment["target"] = {**final_alignment["target"], "passed": semantic.get("target_relation_passed") is True, "basis": "ai_final_target_relation_review", "review_evidence": resolved_target_evidence}
                        semantic_effective["target_passed"] = semantic.get("target_relation_passed") is True
                        semantic_effective["target_basis"] = "ai_final_target_review"
                        semantic_effective["target_evidence"] = resolved_target_evidence
                    else:
                        semantic_effective["target_passed"] = True
                        semantic_effective["target_basis"] = "script_passage_alignment"
                    semantic_effective["work_consistency_passed"] = (
                        semantic.get("work_consistency_passed") is True if work_conflict_markers else True
                    )
                    semantic_effective["work_consistency_basis"] = (
                        "ai_reviewed_comparison" if work_conflict_markers else "script_no_comparison_marker"
                    )
                    semantic_basis = clean(semantic.get("review_basis"))
                    alignment_basis = (
                        "script_high_confidence_semantic_gate"
                        if semantic_basis == "script_high_confidence_semantic_gate"
                        else "ai_final_excerpt_review"
                    )
                    final_alignment["aspect"] = {**final_alignment["aspect"], "passed": bool(aspect_evidence) and aspect_evidence in excerpt, "basis": alignment_basis, "review_evidence": aspect_evidence}
                    final_alignment["stance"] = {**final_alignment["stance"], "passed": clean(semantic.get("stance")) == clean(definition["stance"]), "reviewed": clean(semantic.get("stance")), "basis": alignment_basis, "review_evidence": stance_evidence}

                staged_item = {"viewId": view_id, "sourceId": row["id"], "channel": row["channel"], "author": row["author"], "title": row["title"], "publishedAt": row["published"], "url": safe_url(row["url"]), "excerpt": excerpt, "body": body, "sourceStance": row.get("stance", ""), "clusterStance": definition["stance"], "mediaAuthority": {"subjectId": row.get("media_subject_id", ""), "subjectName": row.get("media_subject_name", ""), "accountAlias": row.get("media_account_alias", ""), "accountType": row.get("media_account_type", ""), "tier": row.get("media_authority_tier", "unclassified"), "rank": int(row.get("media_authority_rank", 0)), "basis": row.get("media_authority_basis", "")}, "linkHealth": row.get("link_health", {}), "alignment": final_alignment, "semanticReview": semantic_effective, "factWarning": row.get("fact_warning", ""), "excerptProvenance": {"fragments": fragments, "positions": positions, "basis": basis, "displayNormalization": DISPLAY_NORMALIZATION}, "excerptLengthReview": {"length": len(excerpt), "preferredMin": EXCERPT_PREFERRED_MIN, "max": EXCERPT_MAX, "exception": short_excerpt, "reason": "短摘录本身含可核验的具体依据" if short_excerpt and specific_support_evidence else "", "specificSupportPassed": bool(specific_support_evidence) if short_excerpt else True, "specificSupportEvidence": specific_support_evidence if short_excerpt else ""}}
                staged_items.append(staged_item)
                if item_failures:
                    failures.extend({"view_id": view_id, "stage": "semantic", "issue": issue} for issue in item_failures)
                    continue
                display_items.append(staged_item)
                provenance.append({"view_id": view_id, "source_id": row["id"], "fragments": fragments, "positions": positions, "basis": basis})
            rare_signal = definition.get("rare_signal") is True
            display_audit = {
                "batch": batch,
                "cluster_id": str(definition["id"]),
                "assigned": len(members),
                "kept": len(display_items),
                "dropped": drop_counts[(batch, str(definition["id"]))],
                "rare_signal": rare_signal,
            }
            cluster_display_audit.append(display_audit)
            batch_display_audit.append(display_audit)
            if display_items:
                clusters.append({
                    "id": str(definition["id"]),
                    "title": definition["title"],
                    "stance": definition["stance"],
                    "reportRole": clean(definition.get("report_role")),
                    "objectiveSubtype": clean(definition.get("objective_subtype")),
                    "scopeType": clean(definition.get("scope_type")),
                    "rareSignal": rare_signal,
                    "rareSignalReason": clean(definition.get("rare_signal_reason") or definition.get("granularity_reason")),
                    "count": len(display_items),
                    "items": display_items,
                })
            if staged_items:
                staged_clusters.append({
                    "id": str(definition["id"]),
                    "title": definition["title"],
                    "stance": definition["stance"],
                    "reportRole": clean(definition.get("report_role")),
                    "objectiveSubtype": clean(definition.get("objective_subtype")),
                    "scopeType": clean(definition.get("scope_type")),
                    "rareSignal": rare_signal,
                    "rareSignalReason": clean(definition.get("rare_signal_reason") or definition.get("granularity_reason")),
                    "count": len(staged_items),
                    "items": staged_items,
                })
        batch_count = sum(cluster["count"] for cluster in clusters)
        staged_count = sum(cluster["count"] for cluster in staged_clusters)
        reviewed_count = cluster_count_audit.get(batch, {})
        if reviewed_count.get("status") != "PASS":
            failures.append({
                "view_id": f"{batch}::cluster-count",
                "stage": "cluster_count_before_excerpt_review",
                "issue": "终审前观点簇数量审查未通过",
            })
        elif reviewed_count.get("cluster_count") != len(clusters):
            count_issues, count_audit = post_excerpt_count_review_issues(
                batch, clusters, post_count_reviews.get(batch)
            )
            count_input = {
                "review_key": batch,
                "fingerprint": count_audit["fingerprint"],
                "batch": batch,
                "pre_excerpt_cluster_count": reviewed_count.get("cluster_count"),
                "post_excerpt_cluster_count": len(clusters),
                "normal_review_range": {"min": 9, "max": 16},
                "expected_range_status": count_audit["range_status"],
                "clusters": [
                    {"id": cluster["id"], "title": cluster["title"], "stance": cluster["stance"], "samples": cluster["count"]}
                    for cluster in clusters
                ],
                "instruction": "终审已明确删除不合格展示段并可能清空簇；只复核终审后的实际一级簇数量与颗粒度，不回改终审前数字，不为进入9至16机械合并或补造观点",
            }
            post_count_review_input.append(count_input)
            post_count_review_audit[batch] = {**count_audit, "status": "PASS" if not count_issues else "REVIEW_REQUIRED", "issues": count_issues}
            if count_issues:
                failures.append({
                    "view_id": f"{batch}::post-excerpt-cluster-count",
                    "stage": "post_excerpt_cluster_count_review",
                    "issue": f"终审后工作台含 {len(clusters)} 个观点簇，终审前为 {reviewed_count.get('cluster_count')}；请完成后置数量复核",
                    "issues": count_issues,
                })
        dataset["batches"].append({
            "name": batch,
            "count": batch_count,
            "clusterCount": len(clusters),
            "clusters": clusters,
        })
        staged_dataset["batches"].append({
            "name": batch,
            "count": staged_count,
            "clusterCount": len(staged_clusters),
            "clusters": staged_clusters,
        })
        dataset["total"] += batch_count
        staged_dataset["total"] += staged_count
    write_json(run_dir / "workbench_dataset.staged.json", staged_dataset)
    final_review_chunks = write_jsonl_chunks(
        run_dir / "final_excerpt_review_chunks", semantic_inputs,
        chunk_size=FINAL_REVIEW_CHUNK_SIZE,
        max_bytes=90_000,
    )
    write_json(run_dir / "final_excerpt_review_contract.json", {
        "scope": "current_period_final_excerpt_review_contract",
        "instruction": "脚本已检查逐字位置、长度和清洁标记。AI按 excerpt_segments 顺序核对方面与立场。cluster_claim_review_required 时，摘录必须能直接放在 cluster_title 下且无需补充推理；泛群像、泛竞争、泛特效、名单和物料不能代替标题中的具体判断。target_review_required 时，须确认目标锚点和当前观点确属同一对象；作品只在标签、名单或综合盘点中出现不能通过。work_consistency_review_required 时，须确认方面证据评价的是目标作品。展示段含购买、抽奖、关注、参与指令或观点前无关八卦时，先重截，无法重截再 drop。",
        "allowed_decisions": ["keep", "drop"],
        "full_source_lookup_file": "retained_sources.jsonl",
        "evidence_scopes": {
            "aspect_evidence_candidate_index": "excerpt_segments",
            "stance_evidence_candidate_index": "excerpt_segments",
            "opinion_evidence_candidate_index": "excerpt_segments",
            "cluster_claim_evidence_candidate_index": "excerpt_segments；仅在 cluster_claim_review_required 时填写",
            "exact_text_fallback": "仅当候选均不适用时，才填写对应的不带 _candidate_index 的逐字证据字段",
            "target_evidence_candidate_index": "target_evidence_segments；候选均不适用时才填写 target_evidence 逐字证据",
            "work_consistency_evidence_when_required": "raw_excerpt；若该字段省略则使用按序拼接的 excerpt_segments"
        },
        "preferred_length": [EXCERPT_PREFERRED_MIN, EXCERPT_MAX],
        "script_auto_accepted_items": len(semantic_auto_accepted),
        "script_auto_accepted_file": "excerpt_semantic_auto_accepted.jsonl",
        "ai_review_items": len(semantic_inputs),
        "chunk_size": FINAL_REVIEW_CHUNK_SIZE,
        "chunk_files": [str(path.relative_to(run_dir)) for path in final_review_chunks],
        "do_not_repeat_instruction_per_item": True,
    })
    write_jsonl(run_dir / "excerpt_semantic_review_input.jsonl", semantic_inputs)
    write_jsonl(run_dir / "excerpt_semantic_auto_accepted.jsonl", semantic_auto_accepted)
    write_jsonl(run_dir / "excerpt_semantic_review_queue.unresolved.jsonl", semantic_pending)
    write_jsonl(run_dir / "excerpt_semantic_review_rejected.jsonl", semantic_rejected)
    write_json(run_dir / "cluster_display_coverage_audit.json", {"clusters": cluster_display_audit})
    write_json(run_dir / "post_excerpt_cluster_count_review_input.json", {
        "scope": "current_period_post_excerpt_cluster_count_review_input",
        "batches": post_count_review_input,
    })
    write_json(run_dir / "post_excerpt_cluster_count_review_audit.json", post_count_review_audit)
    write_json(run_dir / "render_failures.json", {"failures": failures})
    write_json(run_dir / "excerpt_preflight_failures.json", {"failures": preflight_failures})
    if failures:
        failure_stages = {clean(item.get("stage")) for item in failures}
        summary = {"status": "REVIEW_REQUIRED", "stage": "post_excerpt_cluster_count_review" if failure_stages == {"post_excerpt_cluster_count_review"} else "render_repair", "staged_items": staged_dataset["total"], "accepted_items": dataset["total"], "script_auto_accepted_items": len(semantic_auto_accepted), "reviewed_dropped_items": len(semantic_rejected), "failures": len(failures), "unreviewed_final_excerpts": len(semantic_pending), "post_excerpt_count_reviews_required": len(post_count_review_input), "output_not_replaced": str(args.output.resolve())}
        write_json(run_dir / "render_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    write_json(run_dir / "workbench_dataset.json", dataset)
    write_json(run_dir / "excerpt_provenance.json", {"items": provenance})
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(dataset), encoding="utf-8")
    summary = {"status": "RENDERED", "output": str(output), "items": dataset["total"], "script_auto_accepted_items": len(semantic_auto_accepted), "reviewed_dropped_items": len(semantic_rejected), "unique_sources": len({item["sourceId"] for batch in dataset["batches"] for cluster in batch["clusters"] for item in cluster["items"]}), "clusters": {batch["name"]: batch["clusterCount"] for batch in dataset["batches"]}, "single_file_html": True}
    write_json(run_dir / "render_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def command_verify(args: argparse.Namespace) -> None:
    run_dir, output = args.run.resolve(), args.output.resolve()
    sources = {row["id"]: row for row in load_jsonl(run_dir / "retained_sources.jsonl")}
    dataset = load_json(run_dir / "workbench_dataset.json")
    period_config = load_json(run_dir / "period_config.json")
    low = load_jsonl(run_dir / "cluster_review_queue.jsonl")
    cluster_set_unresolved = load_jsonl(run_dir / "cluster_set_review_queue.unresolved.jsonl") if (run_dir / "cluster_set_review_queue.unresolved.jsonl").exists() else []
    cluster_count_unresolved = load_jsonl(run_dir / "cluster_count_review_queue.unresolved.jsonl") if (run_dir / "cluster_count_review_queue.unresolved.jsonl").exists() else []
    source_unresolved = load_jsonl(run_dir / "source_review_queue.unresolved.jsonl") if (run_dir / "source_review_queue.unresolved.jsonl").exists() else []
    excerpt_unresolved = load_jsonl(run_dir / "excerpt_semantic_review_queue.unresolved.jsonl") if (run_dir / "excerpt_semantic_review_queue.unresolved.jsonl").exists() else []
    failures = []
    link_audit_path = run_dir / "source_link_health.applied.json"
    if not link_audit_path.exists():
        failures.append("缺少来源链接健康检查应用记录")
    hard_exclusion_path = run_dir / "hard_exclusion_audit.json"
    if not hard_exclusion_path.exists():
        failures.append("缺少AI成稿泄漏及失效链接硬排除审计")
    for source_id, row in sources.items():
        leak_type, _ = ai_draft_leak(source_text(row))
        if leak_type:
            failures.append(f"保留池仍含AI成稿泄漏来源：{source_id}")
        if row.get("link_health", {}).get("classification") == "confirmed_dead":
            failures.append(f"保留池仍含确认404/410来源：{source_id}")
    count_audit_path = run_dir / "cluster_count_review_audit.json"
    cluster_count_audit = load_json(count_audit_path) if count_audit_path.exists() else {}
    failures.extend(validate_embedded_dataset(dataset, output, allow_legacy=False))
    render_summary = load_json(run_dir / "render_summary.json") if (run_dir / "render_summary.json").exists() else {}
    render_failures = load_json(run_dir / "render_failures.json") if (run_dir / "render_failures.json").exists() else {"failures": []}
    if clean(render_summary.get("status")) != "RENDERED":
        failures.append("最近一次 render 未成功，拒绝对旧工作台或旧数据集复用 PASS")
    elif Path(clean(render_summary.get("output"))).resolve() != output:
        failures.append("最近一次 render 的输出路径与本次 verify 目标不一致")
    pending_render_failures = render_failures.get("failures", [])
    if isinstance(pending_render_failures, list) and pending_render_failures:
        failures.append(f"最近一次 render 仍有 {len(pending_render_failures)} 个失败项")
    if not cluster_count_audit:
        failures.append("缺少整期观点簇数量审查记录")
    for batch in dataset.get("batches", []):
        reviewed_count = cluster_count_audit.get(clean(batch.get("name")), {})
        if reviewed_count.get("status") != "PASS":
            failures.append(f"整期观点簇数量审查未通过：{batch.get('name')}")
        elif reviewed_count.get("cluster_count") != batch.get("clusterCount"):
            failures.append(
                f"最终观点簇数与审查记录不一致：{batch.get('name')} / "
                f"页面={batch.get('clusterCount')} / 审查={reviewed_count.get('cluster_count')}"
            )
    view_ids, per_source = [], Counter()
    per_source_positions: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for batch in dataset.get("batches", []):
        if not batch.get("clusters"):
            failures.append(f"空批次：{batch.get('name')}")
        for cluster in batch.get("clusters", []):
            if not cluster.get("items"):
                failures.append(f"空簇：{batch.get('name')} / {cluster.get('id')}")
            media_ranks = [int(item.get("mediaAuthority", {}).get("rank", -1)) for item in cluster.get("items", [])]
            if media_ranks != sorted(media_ranks, reverse=True):
                failures.append(f"同簇样本未按主流媒体身份优先排序：{batch.get('name')} / {cluster.get('id')}")
            if cluster.get("rareSignal") is True and not clean(cluster.get("rareSignalReason")):
                failures.append(f"稀有信号缺少理由：{batch.get('name')} / {cluster.get('id')}")
            if GENERIC_CLUSTER_TITLE.search(clean(cluster.get("title"))):
                failures.append(f"兜底簇：{cluster.get('id')}")
            cluster_title = clean(cluster.get("title"))
            if len(normalized(cluster_title)) < 16 or not CLUSTER_TITLE_PREDICATE.search(cluster_title):
                failures.append(f"观点簇标题不是完整报告体观点句：{batch.get('name')} / {cluster.get('id')}")
            if clean(cluster.get("stance")) not in ALLOWED_CLUSTER_STANCES:
                failures.append(f"观点簇立场无效：{cluster.get('id')}")
            for item in cluster.get("items", []):
                view_ids.append(item["viewId"])
                per_source[item["sourceId"]] += 1
                if item["sourceId"] not in sources:
                    failures.append(f"展示来源不在保留池：{item['sourceId']}")
                    continue
                body = source_text(sources[item["sourceId"]])
                provenance = item.get("excerptProvenance", {})
                fragments = provenance.get("fragments", [])
                try:
                    positions = validate_fragment_positions(body, fragments, item.get("excerptProvenance", {}).get("positions", []))
                except ValueError as exc:
                    failures.append(f"{item['viewId']}：{exc}")
                    continue
                if fragments_extract_hashtag_interior(body, positions):
                    failures.append(f"展示片段从话题标签内部抽词：{item['viewId']}")
                if positions:
                    per_source_positions[item["sourceId"]].append(
                        (positions[0][0], positions[-1][1], item["viewId"])
                    )
                raw_joined = clean(" ".join(fragment.strip() for fragment in fragments))
                expected_excerpt = clean_display_excerpt(raw_joined) if provenance.get("displayNormalization") == DISPLAY_NORMALIZATION else raw_joined
                if item["excerpt"] != expected_excerpt:
                    failures.append(f"展示文字与原文片段不一致：{item['viewId']}")
                excerpt = clean(item.get("excerpt"))
                if display_excerpt_has_markup(excerpt):
                    failures.append(f"展示片段含话题标签、平台表情、链接或账号标记：{item['viewId']}")
                if excerpt.startswith(BAD_EXCERPT_START) or excerpt.endswith(BAD_EXCERPT_END):
                    failures.append(f"展示片段边界不完整：{item['viewId']}")
                if excerpt.count("“") != excerpt.count("”"):
                    failures.append(f"展示片段引号不成对：{item['viewId']}")
                if item.get("clusterStance") != cluster.get("stance"):
                    failures.append(f"展示立场与观点簇不一致：{item['viewId']}")
                media_authority = item.get("mediaAuthority", {})
                if media_authority.get("tier") not in MEDIA_AUTHORITY_RANK or media_authority.get("rank") != MEDIA_AUTHORITY_RANK.get(media_authority.get("tier")):
                    failures.append(f"展示来源缺少合法媒体身份等级：{item['viewId']}")
                alignment = item.get("alignment", {})
                for dimension in ("target", "aspect", "stance"):
                    if not alignment.get(dimension, {}).get("passed"):
                        failures.append(f"{dimension} 四项校验未通过：{item['viewId']}")
                semantic = item.get("semanticReview", {})
                if clean(semantic.get("decision")) != "keep" or semantic.get("self_contained") is not True:
                    failures.append(f"缺少有效的独立最终摘录语义复核：{item['viewId']}")
                if not clean(semantic.get("aspect_evidence")) or not clean(semantic.get("stance_evidence")):
                    failures.append(f"最终摘录语义复核缺少脚本回填证据：{item['viewId']}")
                if semantic.get("work_consistency_passed") is not True:
                    failures.append(f"作品内部信息一致性未通过：{item['viewId']}")
                if clean(semantic.get("work_consistency_basis")) == "ai_reviewed_comparison":
                    consistency_evidence = clean(semantic.get("work_consistency_evidence"))
                    raw_joined = clean(" ".join(fragment.strip() for fragment in fragments))
                    if not consistency_evidence or consistency_evidence not in raw_joined:
                        failures.append(f"作品一致性证据不是后台逐字原文片段中的子串：{item['viewId']}")
                if "woa.com" in clean(item.get("url")).lower():
                    failures.append(f"禁止的内网链接：{item['viewId']}")
    if len(view_ids) != len(set(view_ids)):
        failures.append("viewId 不唯一")
    if any(count > 2 for count in per_source.values()):
        failures.append("单来源展示超过两个观点片段")
    for source_id, passages in per_source_positions.items():
        if len(passages) == 2:
            left, right = sorted(passages)
            if left[1] > right[0]:
                failures.append(f"单来源两个展示片段发生重叠：{source_id}")
    if low:
        failures.append(f"仍有 {len(low)} 条低置信归簇未复核")
    if cluster_set_unresolved:
        failures.append(f"仍有 {len(cluster_set_unresolved)} 个观点簇集合审查未完成")
    if cluster_count_unresolved:
        failures.append(f"仍有 {len(cluster_count_unresolved)} 个批次的观点簇数量审查未完成")
    if source_unresolved:
        failures.append(f"仍有 {len(source_unresolved)} 条来源AI复核队列未完成")
    if excerpt_unresolved:
        failures.append(f"仍有 {len(excerpt_unresolved)} 条最终摘录语义复核未完成")
    page = output.read_text(encoding="utf-8") if output.exists() else ""
    if not page:
        failures.append("工作台HTML不存在或为空")
    else:
        try:
            embedded_dataset, _ = dataset_from_html(output)
            if embedded_dataset != dataset:
                failures.append("工作台HTML内嵌数据与本次 workbench_dataset.json 不一致")
        except ValueError as exc:
            failures.append(str(exc))
    if 'id="stanceTabs"' not in page or "function renderStanceTabs" not in page:
        failures.append("工作台缺少正面、客观、负面切换组件")
    if re.search(r"<(?:script|img)[^>]+src=[\"']https?://|<link[^>]+href=[\"']https?://", page, re.I):
        failures.append("工作台包含远程加载资源")
    if any(field in row for row in sources.values() for field in FORBIDDEN_UPSTREAM_FIELDS):
        failures.append("保留池包含禁用上游字段")
    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "workbench_items": len(view_ids),
        "unique_sources": len(per_source),
        "clusters": {batch["name"]: batch["clusterCount"] for batch in dataset.get("batches", [])},
        "provenance_passed": f"{len(view_ids) - sum('片段' in failure or '展示文字' in failure for failure in failures)}/{len(view_ids)}",
        "card_files_read": 0,
        "historical_report_fields_read": 0,
        "remote_assets": 0 if not any("远程加载" in failure for failure in failures) else 1,
        "unreviewed_source_queue": len(source_unresolved),
        "unreviewed_final_excerpts": len(excerpt_unresolved),
        "unreviewed_cluster_set": len(cluster_set_unresolved),
        "unreviewed_cluster_count": len(cluster_count_unresolved),
        "output": str(output),
    }
    write_json(run_dir / "verification.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Reusable film-brief source cleaning pipeline")
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--records", required=True, type=Path)
    prepare.add_argument("--config", required=True, type=Path)
    prepare.add_argument("--run", required=True, type=Path)
    prepare.set_defaults(func=command_prepare)
    validate_reviews = commands.add_parser("validate-source-reviews")
    validate_reviews.add_argument("--run", required=True, type=Path)
    validate_reviews.add_argument("--reviews", required=True, type=Path)
    validate_reviews.set_defaults(func=command_validate_source_reviews)
    select = commands.add_parser("select")
    select.add_argument("--run", required=True, type=Path)
    select.add_argument("--reviews", type=Path)
    select.add_argument("--dedup-reviews", type=Path)
    select.add_argument("--link-health", required=True, type=Path)
    select.set_defaults(func=command_select)
    cluster = commands.add_parser("cluster")
    cluster.add_argument("--run", required=True, type=Path)
    cluster.add_argument("--clusters", required=True, type=Path)
    cluster.add_argument("--overrides", type=Path)
    cluster.add_argument("--set-reviews", type=Path)
    cluster.set_defaults(func=command_cluster)
    render = commands.add_parser("render")
    render.add_argument("--run", required=True, type=Path)
    render.add_argument("--excerpt-reviews", type=Path)
    render.add_argument("--semantic-reviews", type=Path)
    render.add_argument("--post-count-reviews", type=Path)
    render.add_argument("--output", required=True, type=Path)
    render.set_defaults(func=command_render)
    merge = commands.add_parser("merge")
    merge.add_argument("--inputs", required=True, nargs="+", type=Path)
    merge.add_argument("--output", required=True, type=Path)
    merge.add_argument("--backup-dir", type=Path)
    merge.add_argument("--report", type=Path)
    merge.set_defaults(func=command_merge)
    verify = commands.add_parser("verify")
    verify.add_argument("--run", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    verify.set_defaults(func=command_verify)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
