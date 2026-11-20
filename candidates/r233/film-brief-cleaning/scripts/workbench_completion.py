"""Separate a positive-count advisory from incomplete or invalid review evidence."""

POSITIVE_TARGET = 10
LEGACY_SHORTFALL = 'POSITIVE_VIEWPOINT_MIN_NOT_MET'
SUPPORTED_POSITIVE_POLICY = 'POSITIVE_MIN_6_TWO_VOICES_V2'
POSITIVE_POLICIES = ('POSITIVE_MIN_6_V1', SUPPORTED_POSITIVE_POLICY)
MIN_POSITIVE = 6


def supported_positive_count(groups):
    count = 0
    for group in groups:
        if group.get('stance') not in ('positive', '正向词倾向'):
            continue
        guidance = group.get('writing_guidance') or {}
        if guidance.get('standalone_eligible') is not True:
            continue
        voices = set(guidance.get('independent_voice_ids', []))
        retained = {sample['candidate_id'] for sample in group['samples']}
        count += len(voices & retained) >= 2
    return count


def positive_count_notice(groups):
    count = sum(g.get('stance') in ('positive', '正向词倾向') for g in groups)
    return {'code': 'POSITIVE_VIEWPOINT_TARGET_NOT_MET', 'severity': 'notice',
            'actual': count, 'target': POSITIVE_TARGET, 'blocks_writing': False,
            'message': f'正面观点实际{count}组，少于参考目标10组；保留有据的独立观点，不为凑数拆组，可继续写稿。'} if count < POSITIVE_TARGET else None


def review_readiness(dataset, review):
    """Accept legacy count-only failures without changing their original status."""
    status = review.get('status')
    policy = dataset.get('quantity_contract')
    if policy == SUPPORTED_POSITIVE_POLICY:
        if supported_positive_count(dataset.get('groups', [])) < MIN_POSITIVE:
            raise ValueError('至少6个正面观点各有两条最终展示的独立有效声音，才能接写稿')
    elif policy=='POSITIVE_MIN_6_V1':
        count=sum(g.get('stance') in ('positive','正向词倾向') and bool(g.get('samples')) for g in dataset.get('groups',[]))
        if count<6:raise ValueError('本版正面至少6个实际非空独立观点是硬门槛，不能用客观或负面补数，不能接写稿')
    if status not in ('FINAL_SELECTED_COMPLETE', LEGACY_SHORTFALL, 'TIME_LIMIT_REACHED'):
        raise ValueError('工作台审样未完成或有数量以外的阻断，不能进入写稿')
    if review.get('pending_count', 0) != 0:
        raise ValueError('工作台仍有待审条目，数量提示不能豁免未完成审核')
    if dataset.get('semantic_verification') != 'FINAL_SELECTED_ONLY' or dataset.get('writing_only'):
        raise ValueError('仅允许承接工作台已审展示数据，不能混入写稿补样')
    groups = dataset.get('groups')
    coverage=dataset.get('candidate_coverage')
    if coverage is not None:
        from candidate_closeout import POLICY
        if (not isinstance(coverage,dict) or coverage.get('policy')!=POLICY
                or coverage.get('scope')!='FINAL_DISPLAY_SAMPLES_ONLY'
                or review.get('candidate_closeout_policy')!=POLICY):
            raise ValueError('新候选关闭策略须由本次新流程生成，不能改写旧部分结果')
        displayed={s['candidate_id'] for g in groups or [] for s in g.get('samples',[])}
        unread=coverage.get('unreviewed_not_selected',[])
        if (not isinstance(unread,list) or any(not isinstance(v,dict) for v in unread)
                or any(v.get('candidate_id') in displayed for v in unread)
                or any(v.get('ai_review_performed') is not False or v.get('semantic_decision') is not None for v in unread)
                or len({v.get('candidate_id') for v in unread})!=len(unread)
                or review.get('unreviewed_cancelled_candidates')!=len(unread)):
            raise ValueError('未审候选须单独留痕，不能进入展示或增加审核计数')
        audited={cid for audit in coverage.get('closeout_audit',[])+coverage.get('stalled_recovery_audit',[])
                 for packet in audit.get('packets',[]) for cid in packet.get('candidate_ids',[])}
        if audited!={v.get('candidate_id') for v in unread}:
            raise ValueError('每条未审候选须能绑定本次实际任务关闭/恢复审计，不能只改pending计数')
        reviewed=coverage.get('reviewed_candidate_count')
        if type(reviewed) is not int or reviewed< len(displayed) or reviewed!=review.get('judged_candidates'):
            raise ValueError('实际审核计数须一致，未审候选不得冒充已审')
    if dataset.get('title_contract')=='FINAL_DISPLAY_EVIDENCE_V2' and (review.get('editorial_status')!='COMPLETE' or dataset.get('final_title_evidence_review',{}).get('status')!='COMPLETE'):
        raise ValueError('本版最终标题证据核对未完成，不能接写稿或以旧版兼容条件豁免')
    if not groups or any(not g.get('samples') for g in groups):
        raise ValueError('工作台为空或仍含空观点组')
    if dataset.get('final_title_evidence_review',{}).get('history_policy')=='ALL_FINAL_RELATIONS_V1':
        for group in groups:
            history=group.get('history_review') or {};proofs=history.get('current_evidence')
            samples={s['candidate_id']:s for s in group['samples']}
            if (history.get('relation') not in ('continuing','developed','opposing','unseen_angle')
                    or history.get('current_evidence_scope')!='FINAL_KEPT_DISPLAY_ONLY'
                    or not isinstance(proofs,list) or not 1<=len(proofs)<=2
                    or any(not isinstance(v,dict) for v in proofs)
                    or any(v.get('candidate_id') not in samples or not isinstance(v.get('quote_snippet'),str)
                           or not 12<=len(v['quote_snippet'])<=140
                           or v['quote_snippet'] not in (samples[v['candidate_id']].get('display_excerpt') or samples[v['candidate_id']]['excerpt']) for v in proofs)):
                raise ValueError('最终跨日关系缺少本组实际留样证据，不能沿用发现seed说明或借其他组样本放行')
    notice = None if policy in POSITIVE_POLICIES else positive_count_notice(groups)
    if status == 'TIME_LIMIT_REACHED':
        # A separately timed continuation may use fully reviewed display data.
        # The original overrun status and duration are never rewritten.
        count = sum(len(g['samples']) for g in groups)
        elapsed, deadline = review.get('elapsed_seconds'), review.get('deadline_seconds')
        if (review.get('pending_count') != 0 or not 1 <= len(groups) <= 16
                or review.get('structure_verification') != 'PASS'
                or review.get('semantic_verification') != 'FINAL_SELECTED_ONLY'
                or review.get('editorial_status') not in ('COMPLETE', 'SKIPPED_INSUFFICIENT_TIME', 'SKIPPED_EDITOR_TIME_LIMIT')
                or review.get('sample_count') != count or review.get('group_count') != len(groups)
                or review.get('finish_reason') != 'INSUFFICIENT_TIME_FOR_ANOTHER_OBSERVED_WAVE'
                or isinstance(elapsed, bool) or isinstance(deadline, bool)
                or not isinstance(elapsed, (int, float)) or not isinstance(deadline, (int, float))
                or not 0 < deadline < elapsed):
            raise ValueError('超时工作台缺少完整审样或仅时间超限的凭据，不能接续写稿')
    if status == LEGACY_SHORTFALL:
        # Older finalizers emitted this terminal status only for a count shortfall.
        # Require its full completion receipt rather than treating any partial data as ready.
        if not notice or not 1 <= len(groups) <= 16:
            raise ValueError('旧版数量不足状态与实际观点数不符，不能按提示交接')
        # Some older status refreshes omit finish-only receipt fields. Missing
        # fields are recomputed from the final display evidence by check_dataset;
        # an explicit failed receipt is never ignored.
        if (review.get('pending_count') != 0 or review.get('structure_verification', 'PASS') != 'PASS'
                or review.get('semantic_verification', dataset.get('semantic_verification')) != 'FINAL_SELECTED_ONLY'
                or review.get('editorial_status') != 'COMPLETE'):
            raise ValueError('旧版数量不足结果缺少完整审样、结构或编辑完成凭据')
        elapsed, deadline = review.get('elapsed_seconds'), review.get('deadline_seconds')
        if (not isinstance(elapsed, (int, float)) or not isinstance(deadline, (int, float))
                or not 0 <= elapsed <= deadline):
            raise ValueError('旧版结果不能证明在原预算内完成，不按数量提示放行')
        if review.get('positive_cluster_count', notice['actual']) != notice['actual']:
            raise ValueError('旧版正面计数与展示数据不一致')
        if review.get('sample_count', sum(len(g['samples']) for g in groups)) != sum(len(g['samples']) for g in groups):
            raise ValueError('旧版样本计数与展示数据不一致')
    return {'ready': True, 'original_status': status,
            'legacy_count_only_completion': status == LEGACY_SHORTFALL,
            'budget_overrun': status == 'TIME_LIMIT_REACHED',
            'notices': [notice] if notice else []}
