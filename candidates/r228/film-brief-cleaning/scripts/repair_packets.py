"""Bounded model tasks and atomic answer merge. Never infer semantic decisions."""
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import tempfile

LOCKED = {'fingerprint','packet_id','profile','dataset_sha256','plan_sha256','draft_sha256',
          'word_sha256','review_state','chart_profile','chart_theme_policy','report_style',
          'history_sha256','period_comparison_sha256','target'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def value_at(value,pointer):
    for key in pointer.strip('/').split('/'):
        key=key.replace('~1','/').replace('~0','~')
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value



def bind_chart_metadata(answer):
    """Bind only transport constants; never infer chart classes or judgments."""
    chart=answer.get('chart_review')
    if isinstance(chart,dict):
        for outer,inner in [('dataset_sha256','dataset_sha256'),('chart_profile','profile'),('chart_theme_policy','theme_policy')]:
            if outer in answer:chart[inner]=deepcopy(answer[outer])
    return answer

def chart_revision_task(packet,answer,review,reviewed_draft):
    """A chart/header-only review must not rewrite approved body paragraphs."""
    issues=review.get('overall_issues',[])
    if not issues or any(r.get('action')!='none' for r in review.get('point_reviews',[])):
        return None
    kinds = set()
    label_indices = set()
    points = answer.get('points', [])
    for issue in issues:
        if not isinstance(issue, str):
            return None
        if re.search(r'图(?:表|左)?标签|chart_label', issue):
            indices = {int(i) for i in re.findall(r'point_index\s*[=:：]\s*(\d+)', issue)}
            valid = {i for i, point in enumerate(points) if 'chart_label' in point}
            label_indices.update(indices if indices and indices <= valid else valid)
        elif re.search(r'图表|图类|图注', issue):
            kinds.add('chart')
        elif not re.search(r'总标题|节标题|章节标题|概述', issue):
            return None
        if re.search(r'总标题|节标题|章节标题|概述', issue):
            kinds.add('heading')
    paths = [f'/points/{i}/chart_label' for i in sorted(label_indices)]
    if 'chart' in kinds:
        paths.append('/chart_review')
    if 'heading' in kinds:
        paths.append('/section_heading')
    if not paths or any(path.split('/')[1] not in answer for path in paths):
        return None
    return {'kind':'fields','scope':'chart_local','answer_sha256':digest(answer),'input_sha256':digest(packet),
            'instructions':'只修所列图标签、图表分类或概述标题；段首句、正文选样和归组不得改动。图标签从同段claim缩短并保留原动词。逐项返回列出的路径，未发现问题的字段保留原值。图表分母、来源及证据仍执行原校验；修改后重新生成并复核，不跳过任何审核。',
            'errors':{'overall_issues':issues},
            'context':dict(packet,current_outline=[{'claim':p.get('claim'),'group_indices':p.get('group_indices')} for p in answer.get('points',[])]),
            'allowed_fields':[{'path':path,'current':value_at(answer,path)} for path in paths],
            'answer_schema':{'updates':[{'path':'列出的精确path','value':'该字段完整新值'}]}}


def complete_answer(answer,reply):
    """The caller binds output to the exact input task; model never copies hashes."""
    result=deepcopy(answer)
    result.update({k:v for k,v in reply.items() if k not in LOCKED})
    return bind_chart_metadata(result)


def focused_reference_repair(item, error):
    """Keep original window numbers; exclude windows known to lack a usable reference."""
    if ('所选句自身没有' not in error.get('error','')
            or 'reference_eligible_window_numbers' not in item
            or not item.get('selection_options')):
        return None
    eligible=set(item['reference_eligible_window_numbers'])
    focused={key:value for key,value in item.items() if key!='selection_options'}
    focused['eligible_selection_windows']=[
        {'original_window_number':i, **option}
        for i,option in enumerate(item['selection_options'],1) if i in eligible]
    focused['reference_repair_rule']=(
        '原选段缺作品指代，修改解释不能通过。若保留，只能使用本处列出的合法窗口，'
        '整窗使用original_window_number作为selected_window，或提交其连续原句。'
        '仍须独立判断观点、立场和归组。不合适则reject并写实际内容理由。'
        '未列窗口没有任何能通过指代的原句，不得再次提交。')
    return focused


def review_task(packet,answer,errors):
    """Retain full evidence of failed/blank items; all other decisions stay frozen."""
    pending={r['candidate_id'] for r in answer['decisions'] if r.get('decision') not in ('keep','reject')}
    pending.update(e['candidate_id'] for e in errors if e.get('candidate_id'))
    items=[r for r in packet['items'] if r['candidate_id'] in pending]
    if {r['candidate_id'] for r in items} != pending:
        raise ValueError('repair errors refer to an unknown candidate')
    if not pending:raise ValueError('no outstanding review items')
    repaired_ids=set()
    remaining_errors=[]
    for error in errors:
        cid=error.get('candidate_id')
        index=next((i for i,item in enumerate(items) if item['candidate_id']==cid),None)
        focused=focused_reference_repair(items[index],error) if index is not None else None
        if focused is not None:
            repaired_ids.add(cid)
            items[index]=focused
            error={key:value for key,value in error.items()
                   if key not in ('selected_excerpt','actual_work_reference')}
        remaining_errors.append(error)
    visible={k:v for k,v in packet.items() if k not in ('items','answer_template','fingerprint','answer_file')}
    visible['items']=items
    visible['conditional_fields']=packet.get('conditional_fields',{})
    decisions=[deepcopy(row) for row in answer['decisions'] if row['candidate_id'] in pending]
    for row in decisions:
        if row['candidate_id'] in repaired_ids:
            cid=row['candidate_id']
            row.clear()
            row.update(candidate_id=cid,decision='',reason='',selected_window=None,
                       selected_excerpt='',evidence_focus='',target_scope=None)
    return {'kind':'review','answer_sha256':digest(answer),'input_sha256':digest(packet),
            'instructions':'读完每条完整证据，保留原审核标准。只返回decisions，每个列出的candidate_id恰好一次；未列条目由脚本保留。选句与作品指代必须真实，不能为通过字段而reject。修复包有original_window_number时保留该原编号。',
            'input':visible,'errors':remaining_errors,
            'answer_schema':{'decisions':decisions}}


def field_paths(answer,errors):
    """Select editable sections from concrete diagnostics; never choose new values."""
    text=errors if isinstance(errors,str) else json.dumps(errors,ensure_ascii=False)
    paths=[]
    for key in ('clusters','new_clusters'):
        for i,group in enumerate(answer.get(key,[])):
            lines=[s for s in text.splitlines() if re.search(r'(?<![\w-])'+re.escape(str(group.get('id','')))+r':',s)]
            for line in lines:
                fields=[];missing_field=False
                for name in ('category','category_reason','core_mechanism','performance_focus','seed_ids','title_evidence','keywords','seed_stance_review','objective_kind'):
                    if re.search(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])',line):
                        if name in group:fields.append(name)
                        else:missing_field=True
                if ('title当前' in line or '标题须' in line or '标题起句' in line) and 'title_evidence' not in line:fields.append('title')
                if '路由词' in line:fields.append('keywords')
                if fields and not missing_field:paths.extend(f'/{key}/{i}/{name}' for name in fields if name in group)
                else:paths.append(f'/{key}/{i}')  # Unknown or missing conditional field: keep this errored object editable.
    # A disallowed list member must be removable, not merely replaceable
    # in place. Make the whole new-cluster list editable for this diagnostic.
    if any(message in text for message in ('new_clusters须为数组','补缺只收positive',
                                            'new_clusters有重复或同作者证据')) and 'new_clusters' in answer:
        paths.append('/new_clusters')
        if 'history_selection' in text and 'history_selection' in answer:
            paths.append('/history_selection')
    if 'cluster_boundaries' in text and 'cluster_boundaries' in answer:paths.append('/cluster_boundaries')
    if 'facet_pair_review' in text and 'facet_pair_review' in answer:
        paths.append('/facet_pair_review')
        # A used/not-used assertion depends on the linked cluster's actual seeds.
        # Expose that cluster too; locking its seed_ids can make the repair impossible.
        for review in answer.get('facet_pair_review', []):
            if (not isinstance(review, dict) or review.get('decision') != 'used'
                    or not review.get('facet') or review['facet'] not in text):
                continue
            for key in ('clusters', 'new_clusters'):
                for i, group in enumerate(answer.get(key, [])):
                    if group.get('id') == review.get('group_id'):
                        paths.append(f'/{key}/{i}')
    if 'overlap_reviews' in text and 'overlap_reviews' in answer:paths.append('/overlap_reviews')
    if 'add_evidence' in text and 'add_evidence' in answer:paths.append('/add_evidence')
    for key,pattern in [('points',r'(?:提纲)?段\s*(\d+)|point\s+(\d+)|点(\d+)'),('groups',r'组\s*(\d+)')]:
        if key not in answer:continue
        for match in re.finditer(pattern,text):
            if key=='points':index=int(next(x for x in match.groups() if x is not None))-(1 if match.group(1) else 0)
            else:
                group_index=int(match.group(1))
                # Feedback names the persistent group ID, not its compact list position.
                if any('group_index' in row for row in answer[key]):
                    index=next((i for i,row in enumerate(answer[key]) if row.get('group_index')==group_index),-1)
                else:index=group_index
            if 0<=index<len(answer[key]):paths.append(f'/{key}/{index}')
    for word,key in [('section heading','section_heading'),('section_heading','section_heading'),
                     ('pairwise_review','pairwise_review'),('pairwise','pairwise_review'),
                     ('图表','chart_review'),('chart_review','chart_review'),('omitted_groups','omitted_groups')]:
        if word in text:
            if key in answer:paths.append('/'+key)
            elif key in answer.get('outline_review',{}):paths.append('/outline_review/'+key)
    for match in re.finditer(r'point_reviews\[(\d+)\]',text):
        i=int(match.group(1))
        if 0<=i<len(answer.get('point_reviews',[])):paths.append(f'/point_reviews/{i}')
    for i,group in enumerate(answer.get('groups',[])):
        if any(row.get('candidate_id') and row['candidate_id'] in text
               for row in group.get('reviews',[]) if isinstance(row,dict)):
            paths.append(f'/groups/{i}')
    for i,row in enumerate(answer.get('reviews',[])):
        if str(row.get('candidate_id','__missing__')) in text:paths.append(f'/reviews/{i}')
    chart_errors=[line.strip() for line in text.splitlines() if '图表：' in line]
    if ('/chart_review' in paths and isinstance(answer['chart_review'].get('other_sources'),list)
            and chart_errors and all(re.search(
                r'图表：other_sources(?:来源无效或重复|须绑定当前展示句内)',line) for line in chart_errors)):
        paths.remove('/chart_review')
        paths.append('/chart_review/other_sources')
    # An unrecognized error gets the complete editable answer, never an invented fix.
    if not paths:paths=['/'+k for k in answer if k not in LOCKED]
    return sorted(p for p in set(paths) if not any(p.startswith(parent+'/') for parent in paths if parent!=p))


def focus_paragraph_length(context, answer, errors, paths):
    """Keep all eligible quotations in affected groups for length-only repairs."""
    if not isinstance(errors,str) or not isinstance(context.get('groups'),list):
        return
    lines=[v.strip() for v in errors.splitlines() if v.strip()]
    header='正文：本次检测到以下问题，请集中修复后再完整校验：'
    starts=[i for i,v in enumerate(lines)
            if v in (header,'report_writing.py: error: '+header)]
    if len(starts)!=1 or any(not v.startswith('usage: report_writing.py ') for v in lines[:starts[0]]):
        return
    problems=lines[starts[0]+1:]
    matches=[re.match(r'^段(\d+)：点(\d+)实际\d+字，目标.*常规范围', v)
             for v in problems]
    if not matches or not all(m and int(m[1])==int(m[2])+1 for m in matches):
        return
    indices={int(m[2]) for m in matches}
    if set(paths)!={f'/points/{i}' for i in indices}:
        return
    if context.get('dataset_sha256')!=answer.get('dataset_sha256'):
        return
    group_ids={gi for i in indices for gi in answer['points'][i].get('group_indices',[])}
    groups=[g for g in context['groups'] if g.get('group_index') in group_ids]
    if not groups or {g['group_index'] for g in groups}!=group_ids:
        return
    context['groups']=groups
    # Chart, global review and all other points are still present in the
    # original answer and checked by the same full validator after merge.
    context.pop('chart_boundary_candidates',None)
    context.pop('chart_contract',None)
    context['repair_scope_rule']=(
        '这次仅修所列段落字数，原段落归组与核心判断不变。仅使用给出的同组已审原句，'
        '不增加原表材料；增删引文时同步更新该段claim_support。'
        '有实质材料再展开，不堆泛赞；确实不足按原规则说明。'
        '未列段落与图表由外层保持不变，修改后仍完整校验。'
        '若当前超长，先查同一组同一分句是否引用两条以上；优先删掉重复、且已有另一条有效证据的引文，'
        '保留每个实际组的至少一条preferred具体支持和每个分句的证据。只有确无重复再压缩到完整具体判断。'
        '不要在补字要求与缩短要求之间来回换句。')


def focus_editor_repair(context, answer, errors, paths):
    """Retain directly implicated groups, their merges, and risk-pair peers."""
    if (not isinstance(errors, str)
            or not errors.startswith('只修以下末端答卷项，不重审其他材料：')
            or not isinstance(context.get('groups'), list)
            or not all(re.fullmatch(r'/groups/\d+', path) for path in paths)):
        return
    touched = {answer['groups'][int(path.split('/')[2])]['group_index'] for path in paths}
    if not touched:
        return
    selected = set(touched)
    for pair in context.get('overlap_pairs', []):
        if touched.intersection(pair):
            selected.update(pair)
    # Include previous merge donors so a still-valid support ID remains visible.
    for row in answer['groups']:
        if row.get('merge_into') in selected:
            selected.add(row['group_index'])
        if row['group_index'] in touched and row.get('merge_into') is not None:
            selected.add(row['merge_into'])
    context['groups'] = [row for row in context['groups'] if row['group_index'] in selected]
    context['overlap_pairs'] = [pair for pair in context.get('overlap_pairs', [])
                                if set(pair) <= selected]
    # A group may cite a valid old sentence from the shared history pool.
    # Preserve that pool even when the current groups are narrowed.
    context['editor_repair_rule'] = (
        '只修列出的答卷组。相邻组仅供核对原有边界或合并关系，不改已通过组。'
        'preferred从retained_evidence逐字复制本组最终保留的display_excerpt片段，'
        '不要重新拼句、改标点或引入未展示上下文。如果没有具体且可安全入稿的连续原句，'
        '设eligible=false、standalone_eligible=false、preferred=[]并说明，保留其它可展示证据。')


def focus_local_writing_repair(context, answer, errors, paths):
    """Keep all workbench evidence for paragraphs affected by local diagnostics."""
    if (not isinstance(errors,str) or context.get('source_scope')!='FINAL_SELECTED_ONLY'
            or context.get('dataset_sha256')!=answer.get('dataset_sha256')
            or not isinstance(context.get('groups'),list)):
        return
    points={int(path.split('/')[2]) for path in paths if re.fullmatch(r'/points/\d+',path)}
    other_path='/chart_review/other_sources'
    repair_other=other_path in paths
    if len(points)+int(repair_other)!=len(paths) or not paths:
        return
    group_ids={index for point in points for index in answer['points'][point]['group_indices']}
    if repair_other:
        chart=answer['chart_review']
        related_ids={row.get('candidate_id') for row in chart['other_sources'] if isinstance(row,dict)}
        boundary=context.get('chart_boundary_candidates',[])
        group_ids.update(row['group_index'] for row in boundary)
        group_ids.update(row['group_index'] for row in context['groups']
                         if any(sample['candidate_id'] in related_ids for sample in row['samples']))
        context['fixed_chart_routes']={
            'categories':[{key:row.get(key) for key in ('id','label','group_indices')}
                          for row in chart.get('categories',[])],
            'excluded_sources':[row['candidate_id'] for row in chart.get('excluded_sources',[])],
            'source_overrides':[row['candidate_id'] for row in chart.get('source_overrides',[])],
            'boundary_review':chart.get('boundary_review',[])}
    groups=[group for group in context['groups'] if group['group_index'] in group_ids]
    if not groups or {group['group_index'] for group in groups}!=group_ids:
        return
    context['groups']=groups
    if not repair_other:
        context.pop('chart_boundary_candidates',None)
        context.pop('chart_contract',None)
    context['local_writing_repair_rule']=(
        '仅调整列出的正文段。保留其它段、整篇提纲和图表；逐条修本次反馈。'
        '当前组内全部已审证据均可选，不回原表。改标题或引文时同步修复逐分句证据。'
        '如反馈涉及相邻段的起句，按current_outline检查原顺序，选适用的不同动词。')
    if repair_other:
        context['local_writing_repair_rule']+=(
            '本次图表只修other_sources中的ID或逐字证据。保留已通过行。'
            '逐字复制本包已有ID及其连续展示片段，不能与固定去向重复。')


def field_task(packet,answer,errors):
    paths=field_paths(answer,errors)
    context=deepcopy(packet)
    context.pop('answer_schema',None);context.pop('answer_template',None)
    focus_paragraph_length(context,answer,errors,paths)
    focus_local_writing_repair(context,answer,errors,paths)
    clusters=answer.get('clusters',answer.get('new_clusters',[]))
    if clusters:
        context['current_viewpoints']=[{k:g.get(k) for k in ('id','title','category','core_mechanism','seed_ids')} for g in clusters]
        touched={int(p.split('/')[2]) for p in paths if p.startswith(('/clusters/','/new_clusters/'))}
        seeds={s for i,g in enumerate(clusters) if not touched or i in touched for s in g.get('seed_ids',[])}
        if '/facet_pair_review' in paths:
            seeds.update(sid for ids in context.get('sparse_facet_checks',{}).values() for sid in ids)
        for key in ('excerpts','discovery_excerpts'):
            if isinstance(context.get(key),list) and seeds and '/cluster_boundaries' not in paths:
                context[key]=[v for v in context[key] if v.get('excerpt_id') in seeds]
    if '/cluster_boundaries' in paths:
        context['boundary_repair_rule'] = (
            'cluster_boundaries是完整数组。反馈为缺少风险对时保留已有合格项，逐对追加缺项；'
            '只提交新增项会使之前的通过项丢失。已有项有误时只修对应项。')
    if 'points' in answer:
        context['current_outline']=[{k:p.get(k) for k in ('claim','group_indices')} for p in answer['points']]
    allowed_fields = [{'path':p,'current':value_at(answer,p)} for p in paths]
    if packet.get('title_policy') == 'FINAL_DISPLAY_EVIDENCE_V2':
        focus_editor_repair(context, answer, errors, paths)
        for field in allowed_fields:
            current = field['current']
            if (re.fullmatch(r'/groups/[0-9]+',field['path']) and isinstance(current,dict)
                    and (('title_candidates' in current and 'samples' in current)
                         or ('retained_evidence' in current and 'editable_samples' in current))):
                # The evidence is already in context. Do not present source-only
                # keys echoed by the model as fields to submit again.
                field['current'] = {key:value for key,value in current.items()
                                    if key not in ('title_candidates','samples','retained_evidence',
                                                   'editable_samples','total_selected')}
        context['editor_answer_rule'] = (
            '每个允许修改的group_index都要交该组完整审核答卷。reviews仅逐项对应editable_samples'
            '（旧包为samples）。非合并组的标题和入稿证据从全部retained_evidence（旧包为title_candidates）选，'
            '其中未列为editable的样本仍然保留。直接引用ID和原句，不把输入组复制到答卷。')
    return {'kind':'fields','answer_sha256':digest(answer),'input_sha256':digest(packet),
            'instructions':'只修改allowed_fields列出的字段并返回updates；未改字段无需提交。保留原有证据判断，不能伪造指纹、重写已通过项或删除审核。语义改动后仍执行原完整校验。',
            'errors':errors,'context':context,
            'allowed_fields':allowed_fields,
            'answer_schema':{'updates':[{'path':'列出的精确path','value':'该字段完整新值'}]}}


def prose_revision_task(packet, answer, review, reviewed_draft):
    """Send only identified paragraphs back for a validated local prose revision."""
    if review.get('decision') != 'revise' or not isinstance(packet.get('groups'),list):
        return None
    if packet.get('dataset_sha256') != answer.get('dataset_sha256'):
        return None
    chart_task=chart_revision_task(packet,answer,review,reviewed_draft)
    if chart_task is not None:return chart_task
    if any(re.search(r'总标题|章节标题|图表|图注', issue) for issue in review.get('overall_issues', [])):
        return None
    points = answer.get('points', [])
    paragraphs = reviewed_draft.get('paragraphs', [])
    if len(points) != len(paragraphs):
        return None
    # The rendered order can differ from the plan order. Only use local
    # pointers when the index and actual selected evidence agree.
    for point, paragraph in zip(points, paragraphs):
        raw_ids = [sample.get('candidate_id') for sample in point.get('samples', [])]
        rendered_ids = [sample.get('candidate_id') for sample in paragraph.get('samples', [])]
        if point.get('claim') != paragraph.get('claim') or raw_ids != rendered_ids:
            return None
    rows = [row for row in review.get('point_reviews', []) if row.get('action') != 'none']
    if not rows or any(row.get('action') != 'revise_plan' for row in rows):
        return None
    indices = sorted(row['point_index'] for row in rows)
    group_ids = {gi for i in indices for gi in points[i]['group_indices']}
    context = {key: value for key,value in packet.items()
               if key not in ('groups','chart_boundary_candidates','chart_contract')}
    context['groups'] = [group for group in packet['groups'] if group['group_index'] in group_ids]
    context['current_outline'] = [{k:p.get(k) for k in ('claim','group_indices')} for p in points]
    paths = [f'/points/{i}' for i in indices]
    return {'kind':'fields','scope':'prose_local','answer_sha256':digest(answer),'input_sha256':digest(packet),
            'instructions':'只落实已通过校验的终审返修项。删掉重复、资讯或越界引文后保留至少两份真正支持同一判断的独立声音；不要把已指出的问题句换成近义表态来补长度。确实需要保留有据短段时按原规则写具体字数例外理由。保持观点归组；改了引文或判断就同步更新该段分句绑定。脚本仍完整校验，实际修订稿仍须全文终审。',
            'errors':{'point_reviews':rows,'overall_issues':review.get('overall_issues', [])},'context':context,
            'allowed_fields':[{'path':p,'current':value_at(answer,p)} for p in paths],
            'answer_schema':{'updates':[{'path':'列出的精确path','value':'该段完整新值'}]}}


QUOTE_FIELDS={'excerpt','display_excerpt','quote','quote_snippet','selected_excerpt','text',
              'selected_quote','opinion_snippet','selection_text','local_target_context'}


def candidate_aliases(task):
    """A per-request bijection, excluding user-visible source strings."""
    ids=[]
    strings=set()
    def visit(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key=='candidate_id' and isinstance(item,str) and item.strip() and item not in ids:
                    ids.append(item)
                visit(item)
        elif isinstance(value,list):
            for item in value:
                visit(item)
        elif isinstance(value,str):
            strings.add(value)
    visit(task)
    aliases={}
    for i,cid in enumerate(ids,1):
        short=f'sample_{i:03d}'
        while short in strings:
            short='_'+short
        aliases[short]=cid
    return aliases


def map_candidate_values(value,mapping,field=None):
    if field in QUOTE_FIELDS:
        return deepcopy(value)
    if isinstance(value,dict):
        return {mapping.get(key,key):map_candidate_values(item,mapping,key)
                for key,item in value.items()}
    if isinstance(value,list):
        return [map_candidate_values(item,mapping,field) for item in value]
    if isinstance(value,str):
        if value in mapping:
            return mapping[value]
        for original,replacement in mapping.items():
            value=re.sub(r'(?<![0-9A-Za-z_])'+re.escape(original)+r'(?![0-9A-Za-z_])',
                         lambda match:replacement,value)
        return value
    return value


def model_view(task):
    """Only shorten opaque references; the bound task stays in original IDs."""
    result=map_candidate_values(task,{cid:short for short,cid in candidate_aliases(task).items()})
    result['evidence_id_rule']=(
        'candidate_id及引用ID只用当前包sample_编号，逐字复制，不自行扩展或改成哈希。'
        '编号由外层按本包一一还原，原文、判断和所有审核要求均保持不变。')
    return result


def merge(task,answer,reply):
    if digest(answer)!=task['answer_sha256']:raise ValueError('answer changed after task preparation')
    reply=map_candidate_values(reply,candidate_aliases(task))
    result=deepcopy(answer)
    if task['kind']=='review':
        expected={r['candidate_id'] for r in task['answer_schema']['decisions']}
        rows=reply.get('decisions',[])
        if not isinstance(rows,list) or len(rows)!=len(expected) or any(not isinstance(r,dict) for r in rows):raise ValueError('decisions数量/对象不符')
        if {r.get('candidate_id') for r in rows}!=expected:raise ValueError('不能修改已通过项或漏审当前条目')
        lookup={r['candidate_id']:r for r in rows}
        result['decisions']=[lookup.get(r['candidate_id'],r) for r in answer['decisions']]
    elif task['kind']=='fields':
        allowed={r['path'] for r in task['allowed_fields']};seen=set()
        rows=reply.get('updates',[])
        if not isinstance(rows,list) or not rows:raise ValueError('updates须非空')
        for row in rows:
            path=row.get('path')
            if path not in allowed or path in seen or 'value' not in row:raise ValueError('updates包含未授权字段、重复字段或缺value')
            seen.add(path);parts=path.strip('/').split('/');parent=result
            for part in parts[:-1]:parent=parent[int(part)] if isinstance(parent,list) else parent[part]
            key=int(parts[-1]) if isinstance(parent,list) else parts[-1]
            if task.get('scope') == 'prose_local':
                value = row['value']
                if not isinstance(value,dict) or value.get('group_indices') != parent[key]['group_indices']:
                    raise ValueError('局部终审返修保持原段观点归组')
            parent[key]=row['value']
        if task.get('scope') in ('prose_local','chart_local'):
            if seen != allowed:
                raise ValueError(f'局部终审返修缺少以下段落更新：{sorted(allowed-seen)}；逐项覆盖本轮路径')
            if result == answer:
                raise ValueError('局部终审返修与原计划完全相同，须落实所列问题并实际修改计划')
    else:raise ValueError('unknown task kind')
    if any(result.get(k)!=answer.get(k) for k in LOCKED):raise ValueError('immutable binding changed')
    return bind_chart_metadata(result)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('--input',required=True);p.add_argument('--answer',required=True);p.add_argument('--feedback',required=True);p.add_argument('--out',required=True);p.add_argument('--kind',choices=['review','fields'],required=True)
    p=sub.add_parser('apply');p.add_argument('--task',required=True);p.add_argument('--answer',required=True);p.add_argument('--reply',required=True)
    args=parser.parse_args()
    if args.action=='prepare':
        packet,answer,feedback=read(args.input),read(args.answer),read(args.feedback)
        if args.kind=='review':
            errors=feedback if isinstance(feedback,list) else feedback.get('errors',[])
            ids={r['candidate_id'] for r in packet['items']}
            errors=[e for e in errors if e.get('candidate_id') in ids]
            task=review_task(packet,answer,errors)
        else:task=field_task(packet,answer,feedback)
        save(args.out,task)
        model_file=Path(args.out).with_name(Path(args.out).stem+'.model.json')
        save(model_file,model_view(task))
        print(json.dumps({'task':args.out,'read_now':str(model_file),'kind':args.kind}))
    else:
        target=Path(args.answer)
        if target.suffix!='.json' or not ('answer' in target.name or target.name in ('editorial_plan.json','final_prose_review.json')):raise ValueError('只能修改答卷')
        before=target.read_bytes();result=merge(read(args.task),read(target),read(args.reply))
        if target.read_bytes()!=before:raise ValueError('concurrent answer writer')
        save(target,result);print(json.dumps({'status':'MERGED_REVALIDATION_REQUIRED','semantic_decisions_inferred':False}))


if __name__=='__main__':main()
