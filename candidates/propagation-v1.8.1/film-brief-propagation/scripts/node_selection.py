"""Select explainable curve nodes before searching events; never infer events here."""
from __future__ import annotations


def select_nodes(series, previous_through=None, limit=2, prominence_fraction=0.15):
    """Return a small proposal plus its curve rationale, not a causal conclusion.

    Prominence is measured against valleys before a higher point on either side.
    A boundary high is provisional (no outside-window observations). Thresholds
    are editable screening heuristics, not business definitions of 'high heat'.
    """
    rows = sorted(series, key=lambda r: r['date'])
    if not rows:
        return []
    if len({r['date'] for r in rows}) != len(rows):
        raise ValueError('节点序列日期重复')
    values = [r['heat'] for r in rows]
    if min(values) < 0:
        raise ValueError('节点热度不能为负')
    maximum, minimum = max(values), min(values)
    span = maximum - minimum
    if span == 0:
        return []  # A flat curve does not establish a peak or warming event.
    candidates = []
    for i, row in enumerate(rows):
        if previous_through and row['date'] <= previous_through:
            continue
        current = values[i]
        reasons = []
        prominence = 0
        # Treat a plateau as one peak at its first observation.
        left_up = i == 0 or current > values[i - 1]
        right_down = i == len(rows) - 1 or current >= values[i + 1]
        if left_up and right_down:
            valleys = []
            for step in (-1, 1):
                j, valley = i + step, current
                observed = False
                while 0 <= j < len(rows):
                    if values[j] > current:
                        break
                    observed = True
                    valley = min(valley, values[j])
                    j += step
                if observed:
                    valleys.append(valley)
            prominence = current - max(valleys) if valleys else 0
            if prominence >= prominence_fraction * span:
                reasons.append('起始高点（窗口外未知）' if i == 0 else
                               '末端回升（尚未确认波峰）' if i == len(rows) - 1 else '显著局部峰值')
        if current == maximum and (i == 0 or values[i-1] != maximum):
            reasons.append('统计周期最高值')
        latest = i == len(rows) - 1
        if latest and i:
            # Latest updates may matter even without a new maximum.
            if current - values[i-1] >= prominence_fraction * span:
                reasons.append('最新节点明显回升')
            if current >= 0.7 * maximum and current >= 0.9 * values[i-1]:
                reasons.append('最新阶段仍较高（待结合全曲线审阅）')
        if reasons:
            candidates.append({
                'date': row['date'], 'heat': current, 'curve_roles': reasons,
                'previous_heat': values[i-1] if i else None,
                'next_heat': values[i+1] if i+1 < len(rows) else None,
                'period_max': maximum, 'period_min': minimum,
                'prominence': prominence,
                '_rank': (current == maximum, latest, prominence, current),
            })
    chosen = sorted(candidates, key=lambda r: r['_rank'], reverse=True)[:limit]
    for item in chosen:
        item.pop('_rank')
    return sorted(chosen, key=lambda r: r['date'])


def curve_context(series, date):
    rows = sorted(series, key=lambda r: r['date'])
    index = next(i for i, r in enumerate(rows) if r['date'] == date)
    return {'node': rows[index], 'previous': rows[index-1] if index else None,
            'next': rows[index+1] if index+1 < len(rows) else None,
            'period_max': max(r['heat'] for r in rows),
            'period_min': min(r['heat'] for r in rows),
            'selected_role': next((n['curve_roles'] for n in select_nodes(rows, limit=len(rows))
                                   if n['date'] == date), []),
            'note': '曲线角色为筛选建议；事件原因须另查当日证据。平滑曲线的插值不参与峰值判断。'}
