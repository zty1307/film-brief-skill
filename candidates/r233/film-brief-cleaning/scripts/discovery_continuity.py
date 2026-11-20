"""Historical viewpoint meaning enters the existing current discovery, not a second AI pass."""
from pathlib import Path
import hashlib,json
VERSION='r109-all-excerpt-history-hints-v2'
RULE='''先依据本期E编号形成独立评价，再在同一次命名中对照previous_viewpoints的全部展示句。目标是本期聚类点位的具体判断有所变化，不是写稿换重点，也不是换作者、例子、演员名、同义词或把旧总括簇拆成子例。优先保留本期证据明确且前期未展示的独立判断，或在旧问题上有实质机制展开的判断；必要的主要延续观点仍可保留，不能为避重复丢掉重要真实评价。没有足够不同点位就如实解释延续，不设新增配额。沿用当前发现包的最终正面门槛，表演最多2簇，不拆近义凑数。分类方面不是点位，换category不构成新角度。
前期句子仅供意义比较，不能进入本期seed_ids、keywords、title_evidence或最终候选。本期标题每个判断仍须有当前E原句依据。每簇history_review记录continuing/developed/opposing/unseen_angle、previous_group_ids、至少15字reason、当前current_evidence和必要的previous_evidence。continuing=同一评价延续；developed=具体判断或机制有实质展开，换细节例子不自动算；opposing=同对象的不同方向评价，不声称声量反转；unseen_angle=前期给定展示材料未出现，不表示全网新增。非unseen须引至少一条匹配前簇的历史原句；unseen不得虚构前期匹配。
答卷history_selection写至少20字selection_summary，解释本期为何保留这些点以及真实差异的限度；omitted_previous逐一说明未匹配前期簇为何未继续保留，不能只写为避免重复。没有新角度时明确保留延续，不反复再命名。上述比较在本次观点发现内完成，不新增全面来源复审，只使用已输入并核对过的前期比较材料，不读旧原表或本期人工基准。历史输入是已交付Word时，H句来自前期刊发段落，关系仅以这些段落为比较范围。'''
RULE += "\n每条本期E附history_candidates，是脚本跨全部旧展示句检索的阅读提示，分数不判语义。先核对E提示及实际读到的相关旧句，写checked_candidates；交卷后按实际核心检索出的紧凑反馈须一次补齐，可多核对。明确irrelevant并写真实区别时可不匹配，相关旧句须纳入previous_group_ids，不能只对照同分类或同立场旧标题。旧中性簇也可能包含同一具体判断，必须读提示的实际句子。continuing允许换新例子、更具体证据或播前到播后；developed仅用于确有新增判断/机制，填development_type和change_statement；不设新增或发展配额。"
RULE += "\n首次交卷后脚本还会按实际title/core跨全部旧展示句检索，若须补核对，只读history_candidate_feedback.json一次完成所列簇的checked_candidates，不重读整个历史包、不重命名、不重审样本。这是历史匹配字段核对，不新增全面复审。提示不相干时如实irrelevant，不为延续或差异配额硬匹配。"
RULE += '\n本版current_evidence和previous_evidence由脚本依据本簇已明确选择的seed及已实际核对为相关的checked_candidates回填逐字绑定，不需重复抄写这两个数组。脚本不推断关系、相关性或新增机制，仍须你完成所有checked_candidates及关系理由。'
RELATIONS={'continuing'  ,'developed','opposing','unseen_angle'}

def bind_explicit_evidence(group,current,old):
 r=group.get('history_review')
 if not isinstance(r,dict):return
 if 'current_evidence' not in r:
  ids=list(dict.fromkeys(sid for ev in group.get('title_evidence',[]) for sid in ev.get('seed_ids',[]) if sid in group.get('seed_ids',[]) and sid in current))
  r['current_evidence']=[{'excerpt_id':sid,'quote_snippet':current[sid]['excerpt']} for sid in ids[:2]]
  r['current_evidence_bound_by_script']=True
 if 'previous_evidence' not in r:
  groups=set(r.get('previous_group_ids',[]))
  ids=[v.get('history_sample_id') for v in r.get('checked_candidates',[]) if isinstance(v,dict) and v.get('relation') in ('continuing','developed','opposing') and v.get('history_sample_id') in old and old[v['history_sample_id']][0] in groups]
  r['previous_evidence']=[{'history_sample_id':sid,'quote_snippet':old[sid][1]['text']} for sid in dict.fromkeys(ids)]
  r['previous_evidence_bound_by_script']=True
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build_context(workspace,target):
 p=Path(workspace)/'period_history_context.json'
 if not p.exists():return None
 from period_history import read_history_dataset
 context=json.loads(p.read_text(encoding='utf-8'));previous=[];inputs=[];seen=set()
 for entry in context['entries']:
  path=Path(entry['path'])
  if sha(path)!=entry['sha256']:raise ValueError('前期对照文件指纹已改变，须重新prepare')
  if entry['sha256'] in seen:continue
  seen.add(entry['sha256']);hi=len(inputs)+1;inputs.append({'path':str(path),'sha256':entry['sha256']})
  data=read_history_dataset(path,target)
  for gi,g in enumerate(data['groups']):
   previous.append({'id':f'H{hi}-G{gi}','title':g['aspect'],'category':g.get('category',''),'stance':g['stance'],'core_mechanism':g.get('core_mechanism',''),
    'displayed_excerpts':[{'id':f'H{hi}-G{gi}-S{si}','text':s.get('display_excerpt') or s['excerpt']} for si,s in enumerate(g['samples'])]})
 return {'version':VERSION,'history_inputs':inputs,'previous_viewpoints':previous,'scope':'PREVIOUS_DISPLAYED_VIEWPOINTS_ONLY_NOT_CURRENT_EVIDENCE'} if previous else None

def validate_history(answer,clusters,current,context):
 if not context:return None
 for item in context['history_inputs']:
  if sha(item['path'])!=item['sha256']:raise ValueError('前期对照文件指纹改变，不能使用原比较')
 previous={g['id']:g for g in context['previous_viewpoints']};matched=set();reviews=[]
 from evidence_quality import required_history_checks
 all_old={s['id']:(g['id'],s) for g in previous.values() for s in g['displayed_excerpts']}
 for g in clusters:bind_explicit_evidence(g,current,all_old)
 missing=[]
 for g in clusters:
  required=required_history_checks(g,context.get('seed_history_candidates',{}),context)
  checked={(v.get('history_sample_id')) for v in (g.get('history_review') or {}).get('checked_candidates',[]) if isinstance(v,dict)}
  absent=[{**v,'text':all_old[v['history_sample_id']][1]['text']} for v in required if v['history_sample_id'] not in checked]
  if absent:missing.append({'viewpoint_id':g['id'],'candidates':absent})
 if missing:
  feedback=Path(context['comparison_feedback_path']) if context.get('comparison_feedback_path') else None
  message='按本期实际标题和core从全部旧展示句补召回候选，请在同一答卷history_review.checked_candidates一次核对全部所列簇（相关则纳入previous_group_ids，不相关解释区别；只修已有观点的历史对照，不重新发现/审样）。'
  if feedback:
   feedback.write_text(json.dumps({'instruction':message,'groups':missing},ensure_ascii=False,indent=2),encoding='utf-8')
   raise ValueError(message+'只读这个紧凑反馈文件：'+str(feedback))
  raise ValueError(message+json.dumps(missing,ensure_ascii=False))
 for g in clusters:
  r=g.get('history_review')
  if not isinstance(r,dict) or r.get('relation') not in RELATIONS or len(str(r.get('reason') or '').strip())<15:raise ValueError(f"{g['id']}: history_review须填写关系和至少15字的具体机制对照")
  ids=r.get('previous_group_ids')
  if not isinstance(ids,list) or len(set(ids))!=len(ids) or any(i not in previous for i in ids):raise ValueError(f"{g['id']}: 历史匹配只能引用包内H-G编号")
  if (r['relation']=='unseen_angle') != (not ids):raise ValueError(f"{g['id']}: 前期未展示不引用前簇，其他关系必须有匹配")
  required=required_history_checks(g,context.get('seed_history_candidates',{}),context)
  checks=r.get('checked_candidates',[])
  if not isinstance(checks,list) or len({v.get('history_sample_id') for v in checks})!=len(checks):raise ValueError('checked_candidates须按历史句ID逐一核对，不能重复')
  checked={v.get('history_sample_id'):v for v in checks}
  if not {v['history_sample_id'] for v in required}<=set(checked):raise ValueError(f"{g['id']}: 必须核对本簇seed检索出的历史原句（跨全部分类和立场）："+str(required))
  for sid,v in checked.items():
   if sid not in all_old or v.get('relation') not in ('continuing','developed','opposing','irrelevant') or len(str(v.get('reason','')).strip())<12:raise ValueError('历史候选核对须指向真实H句，并解释具体相同判断或区别')
   if v['relation']!='irrelevant' and all_old[sid][0] not in ids:raise ValueError(f"{g['id']}: 已认出相关旧句却未纳入previous_group_ids：{sid}")
  if r['relation']=='developed':
   if r.get('development_type') not in ('changed_judgment','new_mechanism') or len(str(r.get('change_statement','')).strip())<12:
    raise ValueError('developed须说明真实新增判断或机制；换演员、例子、集数和语气只算continuing')
  evidence=r.get('current_evidence')
  if not isinstance(evidence,list) or not evidence:raise ValueError(f"{g['id']}: 必须绑定本期E证据")
  for ev in evidence:
   eid=ev.get('excerpt_id');quote=ev.get('quote_snippet')
   if eid not in g.get('seed_ids',[]) or not isinstance(quote,str) or len(quote)<12 or quote not in current[eid]['excerpt']:raise ValueError(f"{g['id']}: 当前比较证据须为本簇seed的至少12字连续摘录，不能用历史句或上下文补写")
  old=r.get('previous_evidence',[]);old_index={s['id']:s for i in ids for s in previous[i]['displayed_excerpts']}
  if not isinstance(old,list) or (ids and not old) or (not ids and old):raise ValueError(f"{g['id']}: 非未展示角度需匹配历史展示原句")
  for ev in old:
   q=ev.get('quote_snippet');sid=ev.get('history_sample_id')
   if sid not in old_index or not isinstance(q,str) or len(q)<12 or q not in old_index[sid]['text']:raise ValueError(f"{g['id']}: 历史证据须来自所匹配前簇的至少12字连续原句")
  matched.update(ids);reviews.append({'viewpoint_id':g['id'],**r})
 selection=answer.get('history_selection')
 if not isinstance(selection,dict) or len(str(selection.get('selection_summary') or '').strip())<20:raise ValueError('history_selection须解释取舍和真实差异限度，至少20字')
 omitted=selection.get('omitted_previous');expected=set(previous)-matched
 if not isinstance(omitted,list) or len(omitted)!=len(expected) or {r.get('previous_group_id') for r in omitted}!=expected or any(len(str(r.get('reason') or '').strip())<12 for r in omitted):raise ValueError('omitted_previous须覆盖全部未匹配的前期簇并逐条说明实际取舍理由')
 return {'version':VERSION,'stage':'CURRENT_VIEWPOINT_DISCOVERY','scope':'CURRENT_SEEDS_AND_PREVIOUS_DISPLAYED_EXCERPTS','history_inputs':context['history_inputs'],
 'previous_viewpoints':[{k:v for k,v in g.items() if k!='displayed_excerpts'} for g in context['previous_viewpoints']],
 'group_reviews':reviews,'selection':selection,'population_novelty_claim':False,'note':'历史关系在命名阶段判断；最终标题仍由本期展示句审样。未入最终工作台的空簇不计最终差异。'}
