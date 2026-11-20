"""Explicit chart coverage of reviewed workbench sources, independent of prose selection."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

PROFILE = 'WORKBENCH_CHART_COVERAGE_V1'
THEME_POLICY = 'MAJOR_VIEWPOINTS_V3'
NEGATIVE_STANCES = ('negative', '负向词倾向')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def display(sample):
    return sample.get('display_excerpt') or sample['excerpt']

def primary_snippets(group,sample):
    return [p['quote_snippet'] for p in group.get('writing_guidance',{}).get('preferred',[])
            if p.get('candidate_id')==sample['candidate_id'] and isinstance(p.get('quote_snippet'),str)
            and p['quote_snippet'] in display(sample)]

def contract():
    return {
        'profile': PROFILE,
        'rule': '统计全部现有工作台有效观点，不以正文是否引用决定其他。每个来源一次；只剔除没有独立观点的纯事实背景，不能因未入稿、弱、泛或writing_eligible=false剔除。主要批评仍可单列。其他必须有少量未单列成主要主题的真实负面意见，固定最后，来源占比不高于任何主类，不指定百分比。',
        'theme_policy': THEME_POLICY,
        'main_topic_rule': '主要图类是有代表性的具体判断，不是容纳所有来源的方面大筐。先按同一评价对象与判断机制确定主类，再查边界；不能因为同属剧情、表演或正面就合并，也不为每条剩余来源新开主类。通常至少两条独立来源共同支持同一判断；独立且重要的单条批评可说明例外，不按数量消灭负面。',
        'workbench_focus_rule': '承接工作台已审核心，不从完整展示句的附带半句重新发现另一观点。库存给出core_mechanism与primary_support_snippets；已有主要支持片段时，图类evidence及边界quote_snippet必须在其中连续选取。每个实际纳入主类的原组至少有一条主要支持证据，不能只证明大组合并中的几个组、忽略边界小组。没有优选片段时用展示句，仍以原组核心核对，真正归错的个别来源须显式override说明。',
        'categories': [{'id':'topic_1','label':'4—30字且覆盖实际判断','group_indices':[0],
                        'core_judgment':'10—80字说明本类共同评价对象和具体判断，不能只写某方面好评',
                        'evidence':[{'candidate_id':'','quote_snippet':'连续至少8字；有primary_support_snippets时必须从其中选，覆盖每个实际纳入的原组，通常2—3条'}],
                        'single_source_reason':'仅一条独立支持时，说明本期为何仍须作为主要图类；其余留空',
                        'reason':'至少12字解释同一判断为何有代表性，以及合并组之间的真实共同机制'}],
        'source_overrides': [{'candidate_id':'','category_id':'topic_1','quote_snippet':'当前展示句内至少8字','reason':'至少12字说明该来源实际主题与整组默认归属的不同'}],
        'excluded_sources': [{'candidate_id':'','reason_kind':'background_fact','quote_snippet':'当前展示句内至少8字','reason':'至少16字说明仅有档期榜单数字等事实，没有独立判断；同句有观点不得只截事实部分排除'}],
        'other_sources': [{'candidate_id':'','quote_snippet':'当前展示句内至少8字','reason':'从已审负面组逐条取真实批评；至少16字说明具体批评及为何不同于已列主类'}],
        'coverage': 'categories.group_indices给整组默认图类。个别混合来源可source_overrides；纯背景与真正零散来源逐条列出。所有source_id必须恰有一个最终去向，未覆盖时报出全部遗漏，不自动归其他。',
        'scope': '不回原表、不读候选池、不改工作台或正文立场。转述也可含观点，保留原评价者；对热度/宣推/受众的分析也是观点，只有纯数字快讯才是背景。新增主要图类须有独立代表性，不为归零其他不断开类。',
        'boundary_review': [{'candidate_id':'风险列表每个ID填写一次','decision':'fits_main/other/background',
                             'quote_snippet':'当前展示句内连续至少8字','reason':'至少16字说明实际核心判断与主类相同或不同；不按方面相同或正文未用决定'}],
        'boundary_rule': '复核inventory的boundary_candidates：单条组、混合评价/口碑概述、写稿不宜组。情感拉扯笔力不等于节奏铺垫，原声配乐不等于画面服饰；这些只是机制边界示例，不是固定分组。混合概述不能另开总括类重复已有观点；先看是否仍集中在同一具体对象和取舍机制，集中则入该讨论项，确实横跨不同机制且无主导主题时仍核对是否有独立真实的零散批评，符合其他口径才可纳入。没被正文引用不自动进其他。',
        'mixed_topic_rule': '绘图可把原本同一具体对象和机制的支持、批评和分歧归入讨论项，标签与core_judgment明示分歧，不改变工作台与正文立场。先确定主要来源共同核心，再核边界，不为残余来源增加并列对象或扩大主类。混合来源按其主导判断归属，不能只取一个相同词或附带评语匹配。表演自然与后期配音剪辑归责、编剧情感笔力与权谋改编取舍等不因共享演员/编剧就成为同题。改题后若必须新增另一评价对象或机制才能装入，按其真实主要观点重新定类；只有零散批评进入其他；不另开重复总括类。',
        'other_rule': '必须填写非空other_sources。优先核对已审负面组里与现有主类机制不同、只零散出现的真实批评；每条选完整语境中的连续批评原话。已有同类主批评的样本继续归该主类，不能拆几条降数或抬高其他。确实无合格材料时如实报本期图表缺口，人工选材无材料时提示补选。',
    }

def empty_review(dataset_hash):
    return {'profile':PROFILE, 'theme_policy':THEME_POLICY,'dataset_sha256':dataset_hash, 'categories':[],
            'source_overrides':[], 'excluded_sources':[], 'other_sources':[],
            'boundary_review':[]}

def boundary_candidates(dataset):
    result=[]
    for gi,g in enumerate(dataset['groups']):
        reasons=[]
        if len({s['source_id'] for s in g['samples']})<2:reasons.append('single_source_group')
        if g.get('stance') in ('中性或混合','objective','neutral','mixed'):reasons.append('mixed_or_overview')
        if g.get('writing_guidance',{}).get('eligible') is False:reasons.append('writing_not_eligible_is_not_background')
        if reasons:
            result.extend({'candidate_id':s['candidate_id'],'group_index':gi,'review_reasons':reasons} for s in g['samples'])
    return result

def validate_major_themes(dataset,review,cats,index,allocations):
    if review.get('theme_policy')!=THEME_POLICY:
        raise ValueError('新答卷须保留theme_policy=MAJOR_VIEWPOINTS_V3；不能降级跳过主要观点边界核对')
    routes={r['candidate_id']:r for r in allocations}
    for cat in cats:
        cid=cat['id'];core=cat.get('core_judgment','')
        if not isinstance(core,str) or not 10<=len(core.strip())<=80:raise ValueError(f'图类{cid}须有10—80字共同评价对象与判断机制')
        evidence=cat.get('evidence',[]);seen=set();support_sources=set();support_groups=set()
        actual_groups={r['group_index'] for r in allocations if r['state']=='category' and r['category_id']==cid}
        limit=max(3,len(actual_groups))
        if not isinstance(evidence,list) or not 1<=len(evidence)<=limit:
            actual=len(evidence) if isinstance(evidence,list) else '非数组'
            raise ValueError(f'图类{cid}的evidence当前为{actual}条，允许1—{limit}条；'
                             f'保留能覆盖实际纳入原组{sorted(actual_groups)}的证据，多出的删去')
        for item in evidence:
            sid=item.get('candidate_id');snippet=item.get('quote_snippet','')
            if sid not in index or sid in seen or routes[sid]['category_id']!=cid or routes[sid]['state']!='category':raise ValueError(f'图类{cid}的支持来源无效、重复或实际不在该类')
            if not isinstance(snippet,str) or len(snippet)<8 or snippet not in display(index[sid][1]):raise ValueError(f'图类{cid}支持须逐字来自当前展示句至少8字')
            anchors=primary_snippets(dataset['groups'][index[sid][0]],index[sid][1])
            if anchors and not any(snippet in anchor for anchor in anchors):raise ValueError(f'图类{cid}来源{sid}须绑定工作台已审主要支持片段，不以附带评语替换原核心；可用片段：'+json.dumps(anchors,ensure_ascii=False))
            seen.add(sid);support_sources.add(index[sid][1]['source_id']);support_groups.add(index[sid][0])
        if actual_groups-support_groups:raise ValueError(f'图类{cid}缺少以下原组的主要支持证据：{sorted(actual_groups-support_groups)}；不能用大组证据代替边界组，按库存core_mechanism与primary_support_snippets核对后修归属或补真实支持')
        if len(support_sources)<2 and len(str(cat.get('single_source_reason','')).strip())<20:
            raise ValueError(f'图类{cid}只有一条独立支持；说明其独立重要性或重新判断是否属主要图类，不凑样本')
    required={r['candidate_id'] for r in boundary_candidates(dataset)};seen=set()
    entries=review.get('boundary_review',[])
    if not isinstance(entries,list):raise ValueError('boundary_review须为数组')
    decisions={'category':'fits_main','other':'other','excluded':'background'}
    for item in entries:
        sid=item.get('candidate_id');snippet=item.get('quote_snippet','')
        if sid not in index or sid in seen:raise ValueError('边界复核来源无效或重复')
        if item.get('decision')!=decisions[routes[sid]['state']]:raise ValueError(f'边界复核与实际来源去向矛盾: {sid}')
        if not isinstance(snippet,str) or len(snippet)<8 or snippet not in display(index[sid][1]):raise ValueError(f'边界复核须绑定当前展示句至少8字: {sid}')
        anchors=primary_snippets(dataset['groups'][index[sid][0]],index[sid][1])
        if anchors and not any(snippet in anchor for anchor in anchors):raise ValueError(f'边界来源{sid}须从工作台已审主要支持片段核对，不取附带半句重定性；可用片段：'+json.dumps(anchors,ensure_ascii=False))
        if len(str(item.get('reason','')).strip())<16:raise ValueError(f'边界复核须说明核心判断是否相同: {sid}')
        seen.add(sid)
    if required-seen:raise ValueError('以下来源未复核主要观点边界：'+json.dumps(sorted(required-seen),ensure_ascii=False))

def coverage_data(dataset, plan, dataset_hash):
    review=plan.get('chart_review')
    if not isinstance(review,dict) or review.get('profile')!=PROFILE:
        raise ValueError('请完成chart_review全工作台绘图归类；未入正文的组不自动算其他')
    if review.get('dataset_sha256')!=dataset_hash:
        raise ValueError('chart_review的dataset_sha256与当前工作台不一致')
    groups=dataset['groups']; cats=review.get('categories',[])
    if not isinstance(cats,list) or not cats:
        raise ValueError('chart_review.categories至少列出一个有当前观点支持的主要图类')
    owners={}; labels=set(); category_by_id={}
    for i,cat in enumerate(cats):
        cid=cat.get('id'); label=cat.get('label',''); gis=cat.get('group_indices',[])
        if not isinstance(cid,str) or not cid or cid in category_by_id:
            raise ValueError('图类id不能为空或重复')
        if not isinstance(label,str) or not 4<=len(label.strip())<=30 or label=='其他' or label in labels:
            raise ValueError('图类label须独立清晰且4—30字；其他由真实零散来源单列')
        if len(str(cat.get('reason','')).strip())<12:
            raise ValueError(f'图类{cid}须解释主题归并依据，不能以压小其他为由')
        if not isinstance(gis,list) or any(type(gi) is not int or not 0<=gi<len(groups) for gi in gis):
            raise ValueError(f'图类{cid}的group_indices无效')
        for gi in gis:
            if gi in owners:raise ValueError(f'group {gi}重复归入绘图类别')
            owners[gi]=cid
        labels.add(label);category_by_id[cid]=i
    index={}
    for gi,g in enumerate(groups):
        for sample in g['samples']:
            cid=sample['candidate_id']
            if cid in index:raise ValueError('同一candidate_id在工作台重复')
            index[cid]=(gi,sample)
    if not review.get('other_sources'):
        raise ValueError('other_sources 必须有实际负面样本。回查当前工作台各负面组中未单列成主要主题的零散批评；'
                         '无合格材料则说明缺口，人工选材时可补选已审负面样本')
    chosen={s['candidate_id'] for p in plan.get('points',[]) for s in p.get('samples',[])}
    exceptions={}
    for kind,field in [('category','source_overrides'),('excluded','excluded_sources'),('other','other_sources')]:
        entries=review.get(field,[])
        if not isinstance(entries,list):raise ValueError(field+'须为数组')
        for entry in entries:
            cid=entry.get('candidate_id')
            if cid not in index or cid in exceptions:raise ValueError(f'{field}来源无效或重复: {cid}')
            reason=str(entry.get('reason','')).strip(); snippet=entry.get('quote_snippet','')
            if len(reason)<(12 if kind=='category' else 16):raise ValueError(f'{field}需具体的归属/排除理由: {cid}')
            if not isinstance(snippet,str) or len(snippet)<8 or snippet not in display(index[cid][1]):
                raise ValueError(f'{field}须绑定当前展示句内连续至少8字: {cid}')
            if kind=='category' and entry.get('category_id') not in category_by_id:
                raise ValueError(f'来源override未指向已有图类: {cid}')
            if kind=='other':
                gi, sample = index[cid]
                group = groups[gi]
                if group.get('stance') not in NEGATIVE_STANCES:
                    raise ValueError(f'other_sources 只接收工作台已审负面样本: {cid}')
                anchors = primary_snippets(group,sample)
                if anchors and not any(snippet in anchor for anchor in anchors):
                    raise ValueError(f'other_sources 须引用已审主要支持片段，不能用附带批评改写原主要归属: {cid}')
            if kind=='excluded':
                if entry.get('reason_kind')!='background_fact':raise ValueError('只允许把纯背景排除观点图统计，不按立场或正文取舍删来源')
                if cid in chosen:raise ValueError(f'已作为正文观点证据的来源又被标为纯背景: {cid}；先核清观点与背景的矛盾')
            exceptions[cid]={'state':kind,'category_id':entry.get('category_id') if kind=='category' else None,
                             'reason':reason,'quote_snippet':snippet}
    allocations=[]; missing=[]; by_source={}
    for cid,(gi,sample) in index.items():
        route=exceptions.get(cid)
        if route is None and gi in owners:route={'state':'category','category_id':owners[gi],
                                                'reason':cats[category_by_id[owners[gi]]]['reason'],'quote_snippet':None}
        if route is None:missing.append({'group_index':gi,'candidate_id':cid});continue
        item={'candidate_id':cid,'source_id':sample['source_id'],'group_index':gi,**route}
        if item['source_id'] in by_source:
            prior=by_source[item['source_id']]
            if (item['state'],item['category_id'])!=(prior['state'],prior['category_id']):
                raise ValueError('同一来源的不同展示句不能分到多个图类或部分排除')
        else:by_source[item['source_id']]=item
        allocations.append(item)
    if missing:
        raise ValueError('以下工作台来源尚未绘图归类；先合理归并/新增图类或逐条说明背景/零散，不自动进其他：'+json.dumps(missing,ensure_ascii=False))
    if review.get('theme_policy') or plan.get('chart_theme_policy'):
        validate_major_themes(dataset,review,cats,index,allocations)
    counts=[0]*(len(cats)+1);excluded=[]
    for item in by_source.values():
        if item['state']=='excluded':excluded.append(item);continue
        bucket=category_by_id[item['category_id']] if item['state']=='category' else len(cats)
        counts[bucket]+=1
    total=sum(counts)
    if not total:raise ValueError('排除背景后没有有效观点，不能生成比例图')
    main_counts=[count for count in counts[:-1] if count]
    if not main_counts:
        raise ValueError('至少有一个实际有样本的主题类别，其他不能成为唯一图类')
    if counts[-1]>min(main_counts):
        raise ValueError(f'other_sources 实际{counts[-1]}条，高于最小主类的{min(main_counts)}条；'
                         '核对主批评与零散批评的边界，按原话修归类。保留实际来源数')
    exact=[c*100/total for c in counts];percent=[math.floor(v) for v in exact]
    for i in sorted(range(len(counts)),key=lambda i:(-(exact[i]-percent[i]),i))[:100-sum(percent)]:percent[i]+=1
    rows=[{'point_index':None,'category_id':cat['id'],'label':cat['label'],'count':counts[i],'percent':percent[i],
           'group_indices':cat['group_indices']} for i,cat in enumerate(cats) if counts[i]]
    rows.append({'point_index':None,'category_id':None,'label':'其他','count':counts[-1],'percent':percent[-1],'group_indices':[]})
    order={c['id']:i for i,c in enumerate(cats)}
    rows.sort(key=lambda r:(r['category_id'] is None,-r['count'],order.get(r['category_id'],len(cats))))
    work_label=plan.get('work_label','电视剧')
    if work_label not in ('电视剧','网络剧','电影','综艺',''):raise ValueError('work_label无效')
    warnings=[]
    excluded_count=len(excluded)
    caption=f'注：按本期工作台观点来源归类统计（去重后{total}条，另有纯背景{excluded_count}条未计），非全网舆论占比。'
    if dataset.get('human_selection'):
        caption=f'注：仅统计人工选中的样本（去重后{total}条，另有纯背景{excluded_count}条未计），不代表原表或全网舆论占比。'
    return {'title':f'{work_label}《{dataset["target"]}》'+('人工选材观点分布' if dataset.get('human_selection') else '相关话题舆论观点分布'),'rows':rows,
            'profile':PROFILE,'dataset_sha256':dataset_hash,'population_claim':False,'denominator':total,
            'reviewed_source_count':len(by_source),'excluded_background_count':excluded_count,
            'other_count':counts[-1],'percentage_rounding':'largest_remainder_sum_100',
            'basis':('本图仅统计人工选中的样本，不代表原表或全网分布。' if dataset.get('human_selection') else '')+'本次输入展示来源按实际观点归类、来源ID去重；只有明确纯背景不计。与正文是否引用无关。',
            'caption':caption,'allocation':allocations,'excluded_sources':excluded,'review_warnings':warnings,
            'classification_review':review,'theme_policy':review.get('theme_policy')}

def prepare(dataset_path,plan_path,outdir):
    ds=read(dataset_path);plan=read(plan_path);dh=digest(dataset_path)
    if plan.get('dataset_sha256')!=dh:raise ValueError('既有成稿计划与工作台指纹不同')
    used={gi:i for i,p in enumerate(plan.get('points',[])) for gi in p.get('group_indices',[])}
    inventory={'target':ds['target'],'dataset_sha256':dh,'plan_sha256':digest(plan_path),
               'chart_contract':contract(),'boundary_candidates':boundary_candidates(ds),'existing_body_topics':[{'point_index':i,'claim':p['claim'],
               'chart_label':p['chart_label'],'group_indices':p['group_indices']} for i,p in enumerate(plan['points'])],
               'groups':[{'group_index':i,'category':g.get('category'),'stance':g['stance'],'aspect':g['aspect'],'core_mechanism':g.get('core_mechanism'),
                          'body_point_index':used.get(i),'writing_eligible':g.get('writing_guidance',{}).get('eligible'),
                          'samples':[{'candidate_id':s['candidate_id'],'source_id':s['source_id'],
                                      'channel':s['source_channel'],'display_excerpt':display(s),'primary_support_snippets':primary_snippets(g,s)} for s in g['samples']]}
                         for i,g in enumerate(ds['groups'])]}
    dest=Path(outdir);dest.mkdir(parents=True,exist_ok=True)
    (dest/'chart_inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    blank=empty_review(dh);blank['plan_sha256']=digest(plan_path)
    (dest/'chart_review.json').write_text(json.dumps(blank,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'status':'CHART_REVIEW_REQUIRED','read_now':str(dest/'chart_inventory.json'),
            'answer_file':str(dest/'chart_review.json'),'instruction':'只补全绘图归属，保持既有正文、工作台与计数原文；不读原表或基准。'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for name in ('prepare','check'):
        p=sub.add_parser(name);p.add_argument('--dataset',required=True);p.add_argument('--plan',required=True)
        p.add_argument('--outdir' if name=='prepare' else '--review',required=True)
    args=parser.parse_args()
    try:
        if args.command=='prepare':result=prepare(args.dataset,args.plan,args.outdir)
        else:
            plan=read(args.plan);review=read(args.review)
            if review.get('plan_sha256') and review['plan_sha256']!=digest(args.plan):raise ValueError('既有正文计划已经改变')
            plan['chart_review']=review;plan['chart_theme_policy']=THEME_POLICY;result=coverage_data(read(args.dataset),plan,digest(args.dataset))
            result={'status':'CHART_COVERAGE_VALID','denominator':result['denominator'],
                    'excluded_background_count':result['excluded_background_count'],'other_count':result['other_count'],
                    'rows':result['rows'],'review_warnings':result['review_warnings']}
        print(json.dumps(result,ensure_ascii=False))
    except (KeyError,ValueError) as exc:parser.error(str(exc))

if __name__=='__main__':main()
