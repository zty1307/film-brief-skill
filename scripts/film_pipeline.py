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
from urllib.parse import urlparse


CHANNEL_PRIORITY = {"境内新闻": 5, "公众文章": 4, "今日头条": 3, "微博": 2, "小红书": 1, "抖音": 0}
CHANNEL_BASE = {"境内新闻": 3.5, "公众文章": 3.5, "今日头条": 2.5, "微博": 1.0, "小红书": 0.5, "抖音": -2.0}
POST_BASE = {"原帖": 3.0, "评论": -2.0, "转帖": -1.0}
SOCIAL_CHANNELS = {"微博", "小红书"}
ALLOWED_DECISIONS = {"retain_core", "retain_consensus", "exclude"}
ALLOWED_CLUSTER_STANCES = {"positive", "objective", "negative"}
ALLOWED_CLUSTER_REPORT_ROLES = {"report_point", "subtopic", "data_note", "rare_signal"}
ALLOWED_OBJECTIVE_SUBTYPES = {"neutral_fact", "sentiment_distribution", "balanced_observation"}
DRAMA_CLUSTER_SCOPES = {"pre_broadcast_expectation", "current_broadcast_reaction", "later_reputation", "mixed_time_explicit"}
VARIETY_CLUSTER_SCOPES = {"latest_episode", "previous_episode_prominent", "program_level_current", "mixed_scope_explicit"}
ALLOWED_DROP_FAILURE_CHECKS = {
    "target", "aspect", "stance", "self_contained", "work_consistency", "evidence_specificity"
}
CLUSTER_TITLE_PREDICATE = re.compile(
    r"^(?:肯定|认可|称赞|关注|讨论|质疑|批评|担忧|认为|看好|指出|反映|赞赏|期待|吐槽|不满|"
    r"呈现|展现|聚焦|强调|记录|客观梳理|客观记录|汇集|分析|以|仅以|把|对|有媒体|节目官方|开播反馈|"
    r"[^，。]{2,16}以)|，(?:认为|称|指出|反映|质疑|批评|担忧|引发|带来|体现|呈现|展现)"
)
GENERIC_CLUSTER_TITLE = re.compile(
    r"其他综合|未分类|待归类|兜底|当前片段(?:中的)?(?:相关事实|直接表达)|未形成完整评价"
)
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
PROMOTION_OPERATIONAL = re.compile(
    r"抽奖|随机抽|加抽|兑奖|礼包|赠票|扫码|二维码|购票|单人票|双人票|票价|报名|"
    r"活动规则|领取福利|点击链接|直播间下单"
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
    text = html.unescape(clean(value))
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


def promotion_review_markers(value: object) -> list[str]:
    """Find operational promotion language that requires an explicit opinion check."""
    return sorted(set(PROMOTION_OPERATIONAL.findall(clean(value))))


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
    body = data_value(data, "正文内容", "正文", "body") or clean(record.get("body"))
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
        "author": data_value(data, "用户名", "昵称", "作者", "author") or clean(record.get("author")) or "未署名",
        "certification_type": data_value(data, "认证类型（名人、媒体、企业等）", "认证类型", "certification_type"),
        "certification_info": data_value(data, "认证信息（认证主体）", "认证信息", "认证主体", "certification_info"),
        "url": safe_url(data_value(data, "发文链接", "原贴url", "url") or record.get("url")),
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


def filename_period(name: str) -> tuple[datetime | None, datetime | None]:
    match = re.search(r"(\d{4}\.\d{2}\.\d{2}) (\d{2})_(\d{2})至(\d{4}\.\d{2}\.\d{2}) (\d{2})_(\d{2})", name)
    if not match:
        return None, None
    start = datetime.strptime(" ".join(match.group(1, 2, 3)), "%Y.%m.%d %H %M")
    end = datetime.strptime(" ".join(match.group(4, 5, 6)), "%Y.%m.%d %H %M").replace(second=59)
    return start, end


def period_state(row: dict, config: dict) -> tuple[str, str]:
    batch_window = config.get("period_windows", {}).get(row["batch"], {})
    if batch_window:
        start = parse_datetime(batch_window.get("start"))
        end = parse_datetime(batch_window.get("end"))
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


def target_layers(target: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return explicit work anchors, ambiguous aliases, people/role helpers and comparison works."""
    strong = [clean(value) for value in target.get("strong_terms", []) if clean(value)]
    weak = [clean(value) for value in target.get("weak_terms", []) if clean(value)]
    auxiliary = [clean(value) for value in target.get("auxiliary_terms", []) if clean(value)]
    comparisons = [clean(value) for value in target.get("comparison_terms", []) if clean(value)]
    return strong, weak, auxiliary, comparisons


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
        r"鲜活|新鲜|创新|突破|值得|厉害|太会|稳了|共鸣|欢呼|好感|幽默|有趣|反差|进步|魅力|折服|热忱|有爱|心满意足|沉浸|走心|戳心|不可替代|灵魂"
    )
    negative_pattern = re.compile(
        r"难看|失望|拉胯|油腻|尴尬|出戏|违和|不合理|拖沓|注水|悬浮|差评|毁|魔改|弃剧|劝退|翻车|用力过猛|"
        r"过度|审美疲劳|消耗|消费|不适|没分寸|套路|生硬|低质"
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
    review_queue = []
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
            review_queue.append({
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
    write_jsonl(run_dir / "normalized_sources.jsonl", sources)
    write_jsonl(run_dir / "out_of_period_sources.jsonl", out_of_period)
    write_jsonl(run_dir / "source_decisions.auto.jsonl", decisions)
    write_jsonl(run_dir / "source_review_queue.jsonl", review_queue)
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


def resolve_source_review_evidence(review: dict, row: dict) -> tuple[str, list[int] | None]:
    """Validate review evidence or derive it safely from an exact source position."""
    source = source_text(row)
    position = review.get("evidence_position")
    supplied = clean(review.get("evidence"))
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
    link_health = load_link_health(args.link_health)
    expected_urls = {clean(row.get("url")) for row in sources.values() if clean(row.get("url"))}
    missing_link_checks = sorted(expected_urls - set(link_health))
    if missing_link_checks:
        raise ValueError(f"link_health 未覆盖 {len(missing_link_checks)} 个来源 URL，例如：{missing_link_checks[:3]}")
    review_queue = load_jsonl(run_dir / "source_review_queue.jsonl")
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
        evidence, evidence_position = resolve_source_review_evidence(review, row) if review else ("", None)
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"非法来源决定：{source_id} {decision}")
        if review and not reason:
            raise ValueError(f"AI复核缺少理由：{source_id}")
        if review and not evidence:
            raise ValueError(f"AI复核缺少逐字依据或 evidence_position：{source_id}")
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
        reviewed_states = {row["decision"] == "exclude" for row in reviewed}
        if len(reviewed_states) > 1:
            raise ValueError(f"同一 copy family 存在互相冲突的人工去留判断：{family[:6]}")
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


def keyword_pairs(definition: dict) -> list[tuple[str, float]]:
    result = []
    for item in definition.get("keywords", []):
        if isinstance(item, str):
            result.append((item, 1.0))
        elif isinstance(item, list) and len(item) >= 2:
            result.append((clean(item[0]), float(item[1])))
    return [(term, weight) for term, weight in result if term]


def validate_clusters(payload: dict, batches: set[str]) -> dict[str, list[dict]]:
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
    return definitions


def cluster_score(fragment: str, title: str, definition: dict) -> tuple[float, list[str]]:
    score, hits = 0.0, []
    for term, weight in keyword_pairs(definition):
        body_count = min(fragment.count(term), 3)
        title_count = min(title.count(term), 2)
        if body_count or title_count:
            hits.append(term)
            score += body_count * weight * 2.2 + title_count * weight * 0.6
    score += min(5.0, len(OPINION.findall(fragment)) * 0.7)
    required = [clean(term) for term in definition.get("required_any", []) if clean(term)]
    if required and not any(term in fragment for term in required):
        score -= 12.0
    negative = [clean(term) for term in definition.get("negative_cues", []) if clean(term)]
    if negative and not any(term in fragment for term in negative):
        score -= 20.0
    return score, hits


def best_window(row: dict, definition: dict, anchor_terms: list[str] | None = None, target_config: dict | None = None) -> dict:
    body = source_text(row)
    spans = sentence_spans(body)
    candidates = []
    strong_terms, weak_terms, _, comparisons = target_layers(target_config or {})
    title_strong = term_hits(clean(row.get("title")), strong_terms)
    title_comparisons = term_hits(clean(row.get("title")), comparisons)
    for start in range(len(spans)):
        for end in range(start, min(len(spans), start + 2)):
            fragment = body[spans[start]["start"]:spans[end]["end"]]
            score, hits = cluster_score(fragment, row.get("title", ""), definition)
            strong_hits = term_hits(fragment, strong_terms)
            weak_hits = term_hits(fragment, weak_terms)
            comparison_hits = term_hits(fragment, comparisons)
            if strong_hits:
                score += 30.0 + 5.0 * len(strong_hits)
            elif weak_hits:
                score += 8.0 + 2.0 * len(weak_hits)
            elif title_strong and not title_comparisons:
                score += 5.0
            else:
                score -= 25.0
            score -= 10.0 * len(comparison_hits)
            if anchor_terms:
                score += sum(14.0 for term in anchor_terms if term and term in fragment)
            candidates.append({"start": spans[start]["start"], "end": spans[end]["end"], "text": fragment, "score": round(score, 3), "hits": hits, "strong_target_hits": strong_hits, "weak_target_hits": weak_hits, "comparison_hits": comparison_hits})
    return max(candidates, key=lambda item: (item["score"], len(item["hits"]), -item["start"])) if candidates else {"start": 0, "end": len(body), "text": body, "score": 0.0, "hits": []}


def passage_alignment(
    row: dict,
    window: dict,
    definition: dict,
    target_config: dict,
    override: dict,
    secondary: bool = False,
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
    else:
        title_hits = term_hits(clean(row.get("title")), strong_terms)
        source_comparisons = term_hits(source, comparisons)
        target_passed = bool(title_hits and not source_comparisons)
        target_basis = "single_work_title_anchor" if target_passed else "no_passage_target_anchor"

    required = [clean(term) for term in definition.get("required_any", []) if clean(term)]
    aspect_hits = [term for term in required if term in text]
    aspect_passed = not required or bool(aspect_hits)

    expected = clean(definition.get("stance"))
    inferred = stance(text)
    if reviewed_stance:
        if reviewed_stance != expected:
            raise ValueError(f"{row['id']} 的 {stance_key} 与目标簇立场不一致")
        stance_passed, stance_basis = True, "ai_semantic_review"
    elif expected == "positive":
        stance_passed, stance_basis = inferred == "正向", "window_stance"
    elif expected == "negative":
        stance_passed, stance_basis = inferred == "负向", "window_stance"
    else:
        stance_passed = inferred == "混合或中性" or bool(definition.get("objective_meta"))
        stance_basis = "objective_meta" if definition.get("objective_meta") else "window_stance"

    return {
        "target": {"passed": target_passed, "basis": target_basis, "target_hits": target_hits, "strong_hits": strong_hits, "weak_hits": weak_hits, "comparison_hits": comparison_hits, "review_evidence": evidence},
        "aspect": {"passed": aspect_passed, "hits": aspect_hits},
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


def cluster_set_review_fingerprint(batch: str, definition: dict, members: list[dict]) -> str:
    payload = {
        "batch": batch,
        "cluster_id": str(definition["id"]),
        "title": clean(definition.get("title")),
        "stance": clean(definition.get("stance")),
        "source_ids": sorted({item["source_id"] for item in members}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def cluster_member_review_fingerprint(batch: str, definition: dict, member: dict) -> str:
    payload = {
        "batch": batch,
        "cluster_id": str(definition["id"]),
        "title": clean(definition.get("title")),
        "stance": clean(definition.get("stance")),
        "source_id": member["source_id"],
        "passage": clean(member.get("window", {}).get("text")),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cluster_member_review_map(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("scope") != "current_period_cluster_member_semantic_reviews":
        raise ValueError("cluster_member_reviews scope 错误")
    reviews = payload.get("reviews")
    if not isinstance(reviews, dict):
        raise ValueError("cluster_member_reviews 缺少 reviews 对象")
    return {str(key).strip(): value for key, value in reviews.items() if isinstance(value, dict)}


def validate_cluster_member_review(
    batch: str,
    definition: dict,
    member: dict,
    review: dict | None,
    title_claim_count: int,
) -> list[str]:
    source_id = member["source_id"]
    fingerprint = cluster_member_review_fingerprint(batch, definition, member)
    passage = clean(member.get("window", {}).get("text"))
    review = review or {}
    issues = []
    if clean(review.get("fingerprint")) != fingerprint:
        issues.append("review_fingerprint_missing_or_stale")
    if clean(review.get("decision")) != "pass":
        issues.append("review_decision_not_pass")
    for field in (
        "target_passed",
        "title_support_passed",
        "stance_passed",
        "scope_checked",
        "source_role_checked",
    ):
        if review.get(field) is not True:
            issues.append(f"{field}_not_true")
    evidence = clean(review.get("evidence"))
    if len(normalized(evidence)) < 4:
        issues.append("evidence_too_short")
    elif evidence not in passage:
        issues.append("evidence_not_in_assigned_passage")
    claim_indices = review.get("supported_claim_indices")
    if (
        not isinstance(claim_indices, list)
        or not claim_indices
        or any(not isinstance(index, int) or index < 0 or index >= title_claim_count for index in claim_indices)
    ):
        issues.append("supported_claim_indices_invalid")
    if not clean(review.get("reason")):
        issues.append("reason_missing")
    return issues


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
    member_passages = {
        item["source_id"]: clean(item.get("window", {}).get("text"))
        for item in members
    }
    issues = []
    review = review or {}
    if clean(review.get("fingerprint")) != fingerprint:
        issues.append("review_fingerprint_missing_or_stale")
    if clean(review.get("decision")) != "pass":
        issues.append("review_decision_not_pass")
    for field in (
        "title_claims_passed",
        "stance_purity_passed",
        "scope_purity_passed",
        "granularity_passed",
        "source_role_checked",
    ):
        if review.get(field) is not True:
            issues.append(f"{field}_not_true")
    for field in ("reason", "scope_reason", "granularity_reason", "source_role_reason"):
        if not clean(review.get(field)):
            issues.append(f"{field}_missing")

    report_role = clean(review.get("report_role"))
    if report_role not in ALLOWED_CLUSTER_REPORT_ROLES:
        issues.append("report_role_invalid")
    if clean(definition.get("title")).startswith("记录") and report_role != "data_note":
        issues.append("record_title_requires_data_note_role")
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
    claims = review.get("title_claims")
    title_claim_support: set[str] = set()
    if not isinstance(claims, list) or not claims:
        issues.append("title_claims_missing")
    else:
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or not clean(claim.get("claim")):
                issues.append(f"title_claim_{index}_invalid")
                continue
            support = claim.get("supporting_source_ids")
            if not isinstance(support, list) or not support:
                issues.append(f"title_claim_{index}_support_missing")
            elif any(clean(source_id) not in member_ids for source_id in support):
                issues.append(f"title_claim_{index}_support_outside_cluster")
            else:
                title_claim_support.update(clean(source_id) for source_id in support)
    if title_claim_support != member_ids:
        issues.append("title_claim_support_does_not_cover_all_members")

    member_support = review.get("member_support")
    if not isinstance(member_support, dict):
        issues.append("member_support_missing")
    else:
        support_ids = {clean(source_id) for source_id in member_support}
        if support_ids != member_ids:
            issues.append("member_support_does_not_cover_all_members")
        claim_count = len(claims) if isinstance(claims, list) else 0
        for source_id in sorted(member_ids):
            item = member_support.get(source_id)
            if not isinstance(item, dict):
                issues.append(f"member_support_{source_id}_invalid")
                continue
            evidence = clean(item.get("evidence"))
            if len(normalized(evidence)) < 4:
                issues.append(f"member_support_{source_id}_evidence_too_short")
            elif evidence not in member_passages.get(source_id, ""):
                issues.append(f"member_support_{source_id}_evidence_not_in_passage")
            claim_indices = item.get("claim_indices")
            if (
                not isinstance(claim_indices, list)
                or not claim_indices
                or any(not isinstance(index, int) or index < 0 or index >= claim_count for index in claim_indices)
            ):
                issues.append(f"member_support_{source_id}_claim_indices_invalid")
            elif isinstance(claims, list):
                for index in claim_indices:
                    claim_support = claims[index].get("supporting_source_ids", []) if isinstance(claims[index], dict) else []
                    if source_id not in {clean(value) for value in claim_support}:
                        issues.append(f"member_support_{source_id}_claim_link_missing")

    objective_subtype = clean(review.get("objective_subtype"))
    if clean(definition.get("stance")) == "objective":
        if review.get("objective_purity_passed") is not True:
            issues.append("objective_purity_not_true")
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
    for field in (
        "reader_load_reviewed",
        "overfragmentation_checked",
        "overbreadth_checked",
        "no_forced_merge_or_split",
    ):
        if review.get(field) is not True:
            issues.append(f"{field}_not_true")
    if review.get("cluster_count") != cluster_count:
        issues.append("cluster_count_incorrect")
    if clean(review.get("range_status")) != expected_range_status:
        issues.append("range_status_incorrect")
    reason = clean(review.get("reason"))
    if len(normalized(reason)) < 8:
        issues.append("reason_missing_or_too_short")
    exception_required = expected_range_status != "within_range"
    if bool(review.get("exception_approved")) != exception_required:
        issues.append("exception_approval_incorrect")
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
    definitions = validate_clusters(load_json(args.clusters.resolve()), {row["batch"] for row in sources})
    overrides = flatten_overrides(args.overrides)
    set_reviews = cluster_set_review_map(args.set_reviews)
    count_reviews = cluster_count_review_map(args.set_reviews)
    member_reviews = cluster_member_review_map(args.member_reviews)
    by_id = {batch: {str(item["id"]): item for item in values} for batch, values in definitions.items()}
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    low_confidence = []
    assignment_audit = []
    for row in sources:
        batch_defs = definitions[row["batch"]]
        target_config = config["targets"][row["batch"]]
        scores = []
        for definition in batch_defs:
            window = best_window(row, definition, target_config=target_config)
            scores.append((window["score"], definition, window))
        scores.sort(key=lambda item: item[0], reverse=True)
        override = overrides.get(row["id"], {})
        if override and not clean(override.get("reason")):
            raise ValueError(f"归簇修正缺少 reason：{row['id']}")
        primary_id = clean(override.get("cluster")) or str(scores[0][1]["id"])
        if primary_id not in by_id[row["batch"]]:
            raise ValueError(f"未知簇修正：{row['id']} -> {primary_id}")
        primary_def = by_id[row["batch"]][primary_id]
        primary_window = best_window(row, primary_def, [clean(term) for term in override.get("anchor_terms", [])], target_config)
        primary_alignment = passage_alignment(
            row, primary_window, primary_def, config["targets"][row["batch"]], override
        )
        second_score = next((score for score, definition, _ in scores if str(definition["id"]) != primary_id), 0.0)
        margin = primary_window["score"] - second_score
        required_negative = [clean(term) for term in primary_def.get("negative_cues", []) if clean(term)]
        issue = alignment_issue(primary_alignment)
        if not issue and primary_window["score"] <= 0:
            issue = "no_positive_cluster_evidence"
        elif not issue and required_negative and not any(term in primary_window["text"] for term in required_negative):
            issue = "negative_cluster_missing_negative_cue"
        elif not issue and not override and primary_window["score"] < 5:
            issue = "low_cluster_evidence"
        elif not issue and not override and margin < 1.5:
            issue = "small_top_two_margin"
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
            low_confidence.append({"source_id": row["id"], "batch": row["batch"], "provisional_cluster": primary_id, "issue": issue, "title": row["title"], "body": source_text(row), "top_scores": [{"cluster": str(definition["id"]), "title": definition["title"], "score": score} for score, definition, _ in scores[:3]]})
        secondary_id = clean(override.get("secondary_cluster"))
        if secondary_id:
            if secondary_id == primary_id or secondary_id not in by_id[row["batch"]]:
                raise ValueError(f"第二簇修正不合法：{row['id']} -> {secondary_id}")
            secondary_def = by_id[row["batch"]][secondary_id]
            secondary_window = best_window(row, secondary_def, [clean(term) for term in override.get("secondary_anchor_terms", [])], target_config)
            if max(primary_window["start"], secondary_window["start"]) < min(primary_window["end"], secondary_window["end"]):
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
                low_confidence.append({"source_id": row["id"], "batch": row["batch"], "provisional_cluster": secondary_id, "issue": secondary_issue, "title": row["title"], "body": source_text(row)})

    set_review_input = []
    set_review_queue = []
    member_review_input = []
    member_review_queue = []
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
            input_item = {
                "review_key": review_key,
                "fingerprint": fingerprint,
                "batch": batch,
                "cluster_id": str(definition["id"]),
                "title": definition["title"],
                "stance": definition["stance"],
                "content_mode": clean(config["targets"][batch].get("content_mode")) or "serial_drama",
                "independent_sources": size,
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
                    }
                    for item in members
                ],
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

            set_review = set_reviews.get(review_key, {})
            title_claims = set_review.get("title_claims") if isinstance(set_review, dict) else []
            title_claim_count = len(title_claims) if isinstance(title_claims, list) else 0
            for member in members:
                source_id = member["source_id"]
                member_key = f"{batch}\t{definition['id']}\t{source_id}"
                member_input = {
                    "review_key": member_key,
                    "fingerprint": cluster_member_review_fingerprint(batch, definition, member),
                    "batch": batch,
                    "cluster_id": str(definition["id"]),
                    "title": definition["title"],
                    "stance": definition["stance"],
                    "title_claims": title_claims,
                    "source_id": source_id,
                    "channel": member["source"].get("channel", ""),
                    "author": member["source"].get("author", ""),
                    "published": member["source"].get("published", ""),
                    "episode_scope": member["source"].get("episode_scope", ""),
                    "passage": clean(member.get("window", {}).get("text")),
                }
                member_review_input.append(member_input)
                member_issues = validate_cluster_member_review(
                    batch,
                    definition,
                    member,
                    member_reviews.get(member_key),
                    title_claim_count,
                )
                if member_issues:
                    member_review_queue.append({**member_input, "issues": member_issues})

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
            chosen = order_for_workbench(members)
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
    write_json(run_dir / "clustered_items.json", {"definitions": definitions, "items": selected})
    write_json(run_dir / "workbench_order_audit.json", {"random": False, "clusters": order_audit})
    write_json(run_dir / "cluster_assignment_audit.json", {"assignments": assignment_audit, "cluster_members": cluster_members})
    write_jsonl(run_dir / "cluster_review_queue.jsonl", low_confidence)
    write_json(run_dir / "cluster_set_review_input.json", {"scope": "current_period_cluster_set_review_input", "clusters": set_review_input})
    write_jsonl(run_dir / "cluster_set_review_queue.unresolved.jsonl", set_review_queue)
    write_json(run_dir / "cluster_count_review_input.json", {"scope": "current_period_cluster_count_review_input", "batches": count_review_input})
    write_jsonl(run_dir / "cluster_count_review_queue.unresolved.jsonl", count_review_queue)
    write_json(run_dir / "cluster_count_review_audit.json", count_review_audit)
    write_json(run_dir / "cluster_member_semantic_review_input.json", {"scope": "current_period_cluster_member_semantic_review_input", "members": member_review_input})
    write_jsonl(run_dir / "cluster_member_semantic_review_queue.unresolved.jsonl", member_review_queue)
    summary = {"status": "CLUSTERED", "retained_sources": len(sources), "assignments": len(assignment_audit), "workbench_items": len(selected), "unique_workbench_sources": len({item["source_id"] for item in selected}), "low_confidence_unresolved": len(low_confidence), "cluster_set_review_unresolved": len(set_review_queue), "cluster_count_review_unresolved": len(count_review_queue), "cluster_member_semantic_review_unresolved": len(member_review_queue), "batches": dict(Counter(item["batch"] for item in selected))}
    write_json(run_dir / "cluster_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def excerpt_candidates(body: str, window: dict, definition: dict) -> list[dict]:
    spans = sentence_spans(body)
    anchored = [index for index, span in enumerate(spans) if span["start"] < window["end"] and span["end"] > window["start"]]
    if not anchored and spans:
        anchored = [min(range(len(spans)), key=lambda index: abs(spans[index]["start"] - window["start"]))]
    indexes = sorted(set(index for anchor in anchored for index in (anchor - 1, anchor, anchor + 1) if 0 <= index < len(spans)))
    candidates = []
    for start in indexes:
        for end in range(start, min(len(spans), start + 2)):
            if not any(index in anchored for index in range(start, end + 1)):
                continue
            raw = [body[spans[index]["start"]:spans[index]["end"]] for index in range(start, end + 1)]
            visible = " ".join(part.strip() for part in raw)
            display_length = len(clean_display_excerpt(visible))
            if len(normalized(visible)) < 18 or display_length > EXCERPT_MAX or LEADING_FRAGMENT.search(visible):
                continue
            cluster_value, _ = cluster_score(visible, "", definition)
            voice = len(JUDGEMENT.findall(visible)) * 2.2 + len(OPINION.findall(visible)) * 0.6
            length_bonus = min(display_length, EXCERPT_PREFERRED_MIN) / 20
            if display_length < EXCERPT_PREFERRED_MIN:
                length_bonus -= (EXCERPT_PREFERRED_MIN - display_length) / 12
            candidates.append({"fragments": raw, "positions": [[spans[index]["start"], spans[index]["end"]] for index in range(start, end + 1)], "excerpt": visible, "score": round(cluster_value * 1.4 + voice + length_bonus, 3)})
    if not candidates:
        raw = body[window["start"]:window["end"]]
        if len(raw) > EXCERPT_MAX:
            cut = max((raw.rfind(mark, EXCERPT_PREFERRED_MIN, EXCERPT_MAX + 1) + 1 for mark in "。！？；!?;"), default=0)
            raw = raw[:cut or EXCERPT_MAX]
        return [{"fragments": [raw], "positions": [[window["start"], window["start"] + len(raw)]], "excerpt": raw.strip(), "score": 0.0}]
    candidates.sort(key=lambda item: (-item["score"], item["positions"][0][0]))
    return candidates


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
        tag_start < start and end < tag_end
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
                    if clean(semantic.get("decision")) != "keep" or not clean(semantic.get("reason")):
                        failures.append(f"样本缺少已通过的最终语义复核：{source} / {view_id}")
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
    cluster_member_unresolved = load_jsonl(run_dir / "cluster_member_semantic_review_queue.unresolved.jsonl") if (run_dir / "cluster_member_semantic_review_queue.unresolved.jsonl").exists() else []
    if cluster_member_unresolved:
        summary = {
            "status": "REVIEW_REQUIRED",
            "stage": "cluster_member_semantic_review",
            "unreviewed_members": len(cluster_member_unresolved),
            "output_not_replaced": str(args.output.resolve()),
        }
        write_json(run_dir / "render_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1)
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
    failures = []
    drop_reason_counts: Counter[tuple[str, str, str]] = Counter()
    drop_counts: Counter[tuple[str, str]] = Counter()
    cluster_display_audit = []
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
                try:
                    review = excerpt_reviews.get(view_id)
                    if review:
                        fragments = [str(value) for value in review.get("fragments", [])]
                        supplied_positions = review.get("positions")
                        positions = validate_fragment_positions(body, fragments, supplied_positions) if supplied_positions else locate_fragments(body, fragments)
                        raw_excerpt = " ".join(fragment.strip() for fragment in fragments)
                        basis = "ai_verbatim_review"
                    else:
                        candidate = excerpt_candidates(body, item["window"], definition)[0]
                        fragments, positions, raw_excerpt = candidate["fragments"], candidate["positions"], candidate["excerpt"]
                        basis = "deterministic_extractive_candidate"
                    validate_fragment_positions(body, fragments, positions)
                    excerpt = clean_display_excerpt(raw_excerpt)
                    if not excerpt:
                        raise ValueError("展示片段清除话题标签、平台表情、链接和账号标记后为空，需回看全文重截")
                    if display_excerpt_has_markup(excerpt):
                        raise ValueError("展示片段清理后仍含话题标签、平台表情、链接或账号标记")
                    if len(excerpt) > EXCERPT_MAX:
                        raise ValueError(f"展示片段超过 {EXCERPT_MAX} 字，需保留核心判断和具体依据后重截")
                except (ValueError, IndexError) as exc:
                    failures.append({"view_id": view_id, "stage": "verbatim", "issue": str(exc)})
                    continue

                # 最终立场不再复用归簇阶段的 passage_stance；它由独立摘录语义复核给出。
                final_alignment = passage_alignment(
                    row, {"text": excerpt}, definition,
                    period_config["targets"][batch], {},
                )
                promotion_markers = promotion_review_markers(excerpt)
                short_excerpt = len(excerpt) < EXCERPT_PREFERRED_MIN
                semantic_input = {
                    "view_id": view_id, "batch": batch, "cluster_id": str(definition["id"]),
                    "cluster_title": definition["title"], "cluster_stance": definition["stance"],
                    "excerpt": excerpt, "adjacent_context": clean(item.get("window", {}).get("text")),
                    "promotion_markers": promotion_markers,
                    "excerpt_length": len(excerpt),
                    "preferred_length": [EXCERPT_PREFERRED_MIN, EXCERPT_MAX],
                    "short_excerpt": short_excerpt,
                    "instruction": "独立判断最终摘录是否指向目标作品、支持该方面、立场一致、作品内部信息不矛盾且可独立理解；摘录优先控制在70至150字，并同时保留完整判断和至少一处具体依据。短于70字时先回看全文补足同一观点、同一立场的依据；全文确实没有可补内容时，只有摘录仍包含具体台词、动作、情节、表演处理、争议事实或可复核分析依据才可 keep，并必须填写 short_excerpt_justified=true、具体 short_excerpt_reason、specific_support_passed=true 和摘录中的逐字 specific_support_evidence。作品标签、人物名及好看、封神、绝了、笑点拉满、期待等泛泛态度不算具体依据，即使原文再无内容也应 drop，failed_checks 填 evidence_specificity。禁止拼入无关内容凑字。超过150字不能 keep。所有合格独立表达都应 keep，不设每簇数量配额。纯抽奖、购票、报名、礼包、扫码、活动规则或演员资料没有独立观点，不能 keep；若摘录含 promotion_markers，keep 时必须另给 independent_opinion_passed=true 和摘录中的逐字 opinion_evidence。来源中夹有宣传内容但另有完整判断时，重截并保留判断。只有回看全文并尝试重截后仍不合格才可 drop；若只是方面或立场错配，必须先检查能否转入其他现有簇或形成新簇",
                }
                semantic_inputs.append(semantic_input)
                semantic = semantic_reviews.get(view_id)
                item_failures = []
                if semantic is None:
                    semantic_pending.append(semantic_input)
                    item_failures.append("缺少独立的最终摘录语义复核")
                else:
                    semantic_decision = clean(semantic.get("decision"))
                    if semantic_decision not in {"keep", "drop"}:
                        item_failures.append("最终摘录语义复核 decision 必须为 keep 或 drop")
                    if not clean(semantic.get("reason")):
                        item_failures.append("最终摘录语义复核缺少理由")
                    if semantic_decision == "keep":
                        reviewed_stance = clean(semantic.get("stance"))
                        if reviewed_stance != clean(definition["stance"]):
                            item_failures.append(f"摘录立场 {reviewed_stance or '空'} 与观点簇立场不一致")
                        for field, label in (("target_passed", "目标作品"), ("aspect_passed", "评价方面"), ("work_consistency_passed", "作品内部信息一致性"), ("self_contained", "片段自足性")):
                            if semantic.get(field) is not True:
                                item_failures.append(f"{label}未通过独立语义复核")
                        for field, label in (("target_evidence", "目标证据"), ("aspect_evidence", "方面证据"), ("stance_evidence", "立场证据"), ("work_consistency_evidence", "作品一致性证据")):
                            evidence = clean(semantic.get(field))
                            evidence_scope = raw_excerpt if field in {"target_evidence", "work_consistency_evidence"} else excerpt
                            if not evidence or evidence not in evidence_scope:
                                scope_label = "后台逐字原文片段" if field in {"target_evidence", "work_consistency_evidence"} else "清洁后的最终摘录"
                                item_failures.append(f"{label}不是{scope_label}中的逐字子串")
                        if promotion_markers:
                            opinion_evidence = clean(semantic.get("opinion_evidence"))
                            if semantic.get("independent_opinion_passed") is not True:
                                item_failures.append("含促销操作词的摘录未确认存在独立观点")
                            if not opinion_evidence or opinion_evidence not in excerpt:
                                item_failures.append("含促销操作词的摘录缺少最终摘录中的逐字观点证据")
                        if short_excerpt:
                            if semantic.get("short_excerpt_justified") is not True:
                                item_failures.append("不足70字的摘录未确认全文无法补足同观点依据")
                            if not clean(semantic.get("short_excerpt_reason")):
                                item_failures.append("不足70字的摘录缺少逐来源例外理由")
                            support_evidence = clean(semantic.get("specific_support_evidence"))
                            if semantic.get("specific_support_passed") is not True:
                                item_failures.append("不足70字的摘录未确认含有可复核的具体依据")
                            if not support_evidence or support_evidence not in excerpt:
                                item_failures.append("不足70字的摘录缺少最终摘录中的逐字具体依据")
                    elif semantic_decision == "drop" and not item_failures:
                        drop_reason = clean(semantic.get("reason"))
                        failed_checks = semantic.get("failed_checks")
                        if not isinstance(failed_checks, list) or not failed_checks:
                            item_failures.append("drop 必须填写至少一项 failed_checks")
                        else:
                            invalid_checks = sorted({clean(value) for value in failed_checks} - ALLOWED_DROP_FAILURE_CHECKS)
                            if invalid_checks:
                                item_failures.append(f"drop 含未知 failed_checks：{invalid_checks}")
                        if semantic.get("reexcerpt_attempted") is not True:
                            item_failures.append("drop 前必须回看全文并确认 reexcerpt_attempted=true")
                        if isinstance(failed_checks, list) and ({"aspect", "stance"} & set(failed_checks)):
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
                            drop_reason_counts[(batch, str(definition["id"]), reason_key)] += 1
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
                if semantic:
                    final_alignment["target"] = {**final_alignment["target"], "passed": semantic.get("target_passed") is True, "basis": "independent_final_excerpt_semantic_review", "review_evidence": clean(semantic.get("target_evidence"))}
                    final_alignment["aspect"] = {**final_alignment["aspect"], "passed": semantic.get("aspect_passed") is True, "review_evidence": clean(semantic.get("aspect_evidence"))}
                    final_alignment["stance"] = {**final_alignment["stance"], "passed": clean(semantic.get("stance")) == clean(definition["stance"]), "reviewed": clean(semantic.get("stance")), "basis": "independent_final_excerpt_semantic_review", "review_evidence": clean(semantic.get("stance_evidence"))}

                staged_item = {"viewId": view_id, "sourceId": row["id"], "channel": row["channel"], "author": row["author"], "title": row["title"], "publishedAt": row["published"], "url": safe_url(row["url"]), "excerpt": excerpt, "body": body, "sourceStance": row.get("stance", ""), "clusterStance": definition["stance"], "mediaAuthority": {"subjectId": row.get("media_subject_id", ""), "subjectName": row.get("media_subject_name", ""), "accountAlias": row.get("media_account_alias", ""), "accountType": row.get("media_account_type", ""), "tier": row.get("media_authority_tier", "unclassified"), "rank": int(row.get("media_authority_rank", 0)), "basis": row.get("media_authority_basis", "")}, "linkHealth": row.get("link_health", {}), "alignment": final_alignment, "semanticReview": semantic or {}, "factWarning": row.get("fact_warning", ""), "excerptProvenance": {"fragments": fragments, "positions": positions, "basis": basis, "displayNormalization": DISPLAY_NORMALIZATION}, "excerptLengthReview": {"length": len(excerpt), "preferredMin": EXCERPT_PREFERRED_MIN, "max": EXCERPT_MAX, "exception": short_excerpt, "reason": clean((semantic or {}).get("short_excerpt_reason")) if short_excerpt else "", "specificSupportPassed": (semantic or {}).get("specific_support_passed") is True if short_excerpt else True, "specificSupportEvidence": clean((semantic or {}).get("specific_support_evidence")) if short_excerpt else ""}}
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
            cluster_drop_total = drop_counts[(batch, str(definition["id"]))]
            if cluster_drop_total >= 5:
                repeated_reason, repeated_count = max(
                    (
                        (reason, count)
                        for (reason_batch, reason_cluster, reason), count in drop_reason_counts.items()
                        if reason_batch == batch and reason_cluster == str(definition["id"])
                    ),
                    key=lambda pair: pair[1],
                )
                if repeated_count / cluster_drop_total >= 0.6:
                    failures.append({
                        "view_id": f"{batch}::{definition['id']}",
                        "stage": "semantic_review_quality",
                        "issue": f"同一模板化删除理由覆盖 {repeated_count}/{cluster_drop_total} 条（已剥离理由中引用的逐条原文后统计）：{repeated_reason}。必须逐条回看全文、先尝试重截，并写出真正不同的失败项与具体原因",
                    })
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
        if reviewed_count.get("status") != "PASS" or reviewed_count.get("cluster_count") != len(clusters):
            failures.append({
                "view_id": f"{batch}::cluster-count",
                "stage": "cluster_count_after_excerpt_review",
                "issue": (
                    f"终审后工作台含 {len(clusters)} 个观点簇，与已审数量 "
                    f"{reviewed_count.get('cluster_count', '缺失')} 不一致；请修复空簇或重跑整期簇数审查"
                ),
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
    write_jsonl(run_dir / "excerpt_semantic_review_input.jsonl", semantic_inputs)
    write_jsonl(run_dir / "excerpt_semantic_review_queue.unresolved.jsonl", semantic_pending)
    write_jsonl(run_dir / "excerpt_semantic_review_rejected.jsonl", semantic_rejected)
    write_json(run_dir / "cluster_display_coverage_audit.json", {"clusters": cluster_display_audit})
    write_json(run_dir / "render_failures.json", {"failures": failures})
    if failures:
        summary = {"status": "REVIEW_REQUIRED", "staged_items": staged_dataset["total"], "accepted_items": dataset["total"], "reviewed_dropped_items": len(semantic_rejected), "failures": len(failures), "unreviewed_final_excerpts": len(semantic_pending), "output_not_replaced": str(args.output.resolve())}
        write_json(run_dir / "render_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    write_json(run_dir / "workbench_dataset.json", dataset)
    write_json(run_dir / "excerpt_provenance.json", {"items": provenance})
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(dataset), encoding="utf-8")
    summary = {"status": "RENDERED", "output": str(output), "items": dataset["total"], "reviewed_dropped_items": len(semantic_rejected), "unique_sources": len({item["sourceId"] for batch in dataset["batches"] for cluster in batch["clusters"] for item in cluster["items"]}), "clusters": {batch["name"]: batch["clusterCount"] for batch in dataset["batches"]}, "single_file_html": True}
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
    cluster_member_unresolved = load_jsonl(run_dir / "cluster_member_semantic_review_queue.unresolved.jsonl") if (run_dir / "cluster_member_semantic_review_queue.unresolved.jsonl").exists() else []
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
                if clean(semantic.get("decision")) != "keep" or not clean(semantic.get("reason")):
                    failures.append(f"缺少有效的独立最终摘录语义复核：{item['viewId']}")
                if semantic.get("work_consistency_passed") is not True:
                    failures.append(f"作品内部信息一致性未通过：{item['viewId']}")
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
    if cluster_member_unresolved:
        failures.append(f"仍有 {len(cluster_member_unresolved)} 条成员与观点标题语义审查未完成")
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
        "unreviewed_cluster_members": len(cluster_member_unresolved),
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
    cluster.add_argument("--member-reviews", type=Path)
    cluster.set_defaults(func=command_cluster)
    render = commands.add_parser("render")
    render.add_argument("--run", required=True, type=Path)
    render.add_argument("--excerpt-reviews", type=Path)
    render.add_argument("--semantic-reviews", type=Path)
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
