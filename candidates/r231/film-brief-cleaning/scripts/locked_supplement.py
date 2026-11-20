"""Append independently reviewed additions to the frozen workbench."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
import time

from workbench_completion import supported_positive_count


POSITIVE = ('positive', '正向词倾向')


def lock_reviewed_board(state, dataset):
    displayed = {g['viewpoint_id'] for g in dataset['groups']}
    state['locked_group_indices'] = [
        index for index, group in enumerate(state['pool']) if group.get('viewpoint_id') in displayed
    ]
    state['locked_candidate_ids'] = [s['candidate_id'] for g in dataset['groups'] for s in g['samples']]
    indices={g['viewpoint_id']:i for i,g in enumerate(state['pool'])}
    state['supplement_baseline_counts']={str(indices[g['viewpoint_id']]):len(g['samples']) for g in dataset['groups']}
    state['supplement_baseline_supported']=[indices[g['viewpoint_id']] for g in dataset['groups'] if supported_positive_count([g])]
    state['reviewed_source_ids_before_supplement'] = sorted({s['source_id'] for s in state['judgments'].values()})


def format_sample(item, sources):
    from rapid_film_workbench import display_excerpt, excerpt_length_error, source_exclusion

    record = sources[item['source_id']]
    text = str(record['data'].get(item['text_field']) or '')
    quote = item['excerpt']
    if (source_exclusion(record) or text[item['start']:item['end']] != quote
            or excerpt_length_error(quote, record.get('channel'))):
        raise ValueError('补审样本无法通过原文、来源或字数校验')
    return {**item, 'source': record, 'source_text': text,
            'display_excerpt': display_excerpt(quote), 'ai_decision': 'keep'}


def accepted_samples(state, sources):
    """Mechanical checks may drop an addition. They never replace a locked sample."""
    from source_policy import sample_keys

    locked = set(state['locked_candidate_ids'])
    ledger = state['judgments']
    occupied = set()
    identities = set()
    for cid in locked:
        item = ledger[cid]
        occupied.update(sample_keys(item, sources[item['source_id']]))
        identities.add((item['group_index'], item['identity']))
    additions = {}
    committed=state.get('supplement_committed_candidate_ids', [])
    prior_indices=set(state['locked_group_indices']) | {int(i) for i in state.get('supplement_groups_approved',{})}
    candidates=state.get('supplement_candidate_ids',state.get('coverage_allowed_ids',[]))
    ordered_ids=list(dict.fromkeys(committed+sorted(candidates, key=lambda cid: ledger.get(cid,{}).get('group_index') not in prior_indices)))
    for cid in ordered_ids:
        item = ledger.get(cid)
        if not item or item['decision'] != 'keep' or cid in locked:
            continue
        index=item['group_index']
        if state.get('supplement_baseline_counts',{}).get(str(index),0)+len(additions.get(index,()))>=10:
            continue
        keys = sample_keys(item, sources[item['source_id']])
        identity = (item['group_index'], item['identity'])
        if keys & occupied or identity in identities:
            continue
        try:
            sample = format_sample(item, sources)
        except ValueError:
            continue
        occupied.update(keys)
        identities.add(identity)
        additions.setdefault(item['group_index'], []).append(sample)
    return additions


def finish_locked(workspace, state, sources, digest, status):
    from final_sample_review import save
    from model_packet import write_packet
    from workbench_enrichment import CHECKPOINT, read

    from supplement_schedule import review_time, seconds_left, PUBLISH_RESERVE_SECONDS
    if state.get('coverage_repair_active'):
        review_time(state)
        state['coverage_repair_active']=False
    existing = set(state['locked_group_indices'])
    reviewed={int(i) for i in state.get('supplement_groups_approved',{})} | {int(i) for i in state.get('supplement_groups_skipped',{}) if i.isdecimal()}
    current = accepted_samples(state, sources)
    committed=state.setdefault('supplement_committed_candidate_ids',[])
    eligible=existing | {int(i) for i in state.get('supplement_groups_approved',{})}
    for index in current:
        if index in eligible:
            committed.extend(row['candidate_id'] for row in current[index] if row['candidate_id'] not in committed)
    locked = read(workspace/CHECKPOINT/'final_dataset.json')
    unseen = []
    if not (state.get('final_history_check_policy') or state.get('discovery_history_context')):
        for index, samples in current.items():
            group = state['pool'][index]
            if index not in existing | reviewed and group['stance'] in POSITIVE and len(samples) >= 2:
                unseen.append({'group_index': index, 'title': group['aspect'],
                               'core_mechanism': group['core_mechanism'],
                               'samples': [{'candidate_id': s['candidate_id'],
                                            'display_excerpt': s['display_excerpt']} for s in samples]})
    if unseen and seconds_left(state) > PUBLISH_RESERVE_SECONDS + 30:
        prior = [{'id': g['viewpoint_id'], 'title': g['aspect'], 'core_mechanism': g['core_mechanism']}
                 for g in locked['groups']]
        for i in state.get('supplement_groups_approved',{}):
            group=state['pool'][int(i)]
            prior.append({'id':group['viewpoint_id'],'title':group['aspect'],'core_mechanism':group['core_mechanism']})
        for name in ('supplement_viewpoints_input','supplement_viewpoints_answer'):
            old=workspace/f'{name}.json'
            if old.is_file():old.replace(workspace/f"{name}.batch-{len(state['supplement_batches'])}.json")
        binding = digest([state['binding'], prior, unseen])
        packet = {'fingerprint': binding,'locked_viewpoints':prior,'new_groups':unseen,
                  'rules':'只核对new_groups。旧观点标题与判断只用于判断交叉，不能修改或复核旧观点。'
                          '每个新点只有在与旧点及其他新增点不重叠、有两条独立具体原话共同支持时才填keep，否则reject。'
                          '旧点的一种实例、手段改名、总括或不同演员不能另算。'
                          '只引用该组提供的逐字展示句，逐分句证明原标题。一个新点失败不影响其他新点。',
                  'answer_schema':{'fingerprint':binding,'groups':[{'group_index':'new_groups中的整数',
                      'decision':'keep或reject','distinction':'至少12字说明与最相近观点的具体区别或拒绝理由',
                      'title_support':[{'claim_index':'0起始分句序号','candidate_id':'本组ID','quote_snippet':'至少8字连续原句'}],
                      'writing_guidance':{'eligible':True,'standalone_eligible':True,'reason':'至少12字具体理由',
                          'independent_voice_ids':['两条或更多独立样本ID'],
                          'preferred':[{'candidate_id':'独立样本ID','quote_snippet':'12—120字连续评价原句'}]}}]}}
        write_packet(workspace/'supplement_viewpoints_input.json',packet)
        state.update(status='SUPPLEMENT_VIEWPOINTS_REQUIRED', supplement_binding=binding,
                     supplement_new_indices=[row['group_index'] for row in unseen])
        result=status(workspace,state)
        result.update(read_now=str(workspace/'supplement_viewpoints_input.json'),
                      answer_file=str(workspace/'supplement_viewpoints_answer.json'),
                      next_command=f'"{sys.executable}" -X utf8 "{Path(__file__).with_name("final_sample_review.py")}" supplement-collect --workspace "{workspace}"')
        save(workspace/'final_review_status.json',result)
        return result
    state['status']='PREPARING_FINAL_AI'
    from coverage_repair import prepare
    optional=prepare(workspace,state,sources,digest,status)
    if optional:return optional
    state.setdefault('supplement_stop_reason','FRESH_CANDIDATES_EXHAUSTED')
    state['status']='FINAL_SELECTED_COMPLETE'
    state['finish_reason']='LOCKED_WORKBENCH_AND_INDEPENDENT_SUPPLEMENTS'
    return status(workspace,state)


def collect_new_groups(workspace, state, answer, sources):
    from final_evidence_gate import validate_title_support, validate_writing_guidance, normalize
    from workbench_enrichment import CHECKPOINT, read

    if state.get('status') != 'SUPPLEMENT_VIEWPOINTS_REQUIRED' or answer.get('fingerprint') != state.get('supplement_binding'):
        raise ValueError('新增观点答卷与本次审查不匹配')
    current = accepted_samples(state, sources)
    old = read(workspace/CHECKPOINT/'final_dataset.json')
    names = {normalize(g['aspect']) for g in old['groups']}
    names.update(normalize(state['pool'][int(i)]['aspect']) for i in state.get('supplement_groups_approved',{}))
    rows = answer.get('groups', [])
    if not isinstance(rows, list):
        raise ValueError('新增观点审核须提交groups列表')
    approved = {}
    errors = {}
    occurrences = Counter(row.get('group_index') for row in rows
                          if isinstance(row,dict) and type(row.get('group_index')) is int)
    for row in rows:
        index = row.get('group_index') if isinstance(row,dict) else None
        if type(index) is not int or index not in state['supplement_new_indices']:
            continue
        if occurrences[index] != 1:
            errors[str(index)] = '同一个新增观点有重复答卷'
            continue
        if row.get('decision') != 'keep':
            errors[str(index)] = row.get('distinction') or '模型未同意纳入'
            continue
        group = state['pool'][index]
        title = group['aspect']
        samples = {s['candidate_id']:s for s in current.get(index, [])}
        try:
            if normalize(title) in names or len(str(row.get('distinction','')).strip())<12:
                raise ValueError('缺少该新增点与现有观点的实质区别')
            problems = validate_title_support(title,row.get('title_support'),samples)
            if problems:
                raise ValueError('；'.join(problems))
            guidance = validate_writing_guidance(row.get('writing_guidance'),samples,120,True)
            if not guidance['standalone_eligible']:
                raise ValueError('新增观点尚未有两份具体有效证据支撑独立成段')
        except ValueError as exc:
            errors[str(index)] = str(exc)
            continue
        approved[str(index)] = {'writing_guidance':guidance, 'title_support':row['title_support'],
                                'distinction':row['distinction']}
        names.add(normalize(title))
    for index in state['supplement_new_indices']:
        if str(index) not in approved:errors.setdefault(str(index),'该组没有有效审核结论')
    state.setdefault('supplement_groups_approved',{}).update(approved)
    state.setdefault('supplement_groups_skipped',{}).update(errors)


def overlay(workspace, baseline, receipt):
    from final_sample_review import source_map
    from source_policy import verify_unique_samples
    from source_balance import audit
    from workbench_enrichment import read
    from report_evidence import writing_readiness

    path = workspace/'final_review_state.json'
    if not path.is_file():
        return baseline,receipt,False
    state = read(path)
    if not state.get('locked_candidate_ids'):
        return baseline,receipt,False
    pool = read(workspace / state.get('pool_file','candidate_pool.json'))
    sources = source_map(workspace)
    result = deepcopy(baseline)
    existing = {g['viewpoint_id']:g for g in result['groups']}
    additions = Counter()
    original_count = supported_positive_count(result['groups'])
    for index,samples in accepted_samples(state,sources).items():
        group = pool[index]
        if index in state['locked_group_indices']:
            dest = existing[group['viewpoint_id']]
            extra = samples[:10-len(dest['samples'])]
            dest['samples'].extend(extra)
            additions[dest['viewpoint_id']] += len(extra)
        elif str(index) in state.get('supplement_groups_approved', {}) and len(result['groups']) < 16:
            if supported_positive_count(result['groups']) >= 10:
                continue
            approved = state['supplement_groups_approved'][str(index)]
            result['groups'].append({'aspect':group['aspect'],'stance':group['stance'],
                'category':group['category'],'core_mechanism':group['core_mechanism'],
                'viewpoint_id':group['viewpoint_id'],'objective_kind':group.get('objective_kind'),
                'candidate_count':len(group['candidates']),'samples':samples[:10],
                'history_review':None,'writing_guidance':approved['writing_guidance']})
            additions[group['viewpoint_id']] += len(samples[:10])
    receipt=dict(receipt,supplement_batch_count=len(state.get('supplement_batches',[])),
                 supplement_stop_reason=state.get('supplement_stop_reason'),supplement_batches=state.get('supplement_batches',[]))
    if not sum(additions.values()):
        return baseline,receipt,False
    verify_unique_samples(result)
    groups = result['groups']
    count = supported_positive_count(groups)
    assert count >= original_count
    grouped_count = sum(len(g['samples']) for g in groups)
    result['supported_positive_cluster_count'] = count
    result['candidate_coverage']['reviewed_candidate_count'] = len(state['judgments'])
    result['writing_readiness'] = writing_readiness(groups)
    result['source_balance_audit'] = audit(groups)
    result['supplement']={'added_samples_by_viewpoint':dict(additions),
                          'approved_new_viewpoints':state.get('supplement_groups_approved',{}),
                          'skipped_new_viewpoints':state.get('supplement_groups_skipped',{})}
    receipt=dict(receipt,sample_count=grouped_count,group_count=len(groups),
                 judged_candidates=len(state['judgments']),supported_positive_cluster_count=count,
                 positive_cluster_count=sum(g['stance'] in POSITIVE for g in groups),
                 source_balance_audit=result['source_balance_audit'],
                 groups_below_ten=[{'aspect':g['aspect'],'stance':g['stance'],'count':len(g['samples'])}
                                  for g in groups if len(g['samples'])<10],
                 supplemental_sample_count=sum(additions.values()),supplemental_samples_by_viewpoint=dict(additions))
    return result,receipt,True
