"""One current-period AI discovery handoff, then deterministic candidate routing."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import re
import sys
from pathlib import Path
from rapid_film_workbench import (save, WORK_SCOPE_RULE, lexical_features, text_overlap,
                                 rank_candidates, source_priority, ARTICLE_CHANNELS,
                                 display_excerpt, excerpt_min_chars, sentence_spans, viewpoint_passages)
import hashlib
from cluster_contract import BOUNDARY_RULE, CATEGORIES, validate_cluster_boundaries

STANCES = {"positive": "正向词倾向", "objective": "中性或混合", "negative": "负向词倾向"}
DISCOVERY_RULE = """本次是初次观点发现，不是最终验收。先提交当前原句能支持的真实初稿；不足六个时如实交已有点和缺口，后续控制器会逐条审样、做有界补缺和最终数量验收。不要为首次提交即达到最终数量而穷举分组或反复改写，不得硬拆；完整说明一次判断即可。只读本期脚本代表摘录，形成有本期证据的同层级一级观点簇，总数最多16组，最终正面至少6个独立非空组是硬门槛；发现不足可先提交真实缺口，由控制器安排一次有界补缺，不得凑数或伪造通过。标题使用可直接写进简报的完整观点句，通常24—36字，以接近30字为宜，例如“肯定某种具体处理，认为它带来某种观感或价值”；逗号前点明对象与做法，逗号后概括一个评价意义，两层围绕一个核心主张。只留共同核心，不复述情节、不枚举动作、不把论据和人名名单塞进标题；具体例子留在样本。完整不是越长越好，不要压成方面标签或短口号，也不要把无关议题塞进一个标题。标题机器范围12—48字，允许逗号和顿号，意义是否统一由你判断。
正面和客观中，以演员演技、表演层次、角色演绎为核心的观点合计最多1—2组。先按共同评价机制合并主演、男主、配角等人物实例，通常用一组概括群像表演；只有证据确实支持两种不同的表演机制，才用第二组。不得因演员/角色不同、样本多或正面组数下限，就逐人拆成三组以上；其他正面机制必须有独立原文依据；不足6个正面组须记录缺口，不靠近义机制补足。角色由剧本如何塑造与演员如何演绎要分清；反串造型若主要评价服装/视觉，可归视觉制作，若主要评价演员演绎则归表演，不因单一噱头自立一个近义表演簇。每簇填写performance_focus布尔值，核心是演技/表演/演绎时为true，作为其他观点的例证时为false。
先统看主要议题与本期阶段。表演、人物塑造、叙事、视听、情感和社会主题中的支持性评价都应按本期证据识别；明确且独立的正面机制即使材料较少也可成组。正负面使用相同粒度：不能把正面挤进一个大组却按每个人、每个近义批评分出多个负面组；不要同时列总括组和仅作为实例的子组。不同机制仍可分开，不按正负客观平均凑组，正面展示下限不代表全网正面占比。具体对象不同但机制相同可合并；造型重复与剧情陈旧通常应分别命名。脚本rule_stance只是词语提示，遇否定、反讽和对比按原句判断。代表句条数和平台数量不代表全网声量。
每组立场positive/objective/negative唯一；客观可以是中性事实、观察或对评价分布的中性概括，具体赞贬分别归入对应组。标题不将司法确认、商业金额等来源自述升级为已证实事实；争议和传闻保留来源说法属性。具体数值、百分比、名次留在摘录，不把开播消息写成播后成绩。不要因背景人物名单或泛词把无关材料拉进组。
每簇给1—5个本输入seed_ids作命名依据。标题可以展开，keywords必须另取3—8个2—8字的简短检索词；选评价对象、处理方式和态度的原文短词，覆盖共同观点的不同说法，不把完整评价句、人名名单或多个词用标点连成一个关键词。至少两个词应描述内容或机制，不只靠剧名、人名、泛泛的好看/不好看来找材料。每词须在至少一个seed的excerpt或随附context原文中逐字出现，不得跨片段拼接，也不要从长句机械截前8字。分组标题不堆具体人名或穷举例子；表演效果与人物形象、主题意义等不同主张不要仅因同为正面便合在一起。seed不是最终keep；后续AI只审核最终展示句。标题通过后固定。只写answer_file完整JSON，不照搬旧报告或旧标题，不审全部来源、不查网络；整体取舍在本次发现完成，不增加第二轮整组复审。"""

DISCOVERY_RULE += '证据按完整观点核对，不按一个细节卡死：同一集中评价主题可以由互补的具体子评价支撑，如演唱听感与歌曲人物适配共同支撑原声音乐感染力，服饰与置景共同支撑同一视觉风格。每份声音都须有对本主题的实际评价，每个标题分句至少一条具体证据，全段至少两份独立声音；不要求每条引文重复同一细节或同时覆盖所有分句。不能仅因都提到演员、音乐或预告就合组；不能把无关问题装进泛泛的整体很好，也不能与别段重复核心评价。独立来源同向互证与不同细节互补均可，纯资讯、名单、期待及同稿转载均不能补足证据。'
DISCOVERY_RULE += " 先从原句找出实际评价，再决定观点。输入不展示脚本猜测的正负面与方面标签，召回位置和平台不代表立场。social_negative_review判为not_criticism的社交句仍可能是具体好评，须与其他摘录一起考虑正面聚类，不能做完负面核对就丢掉。拟独立成段的正面点优先选择至少两份不同来源、共同支持同一判断的E证据，再按共同支持的范围写标题；单条薄弱线索保留真实缺口，不用名单、旧作、热度或期待替代第二份实际评价。发现阶段就区分叙事节奏与故事设定、人物书写与表演等实际不同机制，但有独立证据才分别成组，不为数量细拆。"

TITLE_VERBS = ('认可','肯定','关注','讨论','质疑','批评','担忧','认为','指出','赞赏','称赞','期待')
TITLE_PATTERN = r'^(?:(?:观众|网友|网民|媒体|舆论|公众|影评人|评论者|部分观众|部分网友))?(?:' + '|'.join(TITLE_VERBS) + ')'
DISCOVERY_RULE += ' 标题起句仅选以下判断动词：'+'、'.join(TITLE_VERBS)+'。客观用关注、讨论或指出等适用词，不用观察到、记录到、呈现起句。'
DISCOVERY_RULE += WORK_SCOPE_RULE
DISCOVERY_RULE += ' 拟独立成段的观点在发现时就检查展开依据：两条只有同义短评、没有具体处理、变化或其他可展开信息，可以留作补充但不能当成已足以支撑正文的点。先找本期真实的其他判断，不能留到成稿后才发现只能写一句空话；不增加新审核轮，也不因字数或尚未播出机械否定信息充分的评价。'
DISCOVERY_RULE += " 负面观点最多3组：保留本期证据充分、机制不同的主要批评，近义批评合并，其他负面留在后台，不改成中性或正面凑数。负面候选在相关性接近时先看微博、小红书的真实评论，再看公众号，媒体报道后置；不新增采样、不把渠道当成观点真实性证明。最终正面观点至少6组是硬门槛，每组仍须有彼此不同的评价机制和本期原文证据（如叙事、表演、人物、视听、情感主题、制作或传播表现），不要把所有好评挤成一个大组。正面不足6组时记录缺口并按控制器补缺，不直接交成品；不拆近义组、不虚构、不把客观或负面改标正面。工作台最多45分钟，按控制器时限补缺，仍不足则保留未达标结果。标题的每个实质分句都须有seed原文依据，并在title_evidence中逐条绑定seed编号；不能为了凑完整双句而补写传播影响、社会反响、因果或价值判断；没有第二层依据就只写有证据的完整判断。题材涉及某群体不等于该群体已讨论，创作意图不等于实现效果，来源自述不等于已证实事实。写答卷前在本次命名里检查标题所有分句，不新增审查轮。"
DISCOVERY_RULE += " 确定负面观点前，先核对本输入social_review_ids所列微博、小红书摘录，不依赖rule_stance标签：逐条判断它是否明确批评本期剧集、演员或剧宣；剧宣、夸赞、事实描述、幕后玩笑和否定式好评不算批评。答卷social_negative_review须逐条记录判断与简短依据；真批评若有代表性、与其他批评机制不同，优先作为最多3个负面观点的候选，再用公众号/媒体线索核对；若未成组，说明证据不足、与其他组同义或代表性较弱等实际理由。不能为了社交渠道硬凑负面组，也不能因媒体数量多就跳过真实社交批评。社交核对与观点命名在同一次答卷完成，不新增模型轮次或原始样本。"
DISCOVERY_RULE += " 社交评论里的具体批评即使与赞赏、表情符号同帖出现，也要按批评句本身判断；不得只因整帖偏正面或语气轻松就抹去明确的具体不满。负面组优先考虑观剧后的第一手具体批评，再考虑未播前的猜测和转述；最多3组时，推测性担忧不能挤掉机制独立且可直接引用的实际观剧批评。每条入组的社交批评必须与负面标题同向、同对象：例如‘节奏慢’不能作为‘节奏仓促/过快’的证据，某位演员的局部动作问题不能扩写成整部作品表演差；若两种说法相反须分开取舍，不得塞进同一标题。标题只概括最终可引用的核心批评，不为兼容媒体猜测而增写社交原话没有支持的分句。"
DISCOVERY_RULE += " 社交摘录若只描述慢节奏、浓重风格或需细品等观看特征，没有表达不满或负面结果，应判为非批评；同样，‘不拖沓’‘零注水’‘不让人失望’以及肯定角色改造的说法不能反向当作批评。既不要抹掉混合语气中的明确不满，也不要把中性或正面描述补写成不满。"
DISCOVERY_RULE += " 输入中‘已核实主流媒体观点线索（待核）’来自对原表已核实媒体主体的逐条本地扫描，仅表示来源身份及候选原句。逐条判断是否确有本期作品的独立正面评价或有价值的客观观察；定档、播出提醒、演员名单、剧情简介、官方宣传口号和跨作品背景不能充作观点。媒体来源不能让空泛句子自动入组，也不能挤掉机制不同的独立社交评价。"
DISCOVERY_RULE += " 先按成稿需要检查独立判断与可用引文。具体评价线索只是额外召回位，逐条核对本剧、阶段、原评价者和实际观点。音乐评价要区分演唱、旋律或歌词效果与上线、署名和官方歌词介绍。播前物料的评价按物料范围命名。看见名单、剧情复述或传播总括时留作背景，不当成可独立成段的正面或讨论。各簇只承载一个核心判断，有独立证据才增加观点。报告需6—7段，批评组预计合为1段，所以总簇数不能代替正文可用点数。发现材料不足时说清缺的是哪类评价，不为报告段数硬拆或补猜测。"

ACTING_TITLE_CUES = re.compile(
    r'演技|表演|角色演绎|演绎角色|演员表现|角色诠释|眼神戏|微表情|'
    r'(?:演员|主演|男主|女主|配角).{0,10}打戏|打戏.{0,8}(?:演绎|演技|表演|投入|敬业)'
)
SOCIAL_CHANNELS = ("微博", "小红书")
DISCOVERY_RULE += '\n'+BOUNDARY_RULE
DISCOVERY_INPUT_LIMIT = 144
POSITIVE_NEGATION_CUES = re.compile(r'(?:从来)?(?:没|没有)让人失望|不(?:会|至于|算)?(?:拖沓|无聊|尴尬|失望|差|出戏)|打破.{0,8}套路|不落.{0,8}俗套')
SOCIAL_CRITICISM_CUES = (
    re.compile(r'不太满意|不满意|不喜欢|不适合|不够|看不下|劝退|让人出戏'),
    re.compile(r'(?<!不)(?:拖沓|无聊|尴尬|僵硬|难看|乏味|违和|失望|拉胯|糟糕|生硬|魔改|炒作|硬伤)'),
    re.compile(r'(?:有点儿?|有些|太|过于|略显|明显|感觉|觉得).{0,8}(?:慢|重|假|硬|乱|水|差|尬)'),
)


def social_criticism_cue_score(quote):
    """Loose discovery hint only; the AI decides whether a post truly criticizes."""
    screened = POSITIVE_NEGATION_CUES.sub('', quote)
    return sum(len(pattern.findall(screened)) for pattern in SOCIAL_CRITICISM_CUES)


def social_pacing_conflict(quote, title):
    """Catch opposite pacing claims when a social seed is bound to a title."""
    slow = r'节奏.{0,6}(?:慢|拖沓|缓慢|沉闷)'
    fast = r'节奏.{0,6}(?:仓促|过快|太快|太赶|赶进度)'
    return bool((re.search(slow, quote) and re.search(fast, title)) or
                (re.search(fast, quote) and re.search(slow, title)))

def title_error(title):
    if not isinstance(title,str) or not 12 <= len(title.strip()) <= 48 or not re.match(TITLE_PATTERN,title.strip()):
        return f'title当前={title!r}；标题须12—48字，以适用动词起句：'+ '、'.join(TITLE_VERBS)+'。只改起句/长度，保留原有据判断；不使用记录、反映、观察到。'
    if re.search(r'\d+(?:\.\d+)?\s*(?:%|％|万|亿|家|人|部|场|集|天|年|名)|(?:第[一二三四五六七八九十0-9]+[名位])',title):
        return '固定标题不写具体数值或名次，留在各条摘录中并保留来源说法属性'
    return None

def representative_candidates(candidates, sources, seen, seen_hashes, source_uses, limit=6,
                              review_social_criticism=False):
    """Keep a high-score anchor, then diversify only within a bounded score band.

    For rough negative groups, one later slot can show an existing social post
    with criticism cues even when its generic popularity score is lower. The
    model must still decide whether it is criticism. No raw sample is added.
    Known author/URL identity is only a duplicate hint.
    """
    picked, channels, identities = [], Counter(), set()
    remaining = list(candidates)
    features = {id(item): lexical_features(str(sources[item['source_id']].get('data', {}).get(item['text_field']) or '')[item['start']:item['end']]) for item in remaining}
    while remaining and len(picked) < limit:
        eligible = []
        for item in remaining:
            key = (item['source_id'], item['start'], item['end'], item['text_field'])
            identity = item.get('identity') or ('row:' + item['source_id'])
            if key in seen or identity in identities:
                continue
            if item.get('excerpt_hash') and item['excerpt_hash'] in seen_hashes:
                continue
            eligible.append(item)
        if not eligible:
            break
        best = max(item['score'] for item in eligible)
        band = 0 if not picked else 24 if len(picked) in (2,4) else 12
        near = [item for item in eligible if item['score'] >= best - band]
        social_cues = []
        if review_social_criticism and len(picked) == 1:
            for item in eligible:
                record = sources[item['source_id']]
                if (record.get('channel') not in SOCIAL_CHANNELS or
                        source_priority(record)['media_authority_rank']):
                    continue
                quote = str(record.get('data', {}).get(item['text_field']) or '')[item['start']:item['end']]
                cue_score = social_criticism_cue_score(quote)
                if cue_score:
                    social_cues.append((cue_score, item))
        if social_cues:
            chosen = min(social_cues, key=lambda pair: (
                -pair[0], -min(120, pair[1]['end']-pair[1]['start']),
                -pair[1]['score'], pair[1]['source_id'], pair[1]['start']))[1]
        else:
            chosen = min(near, key=lambda item: (
                int(5 * max((text_overlap(features[id(item)], features[id(old)]) for old in picked), default=0)),
                source_priority(sources[item['source_id']])['platform_risk_rank'],
                -source_priority(sources[item['source_id']])['media_authority_rank'],
                source_priority(sources[item['source_id']])['history_uses'],
                -source_priority(sources[item['source_id']])['channel_priority'],
                channels[sources[item['source_id']].get('channel', '')],
                source_uses[item['source_id']], -item['score'], item['source_id'], item['start']))
        picked.append(chosen)
        seen.add((chosen['source_id'], chosen['start'], chosen['end'], chosen['text_field']))
        if chosen.get('excerpt_hash'):
            seen_hashes.add(chosen['excerpt_hash'])
        identities.add(chosen.get('identity') or ('row:' + chosen['source_id']))
        channels[sources[chosen['source_id']].get('channel', '')] += 1
        source_uses[chosen['source_id']] += 1
        remaining.remove(chosen)
    return picked


def displayable_discovery_candidate(candidate, record):
    """Keep only evidence that can be offered as a legal contiguous quote."""
    text = str(record['data'].get(candidate['text_field']) or '')
    start, end = candidate['start'], candidate['end']
    minimum = excerpt_min_chars(record['channel'])
    if minimum <= len(display_excerpt(text[start:end])) and end-start <= 360:
        return candidate
    if len(text) < minimum or end-start > 360:
        return None
    left, right = max(0,start-120), min(len(text),end+120)
    previous = [m.start()+left for m in re.finditer(r'[^。！？!?\n]+[。！？!?]?',text[left:start])
                if m.start()>0 or left==0 or text[left-1] in '。！？!?\n']
    following = [m.end()+end for m in re.finditer(r'[^。！？!?\n]+[。！？!?]?',text[end:right])
                 if m.end()<right-end or right==len(text) or text[end+m.end()-1] in '。！？!?']
    # At most two neighboring complete sentences on either side. The
    # reviewer still decides which current-work opinion is actually present.
    spans = [(a,b) for a in [start,*previous[-2:]] for b in [end,*following[:2]]
             if minimum <= b-a <= 360 and (a,b)!=(start,end)]
    for a,b in sorted(spans,key=lambda span:(span[1]-span[0],-span[0])):
        excerpt=text[a:b]
        if len(display_excerpt(excerpt))>=minimum:
            return {**candidate,'start':a,'end':b,'expanded_discovery_span':True,
                    'excerpt_hash':hashlib.sha256(re.sub(r'\s+','',excerpt).encode('utf-8')).hexdigest()}
    return None


def prepare_discovery(workspace, state, sources, digest):
    grouped_candidates = []
    from report_evidence import release_notice_only
    seen, seen_hashes, source_uses = set(), set(), Counter()
    protected_kinds = ('verified_media', 'social_criticism', 'report_evidence')
    # Preserve the separate media/social recall lanes, then spread the remaining
    # reading budget across ordinary rough groups instead of dropping the tail.
    eligible_by_span = {}
    for group in state["pool"]:
        eligible = []
        for item in group['candidates']:
            key=(item['source_id'],item['text_field'],item['start'],item['end'])
            if key not in eligible_by_span:
                eligible_by_span[key]=displayable_discovery_candidate(item,sources[item['source_id']])
            selectable = eligible_by_span[key]
            if selectable is None:
                continue
            if selectable.get('expanded_discovery_span'):
                item = {**item, **{field:selectable[field] for field in
                        ('start','end','excerpt_hash','expanded_discovery_span')}}
            eligible.append(item)
        if state.get('report_evidence_policy'):
            eligible = [item for item in eligible if not release_notice_only(
                str(sources[item['source_id']]['data'].get(item['text_field']) or '')[item['start']:item['end']])]
        picked = representative_candidates(eligible, sources, seen, seen_hashes, source_uses,
                                           review_social_criticism=group['stance'] == '负向词倾向')
        grouped_candidates.append((group, picked))
    protected = [(group, candidate) for group, picked in grouped_candidates
                 if group.get('probe_kind') in protected_kinds for candidate in picked]
    ordinary = [(group, picked) for group, picked in grouped_candidates
                if group.get('probe_kind') not in protected_kinds]
    selected = protected[:DISCOVERY_INPUT_LIMIT]
    for offset in range(max((len(picked) for _, picked in ordinary), default=0)):
        for group, picked in ordinary:
            if len(selected) >= DISCOVERY_INPUT_LIMIT:
                break
            if offset < len(picked):
                selected.append((group, picked[offset]))
    excerpts = []
    source_refs = {}
    for group, candidate in selected:
        record = sources[candidate["source_id"]]
        text = str(record["data"].get(candidate["text_field"]) or "")
        a, b = candidate["start"], candidate["end"]
        quote = text[a:b]
        # A self-identifying quote does not need its surrounding sentence
        # repeated in the model input. Keep context for ambiguous targets.
        context = '' if any(term and term in quote for term in state['terms']) else text[max(0,a-60):min(len(text),b+60)]
        source_ref = source_refs.setdefault(candidate['source_id'],f"S{len(source_refs)+1:03d}")
        excerpts.append({**candidate, "excerpt_id": f"E{len(excerpts)+1:03d}", "excerpt": quote,
                         "source_ref":source_ref,
                         "rule_aspect": group["aspect"], "rule_stance": group["stance"],
                         "source_channel": record.get('channel', ''),
                         "verified_mainstream": bool(source_priority(record)['media_authority_rank']),
                         "context": context})
    answer = workspace / "viewpoint_discovery_answer.json"
    from discovery_continuity import build_context, RULE as HISTORY_RULE
    continuity = build_context(workspace,state["target"])
    from evidence_quality import old_candidates
    hints=old_candidates(excerpts,continuity,state['target'])
    if continuity:
        continuity['seed_history_candidates']=hints
        continuity['comparison_feedback_path']=str(workspace/'history_candidate_feedback.json')
    for item in excerpts:item['history_candidates']=hints.get(item['excerpt_id'],[])
    binding = digest([state["binding"], excerpts, DISCOVERY_RULE,continuity])
    social_ids = [item["excerpt_id"] for item in excerpts
                  if item["source_channel"] in SOCIAL_CHANNELS and not item["verified_mainstream"]]
    template = {"fingerprint": binding, "social_negative_review": [], "clusters": [],
                "cluster_boundaries":[],"evidence_shortfall_reason":""}
    payload = {"stage": "CURRENT_PERIOD_VIEWPOINT_DISCOVERY", "target": state["target"],
               "rules": ("先将两份独立原话并排读，去掉各自独有的人名、场景、时间变化和因果细节，用一句话写下两者都实际评价的对象、做法与效果。这句话就是core_mechanism，标题围绕它写。若去掉细节后只剩“很好”，换另一份具体原句，仍不足就报缺口。切勿把一条原话中的例子或发展过程写成其余样本必须证明的条件。拟独立成段的每个点先找两条不同source_ref、"
                         "且voice_ref不同、对同一集中评价主题有具体评价的E原句，把两条放入seed_ids；允许相关细节互补，不要求同一场戏、同一动作或同一人物。同voice_ref代表采样识别为同作者，换链接或不同编号仍只算一份声音。不同编号仅是不同原表来源，"
                         "同稿转载和近义转述仍只算一份。标题概括这一类评价：每份声音均支持同一核心，每个实质分句至少一条具体证据；可以只写一个有据分句。不要把某条样本的场景或人名变成整个观点的筛选条件，也不能把不同主题合成整体很好。"
                         "热度数字、定档、演员名单、上线和剧情简介本身不占正面观点名额。"
                         "如确有不同的正面线索但暂时只有一份声音，明确标注实际缺口，不把它当成已能独立成段。"
                         "工作台优先形成八至十个真正独立、各有双证据的正面点，给成稿至少六个正面点留出余量。"
                         "八至十个是优先范围、六个是硬下限，不为补数拆近义点或延长无效检索。"
                         "初步列出后，先找可能重合或证据偏弱的组，预计合并后仍不足时，再检查材料中是否存在有双证据的不同判断。"
                         "有就一并作为候选，没有就记录缺口，不凑数。不要因同类讨论多就忽略其他评价对象。\n"
                         + DISCOVERY_RULE) + " 同一答卷只用一次完整Write；修复也顺序写完整JSON，不在同一轮并行Edit同一个文件。只引用E编号，不抄来源ID或内容哈希。",
               "min_clusters": 1, "max_clusters": 16, "min_final_positive_clusters": 6,
               "preferred_positive_workbench_range": [8, 10],
               "min_final_independent_voices_per_positive_cluster": 2,
               "positive_count_policy": "HARD_MINIMUM_AT_FINAL_WITH_BOUNDED_REPAIR",
               "max_negative_clusters": 3,
               "allowed_categories":list(CATEGORIES),
               "title_evidence_limits":{"max_items":3,"max_seed_ids_per_item":3,
                   "rule":"每个标题分句1条证据，总计1—3条；不是每个seed单写一条。seed_ids每条1—3个。整条E原句足以支持分句时只填quote_seed_id，脚本回填该E的原文；需要聚焦时只填quote_snippet（从这些E句的excerpt字段选至少8字连续原句）。同时填写两者时，quote_snippet必须逐字出自quote_seed_id对应的同一E。context只助认作品，不能代替excerpt。"},
               "answer_file": str(answer), "fingerprint": binding,
               "excerpts": [{key:item[key] for key in ("excerpt_id","source_ref","excerpt","context","source_channel","verified_mainstream","history_candidates")} for item in excerpts],
               "social_review_ids": social_ids,
               "social_review_schema": {"excerpt_id": "social_review_ids中的E编号，每个一次", "decision": "criticism / not_criticism / out_of_scope", "reason": "简短说明原句为何属于或不属于本期批评；真批评未入组须说明取舍", "cluster_id": "入选的负面簇ID；未入组为null"},
               "answer_template": template,
               "cluster_boundary_schema":{"left":"簇ID","right":"同方面/共享seed/判断相近的另一簇ID，跨立场也核对","distinction":"至少12字的具体机制区别"},
               "cluster_schema": {"id": "本期唯一短ID", "title": "完整报告体观点句", "stance": "positive/objective/negative",
                                  "category":"从allowed_categories选一个字符串，不填数组、不自行改名称","category_reason":"仅其他独立方面必填，至少12字说明不能归入已有方面的原因","core_mechanism":"8—80字，概括两份原话各自都支持的具体判断；只被其中一份支持的例子留在引文",
                                  "seed_ids": ["1—5个输入中的excerpt_id"], "keywords": ["共3—8个，每个为seed原文中2—8字的简短检索词，不是完整句"],
                                  "performance_focus": "布尔值；本簇核心评价演员表演/演技/角色演绎时为true",
                                  "objective_kind":"仅客观簇填neutral_fact或opinion_distribution",
                                  "seed_stance_review":[{"excerpt_id":"客观簇每个seed一次","stance":"positive/negative/mixed/neutral","reason":"至少8字实际判断依据"}],
                                  "title_evidence": [{"claim": "标题中的一个实质分句", "seed_ids": ["支持该分句的excerpt_id"],"quote_seed_id":"整条excerpt支持时填所列E编号", "quote_snippet":"需裁剪时改填至少8字连续支持原句，二选一"}]}}
    from model_packet import voice_refs
    author_refs=voice_refs(excerpts)
    for row, original in zip(payload['excerpts'], excerpts):
        row['voice_ref']=author_refs[row['excerpt_id']]
        if row['context']:
            item = sources[original['source_id']]
            text = str(item['data'].get(original['text_field']) or '')
            start, end = original['start'], original['end']
            row['context'] = {'before': text[max(0, start-60):start], 'after': text[end:end+60]}
        else:
            del row['context']
        if row['history_candidates']:
            row['history_candidates']=[v['history_sample_id'] for v in row['history_candidates']]
        else:
            del row['history_candidates']
        if not row['verified_mainstream']:
            del row['verified_mainstream']
    from report_evidence import facet_hint_index,sparse_facet_checks
    payload['facet_hint_index']=facet_hint_index(payload['excerpts'])
    sparse_checks=sparse_facet_checks(payload['facet_hint_index'])
    if sparse_checks:
        payload['sparse_facet_checks']=sparse_checks
        voices_by_id = {row['excerpt_id']: row['voice_ref'] for row in payload['excerpts']}
        payload['sparse_facet_voice_groups'] = {
            facet: [{'voice_ref': voice,
                     'excerpt_ids': [sid for sid in ids if voices_by_id[sid] == voice]}
                    for voice in dict.fromkeys(voices_by_id[sid] for sid in ids)]
            for facet, ids in sparse_checks.items()}
        payload['facet_pair_review_schema']=[{'facet':'所列方面','decision':'used或not_used',
            'group_id':'used时填写实际正面簇ID，not_used时null',
            'reason':'至少12字，说明两条怎样直接印证或互补支持一个集中评价主题及可否单独成立'}]
        template['facet_pair_review']=[]
        payload['rules']+='\n提交簇前逐项回读sparse_facet_checks的E原句与作者。若两条的具体评价共同支持一个集中主题且区别于其他组，应给这个判断保留两条种子；同一来源有多方面内容时优先给材料较少的真实独立点，已有充足材料的组另选同等证据。若只是旁枝、条件期待、复述同稿或两个不同判断，填not_used并说明，不为索引创建组。'
        payload['rules']+='\nsparse_facet_voice_groups按作者展开本方面的真实原句。某条与其他方面的一条同作者，不等于本方面的两条来自同一作者。跨方面共用来源时，先给确有独立评价且来源较少的方面保留种子，再从充足方面找替代来源；最终每条样本仍只属一组。已经发布的预告、海报中的具体观感可作为物料观察的评价，不能仅因正片未播就把实际评价当成条件期待；没有具体评价的定档稿仍排除。'
    payload['rules']+='\nfacet_hint_index只定位本包可能含具体评价的原句，可优先扫只有少量作者的方面，再回读实际E全文。关键词不代表观点成立，肯定、批评、期待和转述仍须由你区分。'
    if continuity:
        payload['rules'] += '\n'+HISTORY_RULE
        payload['previous_viewpoints']=continuity['previous_viewpoints']
        payload['history_rule_version']=continuity['version']
        payload['cluster_schema']['history_review']={'checked_candidates':[{'history_sample_id':'实际核对的H句ID，先看E提示；交卷后须一次补齐控制器给出的核心近邻反馈','relation':'continuing/developed/opposing/irrelevant','reason':'至少12字对比具体判断，旧分类和立场不限制匹配'}],'development_type':'仅developed填changed_judgment或new_mechanism','change_statement':'仅developed至少12字说明真正新增判断或机制，不以换例子作展开','relation':'continuing/developed/opposing/unseen_angle','previous_group_ids':['H1-G0'],'reason':'至少15字的具体机制对照'}
        payload['answer_template']['history_selection']={'selection_summary':'','omitted_previous':[]}
        payload['history_selection_schema']={'selection_summary':'至少20字解释本期点位取舍与真实差异限度','omitted_previous':[{'previous_group_id':'未被任何当前簇匹配的前簇H-G编号','reason':'至少12字具体取舍理由'}]}
    from model_packet import write_packet, readable_rules
    payload["rules"] = readable_rules(payload["rules"])
    write_packet(workspace / "viewpoint_discovery_input.json", payload)
    save(answer, template)
    state.update(status="VIEWPOINT_DISCOVERY_REQUIRED", discovery_binding=binding, discovery_excerpts=excerpts,
                 discovery_history_context=continuity,discovery_sparse_facet_checks=sparse_checks)
    # Formal discovery already reads every excerpt and performs all validations.
    # Do not spend a second full model call on an unreviewed preliminary sketch.
    return {'read_now': str(workspace / 'viewpoint_discovery_input.json'),
            'answer_file': str(answer),
            'next_command': f'"{sys.executable}" -X utf8 "{Path(__file__).with_name("final_sample_review.py")}" discover-collect --workspace "{workspace}"'}


def validate_social_negative_review(answer, excerpts, clusters):
    social_ids = {key for key, item in excerpts.items()
                  if item["source_channel"] in SOCIAL_CHANNELS and not item.get("verified_mainstream")
                  and not item.get("coverage_only")}
    reviews = answer.get("social_negative_review")
    if not isinstance(reviews, list):
        raise ValueError("social_negative_review须逐条核对本输入微博、小红书摘录")
    ids = [row.get("excerpt_id") for row in reviews if isinstance(row, dict)]
    if any(not isinstance(identity, str) for identity in ids):
        raise ValueError("social_negative_review仅能引用social_review_ids中的E编号")
    if len(ids) != len(reviews) or len(ids) != len(set(ids)) or set(ids) != social_ids:
        raise ValueError(f"social_negative_review须覆盖全部且仅覆盖social_review_ids；缺少：{sorted(social_ids-set(ids))}；多余：{sorted(set(ids)-social_ids)}")
    cluster_stances = {group.get("id"): group.get("stance") for group in clusters if isinstance(group, dict)}
    negative_seeds = {seed for group in clusters if isinstance(group, dict) and group.get("stance") == "negative"
                      for seed in (group.get("seed_ids") if isinstance(group.get("seed_ids"), list) else [])}
    normalized = []
    for row in reviews:
        decision, reason, cluster_id = row.get("decision"), str(row.get("reason") or "").strip(), row.get("cluster_id")
        if decision not in ("criticism", "not_criticism", "out_of_scope") or not reason:
            raise ValueError(f'{row.get("excerpt_id")}: 社交评论核对须写明criticism/not_criticism/out_of_scope和非空依据')
        if cluster_id in ("", None):
            cluster_id = None
        elif not isinstance(cluster_id, str):
            raise ValueError(f'{row["excerpt_id"]}: cluster_id须为本次负面簇ID或null')
        if decision == "criticism":
            if cluster_id is not None and cluster_stances.get(cluster_id) != "negative":
                raise ValueError(f'{row["excerpt_id"]}: 真批评只能关联本次负面簇')
        elif cluster_id is not None or row["excerpt_id"] in negative_seeds:
            raise ValueError(f'{row["excerpt_id"]}: 非批评/超范围评论不能作负面簇证据')
        normalized.append({"excerpt_id": row["excerpt_id"], "decision": decision,
                           "reason": reason, "cluster_id": cluster_id})
    return normalized

def normalize_title_seed_quotes(clusters, excerpts):
    """Resolve a supplied source pointer without changing the chosen wording."""
    issues = []
    for group in clusters:
        evidence = group.get('title_evidence')
        if not isinstance(evidence,list):
            continue
        for index, claim in enumerate(evidence):
            if not isinstance(claim,dict) or not claim.get('quote_seed_id'):
                continue
            seed = claim['quote_seed_id']
            snippet = claim.get('quote_snippet')
            prefix = f"{group.get('id')}: title_evidence[{index}]"
            if (not isinstance(seed,str) or seed not in excerpts
                    or not isinstance(claim.get('seed_ids'),list) or seed not in claim['seed_ids']):
                issues.append(f'{prefix}.quote_seed_id须为本分句seed_ids中实际存在的E编号；'
                              '如只提交逐字quote_snippet，删除quote_seed_id')
                continue
            original = excerpts[seed]['excerpt']
            if not snippet:
                claim['quote_snippet'] = original
            elif not isinstance(snippet,str) or snippet not in original:
                issues.append(f'{prefix}.quote_snippet与quote_seed_id={seed}不一致；'
                              '只保留实际引用的同一E逐字片段，或只填对应整条E的编号')
    return issues


def apply_discovery(workspace, state, answer, sources, digest):
    if state.get("status") != "VIEWPOINT_DISCOVERY_REQUIRED":
        raise ValueError("当前不是本期观点发现阶段")
    if answer.get("fingerprint") != state["discovery_binding"]:
        raise ValueError("观点答卷本期指纹不匹配")
    clusters = deepcopy(answer.get("clusters"))
    if not isinstance(clusters, list) or not 1 <= len(clusters) <= 16:
        raise ValueError("填写1—16个有证据的独立观点；正面不足6组会进入有界补缺或报告缺口，不拆近义组凑数")
    if any(not isinstance(group,dict) for group in clusters):raise ValueError('每簇须为JSON对象')
    if sum(isinstance(group, dict) and group.get('stance') == 'negative' for group in clusters) > 3:
        raise ValueError('负面观点最多3组；合并近义批评或保留主要批评，不将负面改标正面/客观，不编造观点补数量')
    if any(not isinstance(group, dict) or not isinstance(group.get('performance_focus'), bool) for group in clusters):
        raise ValueError('每簇须填写performance_focus布尔值；表演是核心观点时为true，人物只是例证时为false')
    acting_groups = [group for group in clusters if group['stance'] in ('positive', 'objective') and group['performance_focus']]
    if len(acting_groups) > 2:
        raise ValueError('正面和客观的演技/表演观点合计最多2组；先按共同评价机制合并不同演员或角色，不得为满足正面6组而逐人拆分：' + ', '.join(str(group.get('id') or '') for group in acting_groups))
    excerpts = {item["excerpt_id"]: item for item in state["discovery_excerpts"]}
    seed_quote_issues = normalize_title_seed_quotes(clusters, excerpts)
    boundary_problem=None
    try:validate_cluster_boundaries(answer,clusters,excerpts)
    except ValueError as exc:boundary_problem=str(exc)
    social_negative_review = validate_social_negative_review(answer, excerpts, clusters)
    for row in social_negative_review:
        if row['decision'] != 'criticism' or not row['cluster_id']:
            continue
        group = next(group for group in clusters if group.get('id') == row['cluster_id'])
        if social_pacing_conflict(excerpts[row['excerpt_id']]['excerpt'], str(group.get('title') or '')):
            raise ValueError(f"{row['excerpt_id']}: 社交批评的节奏方向与{row['cluster_id']}标题相反；请改标题或取消该绑定，不得把慢说成仓促/快")
    # The model sees both excerpt and context. Accept either, but only after
    # reconstructing the supplied context from the same original source span.
    seed_texts = {}
    for identity, item in excerpts.items():
        parts = [item["excerpt"]]
        if "context" in item:
            original = str(sources[item["source_id"]]["data"].get(item["text_field"]) or "")
            a, b = item["start"], item["end"]
            expected = original[max(0, a-60):min(len(original), b+60)]
            if item["context"] not in ('', expected) or item["excerpt"] != original[a:b]:
                raise ValueError(f'{identity}: seed上下文与原始来源不一致')
            if item['context']:
                parts.append(expected)
        seed_texts[identity] = parts
    # Report all literal keyword mistakes together; never guess or rewrite routing words.
    from report_evidence import sparse_facet_issues
    route_issues = [*seed_quote_issues, *([boundary_problem] if boundary_problem else []),
                    *sparse_facet_issues(state.get('discovery_sparse_facet_checks',{}),
                                         answer.get('facet_pair_review'),clusters)]
    ignored_keywords = []
    for group in clusters:
        if not isinstance(group, dict):
            continue
        problem = title_error(group.get('title'))
        if problem:
            route_issues.append(f'{group.get("id", "未知簇")}: {problem}')
        if (group.get('stance') in ('positive', 'objective') and
                (group.get('category')=='演员表演' or ACTING_TITLE_CUES.search(str(group.get('title') or ''))) and
                group.get('performance_focus') is False):
            route_issues.append(f'{group.get("id", "未知簇")}: 标题以演技/表演为核心时performance_focus须为true；若核心其实是剧本或视觉处理，请改清标题')
        seeds, words = group.get("seed_ids"), group.get("keywords")
        if (not isinstance(seeds, list) or not seeds or
                any(not isinstance(seed, str) or seed not in excerpts for seed in seeds) or
                not isinstance(words, list) or any(not isinstance(word, str) for word in words)):
            continue
        if not 3 <= len(words) <= 8 or len(set(word.strip() for word in words)) < 3:
            route_issues.append(f'{group.get("id", "未知簇")}: keywords须有3—8个不重复的原文短词；当前={words!r}')
        evidence = group.get('title_evidence')
        if not isinstance(evidence, list) or not evidence or len(evidence) > 3:
            route_issues.append(f'{group.get("id", "未知簇")}: title_evidence须逐条绑定标题实质分句和1—3个当前seed编号')
        else:
            for claim in evidence:
                if (not isinstance(claim, dict) or not str(claim.get('claim') or '').strip() or
                        not isinstance(claim.get('seed_ids'), list) or not 1 <= len(claim['seed_ids']) <= 3 or
                        any(seed not in excerpts for seed in claim['seed_ids'])):
                    route_issues.append(f'{group.get("id", "未知簇")}: title_evidence存在空分句或未知seed编号')
        too_long = [word for word in words if not 2 <= len(word.strip()) <= 8]
        if too_long:
            route_issues.append(f'{group.get("id", "未知簇")}: keywords须为2—8字原文短词，不是标题或完整句：{too_long}；从seed重新选词，不机械截断')
        absent = [word.strip() for word in words
                  if not any(word.strip() in part for seed in seeds for part in seed_texts[seed])]
        if absent:
            literal_words = list(dict.fromkeys(word.strip() for word in words if word.strip() not in absent))
            if len(literal_words) >= 3 and not too_long:
                group["keywords"] = literal_words
                ignored_keywords.append({"viewpoint_id": group.get("id"), "nonliteral_keywords": absent})
            else:
                route_issues.append(f'{group.get("id", "未知簇")}: 未出现的路由词：{absent}；当前seed：{seeds}')
    if route_issues:
        raise ValueError("请一次修复全部路由词逐字出处错误和标题结构错误，只改列出的字段：\n" + "\n".join(route_issues))
    titles, ids = set(), set()
    validated = []
    for group in clusters:
        title = str(group.get("title") or "").strip()
        identity = str(group.get("id") or "").strip()
        if not identity or identity in ids or title in titles:
            raise ValueError("簇ID/标题不能为空或重复")
        if title_error(title):
            raise ValueError("标题须为对象+方向+依据的报告体观点句，而非方面短词：" + title)
        if group.get("stance") not in STANCES:
            raise ValueError("立场必须positive/objective/negative")
        seeds, keywords = group.get("seed_ids"), group.get("keywords")
        if not isinstance(seeds, list) or not 1 <= len(seeds) <= 5 or len(set(seeds)) != len(seeds) or any(seed not in excerpts for seed in seeds):
            raise ValueError(f'{identity}: seed_ids须为1—5个当前E编号；未知编号：{[seed for seed in seeds if seed not in excerpts] if isinstance(seeds,list) else seeds}')
        if not isinstance(keywords, list) or not 3 <= len(keywords) <= 8 or any(not isinstance(word,str) or not 2 <= len(word.strip()) <= 8 for word in keywords):
            raise ValueError(f"{identity}: keywords必须为3—8个2—8字具体原文短词；当前={keywords!r}")
        keywords = list(dict.fromkeys(word.strip() for word in keywords))
        absent = [word for word in keywords if not any(word in part for seed in seeds for part in seed_texts[seed])]
        if len(keywords) < 3 or absent:
            raise ValueError(f'{identity}: 路由词须为seed摘录或随附上下文中的逐字词，未出现：{absent}；当前seed：{seeds}')
        title_evidence = group.get('title_evidence')
        if not isinstance(title_evidence, list) or not title_evidence or len(title_evidence) > 3:
            raise ValueError(f'{identity}: title_evidence须逐条绑定标题实质分句和1—3个当前seed编号')
        normalized_evidence = []
        for claim in title_evidence:
            if (not isinstance(claim, dict) or not str(claim.get('claim') or '').strip() or
                    not isinstance(claim.get('seed_ids'), list) or not 1 <= len(claim['seed_ids']) <= 3 or
                    any(seed not in excerpts for seed in claim['seed_ids'])):
                raise ValueError(f'{identity}: title_evidence存在空分句或未知seed编号')
            normalized_evidence.append({'claim': str(claim['claim']).strip(), 'seed_ids': list(dict.fromkeys(claim['seed_ids'])), 'quote_snippet': claim['quote_snippet']})
        titles.add(title); ids.add(identity)
        validated.append({**group, "title": title, "keywords": keywords, "title_evidence": normalized_evidence})
    from discovery_continuity import validate_history
    history_review=validate_history(answer,validated,excerpts,state.get('discovery_history_context'))
    if history_review:
        state['discovery_continuity']=history_review
        save(workspace/'discovery_continuity.json',history_review)
    # All deterministic candidates retained; no full-source AI assignment.
    candidates = {}
    for group in state["pool"]:
        for item in group["candidates"]:
            key = (item["source_id"], item["start"], item["end"], item["text_field"])
            entry = candidates.setdefault(key, {**item, "rule_stances": []})
            if group["stance"] not in entry["rule_stances"]:
                entry["rule_stances"].append(group["stance"])
    for item in excerpts.values():
        if not item.get('expanded_discovery_span'):
            continue
        key=(item['source_id'],item['start'],item['end'],item['text_field'])
        if key not in candidates:
            hidden={'excerpt_id','source_ref','excerpt','rule_aspect','rule_stance','context','history_candidates'}
            candidates[key]={k:v for k,v in item.items() if k not in hidden}
            candidates[key]['rule_stances']=[item['rule_stance']]
    pool = []
    # Full text stays in the script. Rescan eligible long articles after actual
    # period-specific keywords exist, rather than freezing generic early picks.
    article_index = {}
    for item in candidates.values():
        record = sources[item['source_id']]
        text = str(record['data'].get(item['text_field']) or '')
        if record.get('channel') in ARTICLE_CHANNELS and len(text) > 360:
            key = (item['source_id'], item['text_field'])
            if key not in article_index:
                article_index[key] = (item, text, list(sentence_spans(text)))
    for group in validated:
        seed_keys = {(excerpts[seed]["source_id"], excerpts[seed]["start"], excerpts[seed]["end"], excerpts[seed]["text_field"]) for seed in group["seed_ids"]}
        # Keep the exact passage that supported discovery available for review.
        # A fresh keyword match elsewhere in its article may say something else.
        article_seeds = {}
        for seed_id in group['seed_ids']:
            item = excerpts[seed_id]
            key = (item['source_id'], item['text_field'])
            article_seeds.setdefault(key, item)
        routed = []
        for key, item in candidates.items():
            if (item['source_id'], item['text_field']) in article_index:
                continue
            text = str(sources[item["source_id"]]["data"].get(item["text_field"]) or "")
            quote = text[item["start"]:item["end"]]
            hits = [word for word in group["keywords"] if word in quote]
            if key not in seed_keys and not hits:
                continue
            score = item["score"] + 15*len(hits) + sum(min(8,len(word)) for word in hits)
            score += 60 if key in seed_keys else 0
            score += 10 if STANCES[group["stance"]] in item["rule_stances"] else -10
            routed.append({key:value for key,value in {**item, "score":score}.items() if key != "rule_stances"})
        for (source_id, field), (base, text, spans) in article_index.items():
            passages = viewpoint_passages(text, spans, group['keywords'], state.get('terms', [state['target']]), STANCES[group['stance']])
            seed = article_seeds.get((source_id, field))
            if seed:
                a, b = seed['start'], seed['end']
                minimum = excerpt_min_chars(sources[source_id].get('channel'))
                if b-a <= 360 and len(display_excerpt(text[a:b])) >= minimum:
                    other = [p for p in passages if p['end'] <= a or p['start'] >= b]
                    score = max(seed['score'], passages[0]['score'] if passages else 0) + 60
                    passages = [{'start': a, 'end': b, 'text': text[a:b], 'score': score}, *other[:1]]
            if not passages:
                continue
            best = passages[0]
            options = [{'start': item['start'], 'end': item['end'], 'text': item['text']} for item in passages]
            # Same baseline range as sentence routes, without rewarding seed status
            # for a different, unreviewed paragraph in the same source.
            routed.append({k:v for k,v in {**base, 'start': best['start'], 'end': best['end'],
                           'score': best['score'] + 30, 'selection_options': options,
                           'excerpt_hash': hashlib.sha256(re.sub(r'\s+', '', best['text']).encode('utf-8')).hexdigest()}.items()
                           if k != 'rule_stances'})
        routed = rank_candidates(routed, sources, stance=group['stance'])
        pool.append({"aspect": group["title"], "category":group['category'],"core_mechanism":group['core_mechanism'],
                     "objective_kind":group.get('objective_kind'),
                     "stance": STANCES[group["stance"]], "candidates": routed,
                     "viewpoint_id": group["id"],"history_review":group.get("history_review"), "keywords": group["keywords"], "seed_ids": group["seed_ids"]})
    save(workspace / "viewpoint_definitions.json", {"fingerprint":state["discovery_binding"],"clusters":validated,
                                                    "social_negative_review":social_negative_review,
                                                    "ignored_nonliteral_keywords":ignored_keywords,
                                                    "facet_pair_review":answer.get('facet_pair_review',[]),
                                                    "cluster_boundaries":answer['cluster_boundaries'],
                                                    "evidence_shortfall_reason":answer.get('evidence_shortfall_reason',''),"discovery_continuity":history_review})
    save(workspace / "viewpoint_candidate_pool.json", pool)
    positive_count = sum(group.get('stance') == 'positive' for group in validated)
    state.update(pool=pool, pool_file="viewpoint_candidate_pool.json", pool_hash=digest(pool),
                 evidence_shortfall_reason=answer.get('evidence_shortfall_reason',''),
                 binding=digest([state["raw_hash"],pool,state["target"],state["discovery_binding"]]),
                 ai_viewpoint_discovery=True, viewpoint_definition_count=len(pool),
                 min_positive_clusters=6, positive_cluster_target=6, positive_count_policy="HARD_MINIMUM",
                 positive_cluster_count=positive_count,
                 positive_cluster_shortfall=positive_count < 6,
                 title_contract="FINAL_DISPLAY_EVIDENCE_V2", status="PREPARING_FINAL_AI")
