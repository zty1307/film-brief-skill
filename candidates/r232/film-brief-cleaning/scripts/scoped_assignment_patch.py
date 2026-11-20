"""Scoped, locked-caller reassignment transport. Never calls or approves models."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import native_assignment as assignment
import native_review as transport


def checked_path(workspace, value):
    path = Path(value).resolve()
    if not path.is_relative_to(workspace.resolve()):
        raise ValueError('Patch artifact outside current workspace')
    return path


def current_view_raw_candidates(w, source, records, current_views):
    """Offer guarded current-view raw spans, never search/rewrite cleaned text."""
    pipeline = w.pipeline_api()
    full = pipeline.source_text(source)
    candidates = []
    for record in records:
        view = current_views.get(record['view_id'])
        if not view:
            continue
        item = view['item']
        if (view['batch'] != source['batch'] or view['cluster_id'] != record['current_cluster_id']
                or item.get('viewId') != record['view_id'] or item.get('sourceId') != source['id']
                or pipeline.excerpt_evidence_segments(item.get('excerpt', ''))
                != (record.get('input_item') or {}).get('excerpt_segments')):
            continue
        provenance = item.get('excerptProvenance') or {}
        fragments, positions = provenance.get('fragments'), provenance.get('positions')
        if (not isinstance(fragments, list) or not 1 <= len(fragments) <= 2
                or any(not isinstance(value, str) for value in fragments)):
            continue
        try:
            checked = pipeline.validate_fragment_positions(full, fragments, positions)
        except (ValueError, TypeError, KeyError):
            continue
        if pipeline.clean_display_excerpt(' '.join(value.strip() for value in fragments)) != item.get('excerpt'):
            continue
        for text, position in zip(fragments, checked):
            candidate = assignment.exact_literal_candidate(full, text, position)
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def scoped_source_row(w, source, records, existing_override, config, current_views=None):
    """Literal menus only; neither source attribution nor proposals are approved."""
    full = w.pipeline_api().source_text(source)
    target = config.get('targets', {}).get(source['batch'], {})
    row = {'source_id': source['id'], 'batch': source['batch'], 'requests': records,
           'target_terms': [term for field in ('strong_terms', 'weak_terms') for term in target.get(field, [])],
           'existing_override': existing_override, 'passage_candidates': [], 'target_evidence_candidates': []}
    for family, texts in (
            ('passage_candidates', [segment for record in records
                                    for segment in (record['input_item'].get('excerpt_segments') or {}).values()]),
            ('target_evidence_candidates', [segment for record in records
                                            for segment in (record['input_item'].get('target_evidence_segments') or {}).values()])):
        for text in texts:
            candidate = assignment.exact_literal_candidate(full, text)
            if candidate and candidate['text'] not in {item['text'] for item in row[family]}:
                row[family].append({**candidate, 'candidate_index': len(row[family]) + 1})
    for candidate in current_view_raw_candidates(w, source, records, current_views or {}):
        if candidate['text'] not in {item['text'] for item in row['passage_candidates']}:
            row['passage_candidates'].append({**candidate,
                'candidate_index': len(row['passage_candidates']) + 1, 'origin': 'current_view_raw_provenance'})
    # Some final views need no separate target review, so they have no target
    # menu. A real move still needs target evidence: expose the existing
    # pipeline's exact anchor spans, not a guessed attribution or decision.
    for item in w.pipeline_api().target_evidence_candidates(source, target):
        if len(row['target_evidence_candidates']) >= 3:
            break
        candidate = assignment.exact_literal_candidate(full, item['text'], [item['start'], item['end']])
        if candidate and candidate['text'] not in {entry['text'] for entry in row['target_evidence_candidates']}:
            row['target_evidence_candidates'].append({**candidate,
                'candidate_index': len(row['target_evidence_candidates']) + 1, 'origin': 'source_anchor_lookup_hint',
                'context_before': full[max(0, candidate['start'] - 60):candidate['start']],
                'context_after': full[candidate['end']:candidate['end'] + 60]})
    return row


def cluster_set_repair_records(w, p, source_ids):
    """Bind explicitly requested IDs to actual current members, not prose/gold labels."""
    if not source_ids or len(source_ids) > 60 or len(set(source_ids)) != len(source_ids):
        raise ValueError('Choose 1-60 distinct current member source IDs')
    groups = {(row['batch'], row['cluster_id']): row
              for row in w.read_json(p['run'] / 'cluster_set_review_input.json')['clusters']}
    records, found = {}, set()
    for member in w.read_json(p['run'] / 'provisional_clustered_items.json')['items']:
        source_id = member['source_id']
        group = groups.get((member['batch'], member['cluster_id']))
        if source_id not in source_ids or not group:
            continue
        view_id = f"{source_id}::{member['cluster_id']}"
        if view_id in records:
            raise ValueError('Ambiguous duplicate current membership')
        records[view_id] = {
            'view_id': view_id, 'source_id': source_id, 'batch': member['batch'],
            'current_cluster_id': member['cluster_id'], 'review_fingerprint': group['fingerprint'],
            'scope': 'current_cluster_set_member_repair_request_not_approval',
            'input_item': {'excerpt_segments': w.pipeline_api().excerpt_evidence_segments(member['window']['text']),
                           'target_evidence_segments': {}}}
        found.add(source_id)
    if found != set(source_ids):
        raise ValueError('Requested source outside actual current cluster-set membership')
    if any(sum(record['source_id'] == source_id for record in records.values()) > 1 for source_id in found):
        raise ValueError('Existing multiple primary/secondary memberships need an explicit multi-route repair, not this single-route patch')
    return records


def prepare(w, workspace, source_ids=None):
    manifest = w.load_manifest(workspace)
    state = w.status_payload(workspace, manifest)
    origin = state.get('stage')
    if origin not in {'final_assignment_repair', 'cluster_set_review'}:
        return {'status': 'NOT_APPLICABLE', 'workflow_stage': state.get('stage')}
    p = w.paths_for(manifest)
    if origin == 'cluster_set_review':
        if not source_ids:
            return {'status': 'WAITING_SOURCE_IDS', 'canonical_written': False,
                    'instruction': 'Append --source-id and the explicitly identified current member IDs; no definition edit is needed to unlock this repair'}
        records = cluster_set_repair_records(w, p, source_ids)
        pending_views = set(records)
        pending_sources = sorted(source_ids)
    else:
        if source_ids:
            raise ValueError('Final repair scope comes from guarded pending requests, not CLI source IDs')
        pending_views = set(state['pending_view_ids'])
        pending_sources = state['pending_source_ids']
        records = {}
    semantic_input = p['run'] / 'excerpt_semantic_review_input.jsonl'
    current_fingerprints = ({row['view_id']: row['review_fingerprint'] for row in w.read_jsonl(semantic_input)}
                            if origin == 'final_assignment_repair' else {})
    for plan_file in (sorted((p['run'] / 'native_reviews').glob('*/dispatch.json'))
                      if origin == 'final_assignment_repair' else []):
        prior = w.read_json(plan_file)
        if (prior.get('status') == 'COLLECTED'
                and prior.get('stage') == 'final_excerpt_review'
                and (prior.get('_workflow') or {}).get('workflow_id') == manifest['workflow_id']):
            for record in prior.get('reassignment_requests', []):
                if (record['view_id'] in pending_views
                        and record.get('review_fingerprint') == current_fingerprints.get(record['view_id'])):
                    records[record['view_id']] = record
    if set(records) != pending_views:
        raise ValueError('Current guarded reassignment records are incomplete')
    staged_dataset = p['run'] / 'workbench_dataset.staged.json'
    protected = [p['config'], p['clusters'], p['overrides'], p['run'] / 'retained_sources.jsonl', staged_dataset,
                 semantic_input, p['excerpts'], p['semantic'],
                 w.CONTROLLER, w.PIPELINE, Path(__file__), Path(assignment.__file__), Path(transport.__file__),
                 *map(Path, state['input_files'])]
    if origin == 'cluster_set_review':
        protected += [p['set'], p['run'] / 'provisional_clustered_items.json',
                      p['run'] / 'cluster_set_review_input.json']
    guards = {str(path.resolve()): w.sha256(path) if path.exists() else None for path in protected}
    token = hashlib.sha256(json.dumps({'workflow_id': manifest['workflow_id'], 'guards': guards, 'repair_origin': origin,
                                       'pending_views': sorted(pending_views)}, sort_keys=True).encode()).hexdigest()
    folder = p['run'] / 'scoped_assignment_patches' / token
    folder.mkdir(parents=True, exist_ok=True)
    plan_file = folder / 'plan.json'
    if plan_file.exists():
        plan = w.read_json(plan_file)
        for name, expected in plan['guards'].items():
            path = checked_path(workspace, name) if Path(name).is_relative_to(workspace) else Path(name)
            if (w.sha256(path) if path.exists() else None) != expected:
                raise ValueError('Existing scoped patch input changed')
        return handoff(w, workspace, plan_file, plan)
    parent = w.read_json(p['overrides'])
    snapshot = folder / 'base-overrides.json'
    snapshot.write_bytes(p['overrides'].read_bytes())
    sources = {row['id']: row for row in w.read_jsonl(p['run'] / 'retained_sources.jsonl')}
    current_views = {}
    if origin == 'final_assignment_repair' and staged_dataset.exists():
        for batch in w.read_json(staged_dataset).get('batches', []):
            for cluster in batch.get('clusters', []):
                for item in cluster.get('items', []):
                    view_id = item.get('viewId')
                    if view_id in current_views:
                        raise ValueError('Duplicate current-view provenance; cannot safely offer raw spans')
                    current_views[view_id] = {'item': item, 'batch': batch['name'], 'cluster_id': cluster['id']}
    config = w.read_json(p['config'])
    rows = []
    for source_id in sorted(pending_sources):
        own_records = [record for record in records.values() if record['source_id'] == source_id]
        rows.append(scoped_source_row(w, sources[source_id], own_records, parent['overrides'].get(source_id), config, current_views))
    input_file = folder / 'scoped-input.jsonl'
    w.write_jsonl(input_file, rows)
    views, batch = [], []
    def publish(items):
        path = folder / f'reading-{len(views) + 1}.json'
        path.write_text(transport.readable_json_text(items), encoding='utf-8')
        views.append(str(path))
    for row in rows:
        if batch and transport.json_display_units(transport.readable_json_text(batch + [row])) > 16000:
            publish(batch)
            batch = []
        batch.append(row)
    if batch:
        publish(batch)
    output_file = folder / 'semantic-patch.json'
    w.write_json(output_file, {'overrides': {}})
    for path in [snapshot, input_file, *map(Path, views)]:
        guards[str(path.resolve())] = w.sha256(path)
    plan = {'status': 'PREPARED', 'workflow_id': manifest['workflow_id'], 'workspace': str(workspace.resolve()),
            'guards': guards, 'canonical_file': str(p['overrides'].resolve()), 'base_snapshot': str(snapshot),
            'input_file': str(input_file), 'output_file': str(output_file), 'readable_files': views,
            'cluster_definitions_file': str(p['clusters']),
            'pending_source_ids': sorted(pending_sources), 'pending_view_ids': sorted(pending_views),
            'repair_origin': origin}
    w.write_json(plan_file, plan)
    return handoff(w, workspace, plan_file, plan)


def handoff(w, workspace, plan_file, plan):
    result = {'status': plan['status'], 'plan_file': str(plan_file),
            'read_now': plan['readable_files'] + [plan['cluster_definitions_file']],
            'write_only': plan['output_file'],
            'raw_provenance_rule': 'origin=current_view_raw_provenance是当前展示片段已经核对逐字位置的原始片段，可能保留话题或表情标记；不是新判断。优先选合格原文编号，不重抄清洁后的展示句，不计算位置；作品、方面和态度仍须结合原文判断。',
            'instruction': '依次完整阅读read_now（阅读分片根数组，每项是一个来源；最后文件是当前簇定义），只在write_only的overrides中填写明确同意转簇的来源ID。填写cluster、passage_stance、具体reason；passage_candidate_indexes选一至两个观点编号，target_evidence_candidate_index另选作品归属编号，不重抄、不算位置。source_anchor_lookup_hint只说明该处原文含配置锚点，须结合局部上下文判断它和观点属于同一作品，不能凭锚点自动通过。候选不足才lookup后填逐字passage_fragments/target_evidence并清空对应编号。索引仅在本来源有效，不能代替阅读判断。拒绝请求继续原状态的有效终审否决路径，不填占位通过。已有副簇的受影响来源暂用原状态的明确人工修复路径，不在此补丁提交。不得改plan、阅读副本、快照或整张归类表，不创建转换/探测程序。',
            'apply_command': [w.sys.executable, str(w.CONTROLLER), 'assignment-patch-apply',
                              '--workspace', str(workspace), '--plan-file', str(plan_file)],
            'next_command': [w.sys.executable, str(w.CONTROLLER), 'advance', '--workspace', str(workspace)]}
    if plan.get('repair_origin') == 'cluster_set_review':
        result['instruction'] = ('完整读取read_now，只在write_only提交当前明确识别的错配来源；'
            '填写正确cluster、passage_stance和具体reason，用本来源passage_candidate_indexes及target_evidence_candidate_index选逐字证据。'
            '缺少候选才lookup后填逐字passage_fragments/target_evidence。也可修同簇片段，不必为解锁入口更改定义。'
            '确实无可报告观点才显式__exclude__、合法exclude_reason_code和逐字exclusion_evidence（或exclusion_evidence_candidate_index）；'
            '错归组不是来源无效。已有副簇受影响时不得在此简化为主簇覆盖。'
            'apply只校验并实际合并，尚须advance及最终摘录验收；不改计划、快照、阅读输入、整张归类表或分组定义。')
    return result


def apply(w, workspace, plan_file):
    plan_file = checked_path(workspace, plan_file)
    if not plan_file.is_relative_to((workspace / 'run' / 'scoped_assignment_patches').resolve()):
        raise ValueError('Not a current scoped patch plan')
    plan = w.read_json(plan_file)
    manifest = w.load_manifest(workspace)
    p = w.paths_for(manifest)
    canonical = checked_path(workspace, plan['canonical_file'])
    if (plan['workspace'] != str(workspace.resolve()) or plan['workflow_id'] != manifest['workflow_id']
            or canonical != p['overrides'].resolve()):
        raise ValueError('Scoped patch belongs to another workflow')
    if plan.get('status') == 'APPLIED':
        if w.sha256(checked_path(workspace, plan['output_file'])) != plan['applied_patch_sha256']:
            return {'status': 'PATCH_CHANGED_AFTER_APPLY', 'canonical_written': False,
                    'instruction': 'Prepare a new current plan; an applied patch cannot be edited and reused'}
        return {'status': 'ALREADY_APPLIED' if w.sha256(canonical) == plan['applied_sha256'] else 'STALE_AFTER_APPLY',
                'canonical_written': False, 'semantic_acceptance': 'not_inferred'}
    for name, expected in plan['guards'].items():
        path = Path(name)
        if path.resolve() != canonical and (w.sha256(path) if path.exists() else None) != expected:
            return {'status': 'STALE', 'changed_file': name, 'canonical_written': False}
    snapshot = checked_path(workspace, plan['base_snapshot'])
    output_file = checked_path(workspace, plan['output_file'])
    input_file = checked_path(workspace, plan['input_file'])
    if any(not path.is_relative_to(plan_file.parent) for path in (snapshot, output_file, input_file)):
        raise ValueError('Patch artifact outside its guarded plan directory')
    submitted_sha256 = w.sha256(output_file)
    parent = w.read_json(snapshot)
    try:
        patch = w.read_json(output_file)
    except (ValueError, OSError) as exc:
        return {'status': 'REPAIR_REQUIRED', 'errors': [str(exc)], 'output_file': str(output_file),
                'canonical_written': False}
    if not isinstance(patch, dict) or set(patch) != {'overrides'} or not isinstance(patch['overrides'], dict):
        return {'status': 'REPAIR_REQUIRED', 'errors': ['Patch must contain semantic overrides only'], 'canonical_written': False}
    if not patch['overrides']:
        return {'status': 'WAITING_DECISIONS', 'canonical_written': False}
    rows = {row['source_id']: row for row in w.read_jsonl(input_file)}
    sources = w.read_jsonl(p['run'] / 'retained_sources.jsonl')
    source_map = {row['id']: row for row in sources}
    candidate, errors = deepcopy(parent), []
    allowed_fields = {'cluster', 'reason', 'passage_stance', 'passage_fragments', 'passage_positions',
                      'anchor_terms', 'target_evidence', 'passage_candidate_indexes', 'target_evidence_candidate_index'}
    cluster_set_origin = plan.get('repair_origin') == 'cluster_set_review'
    if cluster_set_origin:
        allowed_fields |= {'exclude_reason_code', 'exclusion_evidence', 'evidence', 'exclusion_evidence_candidate_index'}
    for source_id, value in patch['overrides'].items():
        try:
            if source_id not in rows or source_id not in plan['pending_source_ids'] or source_id not in source_map:
                raise ValueError('Source outside guarded pending scope')
            if not isinstance(value, dict) or not set(value) <= allowed_fields:
                raise ValueError('Unsupported semantic fields')
            if (parent['overrides'].get(source_id) or {}).get('secondary_cluster'):
                raise ValueError('Affected existing secondary route needs original explicit repair path')
            expanded = assignment.expand_candidate_indexes(w, value, rows[source_id], source_map[source_id])
            if cluster_set_origin and expanded.get('cluster') == '__exclude__':
                candidate['overrides'][source_id] = expanded
                continue  # Same ordinary explicit reason/evidence exclusion validation below.
            if (not w.clean(expanded.get('reason')) or not w.clean(expanded.get('cluster'))
                    or expanded.get('cluster') == '__exclude__' or not expanded.get('passage_fragments')):
                raise ValueError('Explicit AI cluster, stance, reason and opinion evidence required')
            if (not cluster_set_origin
                    and all(record['current_cluster_id'] == expanded['cluster'] for record in rows[source_id]['requests'])):
                raise ValueError('A reassignment patch must choose another current cluster; reject requests through the original final-review path')
            full = w.pipeline_api().source_text(source_map[source_id])
            target = expanded.get('target_evidence')
            if not isinstance(target, str) or not target or target not in full:
                raise ValueError('Separate literal target evidence required')
            candidate['overrides'][source_id] = expanded
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append({'source_id': source_id, 'error': str(exc)})
    definitions, config = w.read_json(p['clusters']), w.read_json(p['config'])
    errors.extend(w.cluster_override_validation_issues(sources, definitions, candidate, config))
    if errors:
        return {'status': 'REPAIR_REQUIRED', 'errors': errors, 'canonical_written': False}
    # Run the very same ordinary primary-window gates before any canonical
    # write. A failed move remains in this owned local patch, not a committed
    # global route followed by another assignment-review round trip.
    pipeline = w.pipeline_api()
    by_batch = {batch: {item['id']: item for item in items}
                for batch, items in definitions['batches'].items()}
    for source_id in patch['overrides']:
        source, override = source_map[source_id], candidate['overrides'][source_id]
        if cluster_set_origin and override['cluster'] == '__exclude__':
            continue  # Already validated by the unchanged ordinary exclusion gate.
        definition = by_batch[source['batch']][override['cluster']]
        target_config = config['targets'][source['batch']]
        window = pipeline.reviewed_window(source, definition, override, target_config)
        alignment = pipeline.passage_alignment(source, window, definition, target_config, override)
        negative = pipeline.prepare_cluster_scoring(definition, target_config)['negative']
        issue = pipeline.assignment_window_issue(definition, window, alignment, override, negative)
        if issue:
            errors.append({'source_id': source_id, 'issue': issue,
                           'required_fix': pipeline.CLUSTER_ASSIGNMENT_ISSUE_GUIDANCE.get(issue, issue),
                           'score': window['score'], 'alignment': alignment})
    if errors:
        return {'status': 'REPAIR_REQUIRED', 'errors': errors, 'canonical_written': False,
                'repair_scope': 'current owned semantic-patch only; unchanged ordinary gates',
                'output_file': str(output_file)}
    if w.sha256(canonical) != plan['guards'][str(canonical)]:
        if w.read_json(canonical) != candidate:
            return {'status': 'STALE', 'changed_file': str(canonical), 'canonical_written': False}
        recovered = True  # Exact previous commit, crash before plan bookkeeping.
    else:
        state = w.status_payload(workspace, manifest)
        if cluster_set_origin:
            scope_matches = (state.get('stage') == 'cluster_set_review'
                             and set(cluster_set_repair_records(w, p, plan['pending_source_ids'])) == set(plan['pending_view_ids']))
        else:
            scope_matches = (state.get('stage') == 'final_assignment_repair'
                             and set(patch['overrides']) <= set(state.get('pending_source_ids', []))
                             and set(plan['pending_view_ids']) <= set(state.get('pending_view_ids', [])))
        if not scope_matches:
            return {'status': 'STALE', 'canonical_written': False, 'reason': 'Pending scope changed'}
        if candidate == parent:
            return {'status': 'NO_ROUTE_CHANGE', 'canonical_written': False}
        if w.sha256(output_file) != submitted_sha256:
            return {'status': 'STALE', 'canonical_written': False, 'changed_file': str(output_file)}
        for name, expected in plan['guards'].items():
            path = Path(name)
            if (w.sha256(path) if path.exists() else None) != expected:
                return {'status': 'STALE', 'changed_file': name, 'canonical_written': False}
        w.write_json(canonical, candidate)  # Caller holds existing workflow_lock.
        recovered = False
    plan.update(status='APPLIED', applied_sha256=w.sha256(canonical), applied_patch_sha256=w.sha256(output_file),
                updated_source_ids=sorted(patch['overrides']))
    w.write_json(plan_file, plan)
    return {'status': 'RECOVERED_ALREADY_APPLIED' if recovered else 'APPLIED_PENDING_REVIEW',
            'canonical_written': not recovered, 'updated_source_ids': sorted(patch['overrides']),
            'semantic_acceptance': 'pending ordinary advance and final excerpt review',
            'next_command': [w.sys.executable, str(w.CONTROLLER), 'advance', '--workspace', str(workspace)]}
