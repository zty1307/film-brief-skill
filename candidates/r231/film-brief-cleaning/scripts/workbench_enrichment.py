"""Keep a completed workbench recoverable while optional samples are reviewed."""
from hashlib import sha256
import json
import shutil

from workbench_completion import supported_positive_count, review_readiness


CHECKPOINT = 'ready_workbench'
FILES = ('final_dataset.json', 'final_review_status.json', 'workbench.html')
ENRICHMENT_MIN_SECONDS = 420


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


def precheck_ready(state):
    """Start the evidence gate once six positive groups have two candidate voices."""
    if not state.get('protect_ready_workbench') or state.get('initial_evidence_check_requested'):
        return False
    voices = {}
    for item in state['judgments'].values():
        index = item['group_index']
        if item['decision'] == 'keep' and state['pool'][index]['stance'] in ('positive', '正向词倾向'):
            voices.setdefault(index, set()).add(item.get('identity') or item['source_id'])
    if sum(len(group) >= 2 for group in voices.values()) < 6:
        return False
    state['initial_evidence_check_requested'] = True
    return True


def has_checkpoint(workspace):
    return (workspace / CHECKPOINT / 'files.json').is_file()


def protect_and_enrich(workspace, state, dataset, result, sources, digest, status):
    if not state.get('protect_ready_workbench') or has_checkpoint(workspace):
        return result
    if result['status'] != 'FINAL_SELECTED_COMPLETE':
        return result
    review_readiness(dataset, result)
    snapshot = workspace / CHECKPOINT
    snapshot.mkdir(exist_ok=True)
    for name in FILES:
        shutil.copy2(workspace / name, snapshot / name)
    (snapshot / 'files.json').write_text(json.dumps({
        name: fingerprint(snapshot / name) for name in FILES
    }), encoding='utf-8')
    state['enrichment_started'] = True
    from locked_supplement import lock_reviewed_board
    lock_reviewed_board(state,dataset)
    from coverage_repair import prepare
    optional = prepare(workspace, state, sources, digest, status)
    return optional or result


def _inventory(dataset):
    samples = set()
    supported = set()
    for group in dataset['groups']:
        key = (group['stance'], group.get('viewpoint_id') or group['aspect'])
        if supported_positive_count([group]):
            supported.add(key)
        samples.update((key, sample['candidate_id'], sample['display_excerpt'])
                       for sample in group['samples'])
    return supported, samples


def select_completed_board(workspace, result, publish_snapshot=False):
    """Return only a reviewed board, retaining every confirmed baseline sample."""
    snapshot = workspace / CHECKPOINT
    hashes = read(snapshot / 'files.json')
    if any(fingerprint(snapshot / name) != hashes.get(name) for name in FILES):
        raise ValueError('已审核工作台的保护快照不完整或已改变')
    baseline = read(snapshot / 'final_dataset.json')
    baseline_receipt = read(snapshot / 'final_review_status.json')
    review_readiness(baseline, baseline_receipt)
    state_path=workspace/'final_review_state.json'
    if state_path.is_file() and read(state_path).get('locked_candidate_ids'):
        from locked_supplement import overlay
        return overlay(workspace,baseline,baseline_receipt)
    if not publish_snapshot and result.get('status') == 'FINAL_SELECTED_COMPLETE':
        try:
            candidate = read(workspace / 'final_dataset.json')
            review_readiness(candidate, result)
        except (ValueError, OSError):
            return baseline, baseline_receipt, False
        old_groups, old_samples = _inventory(baseline)
        new_groups, new_samples = _inventory(candidate)
        if old_groups <= new_groups and old_samples <= new_samples:
            return candidate, result, bool(old_samples < new_samples or old_groups < new_groups)
    return baseline, baseline_receipt, False


def publish_board(workspace, result, elapsed, publish_snapshot=False):
    from final_sample_review import save
    from rapid_film_workbench import render

    dataset, receipt, enriched = select_completed_board(workspace, result, publish_snapshot)
    baseline = read(workspace / CHECKPOINT / 'final_review_status.json')
    receipt = dict(receipt, elapsed_seconds=elapsed,
                   ready_workbench_elapsed_seconds=baseline['elapsed_seconds'],
                   ready_positive_count=baseline['supported_positive_cluster_count'],
                   ready_sample_count=baseline['sample_count'],
                   enrichment_published=enriched, enrichment_last_status=result.get('status'))
    receipt['completed_within_30_minutes'] = elapsed <= 1800
    receipt['completed_within_45_minutes'] = elapsed <= 2700
    receipt['completed_within_budget'] = elapsed <= receipt['deadline_seconds']
    receipt['output'] = str(workspace / 'workbench.html')
    verify = review_readiness(dataset, receipt)
    assert verify['ready']
    save(workspace / 'final_dataset.json', dataset)
    page = workspace / 'workbench.html.tmp'
    page.write_text(render(dataset, receipt), encoding='utf-8')
    page.replace(workspace / 'workbench.html')
    save(workspace / 'final_review_status.json', receipt)
    return receipt
