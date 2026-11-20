"""Prior published outputs as a comparison ledger, never as current evidence."""
from __future__ import annotations
from pathlib import Path
from collections import Counter
import hashlib,json,re
from source_policy import sample_keys,source_keys

def fingerprint(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def read_history_dataset(path,target):
    """Import published Word opinions or a verified old workbench for comparison."""
    if path.suffix.lower() == '.docx':
        from published_report_history import read_published_report
        return read_published_report(path, target)
    text=path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() not in ('.html','.htm'):return json.loads(text)
    match=re.search(r'\b(?:const|let|var)\s+DATA\s*=\s*',text)
    if not match:raise ValueError('历史HTML没有可核的DATA数据块，不能凭页面标题猜测样本')
    payload,_=json.JSONDecoder().raw_decode(text[match.end():])
    batches=payload.get('batches',[]) if isinstance(payload,dict) else []
    if len(batches)!=1 or batches[0].get('name')!=target:raise ValueError('历史HTML须为同一作品的单期工作台')
    groups=[]
    for gi,cluster in enumerate(batches[0].get('clusters',[])):
        samples=[]
        for si,item in enumerate(cluster.get('items',[])):
            field=item.get('textField') or '正文内容';body=item.get('sourceText')
            if not isinstance(body,str):raise ValueError('历史工作台缺少原文文本，不能可靠识别同一来源')
            source_id=str(item.get('sourceId') or f'import-{gi}-{si}')
            record={'id':source_id,'channel':item.get('channel',''),
                'source_file':item.get('sourceFile',''),'sheet':item.get('sourceSheet',''),'row':item.get('sourceRow'),
                'data':{field:body,'链接':item.get('sourceUrlText') or item.get('url') or '',
                        '作者':item.get('author',''),'标题':item.get('title',''),'发表时间':item.get('publishedAt','')}}
            samples.append({'candidate_id':f'historical-{gi}-{si}-{source_id}','source_id':source_id,
                'source':record,'text_field':field,'excerpt':item.get('excerpt',''),
                'display_excerpt':item.get('displayExcerpt') or item.get('excerpt','')})
        stance={'positive':'正向词倾向','objective':'中性或混合','negative':'负向词倾向'}.get(cluster.get('stance'))
        if not stance:raise ValueError('历史工作台立场字段未知')
        groups.append({'aspect':cluster['title'],'category':cluster.get('category',''),'core_mechanism':cluster.get('coreMechanism',''),'stance':stance,'samples':samples})
    return {'target':target,'groups':groups,'historical_import_scope':'COMPARISON_ONLY_NOT_A_NEW_REVIEW'}

def load_history(dataset_paths,target,plan_paths=()):
    plans={}
    for value in plan_paths:
        p=Path(value);plan=json.loads(p.read_text(encoding='utf-8-sig'))
        if plan.get('target')!=target or not plan.get('dataset_sha256'):raise ValueError('历史编辑计划须与目标作品相同并保留数据指纹')
        digest=plan['dataset_sha256']
        if not isinstance(plan.get('points'),list) or not plan['points']:raise ValueError('历史实际成稿计划不能是空白模板')
        plans.setdefault(digest,[]).append((p,plan))
    entries=[];display_uses=Counter();written_uses=Counter();seen=set();matched_plans=set()
    for value in dataset_paths:
        path=Path(value)
        if path.is_dir():path=path/'final_dataset.json'
        data=read_history_dataset(path,target);sha=fingerprint(path)
        from published_report_history import REPORT_SCOPE
        published=data.get('historical_import_scope')==REPORT_SCOPE
        if sha in seen:continue
        seen.add(sha)
        if data.get('target')!=target or not isinstance(data.get('groups'),list):raise ValueError('时间对照须提供同一作品的已交付Word报告、单期工作台HTML或final_dataset.json')
        source_ids={s['candidate_id']:s for g in data['groups'] for s in g['samples']}
        all_keys=set().union(*(sample_keys(s) for s in source_ids.values())) if source_ids else set()
        # Two reports can use precisely the same exported workbench. Keep both
        # actual plans instead of collapsing their distinct editorial choices.
        if published and sha in plans:
            raise ValueError('历史Word已经是实际成稿，无需搭配 --history-plan')
        for variant in plans.get(sha,[None]):
            display_uses.update(all_keys)
            written_keys=set();written_groups=set();written_titles=[];plan_entry=None
            if published:
                written_keys=all_keys.copy()
                written_groups=set(range(len(data['groups'])))
                written_titles=[group['aspect'] for group in data['groups']]
                written_uses.update(written_keys)
            elif variant is not None:
                pp,plan=variant;matched_plans.add(sha)
                for point in plan['points']:
                    written_titles.append(str(point.get('claim') or ''))
                    written_groups.update(point.get('group_indices',[]))
                    for chosen in point.get('samples',[]):
                        cid=chosen.get('candidate_id')
                        if cid not in source_ids:raise ValueError('历史编辑计划引用了对应工作台之外的样本')
                        written_keys.update(sample_keys(source_ids[cid]))
                written_uses.update(written_keys);plan_entry={'path':str(pp.resolve()),'sha256':fingerprint(pp)}
            entries.append({'path':str(path.resolve()),'sha256':sha,'actual_plan':plan_entry,'published_report':published,
                'displayed_groups':[{'title':g['aspect'],'category':g.get('category',''),'stance':g['stance'],
                                     'used_in_report':i in written_groups if published or plan_entry else None}
                                    for i,g in enumerate(data['groups'])],
                'written_titles':written_titles,'displayed_keys':sorted(all_keys),'written_keys':sorted(written_keys)})
    if set(plans)!=matched_plans:raise ValueError('历史编辑计划的数据指纹没有对应的历史工作台')
    return {'target':target,'entries':entries,'display_uses':dict(display_uses),'written_uses':dict(written_uses),
            'scope':'PREVIOUS_OUTPUT_COMPARISON_ONLY','evidence_policy':'所有本期观点和引用仍须来自本期工作台；历史不补入新样本'}

def source_history_uses(record,history):
    keys=source_keys(record)
    return max((3*history.get('written_uses',{}).get(k,0)+history.get('display_uses',{}).get(k,0) for k in keys),default=0)

def annotate_sources(sources,history):
    if history:
        for record in sources.values():record['_history_uses']=source_history_uses(record,history)
    return sources

def continuity_review(dataset,history):
    displayed=set(history.get('display_uses',{}));written=set(history.get('written_uses',{}));rows=[]
    for i,g in enumerate(dataset['groups']):
        samples=g['samples'];new_display=[];new_written=[]
        for s in samples:
            keys=sample_keys(s)
            if not keys & displayed:new_display.append(s['candidate_id'])
            if not keys & written:new_written.append(s['candidate_id'])
        rows.append({'group_index':i,'aspect':g['aspect'],'category':g.get('category',''),
                     'sample_count':len(samples),'not_previously_displayed_ids':new_display,
                     'not_previously_written_ids':new_written if written else None})
    new_count=sum(len(row['not_previously_displayed_ids']) for row in rows)
    return {'previous_periods':len(history.get('entries',[])),'previous_actual_plans':sum(bool(e['actual_plan']) for e in history.get('entries',[])),
            'groups':rows,'not_previously_displayed_samples':new_count,
            'no_observed_new_display_sources':new_count==0,
            'previous_written_titles':[t for e in history.get('entries',[]) for t in e['written_titles']],
            'previous_groups':[g for e in history.get('entries',[]) for g in e['displayed_groups']],
            'instruction':'先独立判断本期证据，再结合实际前稿已用点位选择当天重点；同等质量下优先新来源、此前未展开的独立角度。相同来源也可有新原句，但不据此宣称新增舆情。无实际变化时写持续关注，不随机换分类、改写原句或编造趋势。不要求三天所有观点不同。'}
