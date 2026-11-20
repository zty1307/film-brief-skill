"""Bind an AI full-draft editorial review to the exact current workbench and draft.

The script checks completeness and publication decisions, not the truth of AI judgments.
"""
from __future__ import annotations
from pathlib import Path
import argparse,hashlib,json,re

PROFILE='FINAL_PROSE_REVIEW_V3'
CHECKS=('has_actual_opinion','claim_supported','expression_clear','sufficient_development','no_cross_point_overlap','samples_complementary','lead_style_appropriate')
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,data):Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


ACTION_TERMS=('脱粉','弃剧','退订','退票','抵制','下架','退坑','停看','退圈','举报','道歉','删帖')
def action_claim_attention(claim,samples):
    # Lexical absence is a review hint, never a semantic failure verdict.
    quoted=''.join(x.get('selected_quote','') for x in samples)
    return [term for term in ACTION_TERMS if term in claim and term not in quoted]


def history_context(dataset, group_indices):
    """Use final workbench comparisons only, separately from current quote evidence."""
    context = []
    for group_index in group_indices:
        comparison = dataset['groups'][group_index].get('history_review') or {}
        if comparison.get('current_evidence_scope') != 'FINAL_KEPT_DISPLAY_ONLY':
            continue
        context.append({'group_index': group_index, 'relation': comparison.get('relation'),
                        'reason': comparison.get('reason'),
                        'change_statement': comparison.get('change_statement'),
                        'previous_evidence': comparison.get('previous_evidence', [])})
    return context

def chart_context(dataset, plan, chart):
    """Send the actual plotted classes and their current literal evidence."""
    from chart_coverage import CRITICISM_BAND, DISPLAY_BASIS, OTHER_BAND, THEME_POLICY
    from report_style import point_rank
    rows = chart['rows']
    if chart.get('theme_policy') == THEME_POLICY:
        expected_order = [*range(len(plan['points'])), None]
        if [row['point_index'] for row in rows] != expected_order or rows[-1]['label'] != '其他':
            raise ValueError('图条须按最终正文段落顺序排列，“其他”固定最后；请重新生成Word与图表')
        shares = [row['percent'] for row in rows]
        has_criticism = point_rank(dataset, plan['points'][-1]) == 2
        if (chart.get('display_basis') != DISPLAY_BASIS or sum(shares) != 100
                or any(left < right for left, right in zip(shares, shares[1:]))
                or shares[-1] >= shares[-2] or not OTHER_BAND[0] <= shares[-1] <= OTHER_BAND[1]
                or (has_criticism and not CRITICISM_BAND[0] <= shares[-2] <= CRITICISM_BAND[1])):
            raise ValueError('图表须按指定区间分配并保持降序；请重新生成Word与图表')
    sample_index={row['candidate_id']:row for group in dataset['groups'] for row in group['samples']}
    result={'rows':rows,'denominator':chart['denominator'],
            'other_count':chart.get('other_count'), 'display_basis':chart.get('display_basis'), 'classification':chart.get('classification_review',plan.get('chart_review',{})),
            'workbench_overlap_pairs':dataset.get('writing_overlap_pairs',[]),'evidence':{}}
    review=result['classification']
    evidence=[row for category in review.get('categories',[]) for row in category.get('evidence',[])]
    evidence.extend(row for key in ('source_overrides','out_of_scope_sources','excluded_sources','other_sources')
                    for row in review.get(key,[]))
    for row in evidence:
        item=sample_index.get(row.get('candidate_id'))
        if item:
            result['evidence'][item['candidate_id']]=item.get('display_excerpt') or item['excerpt']
    return result


def prepare(dataset_path,plan_path,outdir):
    from report_writing import render_content
    from report_style import compile_plan
    from docx import Document
    dataset_path,plan_path,outdir=Path(dataset_path),Path(plan_path),Path(outdir)
    ds=read(dataset_path);plan=read(plan_path)
    if plan['dataset_sha256']!=sha(dataset_path):raise ValueError('成稿终审的工作台与写稿计划指纹不符')
    compiled=compile_plan(ds,plan,chart_order=True)
    paragraphs,audit=render_content(ds,compiled,compiled=True)
    original_indices=compiled.get('presentation_order',{}).get('original_point_indices',range(len(audit)))
    actual=[p.text for p in Document(outdir/'opinion_draft.docx').paragraphs if p.text and not p.text.startswith('注：')]
    if paragraphs!=actual:raise ValueError('成稿Word与当前写稿计划不一致，先重新render')
    if audit!=read(outdir/'source_audit.json')['points']:raise ValueError('成稿选样审计与当前写稿计划不一致')
    index={s['candidate_id']:s for g in ds['groups'] for s in g['samples']}
    packet={'profile':PROFILE,'dataset_sha256':sha(dataset_path),'plan_sha256':sha(plan_path),
            'draft_sha256':sha(outdir/'opinion_draft.md'),'word_sha256':sha(outdir/'opinion_draft.docx'),
            'draft_text':(outdir/'opinion_draft.md').read_text(encoding='utf-8-sig'),
            'instructions':'证据按完整观点核对，不按一个细节卡死：同一集中评价主题可以由互补的具体子评价支撑，如演唱听感与歌曲人物适配共同支撑原声音乐感染力，服饰与置景共同支撑同一视觉风格。每份声音都须有对本主题的实际评价，每个标题分句至少一条具体证据，全段至少两份独立声音；不要求每条引文重复同一细节或同时覆盖所有分句。不能仅因都提到演员、音乐或预告就合组；不能把无关问题装进泛泛的整体很好，也不能与别段重复核心评价。独立来源同向互证与不同细节互补均可，纯资讯、名单、期待及同稿转载均不能补足证据。完整通读最终稿和所选展示句，不能只检查字段。整节标题用“肯定剧集……”概括实际正文中最具代表性的2—3个正面判断，核对标题中各点与正文相对顺序一致。若有批评末段，核对尾句“少量声音吐槽／质疑……”是否概括实际问题。每点须有对象和判断，不可只堆叠方面名词。标题缺陷记入overall_issues。逐段确认真实观点、概括有据、表达自然、展开充分且点位不交叉。段前句以适用的评价动词起句，概括一类舆论，具体细节留在引文。通读相邻段检查有无反复套用同一个起句，持续、进展、热议和分歧均须有据。相关方面共同支持同一判断可合写，同一核心评价不重复。批评仅一个末段，编号或此外二选一，不另加争议段。通常3—4条有效声音，两条充分时可用两条，短评有新增信息时可选5—6条，不为数量凑样本。前两份真实独立且具体的评价可以同向互证，不要求第二份必须增加另一种角度或不同事件。不能只因评价结论相同就把samples_complementary判false；此字段也接受有信息的独立互证。第三条及以后优先补充不同细节，不为凑字重复。判断同稿必须有转载、照搬或共同转述来源依据；不同作者不自动独立，措辞近似也不自动同稿。只有几字空泛赞美仍不够支撑独立段落，原证据和篇幅要求不变。上线通知/词曲署名/宣传措辞不自动成为舆论认可；sufficient_development判断信息是否充分，不等于字数是否达到180。低于常规字数时先回读完整已引展示句与同组备选；若已含至少两份独立、具体且互补的有效评价，论述完整，确无遗漏的必要信息，并能说明该观点的独立价值，可填keep_important_opinion、sufficient_development=true、issues=[]；这不是自动通过，其他审核项仍须全部通过。两句只有同义短评、没有可展开的具体信息时，即使来源独立、方面不同，也不能用独立维度或凑够六段作为keep_important_opinion理由，应返修换用同组有效材料；确无材料则据实退回。若是单源、泛赞、资讯、遗漏可用细节或关键判断缺证，则仍应返修或删点。不能一面判有充分实质信息并保留，一面仅因字数把sufficient_development置false；也不能为了达到字数添加无证据的解释。发现问题必须revise并修计划，重新render和终审；有未解决issues不得pass。',
            'paragraphs':[]}
    for i,(text,row) in enumerate(zip(paragraphs[1:],audit)):
        paragraph = {'point_index':i,'plan_point_index':original_indices[i],'point_number':row['point_number'],'text':text,
            'characters':len(re.sub(r'\s+','',text)),'stance':row['stance'],'claim':row['claim'],
            'length_measure':row.get('paragraph_length'),
            'samples':[{'candidate_id':s['candidate_id'],'attribution':s['attribution'],'selected_quote':s['quote'],
                        'display_excerpt':index[s['candidate_id']].get('display_excerpt') or index[s['candidate_id']]['excerpt']} for s in row['samples']]}
        comparison = history_context(ds, row['group_indices'])
        if row.get('paragraph_length', {}).get('characters', 300) < 180 and row['stance'] != 'negative':
            cited = {sample['candidate_id'] for sample in row['samples']}
            paragraph['same_group_alternatives'] = [
                {'candidate_id': sample['candidate_id'],
                 'display_excerpt': sample.get('display_excerpt') or sample['excerpt']}
                for group_index in row['group_indices']
                for sample in ds['groups'][group_index]['samples']
                if sample['candidate_id'] not in cited][:4]
        if comparison:
            paragraph['history_context'] = comparison
        hints=action_claim_attention(paragraph['claim'],paragraph['samples'])
        if hints:paragraph['concrete_action_attention']=hints
        packet['paragraphs'].append(paragraph)
    owners={gi:i for i,row in enumerate(audit) for gi in row['group_indices']}
    overlap_points={tuple(sorted((owners[row['left']],owners[row['right']])))
                    for row in ds.get('writing_overlap_pairs',[])
                    if row['left'] in owners and row['right'] in owners
                    and owners[row['left']]!=owners[row['right']]}
    if overlap_points:
        packet['known_overlap_points']=[row for row in compiled.get('outline_review',{}).get('pairwise_review',[])
            if (row['left'],row['right']) in overlap_points]
        packet['instructions']+=(
            '另逐对通读known_overlap_points所列两段的实际选句。已列区别仍须实审，'
            '如果一段反复引用另一段的核心评价、只是换媒介范围或换例子，'
            '将对应段no_cross_point_overlap设为false，列出重复的原判断和应删改的实际引文。')
    packet['instructions']+='逐条核对实际入稿引文，而不只比较标题：两段核心可独立，也应裁去引文中重复另一段核心评价的旁枝。不能仅凭物料/成片等范围标签认定不重复。局部重复优先缩短该条引文或换同组已选材料，保留仍有独立双证据的观点，不自动删整点。段前句中的观众行为和因果效果须由实际引用的句子支持，不能把反感扩大为行动、把期待扩大为已实现效果；缺证时优先收窄标题，不为标题另造证明。'
    packet['chart_context']=chart_context(ds,compiled,read(outdir/'opinion_distribution.json'))
    packet['chart_review_policy']='CURRENT_CHART_TOPICS_V1'
    packet['instructions']+=(
        '普通段不足180字时，先回读本段已引用展示句的未截取部分及same_group_alternatives，'
        '有同核心的具体细节或独立互补声音就返修补足；两条已选不等于工作台只有两条。'
        '不能仅以已有两条、删段影响覆盖或不想凑字作为保留短段的依据。'
        '确实无可用补充且观点具有独立重要性，才说明实际证据取舍后保留例外。')
    if ds.get('human_selection'):
        from human_selection import RULE
        packet['instructions'] = RULE + packet['instructions'].replace('有信息不足则删点', '有信息不足则退回选材')
    inventory_path=outdir/'candidate_inventory.json'
    if inventory_path.exists() and read(inventory_path).get('user_requirements'):
        packet['user_requirements']=read(inventory_path)['user_requirements']
        packet['instructions']+=' 同时核对用户补充要求；不能满足时说明实际材料限制，不借此增加原表材料或跳过审核。'
    blank={k:packet[k] for k in ('profile','dataset_sha256','plan_sha256','draft_sha256','word_sha256')}
    blank.update(decision='',overall_issues=[],point_reviews=[{'point_index':p['point_index'],
        **{key:None for key in CHECKS},'evidence':{'candidate_id':'','quote_snippet':''},
        'issues':[],'action':'','short_paragraph_action':'','reason':''} for p in packet['paragraphs']])
    blank['chart_review']={'no_duplicate_topics':None,'evidence_matches_categories':None,
                           'other_assignment_reasoned':None,'reason':''}
    # Every standalone reviewer receives the exact response shape. The empty
    # template is never evidence of review; the original validator still requires
    # all judgments, literal quote bindings and concrete unresolved issues.
    packet['answer_schema']={k:v for k,v in blank.items() if k not in ('profile','dataset_sha256','plan_sha256','draft_sha256','word_sha256')}
    from rapid_film_workbench import PUBLISHED_PREMISE_RULE
    packet['instructions'] += PUBLISHED_PREMISE_RULE
    packet['answer_schema_rules']='只返回decision、overall_issues、point_reviews。point_reviews逐项填七个布尔判断、evidence对象(candidate_id和quote_snippet)、issues字符串列表、action(none/revise_plan/omit_point)、reason；不存在explanation或paragraph_reviews替代字段。发现问题填issues并选revise_plan/omit_point，全稿decision=revise；通过时action=none。'
    packet['answer_schema_rules']+=' 另填chart_review三个布尔检查与至少24字实际图表判断理由；发现分类或边界问题须overall_issues写明并revise。'
    # Keep publication checks intact, but give each semantic review one task.
    chart_packet={k:packet[k] for k in ('profile','dataset_sha256','plan_sha256','draft_sha256','word_sha256')}
    chart_packet.update(chart_context=packet.pop('chart_context'),
        point_claims=[{'point_index':p['point_index'],'claim':p['claim']} for p in packet['paragraphs']],
        instructions='核验每个主要图类是否压缩自point_index对应的正文段首句，保留同一个起句评价动词、对象和核心判断，是否有样本支持，以及“其他”是否为少量真实零散负面。从上到下逐条核对是否和最终正文段落顺序相同，展示配比由脚本计算，统计原值保存在sample_percent。批评4%—6%，“其他”2%—4%且固定最末。完整核验图表，不重新决定正文是否通过。发现实质问题decision=revise且overall_issues具体说明。图标签问题逐条以“图标签 point_index=0”等实际零起始序号定位，分类或分母问题另列；全部检查通过才pass。',
        answer_schema={'decision':'','overall_issues':[], 'chart_review':blank['chart_review']})
    save(outdir/'chart_review_input.json',chart_packet)
    chart_blank={k:chart_packet[k] for k in ('profile','dataset_sha256','plan_sha256','draft_sha256','word_sha256')}
    chart_blank.update(chart_packet['answer_schema']);save(outdir/'final_chart_review.json',chart_blank)
    packet.pop('chart_review_policy',None)
    packet['chart_review_mode']='SEPARATE_REQUIRED_V1'
    packet['answer_schema'].pop('chart_review',None)
    packet['answer_schema_rules']='只返回decision、overall_issues、point_reviews；decision仅pass/revise。逐段完整填七项布尔判断、evidence对象(candidate_id与quote_snippet)、issues字符串列表、action(none/revise_plan/omit_point)、reason。通过用action=none，不用keep/pass；发现问题须列具体issues并revise_plan或omit_point，整稿decision=revise。普通短段另填short_paragraph_action(omit/revise/keep_important_opinion)。图表由下一阶段核验，本阶段不填chart_review。'
    packet['repair_materials']={}
    for paragraph in packet['paragraphs']:
        for sample in paragraph['samples']:
            display=sample.pop('display_excerpt')
            if paragraph['characters']<180 and paragraph['stance']!='negative':
                packet['repair_materials'].setdefault(str(paragraph['point_index']),[]).append(
                    {'candidate_id':sample['candidate_id'],'display_excerpt':display,
                     'purpose':'仅供判断可否补写；未入稿内容不能替当前标题补证'})
    packet['instructions']+=' 本次只审正文实际引文；原展示句只在repair_materials供短段返修判断，不能冒充已引用证据。图表材料已分到下一阶段，正文通过也不会跳过图表核验。允许不相邻段落复用合适的动词，不把全篇动词不重复当作额外门槛。'
    packet['instructions']+=' concrete_action_attention仅提示段前句出现具体行为、引文未出现同词，不自动判错。逐项核实引文是否有等义的实际行为证据；不满/反感不能直接推出脱粉，担忧/呼吁不能当已发生行为。该段reason须提及每个提示词并说明实际支持或应删改，不要求同词照抄。无行为证据时claim_supported=false并返修；有明确等义行为证据可通过。'
    save(outdir/'prose_review_input.json',packet)
    save(outdir/'final_prose_review.json',blank)
    return {'status':'FINAL_PROSE_REVIEW_REQUIRED','read_now':str(outdir/'prose_review_input.json'),
            'answer_file':str(outdir/'final_prose_review.json')}

def collect(dataset_path,plan_path,outdir):
    dataset_path,plan_path,outdir=Path(dataset_path),Path(plan_path),Path(outdir)
    packet=read(outdir/'prose_review_input.json');review=read(outdir/'final_prose_review.json')
    current={'profile':PROFILE,'dataset_sha256':sha(dataset_path),'plan_sha256':sha(plan_path),
             'draft_sha256':sha(outdir/'opinion_draft.md'),'word_sha256':sha(outdir/'opinion_draft.docx')}
    for key,value in current.items():
        if packet.get(key)!=value or review.get(key)!=value:raise ValueError('成稿终审已过期或未绑定当前稿件：'+key+'；修改后须重新render并prepare终审')
    reviewed={r.get('point_index'):r for r in review.get('point_reviews',[])}
    for paragraph in packet.get('paragraphs',[]):
        row=reviewed.get(paragraph['point_index'],{})
        for term in paragraph.get('concrete_action_attention',[]):
            if term not in row.get('reason',''):
                raise ValueError(f'段{paragraph["point_index"]+1}：终审reason须说明具体行为“{term}”是否有实际引文依据；词面未出现只是提示，不自动判错。')
    from report_writing import render_content
    from report_style import compile_plan
    dataset=read(dataset_path)
    compiled=compile_plan(dataset,read(plan_path),chart_order=True)
    paragraphs,audit=render_content(dataset,compiled,compiled=True)
    if [p.get('text') for p in packet.get('paragraphs',[])]!=paragraphs[1:]:raise ValueError('终审输入未覆盖当前完整成稿或段落已改变')
    expected=[[(s['candidate_id'],s['quote']) for s in row['samples']] for row in audit]
    actual=[[(s.get('candidate_id'),s.get('selected_quote')) for s in row.get('samples',[])] for row in packet['paragraphs']]
    if expected!=actual:raise ValueError('终审所读引文与当前成稿选样不符')
    result=validate_review(packet,review,str(outdir/'final_prose_review.json'))
    if result['decision']=='pass' and packet.get('chart_review_mode')=='SEPARATE_REQUIRED_V1':
        return collect_chart_review(packet,review,outdir)
    return result


def collect_chart_review(packet,review,outdir):
    path=outdir/'final_chart_review.json'
    answer=read(path) if path.is_file() else {}
    if not answer.get('decision'):
        return {'status':'FINAL_CHART_REVIEW_REQUIRED','decision':'pending',
                'read_now':str(outdir/'chart_review_input.json'),'answer_file':str(path)}
    chart_input=read(outdir/'chart_review_input.json')
    for key in ('profile','dataset_sha256','plan_sha256','draft_sha256','word_sha256'):
        if answer.get(key)!=packet[key] or chart_input.get(key)!=packet[key]:
            raise ValueError('图表终审未绑定当前成稿：'+key)
    checks=('no_duplicate_topics','evidence_matches_categories','other_assignment_reasoned')
    assessment=answer.get('chart_review',{});issues=answer.get('overall_issues')
    if any(type(assessment.get(k)) is not bool for k in checks) or len(str(assessment.get('reason','')).strip())<24:
        raise ValueError('图表终审须填三项布尔判断与至少24字实际理由')
    if not isinstance(issues,list) or any(not isinstance(x,str) or len(x.strip())<8 for x in issues):
        raise ValueError('图表overall_issues须列具体问题')
    unresolved=bool(issues) or not all(assessment[k] for k in checks)
    if unresolved and (not issues or answer['decision']!='revise'):
        raise ValueError('图表存在问题须具体列明并返修，不得pass')
    if not unresolved and answer['decision']!='pass':
        raise ValueError('图表返修须有实际未解决问题')
    review.update(chart_review=assessment,decision=answer['decision'])
    review['overall_issues']=review.get('overall_issues',[])+issues
    save(outdir/'final_prose_review.json',review)
    return {'status':'FINAL_PROSE_REVIEW_PASSED' if not unresolved else 'FINAL_PROSE_REVISION_REQUIRED',
            'decision':answer['decision'],'point_count':len(review['point_reviews']),
            'review_file':str(outdir/'final_prose_review.json')}

def validate_review(packet,review,review_file=''):
    """Check review completeness and publication decisions; AI owns semantic judgments."""
    entries=review.get('point_reviews',[])
    if not isinstance(entries,list) or len(entries)!=len(packet['paragraphs']):raise ValueError('终审必须逐段覆盖完整成稿')
    by_index={r.get('point_index'):r for r in entries}
    if set(by_index)!=set(range(len(packet['paragraphs']))):raise ValueError('终审段落编号缺失或重复')
    format_errors=[]
    for p in packet['paragraphs']:
        i=p['point_index'];r=by_index[i]
        if any(type(r.get(k)) is not bool for k in CHECKS):format_errors.append(f'point_reviews[{i}]七项判断须逐项填true或false')
        if r.get('action') not in ('none','omit_point','revise_plan'):format_errors.append(f'point_reviews[{i}].action只能none/omit_point/revise_plan；通过用none，不用keep/pass')
        e=r.get('evidence')
        if not isinstance(e,dict):format_errors.append(f'point_reviews[{i}].evidence须为candidate_id与quote_snippet对象，不是字符串')
        else:
            samples={v['candidate_id']:v for v in p['samples']};cid=e.get('candidate_id');quote=e.get('quote_snippet')
            if cid not in samples:format_errors.append(f'point_reviews[{i}].evidence.candidate_id须为本段实际ID：{list(samples)}')
            elif not isinstance(quote,str) or len(quote)<8 or quote not in samples[cid]['selected_quote']:
                format_errors.append(f"point_reviews[{i}].evidence.quote_snippet须逐字取本段已引用原句至少8字，中文引号等标点不得替换。当前={quote!r}；可选原句={samples[cid]['selected_quote']!r}")
        if len(str(r.get('reason','')).strip())<12:format_errors.append(f'point_reviews[{i}].reason须至少12字实际判断依据')
        issues=r.get('issues',[])
        if not isinstance(issues,list) or any(not isinstance(x,str) or len(x.strip())<8 for x in issues):format_errors.append(f'point_reviews[{i}].issues须为每条至少8字具体问题列表')
    if format_errors:raise ValueError('\n'.join(format_errors))
    overall=review.get('overall_issues',[])
    if not isinstance(overall,list) or any(not isinstance(x,str) or len(x.strip())<8 for x in overall):raise ValueError('overall_issues须列出具体问题，至少8字')
    unresolved=bool(overall)
    if packet.get('chart_review_policy')=='CURRENT_CHART_TOPICS_V1':
        assessment=review.get('chart_review')
        chart_checks=('no_duplicate_topics','evidence_matches_categories','other_assignment_reasoned')
        if (not isinstance(assessment,dict) or any(type(assessment.get(key)) is not bool for key in chart_checks)
                or len(str(assessment.get('reason') or '').strip())<24):
            raise ValueError('chart_review须填写实际图类去重、证据归属、其他取舍三个布尔值及至少24字理由')
        if not all(assessment[key] for key in chart_checks):
            if not overall:
                raise ValueError('chart_review发现问题，须用overall_issues说明实际图类与引文问题并返修')
            unresolved=True
    for p in packet['paragraphs']:
        r=by_index[p['point_index']]
        if any(type(r.get(k)) is not bool for k in CHECKS):raise ValueError(f"段{p['point_index']}的七项成稿判断必须逐项填true或false")
        evidence=r.get('evidence',{});samples={s['candidate_id']:s for s in p['samples']};cid=evidence.get('candidate_id');snippet=evidence.get('quote_snippet','')
        if cid not in samples or not isinstance(snippet,str) or len(snippet)<8 or snippet not in samples[cid]['selected_quote']:raise ValueError('终审依据须绑定本段实际引文至少8字，不能只复述标题')
        if len(str(r.get('reason','')).strip())<12:raise ValueError('逐段终审须说明实际判断依据，至少12字')
        issues=r.get('issues',[])
        if not isinstance(issues,list) or any(not isinstance(x,str) or len(x.strip())<8 for x in issues):raise ValueError('issues须为具体未解决问题列表，不能用短标签代替')
        failed=not all(r[k] for k in CHECKS)
        action=r.get('action')
        if action not in ('none','omit_point','revise_plan'):raise ValueError('action只能none/omit_point/revise_plan')
        if (failed or issues) and (not issues or action=='none'):
            raise ValueError(f"point_reviews[{p['point_index']}]：已有未解决判断或issues，保留实际问题并把action设为revise_plan或omit_point；不能用none留备注放行")
        if p['stance']!='negative' and p['characters']<180:
            short=r.get('short_paragraph_action')
            if short not in ('omit','revise','keep_important_opinion'):raise ValueError('普通短段须明确删点、返修或保留重要具体观点，不靠单源例外自动通过')
            if short=='keep_important_opinion' and (not r['has_actual_opinion'] or len(r['reason'].strip())<24):raise ValueError('保留短段须有真实重要观点与具体依据，不能只因材料少放行')
            if short in ('omit','revise'):failed=True
        if failed and (not issues or action=='none'):raise ValueError('短段返修也须列明问题并要求修改，不能只留短段标记')
        unresolved|=failed or bool(issues) or action!='none'
    decision=review.get('decision')
    if decision not in ('pass','revise'):raise ValueError('终审decision须pass或revise')
    if decision=='pass' and unresolved:raise ValueError('终审仍有未解决问题，不能标pass或发布；先修计划再重新render和终审')
    if decision=='revise' and not unresolved:raise ValueError('返修须列明具体问题，不能提交空泛revise')
    return {'status':'FINAL_PROSE_REVIEW_PASSED' if decision=='pass' else 'FINAL_PROSE_REVISION_REQUIRED',
            'decision':decision,'point_count':len(entries),'review_file':review_file}

def require_pass(dataset_path,plan_path,outdir):
    result=collect(dataset_path,plan_path,outdir)
    if result['decision']!='pass':raise ValueError('成稿终审尚未通过，不发布工作台与Word；按反馈修稿并重新终审')
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('prepare','check'))
    parser.add_argument('--dataset',required=True);parser.add_argument('--plan',required=True);parser.add_argument('--outdir',required=True);a=parser.parse_args()
    try:result=(prepare if a.command=='prepare' else collect)(a.dataset,a.plan,a.outdir)
    except (ValueError,OSError,KeyError) as exc:parser.error(str(exc))
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
