"""Recall concrete opinion spans from current source rows before clustering.

The cues allocate bounded reading opportunities. They do not classify a
source's stance, certify an opinion, or override the final sample review.
"""
from __future__ import annotations

import hashlib
import re

MUSIC_DETAIL = re.compile(
    r'(?:嗓音|音色|声线|唱腔|歌词|旋律|配乐|伴奏).{0,20}'
    r'(?:贴合|呼应|好听|有力|细腻|感染|抓耳|代入|带入|层次|压住|违和|出戏)'
    r'|(?:听完|听起来|一开口).{0,20}(?:好听|动人|抓耳|宿命感|有力量|代入|出戏)'
    r'|(?:清透|温润|空灵|浑厚|克制).{0,8}(?:嗓音|声线|唱腔)'
)
PERFORMANCE_PORTRAYAL = re.compile(
    r'(?:把|将)[^。！？\n]{0,35}(?:情绪|角色|人物|人设|绝望|求生|破碎|韧劲|城府|层次|气场|野心|傲骨)[^。！？\n]{0,20}(?:演出来|演得)'
    r'|演出了[^。！？\n]{0,20}(?:层次|气场|狠劲|贵气|城府|压迫感|绝望|求生|韧劲)'
)
FACET_CUES = {
    '视听技术与音乐': MUSIC_DETAIL,
    '剧情与叙事': re.compile(r'(?:叙事|剪辑|剧情节奏|铺垫|转场|伏笔).{0,14}(?:紧凑|流畅|利落|自然|扎实|拖沓|割裂|跳跃|生硬)'),
    '人物书写': re.compile(r'(?:人物动机|人设|角色转变|人物成长|群像).{0,14}(?:立体|鲜活|细腻|合理|有层次|自洽|生硬|单薄|突兀)'),
    '演员表演': re.compile(
        r'(?:眼神|台词|表情|动作|对手戏|哭戏).{0,14}(?:自然|克制|细腻|有张力|有感染力|出戏|违和|生硬)'
        +'|'+PERFORMANCE_PORTRAYAL.pattern),
    '服化道与视觉美术': re.compile(r'(?:构图|置景|实景|光影|运镜|妆造|服化道).{0,14}(?:细腻|自然|讲究|考究|贴合|逼真|出戏|违和|粗糙)'),
    '动作设计': re.compile(r'(?:打戏|打斗|招式|动作设计).{0,14}(?:利落|流畅|有力量|干脆|拳拳到肉|生硬|拖沓|敷衍)'),
}
MUSIC_RELEASE = re.compile(
    r'[^。！？!?]{1,130}(?:主题曲|人物曲|片头曲|片尾曲|插曲|单曲|原声专辑|OST)'
    r'[^。！？!?]{0,38}(?:上线|发布|释出)'
    r'(?:[（(][^）)]+[）)])?[。！？!?]*', re.I
)
FIRSTHAND = re.compile(r'听完|听起来|一开口|这首.{0,8}好听|看完|刚看|让我|我觉得')
LISTENING_REACTION = re.compile(r'(?:听完|听了|一开口|这首).{0,26}(?:好听|抓耳|动人|听哭|鸡皮疙瘩)')
HYPOTHETICAL = re.compile(r'(?:如果|假如|倘若|要是)[^。！？!?]{0,60}$')
OFFSCREEN_INTERACTION = re.compile(r'综艺互动|戏外|营业互动|接.{0,4}下班|录制.{0,6}(?:综艺|跑男)|(?:粉丝|粉)看完直呼像偶像剧')
MAX_PER_FACET = 2


MATERIAL_TREATMENT_CUE = re.compile(
    r'(?:预告|海报|默片|物料|宣发|剧宣|角色短片)[^。！？!?]{0,30}'
    r'(?:创新|巧思|新花样|有创意|有想法|有新意|用心|有趣|脑洞|太戳)'
    r'|(?:创新|创意|巧思|脑洞)[^。！？!?]{0,12}(?:宣发|物料|预告|海报|短片)'
)


def facet_hint_index(excerpts):
    """Point readers to underrepresented concrete wording already in the packet."""
    result = {}
    cues = {**FACET_CUES, '宣传物料创意': MATERIAL_TREATMENT_CUE}
    for facet, pattern in cues.items():
        sources = {}
        for row in excerpts:
            voice = row['voice_ref']
            if voice in sources:
                continue
            match = pattern.search(row['excerpt'])
            if match:
                sources[voice] = {'excerpt_id':row['excerpt_id'], 'literal_hint':match.group()}
        if sources:
            result[facet] = list(sources.values())[:5]
    return result


def sparse_facet_checks(index):
    """Return bounded review opportunities for scarce concrete wording."""
    pairs = {}
    for facet, rows in index.items():
        eligible = [row['excerpt_id'] for row in rows
                    if not re.search(r'够不够|是否|如果|假如|能不能|会不会|期待',row['literal_hint'])]
        if 2<=len(eligible)<=4:
            pairs[facet] = eligible
    return pairs


def sparse_facet_issues(checks, reviews, clusters):
    if not checks:
        return []
    if not isinstance(reviews,list) or any(not isinstance(row,dict) for row in reviews):
        return ['facet_pair_review须按所列方面逐项提交']
    by_facet = {row.get('facet'):row for row in reviews if isinstance(row.get('facet'),str)}
    if set(by_facet)!=set(checks) or len(by_facet)!=len(reviews):
        return [f'facet_pair_review须覆盖这些实际方面一次：{list(checks)}']
    issues = []
    by_id = {row['id']:row for row in clusters
             if isinstance(row,dict) and isinstance(row.get('id'),str)}
    for facet, row in by_facet.items():
        group_id = row.get('group_id')
        group = by_id.get(group_id) if isinstance(group_id,str) else None
        if len(str(row.get('reason') or '').strip())<12:
            issues.append(f'facet_pair_review {facet}需说明实际同点、跨点或证据不足的原因')
        if row.get('decision')=='used':
            seed_ids=group.get('seed_ids') if group else None
            available={sid for sid in seed_ids if isinstance(sid,str)} if isinstance(seed_ids,list) else set()
            if group and group.get('stance')!='positive':
                issues.append(f'facet_pair_review {facet}的used仅限正面簇。{group_id}的实际stance={group.get("stance")}，即使种子匹配也不能引用。保留实际立场。没有其他合格正面簇承接此组候选时，填not_used、group_id=null及实际理由。')
            elif not group or len(available.intersection(checks[facet]))<2:
                issues.append(f'facet_pair_review {facet}声明used并引用{group_id}，但该簇seed_ids={seed_ids}，'
                              f'与本方面候选{checks[facet]}实际交集只有{sorted(available.intersection(checks[facet]))}。'
                              '若确实采用这组双证据，须同时修改对应clusters中的seed_ids及必要依赖字段，不能只在reason里写E编号；'
                              '若本簇使用的是其他证据，则此候选对填not_used、group_id=null并说明实际取舍。'
                              '不因本方面已有观点就把未采用的候选对填used。')
        elif row.get('decision')!='not_used' or group_id is not None:
            issues.append(f'facet_pair_review {facet}不用时填not_used、group_id=null及具体原因')
    return issues


def release_notice_only(text: str) -> bool:
    """Flag the narrow one-sentence music bulletin form, preserving mixed reviews."""
    compact = re.sub(r'\s+', '', text)
    return bool(len(compact) <= 210 and MUSIC_RELEASE.fullmatch(compact)
                and not MUSIC_DETAIL.search(compact) and not FIRSTHAND.search(compact))


def _review_window(body, spans, index, minimum):
    """Keep one continuous local window containing the observed cue."""
    from rapid_film_workbench import display_excerpt
    start, end, _ = spans[index]
    if len(display_excerpt(body[start:end])) >= minimum:
        return (start, end) if end-start <= 360 else None
    choices = []
    for left in range(max(0, index-3), index+1):
        for right in range(index, min(len(spans), index+4)):
            a, b = spans[left][0], spans[right][1]
            if b-a <= 360 and len(display_excerpt(body[a:b])) >= minimum:
                choices.append((a, b))
    return min(choices, key=lambda bounds: (bounds[1]-bounds[0], -bounds[0]), default=None)


def grounded_review_window(body, spans, index, minimum, terms):
    """Prefer a legal contiguous span carrying both the cue and current-work name."""
    original=_review_window(body,spans,index,minimum)
    if original is None:
        return None
    from rapid_film_workbench import display_excerpt
    a,b=original
    if any(term in display_excerpt(body[a:b]) for term in terms if term):
        return original
    candidates=[]
    for left in range(max(0,index-3),index+1):
        for right in range(index,min(len(spans),index+4)):
            start,end=spans[left][0],spans[right][1]
            if (start>a or end<b or end-start>360
                    or a-start>160 or end-b>160):
                continue
            visible=display_excerpt(body[start:end])
            if len(visible)>=minimum and any(term in visible for term in terms if term):
                candidates.append((start,end))
    return min(candidates,key=lambda bounds:(bounds[1]-bounds[0],-bounds[0]),default=original)


def report_evidence_probe_groups(sources: dict, terms: list[str]):
    from rapid_film_workbench import (
        excerpt_min_chars, lexical_features, sentence_spans, source_exclusion,
        source_identity, source_priority, text_field, text_overlap,
    )
    from social_criticism_probe import _target_grounded
    from target_reference import WORK_REFERENCE_CUE

    candidates = {name: [] for name in FACET_CUES}
    for record in sources.values():
        data = record['data']
        field = next((name for name in ('正文内容', '正文', '内容', '视频ASR') if text_field(data, name)), None)
        if not field:
            continue
        body = str(data[field])
        cues = {name: cue for name, cue in FACET_CUES.items() if cue.search(body)}
        if not cues or not _target_grounded(data, body, terms) or source_exclusion(record):
            continue
        spans = list(sentence_spans(body))
        priority = source_priority(record)
        for facet, cue in cues.items():
            options = []
            for index, (a, b, cut) in enumerate(spans):
                sentence = body[a:b]
                match = cue.search(sentence)
                if cut or not match or HYPOTHETICAL.search(sentence[:match.start()]):
                    continue
                if re.search(r'够不够|好不好|是否|会不会|能不能|有没有',match.group()):
                    continue
                local = body[max(0, a-160):min(len(body), b+100)]
                named_here = any(term in local for term in terms if term)
                named_title = any(term in str(data.get('标题') or '') for term in terms if term)
                if not (named_here or named_title):
                    continue
                before=body[max(0,a-160):a+match.start()]
                work_mentions=[(m.start(),m.group(1)) for m in re.finditer(r'《([^》]{1,60})》',before)]
                work_mentions.extend((m.start(),term) for term in terms if term
                                     for m in re.finditer(re.escape(term),before))
                if (facet=='演员表演' and work_mentions
                        and max(work_mentions,key=lambda row:row[0])[1] not in terms):
                    continue
                named_in_sentence = set(re.findall(r'《([^》]{1,60})》', sentence))
                if (facet!='视听技术与音乐' and named_in_sentence and not named_in_sentence.intersection(terms)
                        and not WORK_REFERENCE_CUE.search(sentence)):
                    continue
                window = grounded_review_window(body,spans,index,excerpt_min_chars(record.get('channel')),terms)
                if window is None:
                    continue
                start, end = window
                quote = body[start:end]
                if release_notice_only(quote):
                    continue
                from rapid_film_workbench import display_excerpt
                visible=display_excerpt(quote)
                direct_target=any(term in visible for term in terms if term)
                window_works=set(re.findall(r'《([^》]{1,60})》',visible))
                if (window_works and not window_works.intersection(terms) and not direct_target
                        and not WORK_REFERENCE_CUE.search(sentence)):
                    continue
                if facet == '演员表演' and OFFSCREEN_INTERACTION.search(quote) and not any(term in sentence for term in terms):
                    continue
                direct = bool(FIRSTHAND.search(quote))
                score = 44 + 8*named_here + 12*direct + min(8, 2*len(cue.findall(sentence)))
                if facet == '视听技术与音乐' and LISTENING_REACTION.search(quote):
                    score += 16
                portrayal=bool(facet=='演员表演' and PERFORMANCE_PORTRAYAL.search(sentence))
                options.append((score,start,end,direct,direct_target,portrayal))
            if not options:
                continue
            score,start,end,direct,direct_target,portrayal=max(
                options,key=lambda row:(row[4],row[5],row[0],-row[1]))
            quote = body[start:end]
            candidate = {
                'source_id': record['id'], 'start': start, 'end': end, 'text_field': field, 'score': score,
                'identity': source_identity(record), 'report_evidence_aspect': facet,
                'excerpt_hash': hashlib.sha256(re.sub(r'\s+', '', quote).encode('utf-8')).hexdigest(),
            }
            rank = (not direct_target,not portrayal,priority['platform_risk_rank'], -int(direct),
                    -priority['media_authority_rank'], -score, record['id'])
            candidates[facet].append((rank, candidate, lexical_features(quote)))

    groups = []
    for facet, options in candidates.items():
        selected = []
        for _, candidate, features in sorted(options, key=lambda row: row[0]):
            if any(candidate['identity'] == other['identity'] or text_overlap(features, old) >= .65
                   for other, old in selected):
                continue
            selected.append((candidate, features))
            if len(selected) == (4 if facet=='演员表演' else MAX_PER_FACET):
                break
        if selected:
            groups.append({
                'aspect': f'{facet}具体评价线索（待核）', 'stance': '中性或混合',
                'probe_kind': 'report_evidence', 'candidate_count': len(selected),
                'candidates': [candidate for candidate, _ in selected],
            })
    return groups


def writing_readiness(groups: list[dict]) -> dict:
    """Upper bound on paragraphs from the final display, not a quality pass."""
    from report_style import MIN_BODY_PARAGRAPHS, STANCE_ORDER
    from report_writing import prose_exclusion_reason

    nonnegative, criticism, excluded = [], [], []
    for index, group in enumerate(groups):
        guidance = group.get('writing_guidance') or {}
        reason = prose_exclusion_reason(group)
        if not reason and guidance.get('eligible') is False:
            reason = guidance.get('reason') or '末端证据核对判定不可入稿'
        if reason:
            excluded.append({'group_index': index, 'reason': reason})
        elif STANCE_ORDER.get(group.get('stance')) == 2:
            criticism.append(index)
        elif guidance.get('standalone_eligible') is False:
            excluded.append({'group_index':index,'reason':'该组可补充引用，但未达到至少两份独立声音的成段条件'})
        else:
            nonnegative.append(index)
    upper_bound = len(nonnegative) + int(bool(criticism))
    return {
        'policy': 'FINAL_DISPLAY_PARAGRAPH_BOUND_V1',
        'minimum_required': MIN_BODY_PARAGRAPHS,
        'paragraph_upper_bound': upper_bound,
        'evidence_shortfall': max(0, MIN_BODY_PARAGRAPHS-upper_bound),
        'nonnegative_groups': nonnegative,
        'criticism_groups_one_paragraph': criticism,
        'excluded_groups': excluded,
    }
