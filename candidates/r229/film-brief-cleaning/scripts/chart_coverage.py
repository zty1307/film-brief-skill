"""Count reviewed evidence for the final body points and a small, separate Other row."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from report_style import CRITICISM_LEAD_VERBS, REPORT_LEAD_VERBS


PROFILE = 'WORKBENCH_CHART_COVERAGE_V1'
THEME_POLICY = 'BODY_VIEWPOINTS_V2'
LABEL_GUIDANCE = '从同段claim压缩为4—30字，保留原起句评价动词、评价对象和核心判断。省略编号或“此外”“部分声音”等段首结构，不另起标题或增加判断。'
NEGATIVE_STANCES = ('negative', '负向词倾向')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def display(sample):
    return sample.get('display_excerpt') or sample['excerpt']


def primary_snippets(group, sample):
    return [p['quote_snippet'] for p in group.get('writing_guidance', {}).get('preferred', [])
            if p.get('candidate_id') == sample['candidate_id'] and isinstance(p.get('quote_snippet'), str)
            and p['quote_snippet'] in display(sample)]


def validate_body_label(point, point_index):
    label = point.get('chart_label')
    if not isinstance(label, str) or not 4 <= len(label.strip()) <= 30:
        raise ValueError(f'段{point_index + 1} chart_label须为4—30字')
    claim = point['claim'].strip()
    verb = next((verb for verb in REPORT_LEAD_VERBS + CRITICISM_LEAD_VERBS if claim.startswith(verb)), None)
    if verb is None:
        raise ValueError(f'段{point_index + 1} claim缺少规定的起句评价动词')
    label = label.strip()
    if not label.startswith(verb):
        raise ValueError(f'段{point_index + 1} chart_label须以正文起句动词“{verb}”开头，并保留原核心判断')
    return label


def contract():
    return {
        'profile': PROFILE,
        'theme_policy': THEME_POLICY,
        'chart_label': LABEL_GUIDANCE,
        'rule': '主要图类由脚本从最终正文生成，一段对应一条，含唯一批评末段；使用保留正文原起句评价动词的chart_label。只有“其他”可独立于正文编号。图条按最终正文顺序从上到下逐段对应，第一条对应“一是”，第二条对应“二是”。各条显示实际来源占比；“其他”固定最后。不要另填categories。',
        'scope': '从本期最终工作台或人工导出选材统计，每个来源只计一次。正文实际使用的观点组默认归其所在段；其他非本期正文主题留审计，不进分母。按实际内容对少数边界来源填写例外。缺少代表某正文点的样本时回查选材。',
        'source_overrides': [{'candidate_id': '', 'point_index': 0,
                              'quote_snippet': '连续至少8字，具体支持指定正文段的核心判断',
                              'reason': '至少12字，说明来源实际支持的正文点为什么不同于默认组'}],
        'out_of_scope_sources': [{'candidate_id': '', 'quote_snippet': '连续至少8字',
                                  'reason': '至少16字，说明正文组内这条来源的主判断不落在本期任何正文主题'}],
        'excluded_sources': [{'candidate_id': '', 'reason_kind': 'background_fact',
                              'quote_snippet': '连续至少8字',
                              'reason': '至少16字，确属无独立评价的纯背景；带评价按实际判断处理'}],
        'other_sources': [{'candidate_id': '', 'quote_snippet': '连续至少8字的零散负面评价',
                           'reason': '至少16字，说明负面意见实质及与正文主要批评的区别'}],
        'other_rule': '其他必填，取少量有实际已审负面证据、未作为主判断单列的声音，占比不高于最小主类。与正文已有批评同机制的继续归正文对应条目。正面、中性和纯背景不能补其他；缺真实证据说明缺口，人工模式可补选已审负面样本。',
    }


def empty_review(dataset_hash):
    return {'profile': PROFILE, 'theme_policy': THEME_POLICY, 'dataset_sha256': dataset_hash,
            'source_overrides': [], 'out_of_scope_sources': [], 'excluded_sources': [], 'other_sources': []}


def boundary_candidates(dataset):
    result = []
    for index, group in enumerate(dataset['groups']):
        reasons = []
        if len({s['source_id'] for s in group['samples']}) < 2:
            reasons.append('single_source_group')
        if group.get('stance') in ('中性或混合', 'objective', 'neutral', 'mixed'):
            reasons.append('mixed_or_overview')
        if group.get('writing_guidance', {}).get('eligible') is False:
            reasons.append('writing_not_eligible_is_not_background')
        result.extend({'candidate_id': s['candidate_id'], 'group_index': index,
                       'review_reasons': reasons} for s in group['samples'] if reasons)
    return result


def coverage_data(dataset, plan, dataset_hash):
    review = plan.get('chart_review')
    if not isinstance(review, dict) or review.get('profile') != PROFILE or review.get('theme_policy') != THEME_POLICY:
        raise ValueError(f'请按{THEME_POLICY}提交本期图表例外归属，主要图类由最终正文生成')
    if review.get('dataset_sha256') != dataset_hash:
        raise ValueError('chart_review的dataset_sha256与当前工作台不一致')
    if not review.get('other_sources'):
        raise ValueError('other_sources必须有实际已审负面样本；无合格零散负面时据实说明缺口')
    groups = dataset['groups']
    points = plan['points']
    owners = {}
    categories = []
    labels = set()
    chosen = {}
    for point_index, point in enumerate(points):
        label = validate_body_label(point, point_index)
        if label == '其他' or label in labels:
            raise ValueError('各正文点的chart_label须不同，“其他”为单独末行')
        labels.add(label)
        for group_index in point['group_indices']:
            if group_index in owners:
                raise ValueError(f'组{group_index}重复分配给正文图类')
            owners[group_index] = point_index
        evidence = []
        seen = set()
        for support in point.get('claim_support', []):
            for entry in support.get('evidence', []):
                candidate_id = entry['candidate_id']
                if candidate_id not in seen:
                    seen.add(candidate_id)
                    evidence.append({'candidate_id': candidate_id, 'quote_snippet': entry['quote_snippet']})
        categories.append({'id': f'body_{point_index}', 'point_index': point_index, 'label': label,
                           'group_indices': point['group_indices'], 'core_judgment': point['claim'],
                           'evidence': evidence})
        chosen.update({sample['candidate_id']: point_index for sample in point.get('samples', [])})

    index = {}
    for group_index, group in enumerate(groups):
        for sample in group['samples']:
            cid = sample['candidate_id']
            if cid in index:
                raise ValueError('工作台candidate_id重复')
            index[cid] = (group_index, sample)

    exceptions = {}
    fields = {'source_overrides': 'category', 'out_of_scope_sources': 'outside',
              'excluded_sources': 'excluded', 'other_sources': 'other'}
    for field, state in fields.items():
        entries = review.get(field, [])
        if not isinstance(entries, list):
            raise ValueError(f'{field}须为数组')
        for row in entries:
            cid = row.get('candidate_id')
            if cid not in index or cid in exceptions:
                raise ValueError(f'{field}来源无效或重复：{cid}')
            group_index, sample = index[cid]
            reason = str(row.get('reason', '')).strip()
            snippet = row.get('quote_snippet')
            if not isinstance(snippet, str) or len(snippet) < 8 or snippet not in display(sample):
                raise ValueError(f'{field}须绑定当前展示原句至少8字：{cid}')
            if len(reason) < (12 if state == 'category' else 16):
                raise ValueError(f'{field}须写出实际归属理由：{cid}')
            point_index = row.get('point_index') if state == 'category' else None
            if state == 'category' and (type(point_index) is not int or not 0 <= point_index < len(points)):
                raise ValueError(f'{field}须引用已有正文point_index：{cid}')
            if cid in chosen and (state != 'category' or point_index != chosen[cid]):
                raise ValueError(f'实际正文引用须保留在其正文图类：{cid}；请从未引用的候选核对例外')
            if state == 'excluded' and row.get('reason_kind') != 'background_fact':
                raise ValueError('只允许排除没有独立评价的纯背景')
            if state == 'other':
                group = groups[group_index]
                if group.get('stance') not in NEGATIVE_STANCES:
                    raise ValueError(f'other_sources只接收已审负面：{cid}')
                anchors = primary_snippets(group, sample)
                if anchors and not any(snippet in anchor for anchor in anchors):
                    raise ValueError(f'other_sources只能用该样本已审核心片段，不能用附带批评改分：{cid}')
            exceptions[cid] = {'state': state, 'point_index': point_index,
                               'reason': reason, 'quote_snippet': snippet}

    allocations = []
    by_source = {}
    for cid, (group_index, sample) in index.items():
        point_index = owners.get(group_index)
        route = exceptions.get(cid)
        if route is None:
            route = {'state': 'outside' if point_index is None else 'category',
                     'point_index': point_index, 'quote_snippet': None,
                     'reason': '未对应本期任何正文点' if point_index is None else '承接该段所用已审观点组'}
        item = {'candidate_id': cid, 'source_id': sample['source_id'], 'group_index': group_index, **route}
        pi = item['point_index']
        item['category_id'] = f'body_{pi}' if pi is not None else None
        prior = by_source.setdefault(item['source_id'], item)
        if (prior['state'], prior['point_index']) != (item['state'], item['point_index']):
            raise ValueError(f'同一来源不可分到多个图类或部分计入：{item["source_id"]}')
        allocations.append(item)

    counts = [0] * (len(points) + 1)
    excluded = []
    outside = []
    for item in by_source.values():
        if item['state'] == 'category':
            counts[item['point_index']] += 1
        elif item['state'] == 'other':
            counts[-1] += 1
        elif item['state'] == 'excluded':
            excluded.append(item)
        else:
            outside.append(item)
    if not all(counts[:-1]):
        raise ValueError('图表每个正文观点至少须有一条本期已审有效来源')
    if not counts[-1] or counts[-1] > min(counts[:-1]):
        raise ValueError(f'other_sources去重后{counts[-1]}条，须有实际已审负面且不高于最小正文图类的{min(counts[:-1])}条')
    total = sum(counts)
    percent = [count * 100 // total for count in counts]
    remainders = sorted(range(len(counts)), key=lambda i: (-(counts[i] * 100 % total), i))
    for i in remainders[:100 - sum(percent)]:
        percent[i] += 1
    rows = [{'point_index': i, 'category_id': cat['id'], 'label': cat['label'],
             'count': counts[i], 'percent': percent[i], 'group_indices': cat['group_indices']}
            for i, cat in enumerate(categories)]
    rows.append({'point_index': None, 'category_id': None, 'label': '其他',
                 'count': counts[-1], 'percent': percent[-1], 'group_indices': []})
    work_label = plan.get('work_label', '电视剧')
    if work_label not in ('电视剧', '网络剧', '电影', '综艺', ''):
        raise ValueError('work_label无效')
    manual = bool(dataset.get('human_selection'))
    lead = '在人工选材范围内' if manual else ''
    caption = (f'注：{lead}统计正文各观点及“其他”的已审样本（去重后{total}条，'
               f'另有{len(outside)}条未对应正文主题、{len(excluded)}条纯背景未计），非全网舆论占比。')
    return {'title': f'{work_label}《{dataset["target"]}》' + ('人工选材观点分布' if manual else '相关话题舆论观点分布'),
            'rows': rows, 'profile': PROFILE, 'dataset_sha256': dataset_hash, 'population_claim': False,
            'denominator': total, 'reviewed_source_count': len(by_source),
            'excluded_background_count': len(excluded), 'outside_body_count': len(outside),
            'other_count': counts[-1], 'percentage_rounding': 'largest_remainder_sum_100',
            'basis': caption.removeprefix('注：'), 'caption': caption, 'allocation': allocations,
            'excluded_sources': excluded, 'review_warnings': [],
            'classification_review': {**review, 'categories': categories}, 'theme_policy': THEME_POLICY}


def prepare(dataset_path, plan_path, outdir):
    dataset = read(dataset_path)
    plan = read(plan_path)
    dataset_hash = digest(dataset_path)
    if plan.get('dataset_sha256') != dataset_hash:
        raise ValueError('既有成稿计划与工作台指纹不同')
    inventory = {'target': dataset['target'], 'dataset_sha256': dataset_hash, 'plan_sha256': digest(plan_path),
                 'chart_contract': contract(),
                 'existing_body_topics': [{'point_index': i, 'claim': p['claim'],
                                           'chart_label': p['chart_label'], 'group_indices': p['group_indices']}
                                          for i, p in enumerate(plan['points'])],
                 'groups': [{'group_index': i, 'stance': g['stance'], 'aspect': g['aspect'],
                             'samples': [{'candidate_id': s['candidate_id'], 'source_id': s['source_id'],
                                          'display_excerpt': display(s), 'primary_support_snippets': primary_snippets(g, s)}
                                         for s in g['samples']]}
                            for i, g in enumerate(dataset['groups'])]}
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir/'chart_inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    answer = {**empty_review(dataset_hash), 'plan_sha256': digest(plan_path)}
    (outdir/'chart_review.json').write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'status': 'CHART_REVIEW_REQUIRED', 'read_now': str(outdir/'chart_inventory.json'),
            'answer_file': str(outdir/'chart_review.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'check'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--dataset', required=True)
        cmd.add_argument('--plan', required=True)
        cmd.add_argument('--outdir' if name == 'prepare' else '--review', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.dataset, args.plan, args.outdir)
        else:
            plan = read(args.plan)
            review = read(args.review)
            if review.get('plan_sha256') and review['plan_sha256'] != digest(args.plan):
                raise ValueError('既有正文计划已经改变')
            plan.update(chart_review=review, chart_theme_policy=THEME_POLICY)
            chart = coverage_data(read(args.dataset), plan, digest(args.dataset))
            result = {'status': 'CHART_COVERAGE_VALID', **{key: chart[key] for key in
                       ('denominator', 'excluded_background_count', 'outside_body_count', 'other_count', 'rows')}}
        print(json.dumps(result, ensure_ascii=False))
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
