"""Review paragraph evidence and boundaries before composing prose and chart answers."""
import json
from pathlib import Path

from chart_coverage import LABEL_GUIDANCE
from report_style import SECTION_HEADING_GUIDANCE

from model_packet import write_packet

PROFILE = 'EVIDENCE_OUTLINE_V1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def prepare(outdir):
    outdir = Path(outdir)
    inventory = read(outdir / 'candidate_inventory.json')
    packet = {'profile': PROFILE, 'dataset_sha256': inventory['dataset_sha256'],
              'target': inventory['target'], 'groups': [],
              'writing_overlap_pairs': inventory.get('writing_overlap_pairs',[]),
              'instructions': '证据按完整观点核对，不按一个细节卡死：同一集中评价主题可以由互补的具体子评价支撑，如演唱听感与歌曲人物适配共同支撑原声音乐感染力，服饰与置景共同支撑同一视觉风格。每份声音都须有对本主题的实际评价，每个标题分句至少一条具体证据，全段至少两份独立声音；不要求每条引文重复同一细节或同时覆盖所有分句。不能仅因都提到演员、音乐或预告就合组；不能把无关问题装进泛泛的整体很好，也不能与别段重复核心评价。独立来源同向互证与不同细节互补均可，纯资讯、名单、期待及同稿转载均不能补足证据。先通读各组实际展示句，通常择优形成6个有双独立声音支持且互不交叉的正面段；相关互补的工作台点可合写，不相干的不能硬并，其余可留作人工备选，不要求全用；有批评再加一个末段，通常共7段，客观不能补正面数。先写简短提纲及支持原句，不写完整正文或图表。按正文顺序选择适合评价对象的允许动词，先在提纲避免相邻段同词，批评段写清预告或物料等范围。每段至少2个独立有效声音，通常3—4个；不足2个不独立成段，不用例外理由凑第六段。在opinion_snippet摘出实际评价，人物履历/角色介绍/情节摘要/歌曲上线不能代替具体评价。逐个说明证据怎样支持本段；共享方面或相近判断须明确真实区别。writing_overlap_pairs是聚类末端核对过的风险对。若各自成段，实际选样必须分别支持不同处理和判断，给pairwise_review补上evidence。换宣传物料、媒介、范围或举例不能让同一判断变成两个。把跨点的评价从引文中裁去。不能用第六段要求倒逼拆分。不足填insufficient并解释缺口。脚本只核字段与逐字绑定，评价质量和边界由你实际判断。',
              'allowed_lead_verbs': {'positive_or_objective': __import__('report_style').REPORT_LEAD_VERBS, 'negative': __import__('report_style').CRITICISM_LEAD_VERBS},
              'answer_schema': {'decision': 'ready|insufficient', 'shortfall_reason': '',
                                'section_heading': '提纲随选点先填简稿；最终正文完成后按正式模板更新。' + SECTION_HEADING_GUIDANCE, 'points': [{
                                    'claim': '评价动词起句，概括对象与判断，最多48字',
                                    'chart_label': LABEL_GUIDANCE, 'group_indices': [0],
                                    'core_question': '至少12字，写本段独立讨论的问题',
                                    'boundary': '至少12字，写与其他点位的边界',
                                    'evidence': [{'candidate_id': '', 'opinion_snippet': '展示句内连续8—140字实际评价',
                                                  'reason': '至少12字，说明真实评价及对本段的具体支持'}],
                                    'single_source_reason': '', 'continuity_reason': ''}],
                                'pairwise_review': [{'left': 0, 'right': 1, 'distinction': '至少12字真实区别。left、right是从0开始的points数组下标，left小于right，同一对只写一次。',
                                                     'evidence': [{'candidate_id':'两段各一条选样ID','quote_snippet':'该段所选原句内至少8字具体不同评价'}]}],
                                'omitted_groups': [{'group_index': 0, 'reason': '至少12字实际取舍理由'}]}}
    from rapid_film_workbench import PUBLISHED_PREMISE_RULE
    packet['instructions'] += PUBLISHED_PREMISE_RULE
    from viewpoint_granularity import GRANULARITY_RULE
    packet['instructions'] += '\n' + GRANULARITY_RULE
    if inventory.get('human_selection'):
        from human_selection import RULE
        packet['human_selection'] = inventory['human_selection']
        packet['instructions'] = RULE + packet['instructions'][packet['instructions'].index('先写简短提纲'):].replace('不能用第六段要求倒逼拆分。', '').replace('不足填insufficient并解释缺口。', '人工选材不足填insufficient并解释具体缺口。')
    if inventory.get('user_requirements'):
        packet['user_requirements']=inventory['user_requirements']
        packet['requirements_scope']=inventory['requirements_scope']
    sample_fields = ('candidate_id', 'channel', 'suggested_attribution', 'copy_family',
                     'display_excerpt', 'preferred_support_snippet', 'usable_for_writing')
    for group in inventory['groups']:
        row = {key: group[key] for key in ('group_index', 'aspect', 'category', 'stance',
                                         'core_mechanism', 'writing_guidance', 'prose_exclusion_reason',
                                         'history_review') if key in group}
        row['samples'] = [{key: sample[key] for key in sample_fields if key in sample}
                          for sample in group['samples']]
        packet['groups'].append(row)
    write_packet(outdir / 'outline_input.json', packet)
    write_packet(outdir / 'outline_answer.json', {'profile': PROFILE,
                 'dataset_sha256': inventory['dataset_sha256'], 'decision': '',
                 'shortfall_reason': '', 'section_heading': '', 'points': [],
                 'pairwise_review': [], 'omitted_groups': []})


def collect(dataset, outdir):
    from opinion_visuals import validate_outline
    from report_style import compile_plan, REPORT_LEAD_VERBS, CRITICISM_LEAD_VERBS
    from report_writing import prose_exclusion_reason
    from source_policy import sample_keys
    outdir = Path(outdir)
    answer = read(outdir / 'outline_answer.json')
    plan = read(outdir / 'editorial_plan.json')
    if answer.get('profile') != PROFILE or answer.get('dataset_sha256') != plan['dataset_sha256']:
        raise ValueError('提纲必须绑定当前工作台指纹')
    if answer.get('decision') == 'insufficient':
        if len(str(answer.get('shortfall_reason') or '').strip()) < 24:
            raise ValueError('材料不足须具体说明哪些独立判断缺少有效证据，至少24字')
        return {'status': 'INSUFFICIENT_EVIDENCE', 'reason': answer['shortfall_reason']}
    if answer.get('decision') != 'ready' or not isinstance(answer.get('points'), list):
        raise ValueError('完成提纲后明确填ready或insufficient')
    if any(not isinstance(p,dict) for p in answer['points']):
        raise ValueError('points每项必须为完整对象，包含claim、group_indices、evidence，不能只交标题字符串')
    from human_selection import POLICY, validate_points
    validate_points(dataset, answer['points'])
    if dataset.get('quantity_contract') != POLICY and not 6 <= len(answer['points']) <= 7:
        raise ValueError('正文须6—7个独立点；证据不足明确填insufficient，不硬凑')
    negative_points = 0
    lookup = {sample['candidate_id']: (index, sample) for index, group in enumerate(dataset['groups'])
              for sample in group['samples']}
    points, boundaries, errors = [], [], []
    used = set()
    for index, point in enumerate(answer['points']):
        try:
            indices = point.get('group_indices', [])
            if not indices or any(type(i) is not int or not 0 <= i < len(dataset['groups']) for i in indices):
                raise ValueError('绑定有效工作台组')
            for gi in indices:
                group = dataset['groups'][gi]
                if prose_exclusion_reason(group) or group.get('writing_guidance', {}).get('eligible') is False:
                    raise ValueError('背景或已判定不可成文组不能用于凑段')
            negative = all(dataset['groups'][gi]['stance'] == '负向词倾向' for gi in indices)
            stances = {dataset['groups'][gi]['stance'] for gi in indices}
            if '负向词倾向' in stances and len(stances) > 1:
                raise ValueError('批评不能与肯定或客观组混入同一段')
            negative_points += int(negative)
            claim = point.get('claim', '')
            if not isinstance(claim, str) or len(claim) > 48 or not claim.startswith(CRITICISM_LEAD_VERBS if negative else REPORT_LEAD_VERBS):
                raise ValueError(f'claim当前={claim!r}；须48字内且以适用动词起句，可选={CRITICISM_LEAD_VERBS if negative else REPORT_LEAD_VERBS}')
            evidence = point.get('evidence', [])
            if not isinstance(evidence, list) or not evidence:
                raise ValueError('须有实际支持证据')
            if len(evidence) < 2:
                raise ValueError('每段至少2条独立有效声音；单源不能独立成段，无法支撑6段则明确填insufficient，不加弱第二条')
            samples = []
            for entry in evidence:
                cid, quote = entry.get('candidate_id'), entry.get('opinion_snippet')
                if cid not in lookup or lookup[cid][0] not in indices:
                    raise ValueError('证据须来自本段绑定的已审工作台组')
                sample = lookup[cid][1]
                display = sample.get('display_excerpt') or sample['excerpt']
                if not isinstance(quote, str) or not 8 <= len(quote) <= 140 or quote not in display:
                    raise ValueError('opinion_snippet须为展示句内连续8—140字实际评价')
                if len(str(entry.get('reason') or '').strip()) < 12:
                    raise ValueError('说明该原句的评价内容及本段支持关系，不能只复述标题')
                keys = sample_keys(sample, sample['source'])
                if keys & used:
                    raise ValueError('同源或同稿样本不能重复分配或冒充独立声音')
                used.update(keys)
                samples.append({'candidate_id': cid, 'quote': quote, 'selection_reason': entry['reason']})
            points.append({'claim': claim, 'chart_label': point.get('chart_label'), 'group_indices': indices,
                           'samples': samples, 'claim_support': [],
                           'single_source_reason': point.get('single_source_reason', ''),
                           'continuity_reason': point.get('continuity_reason', '')})
            boundaries.append({'point_index': index, 'core_question': point.get('core_question', ''),
                               'boundary': point.get('boundary', '')})
        except ValueError as exc:
            errors.append(f'提纲段{index + 1}：{exc}')
    if negative_points > 1:
        errors.append('批评合计只能一段，不能拆成多个点凑数')
    if errors:
        raise ValueError('\n'.join(errors))
    plan.update(points=points, section_heading=answer.get('section_heading', ''),
                outline_review={'point_boundaries': boundaries,
                                'pairwise_review': answer.get('pairwise_review', []),
                                'omitted_groups': answer.get('omitted_groups', [])})
    validate_outline(dataset, compile_plan(dataset, plan))
    write_packet(outdir / 'editorial_plan.json', plan)
    write_packet(outdir / 'outline_review.json', answer)
    return {'status': 'OUTLINE_REVIEWED', 'point_count': len(points)}
