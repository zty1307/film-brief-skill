"""One entry for raw Excel -> audited workbench -> optional history -> opinion draft.

AI fills the existing discovery/review packets and editorial plan. This controller
owns stage transitions, completed-workbench provenance, budgets and delivery.
"""
from pathlib import Path
import argparse,datetime as dt,hashlib,json,shutil,subprocess,sys,time,math
SCRIPTS=Path(__file__).resolve().parent

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8');t.replace(p)
def command(ws):return f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" advance --workspace "{ws}"'
WORKBENCH_LIMIT_SECONDS = 2700
WRITING_LIMIT_SECONDS = 900

def elapsed(s):return round(time.time()-s['started_epoch'],3)

def budget(a, maximum, label):
 if not 0<a<=maximum:
  raise ValueError(f'{label}预算须为(0,{maximum}]秒')
 return a

def active_budget(s):
 if s.get('phase')=='workbench':
  return s['cleaning_budget_seconds']
 if s.get('phase')=='writing':
  return s['writing_budget_seconds']
 return s['total_budget_seconds']

def stage_times(s):
 if s.get('phase')=='workbench':
  return {'workbench_elapsed_seconds':elapsed(s),'writing_elapsed_seconds':None}
 return {'workbench_elapsed_seconds':s.get('prior_cleaning_seconds'),
         'writing_elapsed_seconds':elapsed(s)}

def run(ws,s,script,args):
 remaining=active_budget(s)-elapsed(s)
 if remaining<=0:raise ValueError('当前阶段预算已用尽，保留已审工作台和实际状态')
 p=subprocess.run([sys.executable,'-X','utf8',str(SCRIPTS/script),*map(str,args)],capture_output=True,text=True,encoding='utf-8',timeout=remaining)
 log=ws/'logs'/f'{len(s["commands"]):03d}-{script}.txt';log.parent.mkdir(exist_ok=True)
 log.write_text(p.stdout+'\n'+p.stderr,encoding='utf-8');s['commands'].append({'script':script,'log':str(log),'returncode':p.returncode})
 if p.returncode:raise ValueError((p.stderr or p.stdout)[-20000:])
 return p.stdout

def status(ws,s,stage,**more):
 s.update(stage=stage,elapsed_seconds=elapsed(s),**more);save(ws/'integrated_state.json',s)
 v={k:s[k] for k in ('target','stage','elapsed_seconds','cleaning_budget_seconds','stage_timings')}
 v['writing_budget_seconds']=s.get('writing_budget_seconds',WRITING_LIMIT_SECONDS)
 v.update(stage_times(s))
 v.update(status='COMPLETE' if stage=='COMPLETE' else 'INCOMPLETE' if stage in ('INCOMPLETE','BUDGET_EXHAUSTED') else 'ACTION_REQUIRED',next_command=command(ws));v.update(more)
 save(ws/'workflow_status.json',v);return v


def writing_repair_feedback(ws,s,error):
 # Compare actual parsed answer, not timestamps changed by no-op text replacement.
 answer=ws/'writing/editorial_plan.json'
 try: current=read(answer)
 except (OSError,ValueError): return {}
 digest=hashlib.sha256(json.dumps(current,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
 old=s.get('last_writing_validation',{})
 repeated=old.get('answer_sha256')==digest and old.get('error')==error
 count=old.get('unchanged_count',0)+1 if repeated else 0
 s['last_writing_validation']={'answer_sha256':digest,'error':error,'unchanged_count':count}
 fields=[]
 if 'section heading' in error or 'section_heading' in error:
  fields.append({'path':'/section_heading','current':current.get('section_heading')})
 feedback={'answer_sha256':digest,'unchanged_after_failed_validation':repeated,
           'unchanged_count':count,'current_fields':fields}
 if repeated:
  feedback['instruction']='答卷内容与上次报错时完全相同，先前修改没有写入。停止猜测字符串替换。读取answer_file中的实际当前值，以Write保存patch_answer.py所需changes补丁，expected必须等于实际原值，value由你依据证据判断。执行补丁工具后读回核对，再advance；不修改规则、审核结论或计时。'
  feedback['patch_command']=f'"{sys.executable}" -X utf8 "{SCRIPTS / "patch_answer.py"}" --workspace "{ws}" --answer "writing/editorial_plan.json" --patch "writing/field_patch.json"'
  feedback['patch_schema']={'changes':[{'path':'实际字段路径','expected':'该字段实际当前值','value':'模型判断的新值'}]}
 return feedback


def close_stage(s,name):
 now=time.time();s['stage_timings'].append({'stage':name,'seconds':round(now-s['stage_started_epoch'],3)});s['stage_started_epoch']=now

def apply_requirements(a,s):
 path=getattr(a,'requirements_file',None)
 if path:
  value=read(path).get('requirements')
  if not isinstance(value,str) or not value.strip() or len(value)>4000:raise ValueError('补充要求须为非空文字，最多4000字')
  s['user_requirements']=value.strip()


def prepare_writing(ws,s):
 args=['prepare','--dataset',ws/'writing_inputs/final_dataset.json','--outdir',ws/'writing']
 for item in s['history']:args+=['--history',item]
 for item in s['history_plan']:args+=['--history-plan',item]
 run(ws,s,'report_writing.py',args)
 if s.get('user_requirements'):
  import model_packet
  inventory=read(ws/'writing/candidate_inventory.json')
  inventory['user_requirements']=s['user_requirements']
  inventory['requirements_scope']='在已选/已审证据范围内遵循补充要求；不改变原评、不伪造来源、不降低审核标准。无法满足时具体说明。'
  save(ws/'writing/candidate_inventory.json',inventory)
  model_packet.writing_input(inventory,ws/'writing')
 import writing_outline
 writing_outline.prepare(ws/'writing')
 return status(ws,s,'WRITING_OUTLINE',read_now=[str(ws/'writing/outline_input.json')],answer_file=str(ws/'writing/outline_answer.json'),
   instruction='先确认每段有真实评价、充分独立证据和清楚边界。填简短提纲，不写正文和图表。材料不足明确填insufficient，不拆近义点，不读原表。')

def prepare_prose_review(ws,s):
 import prose_review
 result=prose_review.prepare(ws/'writing_inputs/final_dataset.json',ws/'writing/editorial_plan.json',ws/'writing')
 return status(ws,s,'FINAL_PROSE_REVIEW',read_now=[result['read_now'],str(SCRIPTS.parent/'references/final-prose-review.md')],answer_file=result['answer_file'],instruction='AI完整通读最终成稿和实际引用，逐段填终审；不是人工审批。检查真实观点、标题有据、表达自然、字数展开及交叉。资讯上线稿不能冒充舆论好评；材料不足时，人工选材模式具体报缺口并退回选材，不自行删选点；自动模式按证据取舍。不补原表、不硬凑字数。有问题必须revise，修计划并重新render和终审，通过后控制器才交付。')

def export_delivery(ws,destination=None):
 """The normal delivery consists of the original workbench and generated Word."""
 expected=['opinion_draft.docx','opinion_draft.md','opinion_distribution.png','opinion_distribution.json','source_audit.json','review_sheet.md']
 if not all((ws/'writing'/f).is_file() for f in expected):raise ValueError('写稿产物不齐，不发布整套结果')
 import report_writing
 ds=read(ws/'writing_inputs/final_dataset.json');plan=read(ws/'writing/editorial_plan.json')
 report_writing.check_dataset(ds,ws/'writing_inputs/final_dataset.json');report_writing.render_content(ds,plan)
 import prose_review
 prose_review.require_pass(ws/'writing_inputs/final_dataset.json',ws/'writing/editorial_plan.json',ws/'writing')
 if plan.get('review_state')!='AI_PREVIEW':raise ValueError('自动一体化只交可审阅草稿，不能伪造人工确认')
 data=ws/'writing_inputs/final_dataset.json'
 if plan.get('dataset_sha256')!=sha(data) or sha(ws/'cleaning/final_dataset.json')!=sha(data):raise ValueError('工作台数据指纹改变，不能发布')
 if not ds.get('human_selection') and not (ws/'cleaning/workbench.html').is_file():raise ValueError('观点工作台页面缺失')
 destination=Path(destination) if destination is not None else ws/'delivery'
 if destination.exists():raise ValueError('交付目录已经存在，不覆盖旧交付文件')
 import sample_links
 links=sample_links.build(ws/'writing_inputs/final_dataset.json',ws/'writing/editorial_plan.json',ws/'writing')
 destination.mkdir(parents=True)
 pairs=[(ws/'cleaning/workbench.html',destination/'观点工作台.html'),(Path(links['output']),destination/'主要舆论观点.docx')]
 if ds.get('human_selection'):pairs=pairs[1:]
 for original,target in pairs:
  shutil.copy2(original,target)
  if sha(original)!=sha(target):raise ValueError('交付文件与流程原生成文件不一致')
 return {'workbench':None if ds.get('human_selection') else str(pairs[0][1]),'word':str(pairs[-1][1]),'sample_links':{k:links[k] for k in ('point_count','sample_count','link_count','missing_link_ids','screenshots_required')},'deliverables':[{'kind':'word' if pair[1].suffix=='.docx' else 'workbench','path':str(pair[1]),'sha256':sha(pair[1])} for pair in pairs]}

def publish(ws,s):
 delivery_files=export_delivery(ws)
 close_stage(s,'prose_review')
 internal=[ws/'cleaning/final_dataset.json',*[ws/'writing'/f for f in ('opinion_draft.md','opinion_distribution.png','opinion_distribution.json','source_audit.json','review_sheet.md','final_prose_review.json','sample_links_receipt.json')]]
 delivery={'target':s['target'],'review_state':'AI_PREVIEW','source_scope':'COMPLETED_WORKBENCH_DISPLAY_ONLY','input_mode':s.get('input_mode','RAW_TO_REPORT'),'workbench_completion':s.get('workbench_completion'),'dataset_sha256':s['dataset_sha256'],'stage_timings':s['stage_timings'],**stage_times(s),'sample_links':delivery_files['sample_links'],'artifacts':delivery_files['deliverables'],'internal_artifacts':[{'path':str(p),'sha256':sha(p)} for p in internal]}
 save(ws/'delivery_manifest.json',delivery)
 return status(ws,s,'COMPLETE',review_state='AI_PREVIEW',**delivery_files,delivery_manifest=str(ws/'delivery_manifest.json'),next_command=None)

def start(a):
 ws=Path(a.workspace).resolve()
 if ws.exists():raise ValueError('一体化工作区必须不存在，避免覆盖或误接旧结果')
 cleaning_budget=budget(a.cleaning_budget_seconds,WORKBENCH_LIMIT_SECONDS,'工作台')
 writing_budget=budget(a.writing_budget_seconds,WRITING_LIMIT_SECONDS,'写稿')
 if not Path(a.source).is_dir():raise ValueError('原表目录不存在')
 ws.mkdir(parents=True);now=time.time()
 s={'target':a.target,'started_epoch':now,'started_at':dt.datetime.now().astimezone().isoformat(),'stage_started_epoch':now,'phase':'workbench','writing_budget_seconds':writing_budget,'cleaning_budget_seconds':cleaning_budget,'history':[str(Path(p).resolve()) for p in a.history],'history_plan':[str(Path(p).resolve()) for p in a.history_plan],'stage_timings':[],'commands':[]}
 s['human_selection_required'] = not getattr(a,'auto_write',False)
 status(ws,s,'PREPARING')
 args=['prepare','--source',Path(a.source).resolve(),'--workspace',ws/'cleaning','--target',a.target,'--deadline-seconds',max(1,cleaning_budget-elapsed(s)),'--discover-viewpoints','--protect-ready-workbench']
 for alias in a.alias:args+=['--alias',alias]
 for item in s['history']:args+=['--history',item]
 for item in s['history_plan']:args+=['--history-plan',item]
 # Historical meaning is compared inside the existing current discovery pass.
 child=json.loads(run(ws,s,'final_sample_review.py',args))
 return status(ws,s,'CLEANING',next_command=child['next_command'],resume_command=command(ws),read_now=[str(ws/'cleaning/viewpoint_discovery_input.json')],instruction='按本Skill现有清洗命令完成发现、逐条审样、有限编辑与本期原生成员关停；控制器只由一个主窗口操作。清洗结束后执行resume_command；默认交工作台等待人工选材，只有显式auto-write才接自动写稿。')

def from_workbench(a):
 from report_writing import check_dataset
 source=Path(a.dataset).resolve();data=read(source);check_dataset(data,source)
 page=source.with_name('workbench.html')
 if not page.is_file():raise ValueError('原工作台页面缺失，不能只拼接一个数据文件冒充工作台')
 from period_history import read_history_dataset
 imported=read_history_dataset(page,data['target'])
 signature=lambda d:[(g['aspect'],[s.get('display_excerpt') or s['excerpt'] for s in g['samples']]) for g in d['groups']]
 if signature(imported)!=signature(data):raise ValueError('工作台页面与展示数据不一致')
 ws=Path(a.workspace).resolve()
 if ws.exists():raise ValueError('接续写稿须使用不存在的新目录，保留原工作台与状态')
 writing_budget=budget(a.writing_budget_seconds,WRITING_LIMIT_SECONDS,'写稿')
 receipt=read(source.with_name('final_review_status.json'));prior=receipt.get('elapsed_seconds')
 if type(prior) not in (int,float) or not math.isfinite(prior) or prior<0:raise ValueError('原工作台缺少有效清洗耗时回执')
 ws.mkdir(parents=True);(ws/'cleaning').mkdir();now=time.time()
 for item in (source,source.with_name('final_review_status.json'),page):shutil.copy2(item,ws/'cleaning'/item.name)
 s={'target':data['target'],'started_epoch':now,'started_at':dt.datetime.now().astimezone().isoformat(),'stage_started_epoch':now,'phase':'writing','writing_budget_seconds':writing_budget,'prior_cleaning_seconds':prior,'cleaning_budget_seconds':receipt.get('deadline_seconds',WORKBENCH_LIMIT_SECONDS),'history':[str(Path(p).resolve()) for p in a.history],'history_plan':[str(Path(p).resolve()) for p in a.history_plan],'stage_timings':[],'commands':[], 'input_mode':'EXISTING_COMPLETED_WORKBENCH','original_workbench':str(source),'original_dataset_sha256':sha(source),'original_review_sha256':sha(source.with_name('final_review_status.json'))}
 apply_requirements(a,s)
 status(ws,s,'CLEANING')
 return advance(argparse.Namespace(workspace=str(ws)))

def from_selection(a):
 from human_selection import materialize, read as read_selection, save as save_selection
 package_path=Path(a.selection).resolve();package=read_selection(package_path)
 data=materialize(package)
 ws=Path(a.workspace).resolve()
 if ws.exists():raise ValueError('人工选材接续写稿须使用不存在的新目录')
 prior=package['origin_review'].get('elapsed_seconds')
 if type(prior) not in (int,float) or not math.isfinite(prior) or prior<0:raise ValueError('选材缺少有效清洗耗时回执')
 writing_budget=budget(a.writing_budget_seconds,WRITING_LIMIT_SECONDS,'写稿')
 ws.mkdir(parents=True);now=time.time()
 for folder in ('cleaning','writing_inputs'):
  dest=ws/folder;dest.mkdir()
  save_selection(dest/'final_dataset.json',data)
  shutil.copy2(package_path,dest/'human_selection.json')
 save_selection(ws/'original_review_receipt.json',package['origin_review'])
 from report_writing import check_dataset
 check_dataset(data,ws/'writing_inputs/final_dataset.json')
 s={'target':data['target'],'started_epoch':now,'started_at':dt.datetime.now().astimezone().isoformat(),
    'stage_started_epoch':now,'phase':'writing','writing_budget_seconds':writing_budget,
    'cleaning_budget_seconds':package['origin_review'].get('deadline_seconds',WORKBENCH_LIMIT_SECONDS),
    'prior_cleaning_seconds':prior,'history':[],'history_plan':[], 'stage_timings':[],'commands':[],
    'input_mode':'HUMAN_SELECTED_WORKBENCH','original_selection':str(package_path),
    'selection_sha256':sha(package_path),'dataset_sha256':sha(ws/'writing_inputs/final_dataset.json'),
    'workbench_completion':{'ready':True,'scope':'HUMAN_SELECTED_REVIEWED_SAMPLES'}}
 apply_requirements(a,s)
 status(ws,s,'PREPARING')
 return prepare_writing(ws,s)


def advance(a):
 ws=Path(a.workspace).resolve();s=read(ws/'integrated_state.json')
 if s['stage'] in ('COMPLETE','INCOMPLETE','BUDGET_EXHAUSTED','HUMAN_SELECTION_REQUIRED','SELECTION_REVISION_REQUIRED'):return read(ws/'workflow_status.json')
 from workbench_enrichment import has_checkpoint
 can_publish_ready=s.get('phase')=='workbench' and s['stage']=='CLEANING' and has_checkpoint(ws/'cleaning')
 if elapsed(s)>=active_budget(s) and not can_publish_ready:
  return status(ws,s,'BUDGET_EXHAUSTED',next_command=None,cleanup_required=True,instruction='当前阶段预算已用尽，保留已完成工作台和实际状态。')
 if s['stage']=='PREPARING':return prepare_writing(ws,s)
 if s['stage']=='CLEANING':
  p=ws/'cleaning/final_review_status.json';v=read(p) if p.exists() else {}
  from workbench_enrichment import publish_board
  use_ready = getattr(a,'use_ready_workbench',False)
  if use_ready and not has_checkpoint(ws/'cleaning'):
   raise ValueError('未存有通过标题证据终审的六观点工作台')
  terminal = ('FINAL_SELECTED_COMPLETE','FINAL_POSITIVE_MIN_NOT_MET','FINAL_TITLE_EVIDENCE_REVIEW_INCOMPLETE',
              'TIME_LIMIT_REACHED','FINAL_SELECTED_PARTIAL','NO_ACCEPTED_SAMPLES','VIEWPOINT_RANGE_NOT_MET')
  if has_checkpoint(ws/'cleaning') and (use_ready or v.get('status') in terminal or
                                       elapsed(s) >= s['cleaning_budget_seconds'] - 30):
   v=publish_board(ws/'cleaning',v,elapsed(s),publish_snapshot=use_ready)
   s['ready_workbench_elapsed_seconds']=v['ready_workbench_elapsed_seconds']
  allowed=('FINAL_SELECTED_COMPLETE','POSITIVE_VIEWPOINT_MIN_NOT_MET')
  allowed+=('TIME_LIMIT_REACHED',)
  if v.get('status')=='VIEWPOINT_SKETCH_REQUIRED':
   inputs=[str(ws/'cleaning/discovery_sketch_input.json')]
   feedback=ws/'cleaning/discovery_sketch_feedback.json'
   if feedback.exists():inputs.append(str(feedback))
   child=f'"{sys.executable}" -X utf8 "{SCRIPTS / "final_sample_review.py"}" sketch-collect --workspace "{ws / "cleaning"}"'
   return status(ws,s,'CLEANING',child_status=v['status'],read_now=inputs,next_command=child,resume_command=command(ws),instruction='先根据原句交简短观点草案，执行sketch-collect；再按下一包正式定稿。草案不算观点或样本审核通过。')
  if v.get('status')=='VIEWPOINT_DISCOVERY_REQUIRED':
   inputs=[str(ws/'cleaning/viewpoint_discovery_input.json')]
   feedback=ws/'cleaning/viewpoint_discovery_feedback.json'
   if feedback.exists():inputs.append(str(feedback))
   child=f'"{sys.executable}" -X utf8 "{SCRIPTS / "final_sample_review.py"}" discover-collect --workspace "{ws / "cleaning"}"'
   return status(ws,s,'CLEANING',child_status=v['status'],read_now=inputs,next_command=child,resume_command=command(ws),instruction='填写或按反馈修本期发现答卷后，执行discover-collect；后续跟随内层命令，完整清洗完成后再执行resume_command接写稿。')
  if v.get('status')=='CANDIDATE_CLOSEOUT_REQUIRED':
   return status(ws,s,'CLEANING',child_status=v['status'],read_now=[v['read_now']],next_command=v['next_command'],instruction='先按本期候选关闭输入核对真实任务回执，再执行close-candidates；不代填未审候选。完成标题证据核对后再接写稿。')
  if v.get('status')=='COVERAGE_REPAIR_REQUIRED':
   child=f'"{sys.executable}" -X utf8 "{SCRIPTS / "final_sample_review.py"}" repair-collect --workspace "{ws / "cleaning"}"'
   return status(ws,s,'CLEANING',child_status=v['status'],read_now=[str(ws/'cleaning/coverage_repair_input.json')],next_command=child,
                 instruction='仍在聚类阶段。交齐当前补缺包并按提示推进。已有锁定工作台时，只做本包歧义归组或新点发现，按包内编号回填。明确归组的候选由脚本直接送逐条审样。')
  if v.get('status')=='SUPPLEMENT_VIEWPOINTS_REQUIRED':
   return status(ws,s,'CLEANING',child_status=v['status'],read_now=[v['read_now']],
                 answer_file=v['answer_file'],next_command=v['next_command'],resume_command=command(ws),
                 instruction='只核对新观点的已审原句及其与锁定观点的边界，原观点和原样本不改。')
  from workbench_completion import POSITIVE_POLICIES
  if v.get('status') in ('FINAL_TITLE_EVIDENCE_REVIEW_INCOMPLETE','FINAL_SELECTED_PARTIAL','NO_ACCEPTED_SAMPLES','FINAL_POSITIVE_MIN_NOT_MET') or (v.get('status')=='TIME_LIMIT_REACHED' and v.get('quantity_contract') in POSITIVE_POLICIES and v.get('positive_cluster_shortfall')):
   close_stage(s,'cleaning_incomplete')
   return status(ws,s,'INCOMPLETE',child_status=v['status'],next_command=None,
     cleanup_required=True,read_now=[str(ws/'cleaning/native_worker_registry.json')],
     instruction='清洗已终止。立即按真实任务状态TaskStop仍运行的本会话写者并shutdown本会话团队，已完成任务直接登记，不继续等待答卷、不接写稿。保留失败状态及原耗时。')
  if v.get('status') not in allowed:return status(ws,s,'CLEANING',child_status=v.get('status'),instruction='按清洗当前handoff和next_command继续，只有完整已审工作台能进入写稿；本版正面至少6个独立非空组，部分结果不得通过。')
  from report_writing import check_dataset
  from source_policy import verify_unique_samples
  data=ws/'cleaning/final_dataset.json';d=read(data);check_dataset(d,data);verify_unique_samples(d)
  from workbench_completion import review_readiness
  s['workbench_completion']=review_readiness(d,v)
  if not (ws/'cleaning/workbench.html').is_file():raise ValueError('完整工作台页面缺失')
  close_stage(s,'handoff_validation' if s.get('input_mode')=='EXISTING_COMPLETED_WORKBENCH' else 'cleaning');s['dataset_sha256']=sha(data)
  if s.get('human_selection_required'):
   from human_selection import HANDOFF_MESSAGE,WRITING_CHOICE,MANUAL_HANDOFF
   delivery=ws/'delivery';delivery.mkdir(exist_ok=True)
   shutil.copy2(ws/'cleaning/workbench.html',delivery/'观点工作台.html')
   return status(ws,s,'HUMAN_SELECTION_REQUIRED',next_command=None,
       workbench=str(delivery/'观点工作台.html'),handoff_message=HANDOFF_MESSAGE,writing_choice=WRITING_CHOICE,manual_handoff=MANUAL_HANDOFF,
       ready_workbench_elapsed_seconds=v.get('ready_workbench_elapsed_seconds'),
       enrichment_published=v.get('enrichment_published'),
       auto_writing_budget_seconds=s.get('writing_budget_seconds',WRITING_LIMIT_SECONDS),
       instruction='先提供可点击工作台及handoff_message。未明确选材方式时调用原生AskUserQuestion，参数见writing_choice，仅问一次并等待；没有该工具则用文字列选项后结束回复。用户没回答不能自动继续。已明确人工或只要工作台时给manual_handoff并结束；已明确全自动时无需再问。选自动后按原工作台from-workbench接续，不重跑聚类，使用独立写稿预算auto_writing_budget_seconds；有补充要求用requirements-file传递。')
  inp=ws/'writing_inputs';inp.mkdir()
  shutil.copy2(data,inp/'final_dataset.json');shutil.copy2(p,inp/p.name)
  if d.get('final_title_evidence_review',{}).get('status') == 'COMPLETE':
   from report_evidence import writing_readiness
   readiness=writing_readiness(d['groups'])
   if readiness['evidence_shortfall']:
    return status(ws,s,'INCOMPLETE',writing_readiness=readiness,workbench=str(ws/'cleaning/workbench.html'),
       next_command=None,instruction=f'已审工作台最多支撑{readiness["paragraph_upper_bound"]}段，少于成稿要求。保留工作台，回查本期候选是否漏掉独立评价。')
  if s.get('phase')=='workbench':
   cleaning_seconds=elapsed(s)
   now=time.time()
   s.update(phase='writing',prior_cleaning_seconds=cleaning_seconds,
            started_epoch=now,stage_started_epoch=now)
  return prepare_writing(ws,s)
 if sha(ws/'cleaning/final_dataset.json')!=s['dataset_sha256'] or sha(ws/'writing_inputs/final_dataset.json')!=s['dataset_sha256']:raise ValueError('清洗完成后的数据指纹改变，拒绝继续写稿')
 try:
  if s['stage']=='WRITING_OUTLINE':
   import writing_outline
   result=writing_outline.collect(read(ws/'writing_inputs/final_dataset.json'),ws/'writing')
   if result['status']=='INSUFFICIENT_EVIDENCE':
    close_stage(s,'outline_review')
    return status(ws,s,'SELECTION_REVISION_REQUIRED' if s.get('input_mode')=='HUMAN_SELECTED_WORKBENCH' else 'INCOMPLETE',next_command=None,evidence_shortfall=result['reason'])
   close_stage(s,'outline_review')
   return status(ws,s,'WRITING',read_now=[str(ws/'writing/writing_input.json'),str(SCRIPTS.parent/'references/writing.md')],
     answer_file=str(ws/'writing/editorial_plan.json'),instruction='已有证据提纲已写入计划。组织正文引文与分句支持，完成图表。只读冻结工作台清单，不补原表。批量反馈集中修复后重新完整校验；最后仍须全稿通读。')
  if s['stage']=='COMPARING':
   run(ws,s,'period_compare.py',['collect','--outdir',ws/'comparison']);close_stage(s,'comparison');return prepare_writing(ws,s)
  if s['stage'] in ('FINAL_PROSE_REVIEW','FINAL_CHART_REVIEW'):
   plan=ws/'writing/editorial_plan.json'
   review_input=read(ws/'writing/prose_review_input.json')
   if review_input.get('plan_sha256')!=sha(plan):
    close_stage(s,'prose_review_invalidated')
    s['stage']='WRITING'
  if s['stage']=='WRITING_REVISION':
   from repair_packets import digest,merge
   writing_dir=ws/'writing'
   revision_input=writing_dir/'prose_revision_input.json'
   revision_answer=writing_dir/'prose_revision_answer.json'
   plan_path=writing_dir/'editorial_plan.json'
   if not revision_answer.is_file():
    return status(ws,s,'WRITING_REVISION',read_now=[str(revision_input)],answer_file=str(revision_answer))
   task=read(revision_input)
   current_plan=read(plan_path)
   if task.get('plan_sha256')==sha(plan_path):
    revised=merge(task,current_plan,read(revision_answer))
    task['merged_plan_digest']=digest(revised)
    save(revision_input,task)
    save(plan_path,revised)
   elif task.get('merged_plan_digest')!=digest(current_plan):
    raise ValueError('局部返修任务已过期，须核对当前写稿计划')
   s['stage']='WRITING'
  if s['stage']=='WRITING':
   run(ws,s,'report_writing.py',['render','--dataset',ws/'writing_inputs/final_dataset.json','--plan',ws/'writing/editorial_plan.json','--outdir',ws/'writing']);close_stage(s,'writing');return prepare_prose_review(ws,s)
  if s['stage'] in ('FINAL_PROSE_REVIEW','FINAL_CHART_REVIEW'):
   import prose_review
   result=prose_review.collect(ws/'writing_inputs/final_dataset.json',ws/'writing/editorial_plan.json',ws/'writing')
   if result['status']=='FINAL_CHART_REVIEW_REQUIRED':
    close_stage(s,'prose_content_review')
    return status(ws,s,'FINAL_CHART_REVIEW',read_now=[result['read_now'],str(SCRIPTS.parent/'references/final-chart-review.md')],answer_file=result['answer_file'],instruction='正文已审通过，继续完整核验当前图表。仍在原总预算内；图表未通过不得发布。')
   if result['decision']=='pass':return publish(ws,s)
   close_stage(s,'prose_review')
   from repair_packets import prose_revision_task
   writing_dir=ws/'writing'
   plan_path=writing_dir/'editorial_plan.json'
   task=prose_revision_task(read(writing_dir/'writing_input.json'),read(plan_path),
                            read(writing_dir/'final_prose_review.json'),read(writing_dir/'prose_review_input.json'))
   if task is not None:
    revision_input=writing_dir/'prose_revision_input.json'
    revision_answer=writing_dir/'prose_revision_answer.json'
    task['plan_sha256']=sha(plan_path)
    save(revision_input,task)
    revision_answer.unlink(missing_ok=True)
    return status(ws,s,'WRITING_REVISION',read_now=[str(revision_input)],answer_file=str(revision_answer),
                  instruction='只提交局部终审任务的updates，随后执行next_command。')
   return status(ws,s,'WRITING',read_now=[str(ws/'writing/final_prose_review.json'),str(ws/'writing/prose_review_input.json'),str(ws/'writing/writing_input.json')],answer_file=str(ws/'writing/editorial_plan.json'),instruction='按成稿终审具体问题修写稿计划。证据不足时人工选材模式退回选材；自动模式删点或从同一工作台换合格声音，不补原表、不灌水，不只改例外理由。修改后执行next_command重新生成Word并再次终审；不得把旧pass复用到新稿。')
  raise ValueError('阶段不支持自动推进：'+s['stage'])
 except subprocess.TimeoutExpired:
  return status(ws,s,'BUDGET_EXHAUSTED',next_command=None,cleanup_required=True,instruction='当前命令超过写稿阶段剩余预算，停止写稿与审样，只按真实注册表收尾本期原生任务并报告未完成。')
 except ValueError as exc:
  if elapsed(s)>=active_budget(s):
   return status(ws,s,'BUDGET_EXHAUSTED',next_command=None,cleanup_required=True,validation_error=str(exc))
  if s['stage']=='WRITING_REVISION':
   return status(ws,s,'WRITING_REVISION',validation_error=str(exc),
       read_now=[str(ws/'writing/prose_revision_input.json')],
       answer_file=str(ws/'writing/prose_revision_answer.json'),
       instruction='按照本轮局部任务提交完整updates，保留实际终审问题与原有计时。')
  if s['stage']=='WRITING':
   inputs=[str(ws/'writing/writing_input.json'),str(SCRIPTS.parent/'references/writing.md')]
   previous=ws/'writing/final_prose_review.json'
   if previous.is_file() and read(previous).get('decision')=='revise':
    inputs.append(str(previous))
   return status(ws,s,'WRITING',validation_error=str(exc),repair_feedback=writing_repair_feedback(ws,s,str(exc)),read_now=inputs,
       answer_file=str(ws/'writing/editorial_plan.json'),
       instruction='修当前字段与字数问题，同时保留已落实的终审修改。不要恢复终审已指出的引文问题；确实缺少合格材料时按原规则说明字数例外。再执行next_command。')
  return status(ws,s,s['stage'],validation_error=str(exc),instruction='只修当前答卷/写稿计划所列错误，保留已通过内容；禁止修改规则、来源和完成状态。再执行next_command。')

def return_selection(a):
 ws=Path(a.workspace).resolve();s=read(ws/'integrated_state.json')
 if s.get('input_mode')!='HUMAN_SELECTED_WORKBENCH' or s['stage'] not in ('WRITING_OUTLINE','WRITING','WRITING_REVISION','FINAL_PROSE_REVIEW','FINAL_CHART_REVIEW'):
  raise ValueError('只在人工选材写稿尚未完成时退回选材')
 if len(a.reason.strip())<12:raise ValueError('具体说明哪个选点缺少什么证据，至少12字')
 close_stage(s,'selection_shortfall')
 return status(ws,s,'SELECTION_REVISION_REQUIRED',next_command=None,evidence_shortfall=a.reason)


def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 a=sub.add_parser('start');a.add_argument('--auto-write',action='store_true');a.add_argument('--source',required=True);a.add_argument('--target',required=True);a.add_argument('--workspace',required=True);a.add_argument('--alias',action='append',default=[]);a.add_argument('--history',action='append',default=[]);a.add_argument('--history-plan',action='append',default=[]);a.add_argument('--cleaning-budget-seconds',type=float,default=WORKBENCH_LIMIT_SECONDS);a.add_argument('--writing-budget-seconds',type=float,default=WRITING_LIMIT_SECONDS)
 a=sub.add_parser('advance');a.add_argument('--workspace',required=True);a.add_argument('--use-ready-workbench',action='store_true')
 a=sub.add_parser('from-workbench');a.add_argument('--requirements-file');a.add_argument('--dataset',required=True);a.add_argument('--workspace',required=True);a.add_argument('--history',action='append',default=[]);a.add_argument('--history-plan',action='append',default=[]);a.add_argument('--writing-budget-seconds','--total-budget-seconds',dest='writing_budget_seconds',type=float,default=WRITING_LIMIT_SECONDS)
 a=sub.add_parser('from-selection');a.add_argument('--requirements-file');a.add_argument('--selection',required=True);a.add_argument('--workspace',required=True);a.add_argument('--writing-budget-seconds','--total-budget-seconds',dest='writing_budget_seconds',type=float,default=WRITING_LIMIT_SECONDS)
 a=sub.add_parser('return-selection');a.add_argument('--workspace',required=True);a.add_argument('--reason',required=True)
 a=p.parse_args()
 try:result=start(a) if a.command=='start' else from_workbench(a) if a.command=='from-workbench' else from_selection(a) if a.command=='from-selection' else return_selection(a) if a.command=='return-selection' else advance(a)
 except (ValueError,OSError,subprocess.TimeoutExpired) as e:p.error(str(e))
 print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
