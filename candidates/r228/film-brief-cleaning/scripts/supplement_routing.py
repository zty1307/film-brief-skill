"""Send unambiguous routes to ordinary review and resolve only ambiguous routes with AI."""
from collections import Counter, defaultdict
import json

from source_policy import sample_keys, source_keys
from supplement_schedule import DISCOVERY_AND_REVIEW_MIN_SECONDS, record_batch, review_plan, seconds_left


def board_counts(state, sources):
    from locked_supplement import accepted_samples

    approved = {int(index) for index in state.get('supplement_groups_approved', {})}
    supported = set(state['supplement_baseline_supported']) | approved
    additions = accepted_samples(state, sources)
    kept = {i: state['supplement_baseline_counts'].get(str(i), 0) + len(additions.get(i, []))
            for i in set(state['locked_group_indices']) | approved}
    return kept, supported


def routed_options(state, sources, digest, kept):
    from rapid_film_workbench import display_excerpt, excerpt_min_chars, source_exclusion, source_selection_key
    from source_balance import channel

    occupied = set()
    channels = defaultdict(Counter)
    author_groups = set()
    for item in state['judgments'].values():
        record = sources[item['source_id']]
        occupied.update(sample_keys(item, record))
        if item['decision'] == 'keep':
            author_groups.add((item['group_index'], item['identity']))
            channels[item['group_index']][channel(record)] += 1
    tried = set(state.get('supplement_offered_candidate_ids', []))
    tried.update(state.get('abandoned_candidates', {}))
    tried.update(state.get('length_ineligible_candidates', {}))
    owner_by_key = defaultdict(set)
    available = []
    excluded = {}
    for index, group in enumerate(state['pool']):
        for candidate in group['candidates']:
            sid = candidate['source_id']
            record = sources[sid]
            body = str(record['data'].get(candidate['text_field']) or '')
            left, right = candidate['start'], candidate['end']
            quote = body[left:right]
            keys = sample_keys({**candidate, 'excerpt': quote}, record)
            if keys & occupied or not quote:
                continue
            if sid not in excluded:
                excluded[sid] = bool(source_exclusion(record))
            if excluded[sid]:
                continue
            for key in keys:
                owner_by_key[key].add(index)
            if index not in kept or kept[index] >= 10 or (index, candidate['identity']) in author_groups:
                continue
            cid = digest([state['binding'], index, candidate])[:20]
            if cid in tried:
                continue
            windows = [option['text'] for option in candidate.get('selection_options', [])]
            if not windows:
                windows = [body[max(0, left-100):right+100]]
            if max(len(display_excerpt(text)) for text in windows) < excerpt_min_chars(record.get('channel')):
                continue
            available.append((index, candidate, cid, quote, keys))
    available.sort(key=lambda row: (kept[row[0]], channels[row[0]][channel(sources[row[1]['source_id']])],
                                    source_selection_key(sources[row[1]['source_id']], state['pool'][row[0]]['stance'],
                                                         row[1].get('score', 0), row[1]['source_id'])))
    direct, ambiguous = [], []
    for row in available:
        owners = set().union(*(owner_by_key[key] for key in row[4]))
        (direct if len(owners) == 1 else ambiguous).append(row)
    return direct, ambiguous, occupied


def begin_review(state, allowed):
    state.update(coverage_repair_active=True, coverage_allowed_ids=allowed, packets=[],
                 status='PREPARING_FINAL_AI', last_measured_round=state['round'])
    state.pop('coverage_review_started', None)
    state['supplement_candidate_ids'] = list(dict.fromkeys(state.get('supplement_candidate_ids', []) + allowed))


def direct_review(workspace, state, options, kept):
    from final_sample_review import next_round

    limit = review_plan(state, 12)['candidate_count']
    picked, seen, authors = [], set(), set()
    counts = Counter()
    for index, candidate, cid, _, keys in options:
        if len(picked) >= limit:
            break
        identity = (index, candidate['identity'])
        if keys & seen or identity in authors or counts[index] >= min(4, 10-kept[index]):
            continue
        counts[index] += 1
        seen.update(keys)
        authors.add(identity)
        picked.append({'candidate_id': cid, 'group_index': index})
    if not picked:
        return None
    record_batch(state, picked, [], {})
    state['supplement_batches'][-1]['mode'] = 'direct_review'
    state['supplement_last_routing_mode'] = 'direct_review'
    begin_review(state, [row['candidate_id'] for row in picked])
    return next_round(workspace, state)


def dispatch(workspace, state, packet, status):
    from coverage_repair import response
    from model_packet import write_packet

    number = len(state['supplement_batches'])
    for filename in ('coverage_repair_input', 'coverage_repair_answer', 'coverage_repair_feedback'):
        old = workspace / f'{filename}.json'
        if old.is_file():
            old.replace(workspace / f'{filename}.route-{number}.json')
    write_packet(workspace / 'coverage_repair_input.json', packet)
    state.update(status='COVERAGE_REPAIR_REQUIRED', coverage_repair_binding=packet['fingerprint'],
                 coverage_seed_ids=[row['excerpt_id'] for row in packet['discovery_excerpts']],
                 coverage_options=[row['candidate_id'] for row in packet['review_options']],
                 supplement_new_slots=packet['new_viewpoint_slots'],
                 supplement_last_routing_mode='discovery', supplement_routing_contract='ROUTE_BY_IDS_V1')
    return response(workspace, state, status)


def prepare(workspace, state, sources, digest, status):
    from coverage_repair import available_discovery_excerpts, extra_coverage_excerpts, read
    from model_packet import voice_refs

    kept, supported = board_counts(state, sources)
    groups = state['pool']
    weak = [index for index in sorted(kept, key=lambda i: (kept[i], i)) if kept[index] < 10]
    slots = max(0, min(3, 10-len(supported), 16-len(groups)))
    if state.get('final_history_check_policy') or state.get('discovery_history_context'):
        slots = 0
    if not weak and not slots:
        state['supplement_stop_reason'] = 'GOALS_REACHED' if len(supported) >= 8 else 'NO_VIEWPOINT_SLOTS'
        return None

    direct, ambiguous, occupied = routed_options(state, sources, digest, kept)
    time_for_discovery = seconds_left(state) >= DISCOVERY_AND_REVIEW_MIN_SECONDS
    retry = state.pop('supplement_routing_retry', None)
    if retry and time_for_discovery:
        packet = retry['packet']
        packet['fingerprint'] = digest([packet['fingerprint'], retry['errors'], len(state['supplement_batches'])])
        packet['answer_schema']['fingerprint'] = packet['fingerprint']
        packet['new_viewpoint_slots'] = min(packet['new_viewpoint_slots'], slots)
        packet['facet_hint_index'] = {}
        packet['repair_only'] = retry['errors']
        packet['retry'] = True
        state['supplement_batches'].append({'batch': len(state['supplement_batches']) + 1, 'mode': 'routing_repair',
                                             'review_options': len(packet['review_options']),
                                             'fresh_excerpts': 0, 'seconds_remaining_at_dispatch': round(seconds_left(state), 2)})
        return dispatch(workspace, state, packet, status)
    if state.get('supplement_last_routing_mode') != 'direct_review' or not time_for_discovery:
        result = direct_review(workspace, state, direct, kept)
        if result:
            return result
    if not time_for_discovery:
        state['supplement_stop_reason'] = 'TIME_RESERVED_FOR_REVIEW_AND_PUBLICATION'
        return None

    previously_offered = state.get('supplement_offered_source_ids', [])
    occupied.update(key for sid in previously_offered for key in source_keys(sources[sid]))
    occupied.update(key for row in direct for key in row[4])
    definitions = read(workspace/'viewpoint_definitions.json')
    used_seeds = {sid for group in definitions['clusters'] for sid in group['seed_ids']}
    seeds = available_discovery_excerpts(state, sources, occupied, used_seeds)[:24]
    extras = extra_coverage_excerpts(workspace, state, sources, occupied, limit=40)[:48-len(seeds)]
    visible = ('excerpt_id', 'source_ref', 'excerpt', 'context', 'source_channel', 'verified_mainstream', 'history_candidates')
    seeds.extend({key: entry[key] for key in visible if key in entry} for entry in extras)
    picked, offered_by_group = [], Counter()
    for index, _, cid, quote, _ in ambiguous:
        if offered_by_group[index] >= 6:
            continue
        picked.append({'candidate_id': cid, 'viewpoint_id': groups[index]['viewpoint_id'], 'excerpt': quote})
        offered_by_group[index] += 1
        if len(picked) == 24:
            break
    if not (picked or seeds):
        result = direct_review(workspace, state, direct, kept)
        if result:
            return result
        state['supplement_stop_reason'] = 'FRESH_CANDIDATES_EXHAUSTED'
        return None

    prior = state.get('coverage_seed_archive', []) + state.get('coverage_extra_excerpts', [])
    all_excerpts = state['discovery_excerpts'] + prior + extras
    entries = {row['excerpt_id']: row for row in all_excerpts}
    refs = voice_refs(all_excerpts)
    for row in seeds:
        row['voice_ref'] = refs[row['excerpt_id']]
        entry = entries[row['excerpt_id']]
        if context_window(entry, sources):
            row['allow_context_window'] = True
    fingerprint = digest([state['binding'], picked, seeds, len(state.get('supplement_batches', []))])
    original = read(workspace/'viewpoint_discovery_input.json')
    from cluster_contract import CATEGORIES
    from report_evidence import facet_hint_index
    from viewpoint_discovery import TITLE_VERBS
    packet = {'fingerprint': fingerprint, 'review_options': picked, 'discovery_excerpts': seeds,
              'existing_viewpoints': [{'id': g['viewpoint_id'], 'category':g['category'],
                                       'title': state.get('editorial_titles', {}).get(str(i), g['aspect']),
                                       'core_mechanism': state.get('editorial_core_mechanisms', {}).get(str(i), g['core_mechanism'])}
                                      for i, g in enumerate(groups)],
              'attachment_groups': [{'viewpoint_id': groups[i]['viewpoint_id'], 'kept': kept[i]} for i in weak],
              'new_viewpoint_slots': slots, 'new_cluster_schema': {**original['cluster_schema'], 'stance': 'positive'},
              'allowed_categories': CATEGORIES, 'allowed_title_verbs': TITLE_VERBS,
              'facet_hint_index': facet_hint_index(seeds),
              'answer_schema': {'fingerprint': fingerprint, 'candidate_ids': ['review_options中的候选编号'],
                                'routes': [{'excerpt_id': '本包E编号', 'viewpoint_id': '允许追加的观点编号',
                                            'window': 'excerpt，或在allow_context_window为真时选context'}],
                                'new_clusters': [], 'cluster_boundaries': [{'left': '观点ID', 'right': '观点ID',
                                                                           'distinction': '具体评价边界'}]},
              'rules': '只判断本包归属不明的候选与有据的新点。已匹配到一个观点的其他候选直接进入逐条审核。'
                       '对review_options，若确认建议观点合适就回填candidate_id。对尚未匹配的E原句，选excerpt_id及目标viewpoint_id；原句窗口默认excerpt，'
                       'context可用才选择context。脚本从原记录绑定出处和原文位置。合计最多12条、每组最多4条，优先给留样少的组。'
                       '新增观点严格遵守new_cluster_schema，限正面，最多new_viewpoint_slots个，每个至少两份独立作者的具体评价，'
                       '和现有观点、其他新增观点都要划清边界。旧结果锁定。本包只安排送审，所有候选还要逐条审核，新观点还要过标题证据与交叉核对。'}
    record_batch(state, picked, seeds, {eid: row['source_id'] for eid, row in entries.items()})
    state['supplement_batches'][-1]['mode'] = 'discovery'
    state.update(coverage_seed_archive=prior, coverage_extra_excerpts=extras)
    return dispatch(workspace, state, packet, status)


def context_window(entry, sources):
    from rapid_film_workbench import excerpt_length_error

    context = entry.get('context')
    if context:
        record = sources[entry['source_id']]
        body = str(record['data'].get(entry['text_field']) or '')
        if body[max(0, entry['start']-60):entry['end']+60] == context and not excerpt_length_error(context, record.get('channel')):
            return context
    return None


def collect(workspace, state, answer, sources, digest, apply_new):
    from coverage_repair import read, route_attachment

    if not isinstance(answer, dict) or state['status'] != 'COVERAGE_REPAIR_REQUIRED' or answer.get('fingerprint') != state['coverage_repair_binding']:
        raise ValueError('当前补审答卷与送审包不匹配')
    packet = read(workspace/'coverage_repair_input.json')
    kept, _ = board_counts(state, sources)
    groups = state['pool']
    eligible = {g['viewpoint_id']: i for i, g in enumerate(groups) if i in kept and kept[i] < 10}
    visible_groups = {row['viewpoint_id'] for row in packet['attachment_groups']}
    excerpts = {row['excerpt_id']: row for row in (state['discovery_excerpts'] + state['coverage_seed_archive'] + state['coverage_extra_excerpts'])
                if row['excerpt_id'] in state['coverage_seed_ids']}
    options = {row['candidate_id']: row['viewpoint_id'] for row in packet['review_options']}
    used = {key for item in state['judgments'].values() for key in sample_keys(item, sources[item['source_id']])}
    allowed = []
    counted = Counter()
    errors = []
    retry_candidates, retry_excerpts = set(), set()

    def rows(field):
        value = answer.get(field, [])
        if not isinstance(value, list):
            errors.append({'field': field, 'error': '字段应为列表'})
            return []
        return value

    def route_order(row):
        group_id = row.get('viewpoint_id') if isinstance(row, dict) else options.get(row) if isinstance(row, str) else None
        index = eligible.get(group_id) if isinstance(group_id, str) else None
        return (kept.get(index, 10), index if index is not None else len(groups))

    changed_pool = False
    routes = sorted(rows('candidate_ids') + rows('routes'), key=route_order)
    for row in routes:
        candidate_id = row if isinstance(row, str) else None
        excerpt_id = row.get('excerpt_id') if isinstance(row, dict) else None
        group_id = row.get('viewpoint_id') if isinstance(row, dict) else options.get(candidate_id)
        if not isinstance(excerpt_id, str):
            excerpt_id = None
        try:
            if not isinstance(group_id, str) or group_id not in eligible or group_id not in visible_groups:
                raise ValueError('须选择本包允许且未满10条的观点编号')
            index = eligible[group_id]
            if len(allowed) >= 12 or counted[index] >= min(4, 10-kept[index]):
                continue
            if candidate_id:
                candidate = next((item for item in groups[index]['candidates']
                                  if digest([state['binding'], index, item])[:20] == candidate_id), None)
                if candidate is None or candidate_id in state['judgments']:
                    raise ValueError('候选已审或不在本包当前观点中')
                record = sources[candidate['source_id']]
                body = str(record['data'].get(candidate['text_field']) or '')
                keys = sample_keys({**candidate, 'excerpt': body[candidate['start']:candidate['end']]}, record)
                if keys & used:
                    raise ValueError('同一原表记录只能归入一个观点')
                used.update(keys)
                cid = candidate_id
            else:
                entry = excerpts.get(excerpt_id)
                if not entry:
                    raise ValueError('须选择本包提供的新原句编号')
                window = row.get('window', 'excerpt')
                support = entry['excerpt'] if window == 'excerpt' else context_window(entry, sources) if window == 'context' else None
                if not support:
                    raise ValueError('只能选该原句列出的窗口')
                candidate, is_new = route_attachment(state, entry, support, sources, index, used, digest)
                if is_new:
                    groups[index]['candidates'].append(candidate)
                    changed_pool = True
                cid = digest([state['binding'], index, candidate])[:20]
            allowed.append(cid)
            counted[index] += 1
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({'candidate_id':candidate_id, 'excerpt_id':excerpt_id, 'viewpoint_id':group_id,
                           'error':str(exc)})
            if candidate_id in options:
                retry_candidates.add(candidate_id)
            if excerpt_id in excerpts:
                retry_excerpts.add(excerpt_id)

    proposed = rows('new_clusters')[:state['supplement_new_slots']]
    boundaries = rows('cluster_boundaries')
    retry_new = False
    for group in proposed:
        group_id = group.get('id') if isinstance(group, dict) else None
        try:
            if not isinstance(group_id, str) or not group_id:
                raise ValueError('新增观点须有有效编号')
            seed_ids = group.get('seed_ids')
            if not isinstance(seed_ids, list) or len(seed_ids) < 2 or any(not isinstance(eid, str) or eid not in excerpts for eid in seed_ids):
                raise ValueError('新增观点须绑定本包至少两份原句')
            seeds = set().union(*(source_keys(sources[excerpts[eid]['source_id']]) for eid in seed_ids))
            if seeds & used:
                raise ValueError('新增观点种子与已有送审原表记录冲突')
            existing = {viewpoint['viewpoint_id'] for viewpoint in state['pool']}
            pairs = [pair for pair in boundaries if isinstance(pair, dict)
                     and ((pair.get('left') == group_id and isinstance(pair.get('right'), str) and pair['right'] in existing)
                          or (pair.get('right') == group_id and isinstance(pair.get('left'), str) and pair['left'] in existing))]
            state['status'] = 'COVERAGE_REPAIR_REQUIRED'
            apply_new(workspace, state, {'fingerprint': packet['fingerprint'], 'new_clusters': [group],
                                       'shortfall_reason': '仅提请审核本组新观点的实际原句与独立边界，仍须通过逐条核验。',
                                       'cluster_boundaries': pairs}, sources, digest)
            allowed.extend(state['coverage_allowed_ids'])
            used.update(seeds)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({'new_viewpoint_id':group_id, 'proposal':group, 'error':str(exc)})
            retry_new = True

    if changed_pool:
        pool_file = 'coverage_viewpoint_candidate_pool.json'
        (workspace/pool_file).write_text(json.dumps(state['pool'], ensure_ascii=False, indent=2), encoding='utf-8')
        state.update(pool_file=pool_file, pool_hash=digest(state['pool']))
    begin_review(state, list(dict.fromkeys(allowed)))
    if errors:
        state['supplement_batches'][-1]['routing_errors'] = errors
        if not packet.get('retry') and (retry_candidates or retry_excerpts or retry_new):
            state['supplement_routing_retry'] = {'errors':errors, 'packet':{
                **packet, 'review_options':[row for row in packet['review_options'] if row['candidate_id'] in retry_candidates],
                'discovery_excerpts':[row for row in packet['discovery_excerpts'] if retry_new or row['excerpt_id'] in retry_excerpts],
                'new_viewpoint_slots':packet['new_viewpoint_slots'] if retry_new else 0}}
    return bool(allowed)
