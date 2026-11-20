"""Build a traceable, human-reviewable film opinion draft from final_dataset.json.

Historical reports are never read by ``prepare`` or ``render``. ``compare`` is a
separate post-draft evaluation step, so old wording cannot leak into a new draft.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from opinion_visuals import validate_outline, chart_data, draw_chart
from chart_coverage import PROFILE as CHART_PROFILE, contract as chart_contract, empty_review
from report_style import (
    STYLE, LIMITS as MANUAL_LIMITS, REPORT_LEAD_VERBS, CRITICISM_LEAD_VERBS,
    compile_plan, criticism_lead, paragraph_measure,
)


NUMERALS = ("一","二","三","四","五","六","七","八","九","十","十一","十二","十三","十四","十五","十六")
VALID_STANCES = {"正向词倾向":"positive","客观词倾向":"objective","中性或混合":"objective","负向词倾向":"negative","positive":"positive","objective":"objective","negative":"negative"}
VALID_VERBS = {"称", "认为", "表示"}
GENERIC_ATTRIBUTIONS = {"微信公众号", "微博网民", "小红书网民", "抖音网民",
                        "境内新闻", "今日头条", "来源待核", "公众文章",
                        "公众号", "公众号文章", "公众账号", "微信公众号文章", "度小视", "搜狐移动版", "MSN新闻", "百度百家", "百家号", "网易号",
                        "腾讯", "新浪新闻", "微博", "小红书", "抖音", "微信"}
UNVERIFIED_QUANTITIES = re.compile(r"\d+(?:[.,]\d+)?\s*(?:万|亿|%|％|套|播放量|预约量|热度值|收视率|市占率)")
UNVERIFIED_SCALE = re.compile(r"(?:\d+(?:[.,]\d+)?|[零一二三四五六七八九十百千万两]+)\s*(?:亩|公顷|平方米|套服装|套造型)")
UNVERIFIED_CHINESE_QUANTITIES = re.compile(r"[一二三四五六七八九十百千两]+(?:万|亿)(?:元|次|套|件|播放|预约|投资|资金|热度|收视)")
UNVERIFIED_PROCESS_OR_HEARSAY = re.compile(r"据说|听说|网传|传闻|(?:审核|审查|过审).{0,16}(?:删改|删减|重剪|补拍)")
NOVELTY_ASSERTION = re.compile(r"填补.{0,16}空白|前所未有|(?:行业|同类题材).{0,6}(?:首部|首次)")
OPINION_FRAMING = re.compile(r"认为|被.{0,8}(?:视为|称为)|有.{0,8}(?:称|评价)")
HEARSAY_CRITICISM = re.compile(r"遭批|被批评|被质疑|被指|被吐槽|核心争议(?:是|在于)|引发.{0,4}争议|(?:网友|观众).{0,5}(?:认为|质疑|争议|吐槽)")
NEGATIVE_EVALUATION = re.compile(r"难看|失望|拉胯|油腻|尴尬|出戏|违和|不合理|拖沓|注水|悬浮|差评|弃剧|劝退|翻车|用力过猛|审美疲劳|不适|套路|生硬|低质|质疑|吐槽|看衰|不满|担心|短板|糟糕|老套|僵硬|呆板|乏味|缺乏|不足|重复|雷同|复制|发腻|疲劳|不符|不贴|不太像|不真实|脱离|削弱|用烂|看多了|遭批|被批|乱七八糟|看不懂|没看懂|(?:剧情|叙事|节奏|观感).{0,4}(?:不行|偏慢|过慢|太慢|有点慢|比较缓慢|过于缓慢)|没看爽|看不爽|不够爽")
UNVERIFIED_PRODUCTION_DURATION = re.compile(
    r'(?:拍(?:了|摄)(?:了)?|拍(?:这场|吻|打)戏(?:拍)?(?:了)?|单场戏(?:拍)?(?:了)?)\s*'
    r'(?:(?:将近|接近|大约|整整|足足|大概|超过|不到|用了|花了|耗时|用时|需要|长达|持续|花费|时间为|时长为|近|约|要)|[，,:：\s]){0,3}'
    r'(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千两]+)(?:多|余|来)?(?:个)?(?:小时|天|遍|条)')
PROMO_MUSIC_EVIDENCE = re.compile(r'抖音.{0,8}(?:配乐|背景音乐|bgm|乐用的是|用的是.{0,6}歌)|(?:宣传|预热|剧宣|短视频|物料).{0,10}(?:配乐|背景音乐|bgm)',re.I)
PROMO_MUSIC_FRAME = re.compile(r'宣传|预热|剧宣|短视频|物料|抖音')
SPECULATIVE_ORGANIZER = re.compile(r"(?:官方|剧方|制作方|平台|公司).{0,15}(?:刻意|有意|规避|缺位|不足|为零|为0|零宣发|无宣传|降低.{0,4}风险|回避.{0,4}风险|控制.{0,4}风险)")
RELAYED_OPINION = re.compile(r'(?:网友|网民|观众|用户|粉丝|书粉|评论区).{0,6}(?:表示|说|称|认为|反馈|提到|直言|赞道|吐槽)|有人(?:表示|说|称|认为|提到)')
NEGATIVE_HEADING_SIGNAL = re.compile(r"负面|争议|分歧|争论|褒贬|意见不一|质疑|批评|吐槽|不满|短板|槽点|偏慢|过慢|太慢|拖沓|劝退|疲劳|不足|乏力|悬浮|失真|违和|不符|欠佳|受挫")
UNSUPPORTED_GUARANTEE = re.compile(r"(?:班底|阵容|导演|编剧|主演|老戏骨).{0,16}(?:保障|背书)|(?:保障|背书).{0,10}(?:品质|质量|口碑|节奏)")
UNSUPPORTED_AGGREGATE_CLAIM = re.compile(r"(?:观众|网民|舆论|好评|反馈|口碑).{0,12}(?:最集中|最多|一致|普遍|主流|大多数|绝大多数)|最集中.{0,5}(?:好评|反馈|评论|声音)")
WHOLE_WEB_JUDGMENT = re.compile(r'全网(?:都|也|在|网友|观众|一片|一致|疯狂|集中|纷纷){0,3}(?:吐槽|批评|好评|称赞|认可|骂)')
UNVERIFIED_AGGREGATE_CAUSALITY = re.compile(r'直接导致.{0,12}(?:口碑|热度|收视).{0,5}(?:崩盘|暴跌|下滑)')
REPORT_JUDGMENT = r'(?:肯定|认可|称赞|赞赏|认为|指出|质疑|批评|讨论|分析|关注|看好|看衰|期待|担忧|担心|热议|吐槽|点赞|赞扬|探讨)'
FILTER_FREE_ASSERTION = re.compile(r'(?:无|零|没有(?:使用|用|添加)?|未(?:使用|用|添加)|不(?:使用|用|添加))(?:任何|额外)?滤镜')
MARKETING_JUDGMENT = re.compile(r'剧宣|宣发|宣传|营销|营业|CP红利',re.I)
COLLABORATION_COUNT = re.compile(r'(?:两|二|三|四|五|六|七|八|九|十|\d+)搭|第?[一二三四五六七八九十\d]+次合作')
ACTING_JUDGMENT = re.compile(r'演技|表演|演员|对手戏|对视|眼神')
OFFSCREEN_PERFORMANCE_CAUSE = re.compile(r'(?:戏外|私下|综艺).{0,70}(?:带到|带入|带进|转化为|转化成|延续到|所以|因此|因而).{0,30}(?:戏里|剧中|角色|表演|对手戏)')
UNRELATED_APPEARANCE_CRITICISM = re.compile(r'(?:造型|妆造|服化道|妆容|服装).{0,8}(?:照搬|套路|难看|粗糙|廉价|劣质|扬短避长)')
CONCRETE_NEGATIVE_OBJECT = re.compile(r'剧情|叙事|节奏|铺垫|改编|原著|剧本|台词|配音|声线|音色|声画|表演|演技|角色|人设|人物|逻辑|剪辑|镜头|滤镜|服装|妆容|妆造|服化道|造型|特效|画面|场景|配乐|音乐|OST|BGM|宣发|宣传|营销|物料|群像|CP',re.I)
FIT_QUALITY_CLAIM = re.compile(r'(?:位置|时机|时点|节点|运用|选用|选择|衔接|卡点|入点|进入|介入|处理).{0,5}(?:准确|精准|恰当|得当|到位)|(?:完美|恰好|恰当|准确|精准)(?:地)?(?:贴合|呼应|衔接)')
FIT_EVALUATION = re.compile(r'准确|精准|恰当|得当|到位|贴合|契合|呼应|符合|适合|恰到好处|相得益彰|巧妙|自然|协调|妥帖|合适|丝丝入扣|严丝合缝|融为一体|相融')
UNSUPPORTED_LENGTH_QUALITY_CLAIM = re.compile(r"\d+集.{0,24}(?:砍去|删去|规避|没有|无|不).{0,8}注水")
PREVIEW_EVIDENCE = re.compile(r"预告(?:片)?|先导片|开播前|尚未开播|未播物料|待播物料")
PREVIEW_CLAIM = re.compile(r"预告(?:片)?|先导片|开播前|播前|物料|待播")
POST_RELEASE_EVIDENCE = re.compile(r"正片|播出后|开播后|首播后|看完|追完|前几集|已播|已经播出")
HYPOTHETICAL_EVIDENCE = re.compile(r"我猜|大概率|倘若|假如|(?:如果|要是).{0,20}(?:会|就)|会不会|恐怕")
HYPOTHETICAL_FRAME = re.compile(r"担忧|担心|忧|预期|猜测|恐|或将|可能|假设")
NARRATIVE_TOPIC = re.compile(r"剧情|叙事|故事|节奏|铺垫|改编|穿书|系统|复仇|套路")
VISUAL_TOPIC = re.compile(r"造型|服化道|妆容|服装|画面|镜头|特效|白衣|白发|吐血|视觉|打戏")
NEGATIVE_MECHANISM_CATEGORIES = (
    re.compile(r"原著|改编|魔改|删减|书粉"),
    re.compile(r"节奏|拖沓|注水|铺垫|线索过密|晦涩"),
    re.compile(r"创业|职场|事业线|职业|悬浮"),
    re.compile(r"感情|情感|恋爱|爱情|CP|关系转折"),
    re.compile(r"造型|服化道|妆容|服装|画面|镜头|视觉|打戏|特效"),
    re.compile(r"演技|表演|台词|配音"),
)
NEGATED_CRITICISM = re.compile(r"(?:不|没|没有|无)(?:会|让人|令人|至于|算|觉得)?(?:拖沓|尴尬|出戏|违和|拉胯|注水|悬浮|失望|难看|生硬|无聊)")
ABSOLUTE_PRODUCTION_PATTERNS = (
    re.compile(r"全程.{0,20}(?:实景|外景|棚拍|棚景|绿幕)"),
    re.compile(r"全剧.{0,20}(?:实景|外景|棚拍|棚景|绿幕)"),
    re.compile(r"(?:没有|无|零)(?:任何)?(?:棚拍|棚景|绿幕)"),
    re.compile(r"(?:百分之百|100\s*%)(?:实景|实拍)"),
)
CORRELATIVE_START = re.compile(r"不但|不仅")
CORRELATIVE_FINISH = re.compile(r"而且|反而|并且|还(?:会|能|有|是|要|让|给|在|将|把|可)|[，,；;]\s*还(?=[\u3400-\u9fff])|也|更|甚至")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty")
    return value.strip()


def source_label(sample: dict) -> str:
    data = sample["source"]["data"]
    channel = sample.get("source_channel", "")
    name = next((str(data.get(k, "")).strip() for k in ("账号昵称", "帐号昵称", "昵称", "用户名")
                 if str(data.get(k, "")).strip() not in ("", "-")), "")
    site = str(data.get("站点名称", "")).strip()
    if site in ("-", "—", "无"):
        site = ""
    if sample.get("media_subject_name"):
        alias = str(sample.get("media_account_alias") or "").strip()
        if alias and not any(mark in alias for mark in (".", "/", ":")):
            return alias
        return site if site and site not in GENERIC_ATTRIBUTIONS else sample["media_subject_name"]
    if channel == "微博":
        return f"微博网民“{name}”" if name else "微博网民"
    if channel == "小红书":
        return f"小红书网民“{name}”" if name else "小红书网民"
    if channel == "公众文章":
        return f"微信公众号“{name}”" if name else "微信公众号"
    if channel == "抖音":
        return f"抖音网民“{name}”" if name else "抖音网民"
    site_prefix = {"网易": "网易号", "百度百家": "百家号", "今日头条": "头条号"}.get(site)
    if site_prefix and name:
        return f"{site_prefix}“{name}”"
    return f"{site}“{name}”" if site and name else (site or name or channel or "来源待核")


def allowed_attributions(sample: dict) -> list[str]:
    """Names that can be read from source metadata; never infer a publisher."""
    data = sample["source"]["data"]
    label = source_label(sample)
    options = {label} if label not in GENERIC_ATTRIBUTIONS else set()
    site = str(data.get("站点名称") or "").strip()
    if site in ("-", "—", "无"):
        site = ""
    subject = str(sample.get("media_subject_name") or "").strip()
    if subject:
        options.add(subject)
    alias = str(sample.get("media_account_alias") or "").strip()
    if subject and alias and not any(mark in alias for mark in (".", "/", ":")):
        options.add(alias)
    if site and site not in GENERIC_ATTRIBUTIONS and site not in ("微信公众号", "微信", "微博", "小红书", "抖音", "腾讯", "新浪新闻"):
        options.add(site)
    return ([label] if label in options else []) + sorted(options - {label})


def contains_critical_judgment(value: str) -> bool:
    return bool(NEGATIVE_EVALUATION.search(NEGATED_CRITICISM.sub("", value)))


def has_terminal_punctuation(value: str) -> bool:
    return bool(re.search(r'''[。！？!?][”’"']*$''', value))


def confirmed_critical_evidence(item: dict) -> bool:
    if contains_critical_judgment(item.get("quote_snippet", "")):
        return True
    reason = item.get("criticism_reason")
    return isinstance(reason, str) and len(reason.strip()) >= 12


def normalized_target_title(target: str) -> str:
    target = target.strip()
    while target.startswith("《") and target.endswith("》"):
        target = target[1:-1].strip()
    return target


def has_absolute_production_assertion(value: str) -> bool:
    return any(pattern.search(value) for pattern in ABSOLUTE_PRODUCTION_PATTERNS)


def has_dangling_correlative(value: str) -> bool:
    """Catch a quote cut after 不但/不仅 without its balancing judgement."""
    for match in CORRELATIVE_START.finditer(value):
        following = re.split(r"[，,；;。！？!?]", value[match.end():])
        if not CORRELATIVE_FINISH.search("，".join(following[:3])):
            return True
    return False


def indexed_samples(dataset: dict) -> dict[str, tuple[int, dict]]:
    result = {}
    for group_index, group in enumerate(dataset["groups"]):
        for sample in group["samples"]:
            cid = sample["candidate_id"]
            if cid in result:
                raise ValueError(f"duplicate candidate_id: {cid}")
            result[cid] = (group_index, sample)
    return result


def display_text(sample: dict) -> str:
    # Older finished workbenches predate the display_excerpt field.
    return (sample.get("display_excerpt") or sample["excerpt"]).strip()


def prose_exclusion_reason(group: dict) -> str | None:
    """Keep an overall reception summary out of the concrete-viewpoint outline."""
    from report_evidence import release_notice_only
    samples = group.get('samples', [])
    if samples and all(release_notice_only(sample.get('display_excerpt') or sample.get('excerpt', ''))
                       for sample in samples):
        return '只含歌曲发布资讯，缺少独立的听感或创作评价'
    text=str(group.get('aspect') or '')+' '+str(group.get('core_mechanism') or '')
    if group.get('category')=='播出与传播观察' and group.get('objective_kind')=='neutral_fact':
        return '纯播出/传播事实是背景信息，不独立充作第二部分创作观点'
    if group.get('category')=='播出与传播观察' and (
            group.get('objective_kind')=='opinion_distribution' or
            re.search(r'口碑.{0,5}(?:两极|分化|撕裂)|评价.{0,5}(?:两极|分化)|者有之.{0,20}亦有之',text)):
        return '总体口碑/数据与评价分布是背景概括，不与其下的具体正负观点并列成文；选实际创作判断，不用总括补点数'
    return None


def has_dangling_scene_fragment(quote: str) -> bool:
    return any(re.fullmatch(r'(?:还有|以及)(?:在|当).*(?:那里|的时候|那一段|那场戏)',
                            part.strip(' \t\r\n“”「」\"'))
               for part in re.split(r'[。！？!?；;]',quote))


def quote_relays_criticism(sample: dict,quote: str) -> bool:
    """Cropping a relay marker from the same sentence does not create a direct voice."""
    text=display_text(sample);contexts=[];offset=0
    while (start:=text.find(quote,offset))>=0:
        before=list(re.finditer(r'[。！？!?；;\n]',text[:start]))
        left=before[-1].end() if before else 0
        end=start+len(quote);after=re.search(r'[。！？!?；;\n]',text[end:])
        right=end if re.search(r'[。！？!?；;][”’\"\']*$',quote) else end+after.start()+1 if after else len(text)
        contexts.append(text[left:right]);offset=start+1
    return bool(contexts) and all(HEARSAY_CRITICISM.search(context) for context in contexts)


def quote_crops_relay_owner(sample: dict,quote: str) -> bool:
    """Preserve who expressed an opinion, regardless of stance or channel."""
    text=display_text(sample);findings=[];offset=0
    while (start:=text.find(quote,offset))>=0:
        boundaries=list(re.finditer(r'[。！？!?；;\n]',text[:start]))
        left=boundaries[-1].end() if boundaries else 0
        prefix=text[left:start]
        # A complete earlier sentence cannot change the owner of this sentence.
        markers=list(RELAYED_OPINION.finditer(prefix))
        if not markers or RELAYED_OPINION.search(quote):
            findings.append(False)
        else:
            suffix=prefix[markers[-1].end():]
            independent=(re.search(r'(?:但|不过|然而|而我)\s*$',suffix)
                         or re.match(r'(?:但|不过|然而|我却|我则|我反而)',quote))
            findings.append(not bool(independent))
        offset=start+1
    return bool(findings) and all(findings)


def concise_section_heading(heading: str,target: str) -> str:
    prefixes=(f'电视剧《{target}》舆论：',f'《{target}》舆论：',
              '主要舆论观点：','舆论观点：','舆论：')
    for _ in range(3):
        prefix=next((p for p in prefixes if heading.startswith(p)),None)
        if prefix is None:break
        heading=heading[len(prefix):].strip()
    return nonempty(heading,'section_heading具体判断')


def has_report_judgment(claim: str) -> bool:
    lead=re.sub(r'^(?:有|部分|少数)?(?:评论|声音|观点|文章|媒体|观众|网民|网友|书粉|舆论|自媒体)','',claim)
    lead = re.sub(r'^(?:持续|继续)', '', lead)
    return bool(re.match(r'^' + REPORT_JUDGMENT, lead)
                or re.match(r'^(?:从|据|结合|基于|在)[^。；;！？]{0,18}' + REPORT_JUDGMENT, lead)
                )


def direct_social_quote_options(sample: dict) -> list[dict]:
    if sample.get('source_channel') not in ('微博','小红书') or not allowed_attributions(sample):return []
    from evidence_quality import quote_options
    return [q for q in quote_options(display_text(sample))
            if 8<=len(q['quote'])<=140 and contains_critical_judgment(q['quote'])
            and not quote_relays_criticism(sample,q['quote'])
            and not has_dangling_scene_fragment(q['quote'])
            and not UNVERIFIED_PROCESS_OR_HEARSAY.search(q['quote'])]


def review_unused_social_sources(point: dict,available: dict) -> list[dict]:
    """Prefer qualifying social spans, with an auditable exception for a genuine mismatch."""
    if not available:return []
    rows=point.get('social_source_review',[])
    if not isinstance(rows,list) or any(not isinstance(v,dict) for v in rows):
        raise ValueError('social_source_review须逐条说明为何未用已有直接社交批评')
    ids=[v.get('candidate_id') for v in rows]
    if any(not isinstance(v,str) for v in ids) or len(ids)!=len(set(ids)) or set(ids)!=set(available):
        raise ValueError('本组已有合格直接社交批评，优先采用至少一条；确不支持当前成文判断时用social_source_review逐条绑定差异，不以热度或曾用过为由跳过：'+str(sorted(available)))
    for row in rows:
        sample=available[row['candidate_id']];snippet=row.get('quote_snippet')
        if (row.get('reason_kind') not in ('not_supporting_current_claim','not_independent','not_a_direct_judgment')
                or len(str(row.get('reason') or '').strip())<16 or not isinstance(snippet,str)
                or len(snippet)<8 or snippet not in display_text(sample)):
            raise ValueError('social_source_review须有具体reason_kind、至少16字差异理由及该展示句内至少8字原句；渠道优先不等于强塞不支持的句子')
    return rows


def check_dataset(dataset: dict, dataset_path: Path) -> None:
    if dataset_path.name != "final_dataset.json":
        raise ValueError("writing accepts only the final_dataset.json shown in the completed workbench")
    if dataset.get("semantic_verification") != "FINAL_SELECTED_ONLY" or dataset.get("writing_only"):
        raise ValueError("writing requires the completed workbench dataset; writing-stage supplements are forbidden")
    from human_selection import POLICY, validate_dataset
    if dataset.get("quantity_contract") == POLICY:
        validate_dataset(dataset, dataset_path)
    status_path = dataset_path.with_name("final_review_status.json")
    if dataset.get("quantity_contract") != POLICY and not status_path.is_file():
        raise ValueError("writing requires a completed final workbench review in final_review_status.json")
    from workbench_completion import review_readiness
    if dataset.get("quantity_contract") != POLICY:
        review_readiness(dataset, read_json(status_path))
    if not dataset.get("groups"):
        raise ValueError("final dataset has no groups")
    for group in dataset["groups"]:
        for sample in group.get("samples", []):
            if sample.get("ai_decision") != "keep":
                raise ValueError(f"unreviewed sample in final dataset: {sample.get('candidate_id')}")
            if sample.get("writing_supplement"):
                raise ValueError(f"writing-stage supplement is forbidden: {sample.get('candidate_id')}")
            check_original(sample)
    from source_policy import verify_unique_samples, policy_exclusion
    verify_unique_samples(dataset)
    if any(policy_exclusion(s['source']) for g in dataset['groups'] for s in g['samples']):
        raise ValueError('已排除来源不能因数量提示进入写稿')


def prepare(args: argparse.Namespace) -> None:
    dataset_path = Path(args.dataset)
    dataset = read_json(dataset_path)
    check_dataset(dataset, dataset_path)
    inventory = {
        "target": dataset["target"],
        "dataset_sha256": sha256(dataset_path),
        "source_scope": dataset.get("semantic_verification"),
        "writing_overlap_pairs": dataset.get("writing_overlap_pairs", []),
        "opinion_scope_rule":"具体宣发批评可入稿；纯热度/档期事实仍是背景，不能按宣发或粉丝等词一刀切排除。",
        "quotation_owner_rule":"所有立场均保留原评价者；同句的网友表示等标记不能裁掉后改成作者本人判断。",
        "claim_form_rule":"claim必须以适用的评价动词开头。评价对象、播前等时间范围写在动词后。段前句概括一类舆论，具体情节、歌词和人物路径放入引文。全文按实际含义选动词，避免连续使用相同句式。",
        "plan_contract": writing_plan_contract(),
        "chart_profile": CHART_PROFILE, "chart_contract": chart_contract(),
        "chart_theme_policy": __import__('chart_coverage').THEME_POLICY,
        "chart_boundary_candidates": __import__('chart_coverage').boundary_candidates(dataset),
        "report_style": STYLE,
        "editorial_style": MANUAL_LIMITS,
        "groups": [],
    }
    from workbench_completion import review_readiness
    from human_selection import POLICY
    inventory['workbench_completion'] = ({'ready':True, 'scope':'HUMAN_SELECTED_REVIEWED_SAMPLES'} if dataset.get('quantity_contract') == POLICY else review_readiness(dataset, read_json(dataset_path.with_name('final_review_status.json'))))
    from evidence_quality import manuscript_families
    families=manuscript_families([s for g in dataset['groups'] for s in g['samples']])
    inventory['copy_family_rule']='同稿转载即使网址/账号不同也只算一个来源；不可在不同点改截片段规避。只比较已审样本自带的原文做出处核对，不提供未审素材。相似观点不等于同稿。'
    for group_index, group in enumerate(dataset["groups"]):
        item = {"group_index": group_index, "stance": group["stance"],
                "objective_kind":group.get('objective_kind'),
                "aspect": group["aspect"], "category":group.get("category",""), "core_mechanism":group.get("core_mechanism",""), "samples": []}
        item['prose_exclusion_reason']=prose_exclusion_reason(group)
        if group.get('history_review'):item['history_review']=group['history_review']
        guide=group.get('writing_guidance')
        if isinstance(guide,dict):item['writing_guidance']=guide
        for sample in group["samples"]:
            source = sample["source"]
            data = source["data"]
            item["samples"].append({
                "candidate_id": sample["candidate_id"], "copy_family":families.get(sample["candidate_id"]),
                "suggested_attribution": source_label(sample),
                "allowed_attributions": allowed_attributions(sample),
                "usable_for_writing": bool(allowed_attributions(sample)),
                "channel": sample.get("source_channel"),
                "media_subject_name": sample.get("media_subject_name") or None,
                "source_file": source["source_file"], "sheet": source["sheet"],
                "row": source["row"], "published_at": data.get("发表时间"),
                "source_title": data.get("标题"),
                "display_excerpt": display_text(sample),
                "primary_support_snippets": __import__('chart_coverage').primary_snippets(group,sample),
                "quote_options": [{"quote_option_id":q['quote_option_id'],"preview":q['quote'][:55]+('…' if len(q['quote'])>55 else ''),"start":display_text(sample).find(q['quote']),"length":len(q['quote'])} for q in __import__('evidence_quality').quote_options(display_text(sample)) if len(q['quote']) <= (140 if dataset.get('writing_contract')=='FOCUSED_QUOTES_V2' else 180)],
                "selection_hint": "先选具体做法/变化与观感。菜单preview可能截短，只用于定位；quote_option_id回填的是display_excerpt所列位置的完整连续原句，勿抄省略号。也可自选展示句内连续quote。不是语义通过。",
                "writing_supplement": bool(sample.get("writing_supplement")),
            })
            if VALID_STANCES.get(group['stance'])=='negative':
                options=direct_social_quote_options(sample)
                item['samples'][-1]['direct_social_criticism_hint']=bool(options)
                item['samples'][-1]['direct_social_quote_option_ids']=[q['quote_option_id'] for q in options]
        if isinstance(guide,dict):
            preferred={v['candidate_id']:v['quote_snippet'] for v in guide.get('preferred',[])}
            for row in item['samples']:
                row['preferred_support_snippet']=preferred.get(row['candidate_id'])
                row['usable_for_writing']=row['usable_for_writing'] and guide['eligible']
            item['samples'].sort(key=lambda row:row['candidate_id'] not in preferred)
        if VALID_STANCES.get(group['stance'])=='negative':
            item['samples'].sort(key=lambda row:not row.get('direct_social_criticism_hint',False))
        if item['prose_exclusion_reason']:
            for row in item['samples']:row['usable_for_writing']=False
        inventory["groups"].append(item)
    if dataset.get('discovery_continuity'):
        inventory['history_inputs']=dataset['discovery_continuity'].get('history_inputs',[])
        inventory['history_rule']='只用groups内最终history_review；命名阶段的旧说明不是最终留样的变化证据。历史仅比较，不作本期引文。'
    outdir = Path(args.outdir)
    comparison_sha=None
    if getattr(args,'period_comparison',None):
        from period_compare import validate_comparison
        comparison=read_json(Path(args.period_comparison))
        if comparison.get('dataset_sha256')!=inventory['dataset_sha256']:raise ValueError('连续取材比较不属于当前工作台')
        validate_comparison(dataset,comparison)
        write_json(outdir/'period_comparison.json',comparison)
        comparison_sha=sha256(outdir/'period_comparison.json')
        inventory['period_comparison']=comparison
    from period_history import load_history,continuity_review
    history=load_history(getattr(args,'history',[]),dataset['target'],getattr(args,'history_plan',[]))
    history_hash=None
    if history['entries']:
        history_file=outdir/'period_history_context.json'
        write_json(history_file,history);history_hash=sha256(history_file)
        inventory['continuity_review']=continuity_review(dataset,history)
    elif dataset.get('continuity_review'):
        inventory['continuity_review']=dataset['continuity_review']
    if dataset.get('writing_contract')=='FOCUSED_QUOTES_V2':
        inventory['writing_limits']={'max_sources_per_point':None,'preferred_sources_per_point':[3,4],'max_quote_characters':140,
            'max_claim_characters':48,'min_quote_characters':8,'short_complete_quote_needs_extra_length_reason':False,
            'rule':'每点通常三至四条独立互补的具体声音，充分时两条，短评有新增信息时可选5—6条，不为数量凑样本，每段至少两条独立有效声音，单条不能独立成段。引文逐句与其他点位对照，裁去跨点评述和泛赞尾句；不补原表。'}
    inventory["writing_limits"].update(MANUAL_LIMITS)
    if dataset.get("quantity_contract") == POLICY:
        from human_selection import configure_inventory
        configure_inventory(inventory, dataset)
    write_json(outdir / "candidate_inventory.json", inventory)
    from model_packet import writing_input
    writing_input(inventory, outdir)
    write_json(outdir / "editorial_plan.json", {
        "target": dataset["target"], "dataset_sha256": inventory["dataset_sha256"],
        "review_state": "AI_PREVIEW", "report_style": STYLE, "chart_profile": CHART_PROFILE, "chart_theme_policy": __import__('chart_coverage').THEME_POLICY, "chart_review": empty_review(inventory["dataset_sha256"]), "section_heading": "", "points": [],
        "human_review_notes": [], "max_point_chars": 380,
        "outline_review": {"point_boundaries": [], "pairwise_review": [], "omitted_groups": []},
        "history_sha256":history_hash,
        "period_comparison_sha256":comparison_sha,
    })
    print(json.dumps({"status": "REVIEW_PLAN_REQUIRED", "inventory": str(outdir / "candidate_inventory.json"),
                      "plan": str(outdir / "editorial_plan.json"), "groups": len(inventory["groups"])}, ensure_ascii=False))


def writing_plan_contract() -> dict:
    """Expose the existing public answer shape; placeholders are not judgments."""
    return {
        "purpose": "按本结构直接填写当前计划；先写出一版再按真实反馈修正，不预读校验源码猜测潜在错误。空字符串/空列表仅为结构占位，不能原样提交。没有提供任何本期选题、答案或引文。",
        "required_top_level": {"report_style":"固定MANUAL_BRIEF_V1，保留prepare值", "section_heading":"1—40字，以规定判断动词起句，概括正文具体评价对象与判断，含负面时体现负面；不写段数、分组、合并等写作过程", "points":"正文至少6段，证据充分可写7段。批评最多一段并放最后，计入总段数，图表、总标题和图注不计。工作台不足以支撑6个独立观点时报告缺口，不补原表、不拆近义点。", "max_point_chars":380, "review_state":"固定AI_PREVIEW；target/dataset_sha256/history_sha256/period_comparison_sha256保持prepare值"},
        "lead_verbs": {"ordinary": list(REPORT_LEAD_VERBS), "criticism": list(CRITICISM_LEAD_VERBS)},
        "point_template": {"claim":"", "chart_label":"", "group_indices":[], "samples":[{"candidate_id":"", "quote":"", "verb":"称", "selection_reason":""}], "claim_support":[{"claim_index":0,"evidence":[{"candidate_id":"","quote_snippet":""}]}], "added_information_quote":"", "length_exception_reason":"", "continuity_reason":""},
        "point_fields": {"claim":"段前句通常15—35字，最多48字，一句概括评价对象和核心判断，可补一层有据效果。具体情节、歌词和动作留在引文。首词须为允许的评价动词，详见lead_verbs；编号、此外和批评段的部分声音均由脚本生成。按逗号、分号逐分句绑定claim_support", "chart_label":"4—30字", "group_indices":"零起始整数；同组全篇只归一点；一个点的组须同立场", "samples":"通常3—4条独立互补有效声音；两条充分时可用两条，短评有新增信息时可选5—6条，不为数量凑样本；本版单条不能独立成段。quote为当前展示句内连续8—140字；不能用同义句或无关尾句补字数", "selection_reason":"每条至少8字说明具体信息价值", "attribution":"可省略由脚本使用已有署名；填写时只能用本条allowed_attributions；样本verb仅称/认为/表示，可省略为称；段前句的吐槽/质疑等写在claim", "single_source_reason":"兼容旧字段，当前答卷省略；它不能豁免段落长度。本版每段至少2条独立有效声音", "continuity_reason":"有历史时每点至少12字；只承接最终history_review，无变化如实延续", "claim_support":"每个逗号/分号分句对应1项，claim_index零起始；每项evidence非空，quote_snippet为该条已选quote内连续至少6字；所有选样均须被绑定"},
        "outline_review_template": {"point_boundaries":[{"point_index":0,"core_question":"","boundary":""}], "pairwise_review":[{"left":0,"right":1,"distinction":""}], "omitted_groups":[{"group_index":0,"reason":""}]},
        "outline_fields": {"point_boundaries":"按points顺序每点1项；core_question、boundary各至少12字；说不清边界即合并/收窄/省去", "pairwise_review":"同方面或近义风险组合填零起始left/right与至少12字真实区别；可覆盖所有实际组合，不能填不存在或重复的组合。writing_overlap_pairs中分别写成两段的组，pairwise_review的evidence须有两条，分别绑定两段当前入稿原句中不同的具体评价", "omitted_groups":"所有未归正文点的组都填，恰好一次；reason至少12字实际理由"},
        "conditional_fields": {"added_information_quote":"第5条及以后的每条samples项内填写：该条引文内连续至少8字的新支持信息，此前引文没有该片段；selection_reason解释互补价值。通常前四条不额外要求此字段；不能以不同措辞冒充信息增量", "social_source_review":"负面点未用微博/小红书且同组有直接社交批评选项时逐条填[{candidate_id,quote_snippet,reason_kind,reason}]；snippet为该展示句内连续至少8字；reason至少16字；reason_kind仅not_supporting_current_claim/not_independent/not_a_direct_judgment", "criticism_reason":"负面绑定片段缺少词表强贬义但确为直接批评时，在对应evidence内填至少12字语义说明，不把转述或中性描述变成亲评", "format":"普通点省略或numbered；唯一负面点编为criticism_summary并移到末尾。该点criticism_opening选numbered或aside，两种形式只能选一项，省略时沿用编号；兼容旧format=aside。claim直接从吐槽、质疑、认为、担忧或看衰起句。脚本补部分声音。各批评分句各有引文，不另加第二个争议或批评段", "length_exception_reason":"普通段目标200—250字（含编号、小标题、出处、标点，去空白）；180—300为常规范围。范围外须有至少12字具体原因，但不能豁免至少2条独立有效声音，不为凑字补原表或泛赞", "name_normalizations":"只在需要规范称谓时于对应samples内填[{from,to,basis}]并保留审计，不能猜来源"},
        "support_limit":"引用说某物出现在某处，只能证明出现；声称时机/位置准确、处理恰当等还需所选原句含相应评价，不能仅凭场景清单推优点。以上结构不代替语义审阅。",
        "chart_review_contract":chart_contract(),
        "manual_style_rule":"先写肯定认可等有据判断，讨论点居中，主要批评合为最后一段；不得用小标题、主创履历和阵容保证凑篇幅，每段通常3—4条不同信息的声音；两条充分时可用两条，短评有新增信息时可选5—6条，不为数量凑样本，段落要有充分的细节而非几句短卡片。不要机械按图占比排正文。不得为正面在前而改变原立场或把未核数据、传闻、纯名单写成好评。",
        "quote_scope_rule":"宣发批评裁去合作次数/别剧履历；配音点裁去独立妆造批评；表演评价不以戏外默契带入戏内作证明；没有杂乱滤镜不等于制作无滤镜。只用清单已有连续原句。不能将创作应当如何的条件要求当作已经做到的好评；普通点不并列负面造型缺陷和人物写法缺陷；唯一末尾criticism_summary可简要汇总主要批评，但各分句须独立绑定真实原评；替演员分责不代替具体声音观感。",
        "preferred_short_negative_rule":"preferred指优选的具体信息，不强迫夹带整段的其他方面；同一preferred内8—140字、含明确评价对象与具体批评的完整连续判断可独立保留；不是按短句长度特批。正面具体层次/服饰细节仍不能裁成泛赞，其他语义、归属和逐字出处检查不变。",
    }


def unsupported_fit_judgment(claim: str, quotes: list[str]) -> bool:
    return bool(FIT_QUALITY_CLAIM.search(claim) and not any(FIT_EVALUATION.search(q) for q in quotes))


def asserts_no_filter(value: str) -> bool:
    for match in FILTER_FREE_ASSERTION.finditer(value):
        before=value[max(0,match.start()-8):match.start()]
        if re.search(r'(?:不是|并非|不等于|不能说|不要说|仿佛|宛如|宛若|好像|如同|像是|看着像|看起来像)$',before):
            continue
        if value[match.end():].startswith('感'):
            continue
        return True
    return False


def claim_scope_issue(claim: str, stance: str) -> str | None:
    if (stance=='negative' and UNRELATED_APPEARANCE_CRITICISM.search(claim)
            and re.search(r'(?:人设|人物塑造|人物性格).{0,8}(?:糟糕|不合理|崩塌|崩坏|单薄|混乱|失真|反派无异)',claim)
            and re.search(r'，|；|同时|而且|以及',claim)):
        return '同一点并列造型缺陷和人物写法缺陷；从现有工作台取一个机制，原样本只能归一点，不能拆同源凑点'
    return None


def quotation_scope_issue(claim: str, quote: str, stance: str) -> str | None:
    if (stance=='positive' and re.search(r'导演|编剧|指导|造型师|班底|团队|主创|阵容',quote)
            and re.search(r'底牌|保驾护航|质量.{0,2}保证|证明过自己|证明了自己|未播先稳',quote)):
        return '主创履历或阵容背书不能代替本点的当前作品具体效果；仅保留同一已审句中真正评价当前服饰、镜头、表演等效果的连续原句，不足可短写或省去，不用此类话凑篇幅'
    if (stance=='positive' and re.match(r'^(?:每个(?:人|角色|人物)|所有(?:人|角色|人物))',quote)
            and re.search(r'都得(?:有|符合|具备|遵循|做到|表现|体现|展现)|都(?:要|应当|应该|必须|需要)',quote)
            and re.search(r'否则|不然|才能',quote)
            and re.search(r'计谋|剧本|叙事|写法|人物逻辑|创作|编剧',quote)
            and not re.search(r'要求|期待|希望|应当|应该|需要',claim)):
        return '写作应当如何的条件要求，不能当作剧作已经做到的好评；换现有已审句的实际评价，不足就少用一条'
    if (stance=='negative' and re.search(r'配音|音色|声线|声画',claim)
            and re.search(r'(?:后期|剪辑|配音).{0,18}(?:拖进|拖入|卷入|背锅|拖累|毁掉).{0,8}(?:争议|演员|演技)|(?:演员|她|他).{0,12}(?:说了不算|背锅)',quote)):
        return '替演员分责或后期背锅的叙述不能替代具体配音观感；只截同一已审句内独立声音批评，不足则省去'
    if MARKETING_JUDGMENT.search(claim) and COLLABORATION_COUNT.search(quote):
        return '宣发观点只保留具体宣传评价，裁去演员合作次数、别剧经历等无关前缀尾句，不把这些资料当当前批评证据'
    if stance=='positive' and ACTING_JUDGMENT.search(claim) and OFFSCREEN_PERFORMANCE_CAUSE.search(quote):
        return '不能以戏外相处或综艺默契推导剧内表演好；保留已审句内实际剧中对手戏、眼神或动作的直接评价'
    if (re.search(r'配音|音色|声线|声画',claim)
            and not re.search(r'造型|妆造|服化道|妆容|服装',claim)
            and UNRELATED_APPEARANCE_CRITICISM.search(quote)):
        return '配音观点引文夹带独立妆造批评，裁去跨方面尾句，仅保留具体声音与声画评价'
    return None


def retains_preferred_support(quote: str, preferred: str, stance: str) -> bool:
    if preferred in quote:
        return True
    core=quote.strip(' 。！？；，.,!?;\n\t')
    if not (stance=='negative' and 8<=len(core)<=140 and core in preferred
            and CONCRETE_NEGATIVE_OBJECT.search(core)
            and (contains_critical_judgment(core) or HEARSAY_CRITICISM.search(core))):
        return False
    from evidence_quality import generic_quote
    return not generic_quote(core)


def check_original(sample: dict) -> None:
    data = sample["source"]["data"]
    original = data.get(sample["text_field"], "")
    if sample["excerpt"] != original[sample["start"]:sample["end"]]:
        raise ValueError(f"source position mismatch: {sample['candidate_id']}")


def quote_guard_issues(sample, quote, claim):
    """Independent quote constraints returned together, also used by the renderer."""
    cid=sample['candidate_id'];issues=[]
    guards=[
        (UNVERIFIED_QUANTITIES.search(quote) or UNVERIFIED_SCALE.search(quote) or UNVERIFIED_CHINESE_QUANTITIES.search(quote), '含未核实数值'),
        (WHOLE_WEB_JUDGMENT.search(quote) or UNVERIFIED_AGGREGATE_CAUSALITY.search(quote), '含无证全网态度或总体口碑因果'),
        (UNVERIFIED_PROCESS_OR_HEARSAY.search(quote), '含传闻或未核实制作过程'),
        (UNVERIFIED_PRODUCTION_DURATION.search(quote), '含未核实拍摄时长或次数'),
        (has_absolute_production_assertion(quote), '含绝对化制作断言'),
        (has_dangling_correlative(quote), '不但/不仅比较句不完整'),
        (has_dangling_scene_fragment(quote), '场景句残缺'),
        (PROMO_MUSIC_EVIDENCE.search(quote) and not PROMO_MUSIC_FRAME.search(claim), '宣传短视频BGM不能写成剧中配乐，claim须保留实际媒介范围'),
        (PREVIEW_EVIDENCE.search(quote) and not POST_RELEASE_EVIDENCE.search(quote) and not PREVIEW_CLAIM.search(claim), '预告/物料引文须在claim保留预告/物料范围'),
        (quote_crops_relay_owner(sample,quote), '裁掉了原评价者归属，须保留网友/观众等转述标记'),
    ]
    for invalid,message in guards:
        if invalid:issues.append(f'{cid}: {message}；从已审展示句保留完整具体判断，不补原文')
    return issues


def writing_preflight(dataset,plan):
    """Report independent quote, support and length problems before first-error guards."""
    lookup=indexed_samples(dataset);issues=[]
    for i,point in enumerate(plan.get('points',[])):
        claim=point.get('claim','');chosen=point.get('samples',[]);quotes={};negative_evidence={}
        if not isinstance(claim,str) or not isinstance(chosen,list):continue
        for choice in chosen:
            cid=choice.get('candidate_id')
            if cid not in lookup:continue
            gi,sample=lookup[cid]
            from evidence_quality import resolve_quote
            try:quote=resolve_quote(sample,choice)
            except (ValueError,KeyError,TypeError):continue
            if not isinstance(quote,str) or quote not in display_text(sample):continue
            quotes[cid]=quote
            issues.extend(f'段{i+1}：{e}' for e in quote_guard_issues(sample,quote,claim))
        for binding in point.get('claim_support',[]):
            for entry in binding.get('evidence',[]):
                cid=entry.get('candidate_id');snippet=entry.get('quote_snippet','')
                if cid in quotes and (not isinstance(snippet,str) or snippet not in quotes[cid]):
                    issues.append(f'段{i+1}：claim_support的{cid}证据不在本次所选quote内；同步修改选句和绑定')
                if cid in lookup and dataset['groups'][lookup[cid][0]]['stance']=='负向词倾向':
                    negative_evidence.setdefault(cid,[]).append(entry)
        for cid,entries in negative_evidence.items():
            if not any(confirmed_critical_evidence(entry) for entry in entries):
                issues.append(f'段{i+1}：{cid}在claim_support.evidence内须填criticism_reason至少12字解释原句实际批评，或改选有直接批评的原句')
    return list(dict.fromkeys(issues))


def render_content(dataset: dict, plan: dict) -> tuple[list[str], list[dict]]:
    plan=compile_plan(dataset,plan)
    manual_style=plan.get("report_style")==STYLE
    from source_policy import sample_keys
    from rapid_film_workbench import source_exclusion
    used_sample_keys=set()
    used_manuscript_samples=[]
    from evidence_quality import same_manuscript_evidence
    if plan.get("target") != dataset["target"]:
        raise ValueError("target mismatch")
    if plan.get("review_state") not in ("AI_PREVIEW", "HUMAN_CONFIRMED"):
        raise ValueError("review_state must be AI_PREVIEW or HUMAN_CONFIRMED")
    if plan["review_state"] == "HUMAN_CONFIRMED" and not plan.get("human_reviewer"):
        raise ValueError("human_reviewer required for HUMAN_CONFIRMED")
    heading = concise_section_heading(nonempty(plan.get("section_heading"), "section_heading"),dataset['target'])
    if manual_style and re.search(r'合并批评|正向观察|观点组装|写稿流程',heading):
        raise ValueError('section_heading须概括实际正文的具体评价对象与判断；不描述段数、分组、合并等写作过程。只修本字段，不能改变已审观点。')
    max_point_chars = plan.get("max_point_chars", 380)
    if not isinstance(max_point_chars, int) or isinstance(max_point_chars, bool) or max_point_chars < 120:
        raise ValueError("max_point_chars must be an integer of at least 120")
    if len(heading)>40:raise ValueError("section_heading最多40字，概括主要正负结论，避免过长标题孤字换行")
    if "\n" in heading:
        raise ValueError("section_heading must be one line")
    points = plan.get("points", [])
    if not isinstance(points, list) or not points:
        raise ValueError("at least one editorial point required")
    if any(any(dataset["groups"][group_index]["stance"] == "负向词倾向"
               for group_index in point.get("group_indices", [])) for point in points):
        if not NEGATIVE_HEADING_SIGNAL.search(heading):
            raise ValueError("section heading omits the negative concern selected in the draft")
    if len(points) > 8 and plan["review_state"] == "AI_PREVIEW" and not dataset.get("human_selection"):
        raise ValueError("brief opinion draft has more than eight points; merge or omit lower-value points")
    sample_index = indexed_samples(dataset)
    used_ids = set()
    paragraphs, audit = [], []
    numbered_count = 0
    aside_count = 0
    point_errors = []
    for index, point in enumerate(points):
        try:
            point_format = point.get("format", "numbered")
            if point_format not in ("numbered", "aside", "criticism_summary"):
                raise ValueError(f"invalid point format: {point_format}")
            is_aside = point_format == "aside" or (point_format == "criticism_summary" and point.get("criticism_opening") == "aside")
            if is_aside:
                aside_count += 1
                if aside_count > 1 or index != len(points) - 1:
                    raise ValueError("one optional aside paragraph must be last")
            else:
                if aside_count:
                    raise ValueError("numbered points must precede aside")
                if numbered_count >= len(NUMERALS):
                    raise ValueError("too many points for the fixed numeral template")
                numbered_count += 1
            claim = nonempty(point.get("claim"), f"point {index} claim").rstrip("。；; ")
            if manual_style and re.search(r"[。！？\r\n]", claim):
                raise ValueError(f"point {index}段前句须为一个完整陈述句，具体展开放入引文")
            if manual_style:
                allowed_leads = CRITICISM_LEAD_VERBS if point_format == 'criticism_summary' else REPORT_LEAD_VERBS
                if not claim.startswith(allowed_leads):
                    raise ValueError(
                        f'point {index}的claim须从以下评价动词直接起句：{"、".join(allowed_leads)}。'
                        f'时间、对象与评价内容写在动词后，编号和部分声音由脚本生成。当前claim：{claim}'
                    )
            if dataset.get('writing_contract')=='FOCUSED_QUOTES_V2' and asserts_no_filter(claim):
                raise ValueError(f'point {index}不能把滤镜克制概括成无滤镜等未经核实的制作事实，保留实际有据的视觉观感')
            if re.match(r"^(?:[一二三四五六七八九十]+是|此外[，,])", claim):
                raise ValueError("claim不带编号或“此外”开头，段首由脚本生成")
            if has_absolute_production_assertion(claim):
                raise ValueError(f"point {index} contains an unverified absolute production assertion")
            if SPECULATIVE_ORGANIZER.search(claim):
                raise ValueError(f"point {index} makes an unsupported organizer/marketing inference")
            # A criticism of promotion is an opinion. Pure broadcast metrics remain
            # excluded by prose_exclusion_reason/writing_guidance, not topic words.
            if UNSUPPORTED_GUARANTEE.search(claim):
                raise ValueError(f"point {index} treats a team or cast lineup as proof of quality")
            if UNSUPPORTED_AGGREGATE_CLAIM.search(claim):
                raise ValueError(f"point {index} infers audience-wide or most-common feedback from selected excerpts")
            if WHOLE_WEB_JUDGMENT.search(claim) or UNVERIFIED_AGGREGATE_CAUSALITY.search(claim):
                raise ValueError(f'point {index}不能以留样推断全网态度或总体口碑因果，概括具体观点并保留评价归属')
            if UNSUPPORTED_LENGTH_QUALITY_CLAIM.search(claim):
                raise ValueError(f"point {index} infers a lack of filler from episode count")
            if NOVELTY_ASSERTION.search(claim) and not OPINION_FRAMING.search(claim):
                raise ValueError(f"point {index} states an unverified novelty claim as fact; retain opinion attribution or narrow the claim")
            group_indices = point.get("group_indices", [])
            if not isinstance(group_indices, list) or not group_indices:
                raise ValueError(f"point {index} needs group_indices")
            if any(not isinstance(gi, int) or gi < 0 or gi >= len(dataset["groups"]) for gi in group_indices):
                raise ValueError(f"point {index} has an invalid group index")
            if any(isinstance(dataset['groups'][gi].get('writing_guidance'),dict) and not dataset['groups'][gi]['writing_guidance']['eligible'] for gi in group_indices):
                    raise ValueError(f'point {index}引用了末端判断仅有泛态度、名单、纯事实背景或不足以成文的组，请从已审可入稿组取舍，不补原表')
            for gi in group_indices:
                exclusion = prose_exclusion_reason(dataset['groups'][gi])
                if exclusion:
                    raise ValueError(f'point {index}不能用组{gi}凑正文段数：{exclusion}')
            if point_format!="criticism_summary" and len(group_indices) > 1 and all(VALID_STANCES.get(dataset["groups"][gi]["stance"]) == "negative"
                                              for gi in group_indices):
                aspects = [dataset["groups"][gi]["aspect"] for gi in group_indices]
                narrative_only = any(NARRATIVE_TOPIC.search(a) and not VISUAL_TOPIC.search(a) for a in aspects)
                visual_only = any(VISUAL_TOPIC.search(a) and not NARRATIVE_TOPIC.search(a) for a in aspects)
                if narrative_only and visual_only:
                    raise ValueError(f"point {index} mixes narrative criticism with visual/appearance criticism")
            samples = point.get("samples", [])
            if manual_style and isinstance(samples, list) and len(samples) < 2:
                raise ValueError('独立正文段至少需要2条独立有效声音；单源理由不能替代证据，不足则删点或说明无法支撑6段，不能硬凑')
            single_aside = (isinstance(samples, list) and len(samples) == 1)
            if single_aside and len(str(point.get("single_source_reason", "")).strip()) < 12:
                raise ValueError("a one-source point needs a concrete single_source_reason; use one strong attributed source instead of padding with a copied article")
            focused_quotes=dataset.get('writing_contract')=='FOCUSED_QUOTES_V2'
            if focused_quotes and not manual_style and isinstance(samples,list) and len(samples)>2:
                raise ValueError(f'point {index}本版最多{3 if manual_style else 2}条独立声音，保留具体互补的信息，不用近义泛赞凑字数')
            if not isinstance(samples, list) or not samples or (not manual_style and len(samples) not in (1, 2, 3)):
                raise ValueError(f"point {index} needs a nonempty sample list; usually 3-4 complementary voices, 2 if sufficient, more when short comments add information")
            prefix = "此外，" if is_aside else f"{NUMERALS[numbered_count - 1]}是"
            lead = criticism_lead(claim) if point_format == "criticism_summary" else claim
            sentence = f"{prefix}{normalize_claim_preface(lead)}。"
            stances = set()
            retained_preferred_groups=set()
            selected = []
            point_source_ids = set()
            sample_errors = []
            for chosen in samples:
                try:
                    cid = nonempty(chosen.get("candidate_id"), "candidate_id")
                    if cid in used_ids:
                        raise ValueError(f"sample repeated across report: {cid}")
                    used_ids.add(cid)
                    if cid not in sample_index:
                        raise ValueError(f"sample not in dataset: {cid}")
                    group_index, sample = sample_index[cid]
                    if source_exclusion(sample['source']):raise ValueError(f'来源已命中当前AI稿/格式/账号排除规则：{cid}')
                    keys=sample_keys(sample)
                    if keys & used_sample_keys:
                        raise ValueError(f'同一样本只能用于一个观点，同源文章/网址/全文/展示句不能跨点重复：{cid}')
                    used_sample_keys.update(keys)
                    for prior in used_manuscript_samples:
                        if same_manuscript_evidence(sample,prior):
                            raise ValueError(f"same-manuscript evidence is not an independent source: {prior['candidate_id']} / {cid}; keep one, choose another displayed independent sample, or use a one-source point with single_source_reason. Do not shorten quotes to evade provenance.")
                    used_manuscript_samples.append(sample)
                    source_id = sample["source_id"]
                    if source_id in point_source_ids:
                        raise ValueError(f"a point needs independent sources, not two passages from one article: {source_id}")
                    point_source_ids.add(source_id)
                    if group_index not in group_indices:
                        raise ValueError(f"sample {cid} is outside point groups")
                    stances.add(VALID_STANCES.get(dataset["groups"][group_index]["stance"], "unknown"))
                    check_original(sample)
                    requested_label = chosen.get('attribution')
                    choices = allowed_attributions(sample)
                    preferred=source_label(sample)
                    site=str(sample['source']['data'].get('站点名称') or '').strip()
                    # A generic medium type is never a publisher. Canonicalize only
                    # through already supplied metadata, retaining an audit trail.
                    platform_only=(requested_label==site and preferred!=site and preferred in choices)
                    label = nonempty((requested_label if requested_label not in GENERIC_ATTRIBUTIONS and not platform_only else None) or next(iter(choices), ''), f'attribution for {cid}')
                    if label not in allowed_attributions(sample):
                        raise ValueError(f"attribution has no matching source metadata: {cid}: {label}")
                    verb = chosen.get("verb", "称")
                    if verb not in VALID_VERBS:
                        raise ValueError(f"样本{cid}的verb为{verb!r}，此字段只填称、认为或表示。吐槽、质疑等用于段前句claim")
                    full_quote = display_text(sample)
                    from evidence_quality import resolve_quote, generic_quote
                    quote = resolve_quote(sample,chosen)
                    if not isinstance(quote, str) or len(quote.strip()) < 8 or quote not in full_quote:
                        raise ValueError(f"quote must be a continuous display excerpt of at least 8 characters: {cid}")
                    quote = quote.strip()
                    if focused_quotes:
                        if asserts_no_filter(quote):
                            raise ValueError(f'quote含未经核实的无滤镜制作断言，请从已审展示句保留实际画面观感：{cid}')
                        issue=quotation_scope_issue(claim,quote,VALID_STANCES.get(dataset['groups'][group_index]['stance'],'unknown'))
                        if issue:
                            raise ValueError(f'point {index}: {issue}：{cid}')
                    guide=dataset['groups'][group_index].get('writing_guidance')
                    if isinstance(guide,dict) and any(v['candidate_id']==cid and retains_preferred_support(quote,v['quote_snippet'],VALID_STANCES.get(dataset['groups'][group_index]['stance'],'unknown')) for v in guide.get('preferred',[])):
                        retained_preferred_groups.add(group_index)
                    if generic_quote(quote):raise ValueError(f'引文只有泛泛态度、缺少具体支持内容，换已审展示句中的具体原句：{cid}')
                    # An exact, complete and specific 8-19-character judgment needs no
                    # length-padding or extra rationale. Generic/fragment guards remain.
                    if len(str(chosen.get('selection_reason','')).strip())<8:raise ValueError(f'样本须有selection_reason，指出具体支持判断及信息价值：{cid}')
                    if not has_terminal_punctuation(quote):
                        following = full_quote[full_quote.find(quote) + len(quote):]
                        if following[:1] in ("。", "！", "？", "!", "?"):
                            quote += following[0]
                    quote_limit=140 if focused_quotes else 180
                    if len(quote) > quote_limit:
                        raise ValueError(f"quote exceeds the {quote_limit}-character writing limit; retain only this point's concrete evidence: {cid}")
                    guard_errors=quote_guard_issues(sample,quote,claim)
                    if guard_errors:raise ValueError('\n'.join(guard_errors))
                    # Presentation-only whitespace cleanup; the original contiguous
                    # selection remains in the audit for exact source verification.
                    shown_quote = re.sub(r"\s+", " ", quote)
                    applied_normalizations = []
                    for change in chosen.get("name_normalizations", []):
                        old = nonempty(change.get("from"), f"normalization source for {cid}")
                        new = nonempty(change.get("to"), f"normalization target for {cid}")
                        basis = nonempty(change.get("basis"), f"normalization basis for {cid}")
                        if old == new or old not in shown_quote or re.search(r"[。！？\n]", old + new):
                            raise ValueError(f"invalid name normalization for {cid}: {old} -> {new}")
                        shown_quote = shown_quote.replace(old, new)
                        applied_normalizations.append({"from": old, "to": new, "basis": basis})
                    sentence += f"{label}{verb}，{shown_quote}"
                    if not has_terminal_punctuation(shown_quote):
                        sentence += "。"
                    source = sample["source"]
                    selected.append({"candidate_id": cid, "group_index": group_index,
                                     "source_file": source["source_file"], "sheet": source["sheet"],
                                     "row": source["row"], "channel": sample["source_channel"],
                                     "attribution": label, "attribution_input":requested_label, "attribution_normalized_from_metadata":label!=requested_label, "quote": shown_quote,
                                     "verbatim_quote": quote,
                                     "name_normalizations": applied_normalizations,
                                     "original_excerpt": sample["excerpt"],
                                     "source_id": sample["source_id"],
                                     "original_position": [sample["start"], sample["end"]],
                                     "media_subject_name": sample.get("media_subject_name") or None,
                                     "writing_supplement": bool(sample.get("writing_supplement")),
                                     "source_policy":"ONE_SOURCE_ONE_VIEWPOINT"})
                except ValueError as exc:
                    sample_errors.append(str(exc))
            if sample_errors:
                raise ValueError("\n".join(sample_errors))
            # Human selection intentionally excludes unselected automatic recommendations.
            # Its evidence is bound and reviewed in the mandatory selected-only outline.
            required_preferred=set() if dataset.get('human_selection') else {gi for gi in group_indices if isinstance(dataset['groups'][gi].get('writing_guidance'),dict) and dataset['groups'][gi]['writing_guidance']['eligible']}
            if not required_preferred<=retained_preferred_groups:
                raise ValueError(f'point {index}须从每个所用组保留至少一条preferred_support_snippet的具体支持信息，不能裁成泛赞：{sorted(required_preferred-retained_preferred_groups)}')
            from final_evidence_gate import specific_claim_issues
            missing_specific=specific_claim_issues(claim,[s['verbatim_quote'] for s in selected])
            if missing_specific:
                raise ValueError(f'point {index}具体说法未被实际入稿引文支持，缺少={missing_specific}；从展示句保留相应具体信息或收窄结论')
            if unsupported_fit_judgment(claim,[s['verbatim_quote'] for s in selected]):
                raise ValueError(f'point {index}位置/时机准确或处理恰当须有原句评价，仅描述出现地点和动作不支持效果判断；收窄为已证观点或省去，不补原表')
            if manual_style:
                from report_style import validate_added_sources
                validate_added_sources(point, selected)
            elif len(selected)==3:
                new=point.get('added_information_quote')
                nested=samples[2].get('added_information_quote')
                if new and nested and new!=nested:
                    raise ValueError(f'point {index}: added_information_quote两处填写不一致，请保留同一条第三引文增量说明')
                new=new or nested or ''
                if not isinstance(new,str) or len(new)<8 or new not in selected[2]['verbatim_quote'] or any(new in x['verbatim_quote'] for x in selected[:2]):
                    raise ValueError(f'point {index}: 第三条须填写added_information_quote（本点或第三条samples内均可）：第三条内至少8字的新支持信息，不能重复前两条')
            if len(stances) != 1 or "unknown" in stances:
                raise ValueError(f"point {index} mixes or lacks stances")
            point_stance = next(iter(stances))
            if (point_stance == "negative" and point_format!="criticism_summary"
                    and sum(bool(pattern.search(claim)) for pattern in NEGATIVE_MECHANISM_CATEGORIES) >= 3):
                raise ValueError(f"point {index} combines three or more criticism mechanisms")
            claim_parts = [part.strip() for part in re.split(r"[，；;]", claim) if part.strip()]
            support = point.get("claim_support")
            if not isinstance(support, list) or len(support) != len(claim_parts):
                raise ValueError(f"point {index} needs one claim_support item per claim clause")
            support=[dict(v) for v in support]
            for claim_index,binding in enumerate(support):
                if 'claim_part' not in binding and type(binding.get('claim_index')) is int and binding['claim_index']==claim_index:
                    binding['claim_part']=claim_parts[claim_index]
                    binding['claim_part_bound_by_script']=True
            selected_by_id = {sample["candidate_id"]: sample for sample in selected}
            bound_ids = set()
            for part, binding in zip(claim_parts, support):
                if binding.get("claim_part") != part:
                    raise ValueError(f"point {index} claim_support clause mismatch: {part}")
                evidence = binding.get("evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise ValueError(f"point {index} claim clause has no evidence: {part}")
                for item in evidence:
                    cid = item.get("candidate_id")
                    snippet = item.get("quote_snippet")
                    if cid not in selected_by_id or not isinstance(snippet, str) or len(snippet.strip()) < 6:
                        raise ValueError(f"point {index} invalid claim evidence: {cid}")
                    if snippet not in selected_by_id[cid]["verbatim_quote"]:
                        raise ValueError(f"point {index} evidence is outside the selected quote: {cid}")
                    bound_ids.add(cid)
            if bound_ids != set(selected_by_id):
                raise ValueError(f"point {index} has samples without claim evidence")
            if point_stance == "negative":
                for sample in selected:
                    if (HYPOTHETICAL_EVIDENCE.search(sample["verbatim_quote"])
                            and not HYPOTHETICAL_FRAME.search(claim)):
                        raise ValueError(f"hypothetical criticism must be framed as concern, not an observed fact: {sample['candidate_id']}")
                    if (sample["channel"] in ("微博", "小红书")
                            and (HEARSAY_CRITICISM.search(sample["verbatim_quote"])
                                 or quote_relays_criticism(sample_index[sample['candidate_id']][1],sample['verbatim_quote']))):
                        raise ValueError(f"social negative quote relays criticism instead of giving a direct opinion: {sample['candidate_id']}")
                evidence_by_sample = {cid: [] for cid in selected_by_id}
                for binding in support:
                    for item in binding["evidence"]:
                        evidence_by_sample[item["candidate_id"]].append(item)
                for cid, items in evidence_by_sample.items():
                    if not any(confirmed_critical_evidence(item) for item in items):
                        raise ValueError(f"negative evidence needs a direct judgment or a concrete criticism_reason explaining its meaning: {cid}")
            for sample in selected:
                if quote_crops_relay_owner(sample_index[sample['candidate_id']][1],sample['verbatim_quote']):
                    raise ValueError(f"quote裁掉了网友/观众等原评价者的转述归属，不能改成账号作者本人的判断；保留同句的评价者标记或换完整独立原句：{sample['candidate_id']}")
            if focused_quotes and not manual_style and not has_report_judgment(claim):
                raise ValueError(f'point {index}段前句须有明确舆论判断')
            if focused_quotes and len(normalize_claim_preface(claim))>48:
                raise ValueError(f'point {index}观点句最多48字；不要为段落字数拉长标题，把具体论据留在引文，材料不足写明例外')
            social_review=[]
            if point_stance=='negative' and not any(s['channel'] in ('微博','小红书') for s in selected):
                available={s['candidate_id']:s for gi in group_indices for s in dataset['groups'][gi]['samples']
                           if direct_social_quote_options(s)}
                social_review=review_unused_social_sources(point,available)
            warnings = []
            if focused_quotes and point_format!="criticism_summary":
                issue=claim_scope_issue(claim,point_stance)
                if issue:raise ValueError(f'point {index}{issue}')
            if single_aside:
                warnings.append(f"单一独立声音附注：{point['single_source_reason']}")
            for row in social_review:warnings.append(f"未用已有社交批评（{row['candidate_id']}）：{row['reason']}")
            if point_stance == "negative":
                for binding in support:
                    for item in binding["evidence"]:
                        if not contains_critical_judgment(item["quote_snippet"]) and confirmed_critical_evidence(item):
                            warnings.append(f"直接批评语义说明（{item['candidate_id']}）：{item['criticism_reason']}")
            direct_social = {"微博", "小红书", "抖音"}
            if re.search(r"观众|网民|书粉", claim) and not any(
                    s["channel"] in direct_social for s in selected):
                warnings.append("观点句称观众、网民或书粉，但所选样本没有直接社交表达；需核实转述归属")
            for a in range(len(selected)):
                for b in range(a + 1, len(selected)):
                    left = re.sub(r"\W", "", selected[a]["quote"])
                    right = re.sub(r"\W", "", selected[b]["quote"])
                    ratio = difflib.SequenceMatcher(None, left, right).ratio()
                    threshold = 0.55 if min(len(left), len(right)) >= 25 else 0.80
                    if ratio >= threshold:
                        raise ValueError(f"two draft quotes are too similar to count as independent sources: "
                                         f"{selected[a]['candidate_id']} / {selected[b]['candidate_id']}")
            if any(re.search(r"\d+集|全程|没有棚|无棚|收视率|热度值|市占率", s["quote"])
                   for s in selected):
                warnings.append("引文含集数、绝对化制作说法或数据，发表前核实事实")
            if len(sentence) > max_point_chars:
                raise ValueError(f"point {index} has {len(sentence)} characters, above max_point_chars={max_point_chars}; shorten quotations to their supporting judgments")
            paragraphs.append(sentence)
            audit.append({"display_order": index + 1,
                          "point_number": None if is_aside else numbered_count,
                          "format": point_format, "claim": claim, "stance": stances.pop(),
                          "group_indices": group_indices, "samples": selected,
                          "claim_support": support,
                          "social_source_review":social_review,
                          "review_warnings": warnings})
        except ValueError as exc:
            point_errors.append(f"段{index + 1}：{exc}")
    if manual_style:
        if focused_quotes and dataset.get('adjacent_lead_policy')=='VARIED_V1':
            for previous,current in zip(audit,audit[1:]):
                if current['display_order']!=previous['display_order']+1:
                    continue
                lead=next((v for v in REPORT_LEAD_VERBS if current['claim'].startswith(v)),None)
                if lead and previous['claim'].startswith(lead):
                    point_errors.append(f"段{current['display_order']}：与上一段同以“{lead}”起句；只调整此段为符合原判断的其他允许动词，同步保留分句证据。")
        for text,row in zip(paragraphs,audit):
            i=row['display_order']-1
            point=points[i]
            measure=paragraph_measure(text,point,row['stance']);row['paragraph_length']=measure
            if measure['needs_length_repair']:
                point_errors.append(f"段{i+1}：点{i}实际{measure['characters']}字，目标{measure['target']}、常规范围{measure['normal_band']}。从同一观点已有展示句补足具体细节或互补声音、裁去冗余；确有证据不足/必要细节时填length_exception_reason，不补原表、不造句、不堆近义泛赞。")
    if point_errors:
        raise ValueError("本次检测到以下问题，请集中修复后再完整校验：\n" + "\n".join(point_errors))
    return [f"二、主要舆论观点：{heading}", *paragraphs], audit


def set_font(run, name: str, points: int, bold: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(points)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), name)


def normalize_claim_preface(text: str) -> str:
    # Opinion predicates retain attribution in an opinion section. Keep bare
    # factual relay predicates (称/表示), single-source qualifiers and timing.
    return re.sub(r"^([一二三四五六七八九十]+是|此外，)?有(?:媒体[与和]自媒体|网民[与和]自媒体|自媒体|媒体|网民)(?=(?:在播前)?(?:肯定|认可|称赞|赞赏|认为|质疑|担忧|担心|指出|看好))",
                  lambda match: match.group(1) or "", text)


def paragraph_segments(text: str, point: dict):
    """Bold the conclusion and exact source attribution, leaving quotations regular."""
    lead, rest = text.split("。", 1)
    yield normalize_claim_preface(lead) + "。", True
    cursor = 0
    for sample in point["samples"]:
        label = sample["attribution"]
        at = rest.find(label, cursor)
        if at < 0:
            raise ValueError("attribution missing from rendered paragraph")
        yield rest[cursor:at], False
        yield label, True
        cursor = at + len(label)
    yield rest[cursor:], False


def grid_paragraph(p, *, snap=True):
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1
    props = p._p.get_or_add_pPr()
    grid = OxmlElement("w:snapToGrid")
    grid.set(qn("w:val"), "1" if snap else "0")
    props.append(grid)


def write_docx(path: Path, target: str, review_state: str, paragraphs: list[str],
               audit: list[dict], chart_path: Path, stats: dict) -> None:
    template=Path(__file__).resolve().parent.parent/'assets/opinion_template.docx'
    document = Document(template)
    document.core_properties.title = f"《{target}》主要舆论观点"
    normal = document.styles["Normal"]
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1
    for section in document.sections:
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        section.top_margin = section.bottom_margin = Cm(2.54)
        section.left_margin = section.right_margin = Cm(3.175)
        section.header_distance = Cm(1.5)
        grid = section._sectPr.find(qn("w:docGrid"))
        if grid is None:
            grid = OxmlElement("w:docGrid")
            section._sectPr.append(grid)
        grid.set(qn("w:type"), "lines")
        grid.set(qn("w:linePitch"), "312")
    heading = document.add_paragraph()
    grid_paragraph(heading)
    heading.paragraph_format.first_line_indent = Pt(32)
    heading.paragraph_format.keep_with_next = True
    set_font(heading.add_run(paragraphs[0]), "黑体", 16)
    chart = document.add_paragraph()
    grid_paragraph(chart, snap=False)
    chart.alignment = WD_ALIGN_PARAGRAPH.CENTER
    chart.add_run().add_picture(str(chart_path), width=Cm(14.5))
    caption = document.add_paragraph()
    grid_paragraph(caption, snap=False)
    set_font(caption.add_run(stats.get("caption") or f"注：按本期工作台已审展示来源统计（去重后{stats['denominator']}条），非全网舆论占比。"), "宋体", 9)
    for text, point in zip(paragraphs[1:], audit):
        p = document.add_paragraph()
        grid_paragraph(p)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.first_line_indent = Pt(32)
        p.paragraph_format.widow_control = True
        for value, bold in paragraph_segments(text, point):
            if value:
                set_font(p.add_run(value), "仿宋_GB2312", 16, bold)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


def render(args: argparse.Namespace) -> None:
    dataset_path, plan_path = Path(args.dataset), Path(args.plan)
    if sha256(dataset_path) != read_json(plan_path).get("dataset_sha256"):
        raise ValueError("dataset fingerprint changed since the editorial plan was prepared")
    dataset, plan = read_json(dataset_path), read_json(plan_path)
    chart_path_arg=getattr(args,'chart_review',None)
    if chart_path_arg:
        review=read_json(Path(chart_path_arg))
        if review.get('plan_sha256') and review['plan_sha256']!=sha256(plan_path):
            raise ValueError('绘图审核所绑定的既有正文计划已改变')
        plan['chart_profile']=CHART_PROFILE;plan['chart_review']=review;plan['chart_theme_policy']=__import__('chart_coverage').THEME_POLICY
    inventory_path=Path(args.outdir)/'candidate_inventory.json'
    if inventory_path.exists() and read_json(inventory_path).get('chart_profile')==CHART_PROFILE and plan.get('chart_profile')!=CHART_PROFILE:
        raise ValueError('保留prepare的chart_profile并完成chart_review；不要把正文未用观点自动归其他')
    if inventory_path.exists() and read_json(inventory_path).get('chart_theme_policy') and plan.get('chart_theme_policy')!=__import__('chart_coverage').THEME_POLICY:
        raise ValueError('保留prepare的chart_theme_policy并完成主要观点边界复核')
    if inventory_path.exists() and read_json(inventory_path).get('report_style')==STYLE and plan.get('report_style')!=STYLE:
        raise ValueError('保留prepare的report_style=MANUAL_BRIEF_V1，按人工简报体写本次新稿')
    plan=compile_plan(dataset,plan)
    comparison_file=Path(args.outdir)/'period_comparison.json';comparison=None
    if plan.get('period_comparison_sha256'):
        if not comparison_file.exists() or sha256(comparison_file)!=plan['period_comparison_sha256']:raise ValueError('连续取材比较文件已改变，须重新prepare')
        from period_compare import validate_comparison
        comparison=read_json(comparison_file)
        if comparison.get('dataset_sha256')!=sha256(dataset_path):raise ValueError('连续取材比较不属于本期工作台')
        validate_comparison(dataset,comparison)
    elif comparison_file.exists():raise ValueError('存在连续取材比较文件但编辑计划缺少对应指纹')
    history_file=Path(args.outdir)/'period_history_context.json'
    history=None
    if plan.get('history_sha256'):
        if not history_file.exists() or sha256(history_file)!=plan['history_sha256']:raise ValueError('历史比较输入指纹改变，须重新prepare')
        history=read_json(history_file)
    elif history_file.exists():raise ValueError('存在历史比较上下文但编辑计划缺少对应指纹')
    if history or dataset.get('continuity_review') or comparison:
        for point in plan.get('points',[]):
            if len(str(point.get('continuity_reason') or '').strip())<12:
                raise ValueError('连续出刊模式每点须写至少12字continuity_reason，解释本期新增、此前未展开或为何继续保留；不编造变化')
    from period_history import continuity_review
    continuity_context=continuity_review(dataset,history) if history else dataset.get('continuity_review')
    check_dataset(dataset, dataset_path)
    failures = writing_preflight(dataset,plan)
    for label, check in (
        ('正文', lambda: render_content(dataset, plan)),
        ('段落边界', lambda: validate_outline(dataset, plan)),
        ('图表', lambda: chart_data(dataset, plan, sha256(dataset_path))),
    ):
        try:
            result = check()
            if label == '正文':
                paragraphs, audit = result
            if label == '图表':
                stats = result
        except ValueError as exc:
            failures.append(f'{label}：{exc}')
    if failures:
        write_json(Path(args.outdir) / 'validation_feedback.json', {'errors': failures})
        raise ValueError("\n".join(failures))
    global_warnings = []
    social = {"微博", "小红书"}
    for stance, text in (("negative", "负面"),):
        selected = [s for point in audit if point["stance"] == stance for s in point["samples"]]
        if selected and not any(s["channel"] in social for s in selected):
            global_warnings.append(f"{text}入稿样本没有微博或小红书直接表达；只在工作台展示样本中复核是否有合适原句，若无则记录证据缺口")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if plan.get('report_style')==STYLE:
        write_json(outdir/'compiled_editorial_plan.json',plan)
        write_json(outdir/'paragraph_lengths.json',{'style':STYLE,'basis':MANUAL_LIMITS['length_basis'],'paragraphs':[{'point_number':v['point_number'],'stance':v['stance'],**v['paragraph_length']} for v in audit]})
    chart_path = outdir / "opinion_distribution.png"
    stats["visual"] = draw_chart(stats, chart_path)
    write_json(outdir / "opinion_distribution.json", stats)
    if stats.get('profile')==CHART_PROFILE:
        write_json(outdir/'chart_classification_audit.json',{k:stats[k] for k in ('profile','dataset_sha256','reviewed_source_count','denominator','excluded_background_count','other_count','allocation','excluded_sources','classification_review','review_warnings')})
    md = outdir / "opinion_draft.md"
    md_paragraphs = ["".join((f"**{value}**" if bold and value else value)
                             for value, bold in paragraph_segments(text, point))
                     for text, point in zip(paragraphs[1:], audit)]
    md.write_text("# " + paragraphs[0] + "\n\n![舆论观点分布](opinion_distribution.png)\n\n"
                  + (stats.get("caption") or f"注：按本期工作台已审展示来源统计（去重后{stats['denominator']}条），非全网舆论占比。") + "\n\n"
                  + "\n\n".join(md_paragraphs) + "\n", encoding="utf-8")
    docx = outdir / "opinion_draft.docx"
    write_docx(docx, dataset["target"], plan["review_state"], paragraphs, audit, chart_path, stats)
    write_json(outdir / "source_audit.json", {
        "target": dataset["target"], "dataset_sha256": sha256(dataset_path),
        "review_state": plan["review_state"], "points": audit,
        "outline_review": plan["outline_review"], "chart_basis": stats["basis"],
        "review_warnings": global_warnings,
        "human_review_notes": plan.get("human_review_notes", []),
        "continuity_review":{
            "history_sha256":plan.get('history_sha256'),
            "context":continuity_context,
            "point_reasons":[point.get('continuity_reason') for point in plan['points']],
            "period_comparison_sha256":plan.get('period_comparison_sha256'),
            "daily_focus":comparison.get('daily_focus') if comparison else None,
        } if history or dataset.get('continuity_review') or comparison else None,
    })
    review_lines = [f"# 《{normalized_target_title(dataset['target'])}》舆论观点人工确认单", "", "",
                    "请逐点确认观点是否独立、标题是否受样本支持、渠道署名和原句是否准确。", ""]
    for point in audit:
        review_title = (f"{NUMERALS[point['point_number'] - 1]}、{point['claim']}"
                        if point["point_number"] else f"补充段、{point['claim']}")
        review_lines.extend([f"## {review_title}", "",
                             f"- [ ] 保留此观点及当前样本", ""])
        for sample in point["samples"]:
            review_lines.append(f"- {sample['attribution']}｜{sample['channel']}｜"
                                f"{sample['source_file']} / {sample['sheet']} / 第{sample['row']}行｜"
                                f"ID {sample['candidate_id']}")
        review_lines.extend(["", "结论分句与原句核对："])
        for binding in point["claim_support"]:
            review_lines.append(f"- {binding['claim_part']}：" + "；".join(
                f"{item['candidate_id']}「{item['quote_snippet']}」" for item in binding["evidence"]))
        review_lines.append("")
        for warning in point["review_warnings"]:
            review_lines.append(f"- 待核：{warning}")
        if point["review_warnings"]:
            review_lines.append("")
    review_lines.extend(["## 本期待核事项", ""])
    review_lines.extend(f"- {warning}" for warning in global_warnings)
    review_lines.extend(f"- {note}" for note in plan.get("human_review_notes", []))
    (outdir / "review_sheet.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "DRAFT_FOR_HUMAN_REVIEW" if plan["review_state"] == "AI_PREVIEW" else "HUMAN_CONFIRMED",
                      "docx": str(docx), "markdown": str(md), "audit": str(outdir / "source_audit.json"),
                      "review_sheet": str(outdir / "review_sheet.md"),
                      "points": len(audit), "samples": sum(len(x["samples"]) for x in audit)}, ensure_ascii=False))


def opinion_section(document: Document) -> list[str]:
    ps = [p.text.strip() for p in document.paragraphs]
    start = next((i for i, p in enumerate(ps) if re.match(r"^[二三四]、(?:主要)?舆论(?:观点|评价)", p)), None)
    if start is None:
        raise ValueError("document has no opinion section")
    end = next((i for i in range(start + 1, len(ps)) if re.match(r"^[三四五]、", ps[i])), len(ps))
    return [p for p in ps[start:end] if p]


def compare(args: argparse.Namespace) -> None:
    baseline = opinion_section(Document(args.baseline))
    draft = opinion_section(Document(args.draft))
    def profile(section: list[str]) -> dict:
        points = [p for p in section[1:] if re.match(r"^[一二三四五六七八九十]+是", p)]
        return {"heading": section[0], "point_count": len(points),
                "other_paragraph_count": len(section[1:]) - len(points),
                "point_lengths": [len(p) for p in points],
                "attribution_counts": [len(re.findall(r"(?:称|认为|表示)，", p)) for p in points],
                "source_mentions": {k: sum(p.count(k) for p in points) for k in
                                    ("微博", "小红书", "微信公众号", "头条号", "百家号", "网易号")}}
    result = {"baseline": profile(baseline), "draft": profile(draft),
              "note": "Structure-only comparison; factual accuracy, point selection and sample quality require human review."}
    write_json(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--dataset", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument('--history',type=Path,action='append',default=[])
    p.add_argument('--history-plan',type=Path,action='append',default=[])
    p.add_argument('--period-comparison',type=Path,help='已完成的period_compare比较结果，须绑定当前工作台')
    p.set_defaults(func=prepare)
    p = sub.add_parser("render")
    p.add_argument("--dataset", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--chart-review", help="仅复核既有成稿图表时，绑定同一工作台和正文计划的绘图归属答卷")
    p.add_argument("--outdir", required=True)
    p.set_defaults(func=render)
    p = sub.add_parser("compare")
    p.add_argument("--baseline", required=True)
    p.add_argument("--draft", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=compare)
    args = parser.parse_args()
    try:
        args.func(args)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
