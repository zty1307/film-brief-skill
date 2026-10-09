"""One current-period AI discovery handoff, then deterministic candidate routing."""
from __future__ import annotations
from collections import Counter
import re
import sys
from pathlib import Path
from rapid_film_workbench import (save, WORK_SCOPE_RULE, lexical_features, text_overlap,
                                 rank_candidates, source_priority, ARTICLE_CHANNELS,
                                 sentence_spans, viewpoint_passages)
import hashlib

STANCES = {"positive": "正向词倾向", "objective": "中性或混合", "negative": "负向词倾向"}
DISCOVERY_RULE = """只读本期脚本代表摘录，形成10—16个同层级一级观点簇，其中正面至少10组。标题使用可直接写进简报的完整观点句，通常24—36字，以接近30字为宜，例如“肯定某种具体处理，认为它带来某种观感或价值”；逗号前点明对象与做法，逗号后概括一个评价意义，两层围绕一个核心主张。只留共同核心，不复述情节、不枚举动作、不把论据和人名名单塞进标题；具体例子留在样本。完整不是越长越好，不要压成方面标签或短口号，也不要把无关议题塞进一个标题。标题机器范围12—48字，允许逗号和顿号，意义是否统一由你判断。
正面和客观中，以演员演技、表演层次、角色演绎为核心的观点合计最多1—2组。先按共同评价机制合并主演、男主、配角等人物实例，通常用一组概括群像表演；只有证据确实支持两种不同的表演机制，才用第二组。不得因演员/角色不同、样本多或正面组数下限，就逐人拆成三组以上；正面至少10组须从其他有独立证据的叙事、人物书写、制作、视听、主题、传播等机制寻找，找不到则如实报不足。角色由剧本如何塑造与演员如何演绎要分清；反串造型若主要评价服装/视觉，可归视觉制作，若主要评价演员演绎则归表演，不因单一噱头自立一个近义表演簇。每簇填写performance_focus布尔值，核心是演技/表演/演绎时为true，作为其他观点的例证时为false。
先统看主要议题与本期阶段。表演、人物塑造、叙事、视听、情感和社会主题中的支持性评价都应按本期证据识别；明确且独立的正面机制即使材料较少也可成组。正负面使用相同粒度：不能把正面挤进一个大组却按每个人、每个近义批评分出多个负面组；不要同时列总括组和仅作为实例的子组。不同机制仍可分开，不按正负客观平均凑组，正面展示下限不代表全网正面占比。具体对象不同但机制相同可合并；造型重复与剧情陈旧通常应分别命名。脚本rule_stance只是词语提示，遇否定、反讽和对比按原句判断。代表句条数和平台数量不代表全网声量。
每组立场positive/objective/negative唯一；客观可以是中性事实、观察或对评价分布的中性概括，具体赞贬分别归入对应组。标题不将司法确认、商业金额等来源自述升级为已证实事实；争议和传闻保留来源说法属性。具体数值、百分比、名次留在摘录，不把开播消息写成播后成绩。不要因背景人物名单或泛词把无关材料拉进组。
每簇给1—5个本输入seed_ids作命名依据。标题可以展开，keywords必须另取3—8个2—8字的简短检索词；选评价对象、处理方式和态度的原文短词，覆盖共同观点的不同说法，不把完整评价句、人名名单或多个词用标点连成一个关键词。至少两个词应描述内容或机制，不只靠剧名、人名、泛泛的好看/不好看来找材料。每词须在至少一个seed的excerpt或随附context原文中逐字出现，不得跨片段拼接，也不要从长句机械截前8字。分组标题不堆具体人名或穷举例子；表演效果与人物形象、主题意义等不同主张不要仅因同为正面便合在一起。seed不是最终keep；后续AI只审核最终展示句。标题通过后固定。只写answer_file完整JSON，不照搬旧报告或旧标题，不审全部来源、不查网络；整体取舍在本次发现完成，不增加第二轮整组复审。"""

TITLE_PATTERN = r"^(?:(?:观众|网友|网民|媒体|舆论|公众|影评人|评论者|部分观众|部分网友))?(?:认可|肯定|关注|讨论|质疑|批评|担忧|认为|指出|赞赏|称赞|期待)"
DISCOVERY_RULE += WORK_SCOPE_RULE
DISCOVERY_RULE += " 负面观点最多3组：保留本期证据充分、机制不同的主要批评，近义批评合并，其他负面留在后台，不改成中性或正面凑数。负面候选在相关性接近时先看微博、小红书的真实评论，再看公众号，媒体报道后置；不新增采样、不把渠道当成观点真实性证明。正面观点至少10组，每组须有彼此不同的评价机制和本期原文证据（如叙事、表演、人物、视听、情感主题、制作或传播表现），不要把所有好评挤成一个大组。材料不足10组时如实报告不足，不拆近义组、不虚构、不把客观或负面改标正面，不反复重跑发现或突破30分钟预算。总数10—16。标题的每个实质分句都须有seed原文依据，并在title_evidence中逐条绑定seed编号；不能为了凑完整双句而补写传播影响、社会反响、因果或价值判断；没有第二层依据就只写有证据的完整判断。题材涉及某群体不等于该群体已讨论，创作意图不等于实现效果，来源自述不等于已证实事实。写答卷前在本次命名里检查标题所有分句，不新增审查轮。"

ACTING_TITLE_CUES = re.compile(r'演技|表演|演绎|演员表现|角色诠释|眼神戏|微表情|打戏')

def title_error(title):
    if not isinstance(title,str) or not 12 <= len(title.strip()) <= 48 or not re.match(TITLE_PATTERN,title.strip()):
        return '标题须12—48字且以报告体判断谓语开头，通常24—36字写清具体处理及评价意义，不复述情节或堆例子'
    if re.search(r'\d+(?:\.\d+)?\s*(?:%|％|万|亿|家|人|部|场|集|天|年|名)|(?:第[一二三四五六七八九十0-9]+[名位])',title):
        return '固定标题不写具体数值或名次，留在各条摘录中并保留来源说法属性'
    return None

def representative_candidates(candidates, sources, seen, seen_hashes, source_uses, limit=6):
    """Keep a high-score anchor, then diversify only within a bounded score band.

    No channel quota or new semantic pass. A lower-quality channel is not forced
    into the reading packet. Known author/URL identity is only a duplicate hint.
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
        chosen = min(near, key=lambda item: (
            int(5 * max((text_overlap(features[id(item)], features[id(old)]) for old in picked), default=0)),
            -source_priority(sources[item['source_id']])['media_authority_rank'],
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


def prepare_discovery(workspace, state, sources, digest):
    excerpts = []
    seen, seen_hashes, source_uses = set(), set(), Counter()
    # Same reading cap as before; vary near-score channels/authors, not AI rounds.
    for group in state["pool"]:
        for candidate in representative_candidates(group['candidates'], sources, seen, seen_hashes, source_uses):
            record = sources[candidate["source_id"]]
            text = str(record["data"].get(candidate["text_field"]) or "")
            a, b = candidate["start"], candidate["end"]
            excerpts.append({**candidate, "excerpt_id": f"E{len(excerpts)+1:03d}", "excerpt": text[a:b],
                             "rule_aspect": group["aspect"], "rule_stance": group["stance"],
                             "source_channel": record.get('channel', ''),
                             "context": text[max(0,a-60):min(len(text),b+60)]})
    answer = workspace / "viewpoint_discovery_answer.json"
    binding = digest([state["binding"], excerpts, DISCOVERY_RULE])
    template = {"fingerprint": binding, "clusters": []}
    payload = {"stage": "CURRENT_PERIOD_VIEWPOINT_DISCOVERY", "target": state["target"],
               "rules": DISCOVERY_RULE + " 同一答卷只用一次完整Write；修复也顺序写完整JSON，不在同一轮并行Edit同一个文件。只引用E编号，不抄来源ID或内容哈希。", "min_clusters": 10, "max_clusters": 16, "min_positive_clusters": 10,
               "max_negative_clusters": 3,
               "answer_file": str(answer), "fingerprint": binding,
               "excerpts": [{key:item[key] for key in ("excerpt_id","excerpt","rule_aspect","rule_stance","context","source_channel")} for item in excerpts],
               "answer_template": template,
               "cluster_schema": {"id": "本期唯一短ID", "title": "完整报告体观点句", "stance": "positive/objective/negative",
                                  "seed_ids": ["输入中的excerpt_id"], "keywords": ["seed原文中2—8字的简短检索词，不是完整句"],
                                  "performance_focus": "布尔值；本簇核心评价演员表演/演技/角色演绎时为true",
                                  "title_evidence": [{"claim": "标题中的一个实质分句", "seed_ids": ["支持该分句的excerpt_id"]}]}}
    save(workspace / "viewpoint_discovery_input.json", payload)
    save(answer, template)
    state.update(status="VIEWPOINT_DISCOVERY_REQUIRED", discovery_binding=binding, discovery_excerpts=excerpts)
    return {"read_now": str(workspace / "viewpoint_discovery_input.json"),
            "next_command": f'"{sys.executable}" -X utf8 "{Path(__file__).with_name("final_sample_review.py")}" discover-collect --workspace "{workspace}"'}

def apply_discovery(workspace, state, answer, sources, digest):
    if state.get("status") != "VIEWPOINT_DISCOVERY_REQUIRED":
        raise ValueError("当前不是本期观点发现阶段")
    if answer.get("fingerprint") != state["discovery_binding"]:
        raise ValueError("观点答卷本期指纹不匹配")
    clusters = answer.get("clusters")
    if not isinstance(clusters, list) or not 10 <= len(clusters) <= 16:
        raise ValueError("本期必须10—16个有材料依据的观点簇；不接受通用24大类")
    if sum(isinstance(group, dict) and group.get('stance') == 'negative' for group in clusters) > 3:
        raise ValueError('负面观点最多3组；合并近义批评或保留主要批评，不将负面改标正面/客观，不编造观点补数量')
    if sum(isinstance(group, dict) and group.get("stance") == "positive" for group in clusters) < 10:
        raise ValueError("正面观点至少10组，每组须有独立原文依据；材料不足时如实报告不足，不凑组、不改标立场，不反复重跑或突破30分钟预算")
    if any(not isinstance(group, dict) or not isinstance(group.get('performance_focus'), bool) for group in clusters):
        raise ValueError('每簇须填写performance_focus布尔值；表演是核心观点时为true，人物只是例证时为false')
    acting_groups = [group for group in clusters if group['stance'] in ('positive', 'objective') and group['performance_focus']]
    if len(acting_groups) > 2:
        raise ValueError('正面和客观的演技/表演观点合计最多2组；先按共同评价机制合并不同演员或角色，不得为满足正面10组而逐人拆分：' + ', '.join(str(group.get('id') or '') for group in acting_groups))
    excerpts = {item["excerpt_id"]: item for item in state["discovery_excerpts"]}
    # The model sees both excerpt and context. Accept either, but only after
    # reconstructing the supplied context from the same original source span.
    seed_texts = {}
    for identity, item in excerpts.items():
        parts = [item["excerpt"]]
        if "context" in item:
            original = str(sources[item["source_id"]]["data"].get(item["text_field"]) or "")
            a, b = item["start"], item["end"]
            expected = original[max(0, a-60):min(len(original), b+60)]
            if item["context"] != expected or item["excerpt"] != original[a:b]:
                raise ValueError(f'{identity}: seed上下文与原始来源不一致')
            parts.append(expected)
        seed_texts[identity] = parts
    # Report all literal keyword mistakes together; never guess or rewrite routing words.
    route_issues = []
    for group in clusters:
        if not isinstance(group, dict):
            continue
        problem = title_error(group.get('title'))
        if problem:
            route_issues.append(f'{group.get("id", "未知簇")}: {problem}')
        if (group.get('stance') in ('positive', 'objective') and
                ACTING_TITLE_CUES.search(str(group.get('title') or '')) and
                group.get('performance_focus') is False):
            route_issues.append(f'{group.get("id", "未知簇")}: 标题以演技/表演为核心时performance_focus须为true；若核心其实是剧本或视觉处理，请改清标题')
        seeds, words = group.get("seed_ids"), group.get("keywords")
        if (not isinstance(seeds, list) or not seeds or
                any(not isinstance(seed, str) or seed not in excerpts for seed in seeds) or
                not isinstance(words, list) or any(not isinstance(word, str) for word in words)):
            continue
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
            raise ValueError("keywords必须为3—8个2—8字具体原文短词")
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
            normalized_evidence.append({'claim': str(claim['claim']).strip(), 'seed_ids': list(dict.fromkeys(claim['seed_ids']))})
        titles.add(title); ids.add(identity)
        validated.append({**group, "title": title, "keywords": keywords, "title_evidence": normalized_evidence})
    # All deterministic candidates retained; no full-source AI assignment.
    candidates = {}
    for group in state["pool"]:
        for item in group["candidates"]:
            key = (item["source_id"], item["start"], item["end"], item["text_field"])
            entry = candidates.setdefault(key, {**item, "rule_stances": []})
            if group["stance"] not in entry["rule_stances"]:
                entry["rule_stances"].append(group["stance"])
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
        pool.append({"aspect": group["title"], "stance": STANCES[group["stance"]], "candidates": routed,
                     "viewpoint_id": group["id"], "keywords": group["keywords"], "seed_ids": group["seed_ids"]})
    save(workspace / "viewpoint_definitions.json", {"fingerprint":state["discovery_binding"],"clusters":validated})
    save(workspace / "viewpoint_candidate_pool.json", pool)
    positive_count = sum(group.get('stance') == 'positive' for group in validated)
    state.update(pool=pool, pool_file="viewpoint_candidate_pool.json", pool_hash=digest(pool),
                 binding=digest([state["raw_hash"],pool,state["target"],state["discovery_binding"]]),
                 ai_viewpoint_discovery=True, viewpoint_definition_count=len(pool),
                 min_positive_clusters=10,
                 positive_cluster_count=positive_count,
                 positive_cluster_shortfall=positive_count < 10,
                 title_contract="FROZEN_SINGLE_CORE_V1", status="PREPARING_FINAL_AI")
