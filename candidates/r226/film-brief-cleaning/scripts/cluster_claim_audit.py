"""Check the proposed writing cores before freezing the clustering workbench."""
from copy import deepcopy
import time

from rapid_film_workbench import save
from workbench_completion import SUPPORTED_POSITIVE_POLICY


def evidence_signature(state, digest):
    return digest([state.get('editorial_titles'), state.get('editorial_core_mechanisms'),
                   state.get('editorial_writing_guidance'), state['judgments']])


def prepare(workspace, state, digest):
    if state.get('quantity_contract') != SUPPORTED_POSITIVE_POLICY:
        return None
    signature = evidence_signature(state, digest)
    if state.get('editorial_claim_audit_signature') == signature:
        return None
    from rapid_film_workbench import display_excerpt
    guides = state.get('editorial_writing_guidance', {})
    groups = []
    for index, group in enumerate(state['pool']):
        guide = guides.get(str(index), {})
        if group['stance'] != '正向词倾向' or not guide.get('standalone_eligible'):
            continue
        groups.append({'group_index': index, 'title': state['editorial_titles'][str(index)],
                       'core': state['editorial_core_mechanisms'][str(index)],
                       'evidence': guide['preferred'],
                       'available_samples': [
                           {'candidate_id': cid, 'display_excerpt': display_excerpt(item['excerpt']),
                            'identity': item.get('identity'), 'source_id': item.get('source_id')}
                           for cid, item in state['judgments'].items()
                           if item.get('decision') == 'keep' and item.get('group_index') == index]})
    if not groups:
        state['editorial_claim_audit_signature'] = signature
        return None
    remaining = state['started_epoch'] + state['deadline_seconds'] - time.time()
    if remaining <= 60:
        state['editorial_status'] = 'SKIPPED_CLAIM_AUDIT_TIME_LIMIT'
        return None
    binding = digest(['CLUSTER_CLAIM_AUDIT_V1', signature, groups])
    rules = (
        '这是工作台发布前的独立证据核对，只读本包。此前的入选和标题不代表成立。'
        '逐组核对拟用的两条原句及同组available_samples已审展示句。先判断实际评价，再判断共同核心。'
        '若原推荐截取不当或选错样本，可从本组available_samples重新选两条，用evidence返回ID和12—120字连续quote_snippet。'
        '只因原推荐句不成立，不能推断整组没有证据；须查看同组已审展示句。没有两份实际支持才退回。'
        'voice_reviews须对应本次最终evidence的两条ID；换选后重新判断两份声音独立、标题支持及与其他组的重复。'
        '同一作者或明显同稿不能算两份；不借未展示上下文，不改原句，不为凑数放行。'
        '两条分别夸感情线与配角群像，不能靠上位词“人物塑造”算成双证据；'
        '两条分别夸不同演员的演绎，可以共同支持表演这一核心，无须同一演员或场景。'
        '也不要求两条使用相同措辞。差别在于实际评价对象与判断能否共同成立。'
        '并列句中的明确评价仍是评价，例如直接说打戏利落流畅，不能仅因同段还夸服装或音乐就判无效。'
        '审的是拟引用评价能否支持本点，不要求整篇文章或整个展示窗口只谈一个方面；'
        '可裁去其他方面，但不能补字、推断未表达的效果，单纯提到某方面仍不算评价。'
        '再把两条原句同时与其他组比较，不能只拿本组一条独特原句解释区别，'
        '而忽略另一条实际重复了相邻组。某组只能找到一条独立支持，就标不成立。'
        '重叠时保留证据更充分的一组，较弱组can_stand_alone=false并指出overlap_with。'
        '不要为达到六组而放行，也不要因只是同方面就合并不同的实际评价。'
        '两份独立声音成立，不自动等于足以独立成段。若两句只是同义的简短褒贬（如都说打戏流畅或节奏不错），没有具体场面、处理方式、变化或其他可展开的信息，可留在工作台作补充，但standalone_eligible=false。独立方面本身不是短段例外的重要性理由；不能为了达到六段把这种点计入成稿数量。两份具体、信息充分的独立评价仍可支持简洁短段，不机械用字数否定。'
        '先核对最终title与内部core是否同步。若title已收窄，而core仍残留被删掉的旧细节，可在core_remove_phrases列出待删除的core逐字片段，并在core_alignment_reason说明。只删除旧元数据里未被最终标题保留的细节，不改title、不增加或改写核心，不用此字段删除标题仍实际要求的判断。随后按最终title及同步后的core核对两份证据与交叉；不能仅因已删标题的旧细节无证据而否定当前有据观点。相同的具体评价可由独立作者相互印证，不要求两句分别评价不同效果；是否能展开须读完整available_samples，不只看短preferred片段。'
        '本包只作证据选择、上述元数据收窄和通过或退回判断，不改标题、不改原句、不写正文、不读原表。'
        '每组voice_reviews覆盖两条ID各一次，supports_core表示实际支持当前核心，'
        'reason具体说明该句评价对象。组级reason说明两句共同判断或实际缺口。'
    )
    from rapid_film_workbench import PUBLISHED_PREMISE_RULE
    rules += PUBLISHED_PREMISE_RULE
    from viewpoint_granularity import GRANULARITY_RULE
    rules += "\n" + GRANULARITY_RULE
    payload = {'profile': 'CLUSTER_CLAIM_AUDIT_V1', 'fingerprint': binding,
               'target': state['target'], 'rules': rules, 'groups': groups,
               'answer_schema': {'fingerprint': binding, 'groups': [
                   {'group_index': '本包编号', 'can_stand_alone': True,
                    'evidence': [{'candidate_id': '本组已审ID', 'quote_snippet': '12—120字连续原句，两项'}],
                    'voice_reviews': [{'candidate_id': '本次evidence证据ID',
                                       'supports_core': True, 'reason': '至少8字实际对象与判断'}],
                    'core_remove_phrases': [], 'core_alignment_reason': '仅删除旧core残留细节时说明与最终title的对应关系，否则留空',
                    'overlap_with': [], 'reason': '至少12字两句共同判断或缺口'}]}}
    stamp = time.time_ns()
    for name in ('display_editor_input.json', 'display_editor_answer.json',
                 'display_editor_feedback.json'):
        path = workspace / name
        if path.exists():
            path.rename(path.with_name(f'{path.stem}.before-claim-audit-{stamp}.json'))
    save(workspace / 'display_editor_input.json', payload)
    save(workspace / 'display_editor_answer.json', {'fingerprint': binding, 'groups': []})
    state.update(status='DISPLAY_EDITOR_REQUIRED', editorial_status='PENDING',
                 editorial_phase='CLAIM_AUDIT', editorial_binding=binding,
                 editorial_claim_audit_groups=groups, editorial_started_epoch=time.time())
    return {'read_now': str(workspace / 'display_editor_input.json'),
            'answer_file': str(workspace / 'display_editor_answer.json')}


def apply(workspace, state, answer, digest):
    if (state.get('editorial_phase') != 'CLAIM_AUDIT'
            or state.get('editorial_status') != 'PENDING'
            or not isinstance(answer, dict)
            or answer.get('fingerprint') != state.get('editorial_binding')):
        raise ValueError('本次聚类核心证据核对的阶段或指纹不匹配')
    expected = {row['group_index']: row for row in state['editorial_claim_audit_groups']}
    rows = answer.get('groups')
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or any(not isinstance(row, dict) for row in rows)
            or any(type(row.get('group_index')) is not int for row in rows)
            or {row['group_index'] for row in rows} != set(expected)):
        raise ValueError('核心证据核对须覆盖本包全部组一次')
    guides = deepcopy(state['editorial_writing_guidance'])
    cores = deepcopy(state['editorial_core_mechanisms'])
    for row in rows:
        index = row['group_index']
        removals = row.get('core_remove_phrases', [])
        if (not isinstance(removals, list) or len(removals) > 3
                or any(not isinstance(phrase, str) or len(phrase.strip()) < 2 for phrase in removals)
                or len(set(removals)) != len(removals)):
            raise ValueError(f'组{index}core_remove_phrases须为最多3个不同的原core连续片段')
        core = expected[index]['core']
        if removals and len(str(row.get('core_alignment_reason', '')).strip()) < 12:
            raise ValueError(f'组{index}删除旧core残留须说明最终标题与元数据的对应关系')
        for phrase in removals:
            if core.count(phrase) != 1:
                raise ValueError(f'组{index}待删除片段须在当前core中逐字且仅出现一次，不得新增或改写核心')
            core = core.replace(phrase, '', 1)
        if removals:
            core = core.strip(' ，、；。,:;')
        if removals and not 8 <= len(core) <= 80:
            raise ValueError(f'组{index}同步后core须保留8—80字实际核心，不得删空')
        cores[str(index)] = core
        voices = row.get('voice_reviews')
        evidence = row.get('evidence', expected[index]['evidence'])
        available = {item['candidate_id']: {**item, 'excerpt': item['display_excerpt']}
                     for item in expected[index].get('available_samples', [])}
        # Legacy packets remain valid; replacement is permitted only with an explicit reviewed pool.
        if not available:
            if evidence != expected[index]['evidence']:
                raise ValueError('旧核对包没有已审候选池，不能更换证据')
        else:
            from final_evidence_gate import validate_writing_guidance
            if not isinstance(evidence, list) or len(evidence) != 2:
                raise ValueError(f'组{index}须提供两条最终核对证据')
            replacement = {**guides[str(index)], 'preferred': evidence,
                           'independent_voice_ids': [proof.get('candidate_id') for proof in evidence
                                                     if isinstance(proof, dict)],
                           'standalone_eligible': True}
            validate_writing_guidance(replacement, available, 120, require_dual_support=True)
        ids = {proof['candidate_id'] for proof in evidence}
        if (not isinstance(voices, list) or len(voices) != len(ids)
                or any(not isinstance(voice, dict) for voice in voices)
                or any(not isinstance(voice.get('candidate_id'), str) for voice in voices)
                or {voice['candidate_id'] for voice in voices} != ids
                or any(type(voice.get('supports_core')) is not bool
                       or len(str(voice.get('reason', '')).strip()) < 8 for voice in voices)):
            raise ValueError(f'组{index}须分别核对两条实际证据ID及各自支持判断')
        overlaps = row.get('overlap_with')
        if (type(row.get('can_stand_alone')) is not bool
                or not isinstance(overlaps, list)
                or any(type(other) is not int or other not in expected or other == index
                       for other in overlaps)
                or len(set(overlaps)) != len(overlaps)
                or len(str(row.get('reason', '')).strip()) < 12):
            raise ValueError(f'组{index}须明确可否独立成段、实际重叠组和具体原因')
        if row['can_stand_alone'] and (overlaps or not all(v['supports_core'] for v in voices)):
            raise ValueError(f'组{index}有不支持核心的证据或重叠，不能同时判独立通过')
        if not row['can_stand_alone']:
            guides[str(index)].update(standalone_eligible=False, reason=row['reason'])
        elif available:
            guides[str(index)].update(preferred=evidence,
                independent_voice_ids=[proof['candidate_id'] for proof in evidence], reason=row['reason'])
    state.update(editorial_status='COMPLETE', editorial_phase='TITLE_EDITOR',
                 editorial_writing_guidance=guides, editorial_core_mechanisms=cores,
                 editorial_claim_audit_reviews=rows)
    state['editorial_claim_audit_signature'] = evidence_signature(state, digest)
    state['editorial_claim_audit_elapsed_seconds'] = round(time.time() - state['editorial_started_epoch'], 3)
    save(workspace / 'cluster_claim_audit.json', {
        'profile': 'CLUSTER_CLAIM_AUDIT_V1', 'fingerprint': answer['fingerprint'],
        'input_groups': state['editorial_claim_audit_groups'], 'reviews': rows,
        'elapsed_seconds': state['editorial_claim_audit_elapsed_seconds']})
