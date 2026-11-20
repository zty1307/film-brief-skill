"""One bounded clustering repair before publication; no writing-side source supplementation."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json,time,sys
from workbench_completion import MIN_POSITIVE, POSITIVE_POLICIES, SUPPORTED_POSITIVE_POLICY

POSITIVE_MIN=MIN_POSITIVE
POLICY=SUPPORTED_POSITIVE_POLICY
PRELOCK_RECALL_MIN_SECONDS=900
LATE_RECALL_LIMIT=56


def editor_reserve(state, default):
    """Estimate repeated editorial work from this run, keeping a safety margin."""
    title_seconds=state.get('editorial_elapsed_seconds')
    claim_seconds=state.get('editorial_claim_audit_elapsed_seconds')
    if not (title_seconds and claim_seconds):
        return default
    return min(default, max(330, title_seconds+claim_seconds+45))

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def positive_counts(state):
    counts=Counter(v['group_index'] for v in state['judgments'].values() if v['decision']=='keep')
    return {i:counts[i] for i,g in enumerate(state['pool']) if g['stance'] in ('positive','正向词倾向')}

def available_discovery_excerpts(state,sources,occupied,used_seeds):
    """Revisit the bounded discovery pool, without guessed semantic labels.

    A source already displayed cannot acquire a second viewpoint owner. Other
    original excerpts remain visible; list position must not hide the tail.
    This changes recall only, never the keep decision or audit requirements.
    """
    from source_policy import source_keys
    from viewpoint_discovery import DISCOVERY_INPUT_LIMIT
    fields=('excerpt_id','source_ref','excerpt','context','source_channel','verified_mainstream','history_candidates')
    available=[e for e in state['discovery_excerpts']
               if not source_keys(sources[e['source_id']]) & occupied]
    available.sort(key=lambda e:(e['excerpt_id'] in used_seeds,e['excerpt_id']))
    return [{k:e[k] for k in fields if k in e} for e in available[:DISCOVERY_INPUT_LIMIT]]


def extra_coverage_excerpts(workspace, state, sources, occupied, limit=32):
    """Take an untouched, aspect-diverse slice of the same frozen raw pool."""
    from rapid_film_workbench import display_excerpt, source_exclusion, source_priority
    from report_evidence import release_notice_only
    from source_policy import sample_keys, source_keys
    from viewpoint_discovery import displayable_discovery_candidate
    from target_reference import WORK_REFERENCE_CUE

    used=set(occupied)
    seen_ids={v['source_id'] for v in state['discovery_excerpts']}
    seen_ids.update(v['source_id'] for v in state['judgments'].values())
    seen_ids.update(state.get('supplement_offered_source_ids', []))
    prior_entries=state.get('coverage_seed_archive',[])+state.get('coverage_extra_excerpts',[])
    if state.get('coverage_repair_attempts'):
        seen_ids.update(v['source_id'] for v in prior_entries)
    last_serial=max((int(v['excerpt_id'][1:]) for v in state['discovery_excerpts']+prior_entries),default=0)
    for source_id in seen_ids:
        used.update(source_keys(sources[source_id]))
    raw_groups=read(workspace/'candidate_pool.json')
    groups=raw_groups if state.get('locked_candidate_ids') else [g for g in raw_groups if g['stance']!='负向词倾向' and g.get('probe_kind')!='social_criticism']
    groups.sort(key=lambda g:g['stance']!='正向词倾向')
    lanes=[(g,iter(g['candidates'])) for g in groups]
    extras=[]; terms=[t for t in state.get('terms',[state['target']]) if t]
    for _ in range(limit if state.get('locked_candidate_ids') else 3):
        for group, rows in lanes:
            for candidate in rows:
                record=sources[candidate['source_id']]
                if candidate['source_id'] in seen_ids or source_keys(record)&used:
                    continue
                selectable=displayable_discovery_candidate(candidate,record)
                if selectable is None:
                    continue
                text=str(record['data'].get(selectable['text_field']) or '')
                a,b=selectable['start'],selectable['end']
                quote=text[a:b]
                visible=display_excerpt(quote)
                context=text[max(0,a-60):min(len(text),b+60)]
                direct=any(term in visible for term in terms)
                if not direct and not (WORK_REFERENCE_CUE.search(visible)
                                       and any(term in display_excerpt(context) for term in terms)):
                    continue
                if release_notice_only(visible) or source_exclusion(record):
                    continue
                keys=sample_keys({**selectable,'excerpt':quote},record)
                if keys&used:
                    continue
                serial=last_serial+len(extras)+1
                extras.append({**selectable,'excerpt_id':f'E{serial:03d}','source_ref':f'X{serial:03d}',
                               'excerpt':quote,'context':'' if direct else context,
                               'rule_aspect':group['aspect'],'rule_stance':group['stance'],
                               'source_channel':record.get('channel',''),'coverage_only':True,
                               'verified_mainstream':bool(source_priority(record)['media_authority_rank'])})
                used.update(keys)
                break
            if len(extras)>=limit:
                break
        if len(extras)>=limit:
            break
    context=state.get('discovery_history_context')
    from evidence_quality import old_candidates
    hints=old_candidates(extras,context,state['target'])
    for item in extras:
        item['history_candidates']=hints.get(item['excerpt_id'],[])
    return extras


def route_attachment(state, entry, support, sources, index, used, digest):
    """Create one ordinary review candidate bound to an offered exact raw span."""
    import hashlib
    import re
    from rapid_film_workbench import excerpt_length_error, source_exclusion
    from source_policy import sample_keys
    fields = ('source_id', 'start', 'end', 'text_field', 'identity', 'score', 'excerpt_hash')
    candidate = {key: entry[key] for key in fields}
    record = sources[candidate['source_id']]
    body = str(record['data'].get(candidate['text_field']) or '')
    start, end = candidate['start'], candidate['end']
    quote = body[start:end]
    if quote != entry['excerpt'] or source_exclusion(record):
        raise ValueError(f"add_evidence {entry['excerpt_id']} 无法绑定原表或来源已排除。")
    if support not in quote:
        left, right = max(0,start-60), min(len(body),end+60)
        local = body[left:right]
        found = local.find(support)
        if local != entry.get('context') or found < 0 or local.find(support,found+1)>=0:
            raise ValueError(f"add_evidence {entry['excerpt_id']} 须用excerpt或随附context内唯一的连续实际评价，不能拼接或补字。")
        start, end = min(start,left+found), max(end,left+found+len(support))
        quote = body[start:end]
        candidate.update(start=start,end=end,
                         excerpt_hash=hashlib.sha256(re.sub(r'\s+','',quote).encode('utf-8')).hexdigest())
    if excerpt_length_error(quote, record.get('channel')):
        raise ValueError(f"add_evidence {entry['excerpt_id']} 含支持句的原文连续窗口超出可展示边界。")
    keys = sample_keys({**candidate, 'excerpt': quote}, record)
    if keys & used:
        raise ValueError(f"add_evidence {entry['excerpt_id']} 已被使用，换独立原句或删除该项。")
    span = ('source_id', 'text_field', 'start', 'end')
    existing = next((row for row in state['pool'][index]['candidates']
                     if all(row[key] == candidate[key] for key in span)), None)
    if existing is not None:
        cid = digest([state['binding'],index,existing])[:20]
        previous = state['judgments'].get(cid, {})
        if (cid in state.get('coverage_options', ()) and previous.get('decision') == 'reject'
                and cid not in state.get('abandoned_candidates', {})
                and cid not in state.get('length_ineligible_candidates', {})
                and not previous.get('script_exclusion_reason') and not previous.get('duplicate_of')):
            used.update(keys)
            return existing, False
        if any(cid in state.get(key,{}) for key in
               ('judgments','abandoned_candidates','length_ineligible_candidates')):
            raise ValueError(f"add_evidence {entry['excerpt_id']} 已有处理结论。"
                             "如在review_options中可申请重审，其他情况换新原句或删除该项。")
        used.update(keys)
        return existing, False
    used.update(keys)
    candidate['selection_options'] = [{'start':candidate['start'], 'end':candidate['end'], 'text':quote}]
    return candidate, True


def response(workspace,state,status):
    return {**status(workspace,state),'read_now':str(workspace/'coverage_repair_input.json'),
            'answer_file':str(workspace/'coverage_repair_answer.json'),
            'next_command':f'"{sys.executable}" -X utf8 "{Path(__file__).with_name("final_sample_review.py")}" repair-collect --workspace "{workspace}"'}

def prepare(workspace,state,sources,digest,status):
    if state.get('quantity_contract') not in POSITIVE_POLICIES:return None
    if state.get('status')=='COVERAGE_REPAIR_REQUIRED':return response(workspace,state,status)
    if state.get('locked_candidate_ids'):
        from supplement_routing import prepare as prepare_supplement
        return prepare_supplement(workspace,state,sources,digest,status)
    attempts=state.get('coverage_repair_attempts',int(bool(state.get('coverage_repair_attempted'))))
    enrichment = bool(state.get('locked_candidate_ids'))
    if state.get('supplement_closed') or (not enrichment and (state.get('coverage_repair_closed') or attempts>=2)):
        return None
    counts=positive_counts(state)
    from source_balance import channel
    channel_counts={i:Counter(channel(sources[v['source_id']]) for v in state['judgments'].values()
                             if v['group_index']==i and v['decision']=='keep') for i in range(len(state['pool']))}
    guides=state.get('editorial_writing_guidance',{})
    retained={i:{cid for cid,v in state['judgments'].items()
                 if v['group_index']==i and v['decision']=='keep'} for i in counts}
    confirmed={i for i,ids in retained.items()
               if guides.get(str(i),{}).get('standalone_eligible') is True
               and len(ids & set(guides[str(i)].get('independent_voice_ids',[])))>=2}
    # Only the current final evidence gate can confirm a supported positive.
    # Preserve count-only behavior for older contracts without this editor.
    if state.get('quantity_contract')!=POLICY:
        confirmed={i for i,n in counts.items() if n}
    shortfall=max(0,POSITIVE_MIN-len(confirmed))
    preferred_min = state.get('workbench_positive_preferred_min', POSITIVE_MIN)
    preferred_shortfall = max(0, preferred_min - len(confirmed))
    # The optional pass shares the original budget and never blocks a finished board.
    protected = state.get('protect_ready_workbench', False)
    if protected and not enrichment and not shortfall:
        return None
    kept_counts = {index: sum(channel_counts[index].values()) for index in channel_counts}
    sparse = {index for index, count in (kept_counts if enrichment else counts).items() if 0 < count < 10}
    optional_backfill = preferred_min > POSITIVE_MIN and (enrichment or not attempts) and bool(sparse or preferred_shortfall)
    weak = set(counts) - confirmed if shortfall or preferred_shortfall else set()
    one_voice={i for i in weak if counts[i]==1} if shortfall else set()
    if optional_backfill:
        weak |= sparse
    if enrichment:
        from locked_supplement import accepted_samples
        published = set(state['locked_group_indices']) | {int(i) for i in state.get('supplement_groups_approved',{})}
        additions = accepted_samples(state,sources)
        kept_counts = {i:state['supplement_baseline_counts'].get(str(i),0)+len(additions.get(i,[])) for i in published}
        confirmed = set(state['supplement_baseline_supported']) | {int(i) for i in state.get('supplement_groups_approved',{})}
        counts = {i:kept_counts[i] for i in published if state['pool'][i]['stance'] in ('positive','正向词倾向')}
        preferred_shortfall = max(0,preferred_min-len(confirmed))
        definitions_count=len(state['pool'])
        new_slots = max(0,min(3, 10-len(confirmed),16-definitions_count))
        if state.get('final_history_check_policy') or state.get('discovery_history_context'):
            new_slots=0
        weak = {i for i,n in kept_counts.items() if n<10}
        optional_backfill = bool(weak or new_slots)
        shortfall = 0
        if not optional_backfill:
            state['supplement_stop_reason']='GOALS_REACHED' if len(confirmed)>=8 else 'NO_VIEWPOINT_SLOTS'
            return None
    imbalanced={i for i in confirmed if channel_counts[i]
                and channel_counts[i]['今日头条']*2>sum(channel_counts[i].values())}
    if enrichment:
        imbalanced &= weak
    if not (shortfall or optional_backfill or (imbalanced and not attempts)):
        return None
    remaining=state['started_epoch']+state['deadline_seconds']-time.time()
    from supplement_schedule import DISCOVERY_AND_REVIEW_MIN_SECONDS
    if remaining<(DISCOVERY_AND_REVIEW_MIN_SECONDS if enrichment else PRELOCK_RECALL_MIN_SECONDS):
        if enrichment:state['supplement_stop_reason']='TIME_RESERVED_FOR_REVIEW_AND_PUBLICATION'
        state['coverage_repair_skip_reason']='剩余清洗时间不足以安排补缺、完整末端核对与收尾，保留真实缺口'
        return None
    # Keep original candidates and decisions. The model requests re-review, never overwrites keep.
    options=[]
    from source_policy import sample_keys
    occupied=set()
    tried=set(state.get('supplement_offered_candidate_ids', ())) if enrichment else (set(state.get('coverage_allowed_ids',[])) if attempts else set())
    known_hashes={}
    previous_findings=[]
    for cid, judgment in state['judgments'].items():
        if enrichment or judgment['decision']=='keep' or cid in tried:
            occupied.update(sample_keys(judgment,sources[judgment['source_id']]))
        if cid in tried:
            previous_findings.append({'group_index':judgment['group_index'],
                'decision':judgment['decision'],'reason':judgment.get('reason','')})
        for name in ('excerpt_hash','proposal_excerpt_hash'):
            if value := judgment.get(name):
                known_hashes.setdefault((judgment['group_index'],value),set()).add(cid)
    for i,g in enumerate(state['pool']):
        if i not in weak|imbalanced:continue
        for c in g['candidates']:
            cid=digest([state['binding'],i,c])[:20];old=state['judgments'].get(cid)
            if cid in tried or (enrichment and cid in state['judgments']):continue
            if cid in state.get('abandoned_candidates',{}) or cid in state.get('length_ineligible_candidates',{}):continue
            if old and (old['decision']=='keep' or old.get('script_exclusion_reason') or old.get('duplicate_of')):continue
            record=sources[c['source_id']]
            body=str(record['data'].get(c['text_field']) or '')
            excerpt=body[c['start']:c['end']]
            if sample_keys({**c,'excerpt':excerpt},record)&occupied:
                continue
            if known_hashes.get((i,c.get('excerpt_hash')),set())-{cid}:
                continue
            from rapid_film_workbench import source_exclusion
            if source_exclusion(record):continue
            if i in imbalanced and i not in weak and channel(record)=='今日头条':continue
            from target_reference import local_binding
            issue=bool(old and any(word in old.get('reason','') for word in ('指代','锚点','预告','物料','正片')))
            options.append((i not in one_voice,(kept_counts[i] if optional_backfill else 0),not issue,channel_counts[i][channel(record)],channel(record)=='今日头条',-c.get('score',0),
                {'candidate_id':cid,'group_index':i,'excerpt':body[c['start']:c['end']],
                 'channel':channel(record),'previous_decision':old.get('decision') if old else None,
                 'previous_reason':old.get('reason') if old else None,'work_reference':local_binding(c,record,state)}))
    picked=[];per_group=Counter();seen=set()
    ordered_options=sorted(options,key=lambda v:v[:-1])
    if shortfall:
        # Offer distinct weak groups in rotation before consuming slots on any one claim.
        by_group={}
        for *_,row in ordered_options:
            by_group.setdefault(row['group_index'],[]).append(row)
        ordered_options=[(rows[offset],) for offset in range(6)
                         for rows in by_group.values() if offset<len(rows)]
    for *_,row in ordered_options:
        if per_group[row['group_index']]>=6 or row['candidate_id'] in seen:continue
        picked.append(row);seen.add(row['candidate_id']);per_group[row['group_index']]+=1
        if len(picked)>=(12 if attempts and not enrichment else 24):break
    definitions=read(workspace/'viewpoint_definitions.json')
    used_seeds={sid for g in definitions['clusters'] for sid in g['seed_ids']}
    seeds=available_discovery_excerpts(state,sources,occupied,used_seeds)
    if enrichment:
        from source_policy import source_keys
        offered=set(state.get('supplement_offered_source_ids', []))
        occupied.update(key for sid in offered for key in source_keys(sources[sid]))
        seeds=available_discovery_excerpts(state,sources,occupied,used_seeds)
    extras=extra_coverage_excerpts(workspace,state,sources,occupied,limit=40 if enrichment else 32) if shortfall or optional_backfill else []
    from viewpoint_discovery import DISCOVERY_INPUT_LIMIT
    fields=('excerpt_id','source_ref','excerpt','context','source_channel','verified_mainstream','history_candidates')
    extra_rows=[{key:entry[key] for key in fields if key in entry} for entry in extras]
    if enrichment:
        seeds=seeds[:24]+extra_rows[:48-min(len(seeds),24)]
        if not (picked or seeds):
            state['supplement_stop_reason']='FRESH_CANDIDATES_EXHAUSTED'
            return None
    elif attempts:
        # Retain the genuinely fresh excerpts, then favor matches to weak groups.
        aliases={
            i:tuple(term for term in definitions['clusters'][i].get('required_any',[]) if len(term)>=2)
            for i in one_voice
        }
        def weak_match(row):
            sample=row['excerpt']+row.get('context','')
            return max((sum(term in sample for term in terms) for terms in aliases.values()),default=0)
        seeds=extra_rows+sorted(seeds,key=weak_match,reverse=True)[:LATE_RECALL_LIMIT-len(extra_rows)]
    else:seeds=extra_rows+seeds[:DISCOVERY_INPUT_LIMIT-len(extra_rows)]
    if shortfall and not (picked or seeds):
        state['coverage_repair_skip_reason']='当前原表可召回的候选已经用尽'
        return None
    from model_packet import voice_identity,voice_refs
    prior_entries=state.get('coverage_seed_archive',[])+state.get('coverage_extra_excerpts',[]) if attempts else []
    all_seeds=state['discovery_excerpts']+prior_entries+extras
    author_refs=voice_refs(all_seeds)
    for row in seeds:
        row['voice_ref']=author_refs[row['excerpt_id']]
    author_lookup={voice_identity(row):author_refs[row['excerpt_id']] for row in all_seeds}
    already_kept_voices={i:sorted({author_lookup[voice_identity(item)]
        for item in state['judgments'].values()
        if item['group_index']==i and item['decision']=='keep' and voice_identity(item) in author_lookup})
        for i in weak|imbalanced}
    fingerprint=digest([state['binding'],state['judgments'],picked,seeds,attempts])
    original=read(workspace/'viewpoint_discovery_input.json')
    cluster_schema=deepcopy(original['cluster_schema'])
    cluster_schema['stance']='positive'
    payload={'fingerprint':fingerprint,'positive_minimum':POSITIVE_MIN,
        'confirmed_positive_group_indices':sorted(confirmed),'confirmed_positive_count':len(confirmed),
        'positive_shortfall':shortfall,'preferred_positive_shortfall':preferred_shortfall,
        'preferred_positive_minimum':preferred_min,'positive_kept_counts':counts,
        'kept_counts':kept_counts,
        'final_titles':state.get('editorial_titles',{}),
        'final_core_mechanisms':state.get('editorial_core_mechanisms',{}),
        'existing_viewpoints':[{'id':g['id'],'title':g['title'],'category':g['category'],'core_mechanism':g['core_mechanism'],'seed_ids':g['seed_ids']} for g in definitions['clusters']],
        'channel_counts':channel_counts,'review_options':picked,'discovery_excerpts':seeds,
        'attachment_groups':sorted(weak | imbalanced),'existing_voice_refs':already_kept_voices,
        'coverage_attempt':attempts+1,
        'prior_attachment_findings':previous_findings,
        'confirmed_core_quotes':{i:guides[str(i)].get('preferred',[]) for i in sorted(confirmed)
                                 if str(i) in guides},
        'weak_core_quotes':{i:guides[str(i)].get('preferred',[]) for i in sorted(weak)
                           if str(i) in guides},
        'new_raw_recall_count':len(extras),
        'final_editor_findings':{i:{k:g[k] for k in ('eligible','standalone_eligible','reason') if k in g}
                                 for i,g in guides.items()},
        'discovery_rules':original.get('rules',[]),'new_cluster_schema':cluster_schema,'allowed_categories':__import__('cluster_contract').CATEGORIES,'allowed_title_verbs':__import__('viewpoint_discovery').TITLE_VERBS,'previous_viewpoints':original.get('previous_viewpoints',[]),
        'history_selection_schema':original.get('history_selection_schema'),
        'answer_schema':{'fingerprint':fingerprint,'recheck':[{'candidate_id':'从review_options选，最多12条','reason':'至少12字说明为何需要重审'}],
            'add_evidence':[{'group_index':'attachment_groups中的整数编号，如0，不是字符串，不是簇ID','excerpt_id':'discovery_excerpts中的E编号','quote_snippet':'excerpt或随附context内连续8—140字实际评价','reason':'至少12字，说明原句支持同一具体评价'}],
            'new_clusters':[],'cluster_boundaries':[{'left':'新增或现有簇ID','right':'另一簇ID','distinction':'至少12字实际区别；至少一侧为新增簇，不重复已有对'}],'history_selection':None,'shortfall_reason':'至少20字解释缺口及本次有依据的补救；确无材料如实说明'},
        'rules':'positive_shortfall是硬下限缺口。先保住六个双证据正面组，再按positive_kept_counts从少到多为已有组补同核心的独立样本，每组最多10条。preferred_positive_shortfall大于零时，同时检查是否有两份独立有效原话支撑的不同观点，有据才新增，优先达到八个。头条失衡时也查同等相关的其他来源。new_clusters的stance只能为positive，旧发现规则中的客观和负面只用于识别排除项。以final_titles和final_core_mechanisms为准检查交叉，勿拆总括与子点。discovery_excerpts仍只来自同一份原表。source_ref以X开头的是本次从未送看的追加原句，与初始池其余原句合计不超过144条；先找独立评价的双证据，不把未知来源当成已审。原句是否有实际评价由你判断。这是限时聚类补缺。正面至少6个独立实际非空组；不按演员拆近义点、不改立场、不编观点。先查空弱组的指代/预告误拒和缺失渠道；recheck只申请进入常规审样，不能直接判keep。最多新增3个确有当前E原句支持且与既有观点不交叉的正面观点，按原发现schema填全字段，风险对写cluster_boundaries。有历史按原规则核对并更新history_selection。旧观点、旧keep不改。无支持就空数组并说明。稿件必须待补审、去重和最终标题核对完成后只读最终工作台。'}
    payload['rules']+='\n已有组差一份声音时，可把本包尚未用于其他组的E原句以add_evidence送进原组常规审核。仅当可找出对原核心有直接证据时才给旧组补样。用confirmed_core_quotes逐字检查边界：如果某条其实支持已确认组，不能用它补到相邻弱组。现有弱组找不到独立证据时优先寻找另一确有双证据的具体评价。证据按完整观点核对，不按一个细节卡死：同一集中评价主题可以由互补的具体子评价支撑，如演唱听感与歌曲人物适配共同支撑原声音乐感染力，服饰与置景共同支撑同一视觉风格。每份声音都须有对本主题的实际评价，每个标题分句至少一条具体证据，全段至少两份独立声音；不要求每条引文重复同一细节或同时覆盖所有分句。不能仅因都提到演员、音乐或预告就合组；不能把无关问题装进泛泛的整体很好，也不能与别段重复核心评价。独立来源同向互证与不同细节互补均可，纯资讯、名单、期待及同稿转载均不能补足证据。add_evidence与recheck合计最多12条，每组最多新增两条E。只改候选，原有审样、组归属和末端核对不变。'
    from report_evidence import facet_hint_index
    payload['facet_hint_index']=facet_hint_index(seeds)
    payload['rules']+='\n有缺口时可用facet_hint_index定位本包中尚未用于已有判断的具体意见；先回读完整E原句和作者编号再决定，索引不等于通过。'
    payload['rules']+='\nnew_clusters的两个主要支持E原句须来自不同voice_ref，同一作者不同记录只算一份；同时仍须查同稿与观点相同。不能重新提交与现有或已合并簇seed集合完全相同的观点，缺证据的旧组可用add_evidence。'
    payload['rules']+='\nadd_evidence须逐字提交实际评价quote_snippet，不能只用reason描述。若评价落在同一E紧邻的随附context里，选其中连续原话，脚本只把同一原表来源连续展开到含评价句，再交逐条审核。单独context不是已审证据。new_clusters的命名证据仍只来自excerpt。'
    payload['rules']='补缺先看已有组。按final_editor_findings找只差独立声音、核心判断仍成立的组，把review_options中的实际原句与previous_reason对读。单条具体评价可以只支持核心机制的一个实例。若原句直接有据、旧拒因只是未覆盖标题的其余对象或细节，优先申请recheck，并写清哪段原话支持什么判断。另有指代、立场、仅介绍设定或期待等实质缺陷时仍排除。已有合格原句优先于泛泛的新候选；预计补回后已满足缺口时不另开近义点。旧样本确实不能补足时，再从现有原表找有双独立证据的不同判断。'+'\n'+payload['rules']
    if enrichment:
        payload['confirmed_core_quotes']={}
        payload['weak_core_quotes']={}
        payload['prior_attachment_findings']=[]
        payload['coverage_attempt']=len(state.get('supplement_batches',[]))+1
        payload['new_viewpoint_slots']=new_slots
        payload['attachment_groups']=sorted(weak,key=lambda i:(kept_counts[i],i))
        payload['rules']='仅处理本次新材料。工作台的旧观点、成段判断和样本均已锁定，不读旧摘录，不复审。按attachment_groups的已留样本数从少到多寻找直接支持同一核心判断的独立样本，不把同平台同稿重复算作新样本。recheck字段只用于从review_options选择从未审过的候选；add_evidence字段用于将本包E原句分给已有组。两字段合计最多12条，每组最多4条，组满10条停止。new_viewpoint_slots为新增正面观点数量上限，为0时填空；其余按原簇schema，须有两份独立作者的连续原话和具体评价，与现有观点边界清楚。cluster_boundaries仅列至少一端为新簇的风险对。每条语义、来源、字数仍由常规审核确认。没有真实合格的候选就留空。'
        from supplement_schedule import record_batch
        record_batch(state,picked,seeds,{e['excerpt_id']:e['source_id'] for e in all_seeds})
        state['supplement_new_slots']=new_slots
    if shortfall:
        payload['one_voice_groups']=sorted(one_voice)
        payload['rules']+='\n正面还未到六组时，本次只保底：优先给one_voice_groups中核心成立的组找第二份独立评价，再看可单列的其他判断。每个拟补正面组若有两条来源不同、都可能支持该组的备选，请同时送审；也可给另一处真实缺口准备后备。有明确证据才送，只剩一条可用候选时据实提交。达到六组后的来源均衡和扩充另走增量。'
    if attempts and not enrichment:
        payload['answer_schema']['recheck'][0]['candidate_id']='从review_options选，重审和补证合计最多6条'
        payload['rules']+='\n这是最后一次小范围补缺。首轮补样后仍有独立观点缺口，按prior_attachment_findings避开已审来源。最多6条重审和补证合计、3个确有双证据的新增观点，按实际缺口适量选择。不要把上一轮只增加留样的近义材料再次送审。原方向无证据就查实际不同的评价，不能重审刚拒绝的来源或拆旧判断。'
    if attempts or enrichment:
        archive_number=f'supplement-{len(state.get("supplement_batches",[]))}' if enrichment else f'attempt-{attempts}'
        for name in ('coverage_repair_input.json','coverage_repair_answer.json','coverage_repair_feedback.json'):
            previous=workspace/name
            if previous.is_file():
                previous.replace(previous.with_name(f'{previous.stem}.{archive_number}.json'))
    if enrichment:
        offered_eids={row['excerpt_id'] for row in seeds}
        extras=[row for row in extras if row['excerpt_id'] in offered_eids]
    from model_packet import write_packet
    write_packet(workspace/'coverage_repair_input.json',payload)
    state.update(status='COVERAGE_REPAIR_REQUIRED',coverage_repair_binding=fingerprint,
                 coverage_options=[r['candidate_id'] for r in picked],coverage_seed_ids=[e['excerpt_id'] for e in seeds],
                 coverage_seed_archive=prior_entries,coverage_extra_excerpts=extras,
                 coverage_attachment_groups=sorted(weak | imbalanced))
    return response(workspace,state,status)

def apply(workspace,state,answer,sources,digest):
    if state.get('supplement_routing_contract')=='ROUTE_BY_IDS_V1':
        from supplement_routing import collect
        return collect(workspace,state,answer,sources,digest,apply_coverage)
    return apply_coverage(workspace,state,answer,sources,digest)


def apply_coverage(workspace,state,answer,sources,digest):
    attempts=state.get('coverage_repair_attempts',int(bool(state.get('coverage_repair_attempted'))))
    enrichment=bool(state.get('locked_candidate_ids'))
    if state.get('status')!='COVERAGE_REPAIR_REQUIRED' or (not enrichment and (state.get('coverage_repair_closed') or attempts>=2)):
        raise ValueError('本期没有可执行的补缺请求')
    evidence_limit=12 if enrichment or not attempts else 6
    cluster_limit=state['supplement_new_slots'] if enrichment else 3
    if answer.get('fingerprint')!=state.get('coverage_repair_binding'):raise ValueError('补缺指纹不符')
    if len(str(answer.get('shortfall_reason') or '').strip())<20:raise ValueError('须说明实际缺口及补救依据')
    rows=answer.get('recheck',[]);new=answer.get('new_clusters',[])
    if not isinstance(rows,list) or len(rows)>evidence_limit or any(not isinstance(r,dict) or r.get('candidate_id') not in state['coverage_options'] or len(str(r.get('reason','')))<12 for r in rows):raise ValueError(f'只能申请重审给定候选，最多{evidence_limit}条且说明依据')
    if len({r['candidate_id'] for r in rows})!=len(rows):raise ValueError('重审ID重复')
    if not isinstance(new,list) or len(new)>cluster_limit:
        raise ValueError(f'new_clusters须为数组，最多{cluster_limit}个有据的独立正面观点')
    invalid=[f'new_clusters[{i}] id={g.get("id")!r} stance={g.get("stance")!r}'
             if isinstance(g,dict) else f'new_clusters[{i}] 不是对象'
             for i,g in enumerate(new) if not isinstance(g,dict) or g.get('stance')!='positive']
    if invalid:
        raise ValueError('；'.join(invalid)+'。补缺只收positive。删除所列非正面新增点，并从cluster_boundaries及history_selection移除其关联；不要改立场充数。')
    if any(not set(g.get('seed_ids',[]))<=set(state['coverage_seed_ids']) for g in new):raise ValueError('新增观点只能用本次提供的E证据')
    attachments=answer.get('add_evidence',[])
    attachment_issues=[]
    if not isinstance(attachments,list):
        attachment_issues.append('add_evidence须为数组')
    else:
        if len(attachments)+len(rows)>evidence_limit:
            attachment_issues.append(f'add_evidence与recheck合计最多{evidence_limit}条')
        for offset,row in enumerate(attachments):
            prefix=f'add_evidence[{offset}]'
            if not isinstance(row,dict):
                attachment_issues.append(prefix+'须为对象');continue
            group=row.get('group_index')
            if type(group) is not int:
                attachment_issues.append(prefix+f'.group_index当前为{group!r}（{type(group).__name__}），须填JSON整数，例如0而不是"0"；合法组为{state.get("coverage_attachment_groups",[])}')
            elif group not in state.get('coverage_attachment_groups',[]):
                attachment_issues.append(prefix+f'.group_index={group}不在合法组{state.get("coverage_attachment_groups",[])}内')
            eid=row.get('excerpt_id')
            if not isinstance(eid,str) or eid not in state['coverage_seed_ids']:
                attachment_issues.append(prefix+f'.excerpt_id={eid!r}不在当前discovery_excerpts中，须选择本包实际E编号')
            quote=row.get('quote_snippet')
            if not isinstance(quote,str) or not 8<=len(quote)<=140:
                attachment_issues.append(prefix+f'.quote_snippet须为8—140字连续原句，当前长度={len(quote) if isinstance(quote,str) else "非字符串"}')
            if len(str(row.get('reason','')).strip())<12:
                attachment_issues.append(prefix+'.reason须至少12字说明原句支持同一核心的依据')
    if attachment_issues:
        raise ValueError('只修以下补证字段，保留已通过内容：'+chr(10)+chr(10).join(attachment_issues))
    selected_eids={row['excerpt_id'] for row in attachments}
    per_group_limit = 4 if state.get('enrichment_started') else 2
    if (len(selected_eids)!=len(attachments)
            or max(Counter(row['group_index'] for row in attachments).values(),default=0)>per_group_limit
            or any(selected_eids.intersection(group.get('seed_ids',[])) for group in new)):
        raise ValueError(f'add_evidence同一E只归一组，每组至多{per_group_limit}条；不能同时作新增组证据')
    staged=deepcopy(state);added=[]
    supplemental=state.get('coverage_seed_archive',[])+state.get('coverage_extra_excerpts',[])
    excerpts={e['excerpt_id']:e for e in state['discovery_excerpts']+supplemental}
    if new:
        old=read(workspace/'viewpoint_definitions.json')
        from model_packet import voice_identity
        seen_seeds={frozenset(group['seed_ids']):group['id'] for group in old['clusters']}
        duplicate_findings=[]
        for group in new:
            seeds=frozenset(group.get('seed_ids',[]))
            if seeds in seen_seeds:
                duplicate_findings.append(f"{group.get('id')}复用了已有簇{seen_seeds[seeds]}的全部E编号")
            if len({voice_identity(excerpts[eid]) for eid in seeds})<2:
                duplicate_findings.append(f"{group.get('id')}的E原句不足两份不同作者声音")
            seen_seeds[seeds]=group.get('id')
        if duplicate_findings:
            raise ValueError('new_clusters有重复或同作者证据：'+'；'.join(duplicate_findings)
                             +'。移除相应候选或换实际独立有据观点，清理cluster_boundaries与history_selection相关项。')
        joined={**read(workspace/'viewpoint_discovery_answer.json'),'clusters':old['clusters']+new,
                'cluster_boundaries':old.get('cluster_boundaries',[])+answer.get('cluster_boundaries',[]),
                'evidence_shortfall_reason':answer['shortfall_reason']}
        if state.get('discovery_history_context'):joined['history_selection']=answer.get('history_selection')
        scratch=workspace/'coverage_routing_validation';scratch.mkdir(exist_ok=True)
        trial=deepcopy(state);trial.update(status='VIEWPOINT_DISCOVERY_REQUIRED',pool=read(workspace/'candidate_pool.json'))
        trial['discovery_excerpts'].extend(supplemental)
        if trial.get('discovery_history_context'):
            trial['discovery_history_context'].setdefault('seed_history_candidates',{}).update(
                {e['excerpt_id']:e['history_candidates'] for e in supplemental})
        from viewpoint_discovery import apply_discovery
        apply_discovery(scratch,trial,joined,sources,digest)
        added=trial['pool'][len(state['pool']):]
        staged['pool'].extend(added)
        staged['coverage_added_definitions']=new
        staged['coverage_discovery_continuity']=trial.get('discovery_continuity')
        if trial.get('discovery_continuity'):staged['discovery_continuity']=trial['discovery_continuity']
    allowed=[];rechecks=[]
    for row in rows:
        cid=row['candidate_id'];old=staged['judgments'].get(cid)
        if old:
            if state.get('locked_candidate_ids') or old['decision']!='reject':
                raise ValueError('已有审核结果的样本不能进入本次追加')
            rechecks.append({'candidate_id':cid,'previous':old,'request_reason':row['reason']})
            del staged['judgments'][cid]
        allowed.append(cid)
    if attachments:
        from source_policy import sample_keys,source_keys
        used={key for item in state['judgments'].values() if state.get('locked_candidate_ids') or item['decision']=='keep'
             for key in sample_keys(item,sources[item['source_id']])}
        chosen=set(allowed)
        for index,group in enumerate(state['pool']):
            for item in group['candidates']:
                if digest([state['binding'],index,item])[:20] in chosen:
                    used.update(source_keys(sources[item['source_id']]))
        for group in new:
            for eid in group['seed_ids']:
                used.update(source_keys(sources[excerpts[eid]['source_id']]))
        for row in attachments:
            index=row['group_index']
            item,is_new=route_attachment(state,excerpts[row['excerpt_id']],row['quote_snippet'],sources,index,used,digest)
            if is_new:
                staged['pool'][index]['candidates'].append(item)
            cid = digest([state['binding'],index,item])[:20]
            if cid in staged['judgments']:
                prior = staged['judgments'].pop(cid)
                rechecks.append({'candidate_id':cid,'previous':prior,'request_reason':row['reason'],
                                 'excerpt_id':row['excerpt_id']})
            allowed.append(cid)
        staged['coverage_attachment_audit']=attachments
    already_reviewed={item['source_id'] for item in staged['judgments'].values()} if enrichment else set()
    seed_sources={eid:e['source_id'] for eid,e in excerpts.items()}
    for index in range(len(state['pool']),len(staged['pool'])):
        group=staged['pool'][index]
        anchors={seed_sources[e] for e in group['seed_ids']}
        candidates=sorted(group['candidates'],key=lambda c:c['source_id'] not in anchors)
        candidates=[c for c in candidates if c['source_id'] not in already_reviewed]
        allowed.extend(digest([state['binding'],index,c])[:20] for c in candidates[:6])
    # At most 30 candidates (12 rechecks + 3*6), one normally audited wave.
    staged.update(coverage_repair_attempted=True,coverage_repair_attempts=attempts+1,
        coverage_repair_active=True,coverage_allowed_ids=allowed,
        coverage_recheck_audit=rechecks,coverage_shortfall_reason=answer['shortfall_reason'],packets=[],
        status='PREPARING_FINAL_AI',last_measured_round=state['round'])
    if enrichment:
        staged.setdefault('supplement_candidate_ids',[]).extend(allowed)
    if new or attachments:
        # Persist the actual extended pool without changing the original ID binding.
        # Old passed judgments must keep their candidate IDs across this repair.
        pool_file='coverage_viewpoint_candidate_pool.json'
        (workspace/pool_file).write_text(json.dumps(staged['pool'],ensure_ascii=False,indent=2),encoding='utf-8')
        staged.update(pool_file=pool_file,pool_hash=digest(staged['pool']),
            viewpoint_definition_count=len(staged['pool']),
            positive_cluster_count=sum(g['stance'] in ('positive','正向词倾向') for g in staged['pool']))
        staged['positive_cluster_shortfall']=staged['positive_cluster_count']<POSITIVE_MIN
        if new:
            (workspace/'viewpoint_definitions.json').write_text((scratch/'viewpoint_definitions.json').read_text(encoding='utf-8'),encoding='utf-8')
    # A previous repair wave must not close this newly bound repair before dispatch.
    staged.pop('coverage_review_started', None)
    state.clear();state.update(staged)
    # Original binding, clock and passed judgments remain unchanged.
    return bool(allowed)
