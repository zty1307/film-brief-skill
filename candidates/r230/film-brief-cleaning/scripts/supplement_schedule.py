"""Schedule fresh supplementation batches against one workbench deadline."""
import time


PUBLISH_RESERVE_SECONDS = 45
DISCOVERY_AND_REVIEW_MIN_SECONDS = 155


def seconds_left(state):
    return state['started_epoch'] + state['deadline_seconds'] - time.time()


def review_plan(state, count):
    measured = state.get('supplement_review_seconds', [])[-4:]
    typical = sorted(measured)[len(measured) // 2] if measured else 37
    remaining = seconds_left(state)
    # Start smaller packets as the remaining time shrinks. Every item still
    # receives the ordinary excerpt, scope and source checks.
    for size in (3, 2, 1):
        cost = PUBLISH_RESERVE_SECONDS + 25 + max(28, typical) * (0.55 + 0.30 * size)
        if cost < remaining:
            return {'packet_size': size, 'candidate_count': min(count, size * 8),
                    'required_seconds': round(cost, 3), 'remaining_seconds': remaining}
    return {'packet_size': 0, 'candidate_count': 0, 'required_seconds': PUBLISH_RESERVE_SECONDS + 25 + max(28, typical) * .85,
            'remaining_seconds': remaining}


def review_time(state):
    if state.get('coverage_review_started') and state.get('round_started_epoch') is not None:
        state.setdefault('supplement_review_seconds', []).append(time.time() - state['round_started_epoch'])


def record_batch(state, picked, seeds, raw_sources):
    state.setdefault('supplement_offered_candidate_ids', []).extend(row['candidate_id'] for row in picked)
    state.setdefault('supplement_offered_source_ids', []).extend(raw_sources[row['excerpt_id']] for row in seeds)
    state.setdefault('supplement_batches', []).append({
        'batch': len(state.get('supplement_batches', [])) + 1,
        'review_options': len(picked), 'fresh_excerpts': len(seeds),
        'seconds_remaining_at_dispatch': round(seconds_left(state), 2)})
