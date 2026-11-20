"""Close transport work without inventing a semantic review of unused candidates."""
from pathlib import Path
import json
import sys
import time

POLICY = 'FINAL_DISPLAY_CLOSEOUT_V1'


def pending_packets(state):
    return [dict(packet_id=p['packet_id'], candidate_ids=ids)
            for p in state['packets']
            if (ids := [v['candidate_id'] for v in p['items']
                        if v['candidate_id'] not in state['judgments']])]


def workers(workspace):
    path = workspace / 'native_worker_registry.json'
    data = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    rows=data.get('workers', data.get('entries', []))
    if not isinstance(rows,list) or any(not isinstance(v,dict) for v in rows):
        raise ValueError('原生写者登记必须是本期逐项记录的列表')
    return rows


def request_closeout(workspace, state, digest, status, reason):
    if state.get('candidate_closeout_policy') != POLICY:
        raise ValueError('旧流程不能通过新候选收尾策略改写原未完成状态')
    pending = pending_packets(state)
    if not pending or not any(v['decision'] == 'keep' for v in state['judgments'].values()):
        raise ValueError('只有存在已审保留样本和未审候选时才能关闭候选任务')
    fingerprint = digest([state['binding'], state['round'], pending, POLICY])
    request = {'fingerprint': fingerprint, 'packets': pending, 'reason': reason}
    state.update(status='CANDIDATE_CLOSEOUT_REQUIRED', candidate_closeout_request=request)
    answer = workspace / 'candidate_closeout_confirmation.json'
    template = {'fingerprint': fingerprint, 'receipts': []}
    answer.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding='utf-8')
    registry = workers(workspace)
    rows = []
    for row in pending:
        matches = [w for w in registry if (w.get('packet_id') or w.get('packet')) == row['packet_id']]
        rows.append({**row, 'registered_writers': matches})
    command = f'"{sys.executable}" -X utf8 "{Path(__file__).with_name("final_sample_review.py")}" close-candidates --workspace "{workspace}"'
    payload = {**request, 'packets': rows, 'answer_file': str(answer), 'next_command': command,
               'instruction': '仅关闭本期列出的未完成候选包。先核对实际原生通知/TaskOutput；仍运行才TaskStop，已完成/失败不重复等，向本期成员shutdown_request。逐包抄真实task_id、terminal_status和原工具回执。确实从未派发的包只能填not_dispatched并确认未启动写者，不能伪造停止。所有未审候选只记未审未入选，不判keep/reject，不增加judged_candidates，不代填原答卷。写确认后立即执行next_command；不再补样，转最终标题核对。',
               'receipt_schema': {'packet_id': '照抄', 'task_id': '已登记真实ID；not_dispatched时null',
                                  'terminal_status': 'stopped|completed|failed|not_dispatched',
                                  'tool_receipt': '含真实task_id的原工具/通知回执文字',
                                  'confirm_no_writer_was_started': '仅not_dispatched填true'}}
    path = workspace / 'candidate_closeout_input.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    result={**status(workspace, state), 'read_now': str(path), 'next_command': command}
    (workspace/'final_review_status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def confirm_closeout(workspace, state, digest):
    if state.get('candidate_closeout_policy') != POLICY or state.get('status') != 'CANDIDATE_CLOSEOUT_REQUIRED':
        raise ValueError('当前不是新流程的候选任务收尾阶段，不能回填旧未完成结果')
    request = state['candidate_closeout_request']
    current = pending_packets(state)
    if digest([state['binding'], state['round'], current, POLICY]) != request['fingerprint']:
        raise ValueError('候选清单已变，不能套用旧停止回执')
    data = json.loads((workspace / 'candidate_closeout_confirmation.json').read_text(encoding='utf-8-sig'))
    rows = data.get('receipts')
    expected = {v['packet_id'] for v in current}
    if (data.get('fingerprint') != request['fingerprint'] or not isinstance(rows, list)
            or len(rows) != len(expected) or any(not isinstance(v, dict) for v in rows)
            or {v.get('packet_id') for v in rows} != expected):
        raise ValueError('须逐包提供当前指纹对应的真实终态回执')
    registry = workers(workspace)
    for row in rows:
        matches = [w for w in registry if (w.get('packet_id') or w.get('packet')) == row['packet_id']]
        if row.get('terminal_status') == 'not_dispatched':
            if matches or row.get('task_id') or row.get('confirm_no_writer_was_started') is not True:
                raise ValueError('已登记写者的包不能称从未派发；须核实际任务并停止')
        elif row.get('terminal_status') in ('stopped', 'completed', 'failed'):
            task_id = row.get('task_id')
            if (not task_id or not any(w.get('task_id') == task_id and w.get('team_name')
                                      and str(w.get('agent_id','')).endswith('@'+w['team_name']) for w in matches)
                    or task_id not in str(row.get('tool_receipt', ''))):
                raise ValueError('终态须绑定本包已登记真实task_id与原工具回执，不能猜ID')
        else:
            raise ValueError('仍运行、未知或无回执的任务不能进入候选关闭状态')
    abandoned = state.setdefault('abandoned_candidates', {})
    for packet in current:
        for cid in packet['candidate_ids']:
            abandoned[cid] = {'packet_id': packet['packet_id'], 'reason': 'UNREVIEWED_NOT_SELECTED_CLOSED',
                              'ai_review_performed': False, 'semantic_decision': None}
    audit = {**request, 'receipts': rows, 'closed_at': time.time(),
             'candidate_count': sum(len(v['candidate_ids']) for v in current),
             'judged_candidates_unchanged': len(state['judgments'])}
    state.setdefault('candidate_closeout_audit', []).append(audit)
    state.setdefault('closed_review_packets', []).extend(state['packets'])
    state.update(packets=[], status='PREPARING_FINAL_AI', finish_reason='VERIFIED_UNUSED_CANDIDATE_CLOSEOUT')
    state.pop('candidate_closeout_request')
    return audit
