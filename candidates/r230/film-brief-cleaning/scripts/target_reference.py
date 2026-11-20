"""Verifiable local work-reference context for the limited editor; no opinion supplementation."""
import re

WORK_REFERENCE_CUE = re.compile(
 r'本剧|该剧|这部剧|整部剧|这剧|剧版|影版|本片|该片|影片|片花|剧集|剧组|剧方|剧宣|'
 r'物料|预告|默片|短片|角色主题曲|原声|OST|剧照|这部作品|该作品|本作品|作品还没播|作品尚未播',
 re.I,
)

def local_binding(item,record,state):
 text=str(record['data'].get(item['text_field']) or '');a,b=item['start'],item['end']
 terms=[x for x in state.get('terms',[state['target']]) if x]
 from rapid_film_workbench import display_excerpt
 left=max(0,a-120);right=min(len(text),b+120)
 context=text[left:right];extension=False
 # Expand reference evidence only, never the selectable quote. Keep a bounded,
 # verbatim body span rather than borrowing the article title or a hashtag.
 if not any(t in display_excerpt(context) for t in terms):
  anchors=[m.start() for t in terms for m in re.finditer(re.escape(t),text[max(0,a-800):a])
           if t in display_excerpt(text[max(0,a-800):a][max(0,m.start()-60):m.end()+60])]
  if anchors:
   anchor=max(0,a-800)+max(anchors)
   left=max(0,anchor-60);context=text[left:right];extension=True
 from final_evidence_gate import comparative_source
 return {'source_title':str(record['data'].get('标题') or item.get('title') or ''),'local_target_context':context,
  'local_target_present':any(t in context for t in terms),
  'comparative_or_multiwork_source':comparative_source(text,str(record['data'].get('标题') or ''),terms),
  'context_scope':'WORK_REFERENCE_ONLY_NOT_OPINION_SUPPORT',
  'context_start':left,'context_end':right,'bounded_body_context_extended':extension}

def review_references(item,record,state):
 """Attach evidence to a new packet item without changing candidate identity."""
 result={**item,'work_reference':local_binding(item,record,state)}
 options=item.get('selection_options') or []
 if options and result['work_reference']['comparative_or_multiwork_source']:
  terms=[term for term in state.get('terms',[state['target']]) if term]
  if not any(WORK_REFERENCE_CUE.search(option['text']) or any(term in option['text'] for term in terms)
             for option in options):
   text=str(record['data'].get(item['text_field']) or '')
   for option in options:
    start,end=option['start'],option['end']
    preceding=text[max(0,start-120):start]
    # A short, contiguous preceding sentence can supply the subject.
    # It remains a candidate window and still requires semantic and span checks.
    boundaries=list(re.finditer(r'[^。！？\n]+[。！？]?\s*$', preceding.rstrip()))
    if not boundaries:
     continue
    prefix=boundaries[-1].group()
    if not (WORK_REFERENCE_CUE.search(prefix) or any(term in prefix for term in terms)):
     continue
    new_start=max(0,start-120)+boundaries[-1].start()
    if (new_start==max(0,start-120) and new_start>0
        and text[new_start-1] not in '。！？\n'):
     continue
    if end-new_start>360:
     continue
    options=[{'start':new_start,'end':end,'text':text[new_start:end]},*options[:1]]
    break
 if options:
  result['selection_options']=[{**option,'work_reference':local_binding(
   {**item,'start':option['start'],'end':option['end']},record,state)} for option in options]
  if result['work_reference']['comparative_or_multiwork_source']:
   from rapid_film_workbench import display_excerpt
   terms=[term for term in state.get('terms',[state['target']]) if term]
   result['reference_eligible_window_numbers']=[
    i for i,option in enumerate(result['selection_options'],1)
    if any(term in display_excerpt(option['text']) for term in terms)
    or (WORK_REFERENCE_CUE.search(option['text']) and option['work_reference']['local_target_present'])
   ]
 return result

REFERENCE_RULE = 'work_reference可能给出向前最多800字内最近剧名所在的连续正文，只用于审核作品承接；它不自动证明当前句属于本剧。须检查期间是否切换作品、转入旧作或戏外真人；标题、话题标签和另一作品的评价仍不能救援。 所选句未点名本剧且来源含多个作品时，target_scope须为嵌套对象，reference、anchor_quote、reason三个字段必须放在该对象内部（不可把anchor_quote写在外面）；reference=current_work_via_context、含本剧名且在local_target_context逐字出现的至少8字anchor_quote、至少15字实际承接reason。所选句自身还须有本剧/该剧/这部剧/整部剧/这剧/剧版/影版/本片/该片/影片/片花/剧集/剧组/剧方/剧宣/物料/预告/默片/短片/角色主题曲/原声/OST等作品或物料指代，不得只是演员戏外动作。所选句以其他《作品》的剧情、演员、镜头等为对象时，不能借上下文救援。无合格原句则reject；不得在原句中添加这些词。若输入提供reference_eligible_window_numbers，保留只可使用这些窗口，列表只说明存在作品指代，不代表立场或观点已经通过。'

def local_reference_issues(selected,binding,review,state):
 """Expose the existing predicates individually; no change to acceptance."""
 issues=[]
 scope=review.get('target_scope',{})
 if not isinstance(scope,dict):return ['target_scope须为对象']
 if scope.get('reference')!='current_work_via_context':issues.append('target_scope.reference须为current_work_via_context')
 if len(str(scope.get('reason') or '').strip())<15:issues.append('target_scope.reason须至少15字说明实际局部承接')
 context=binding.get('local_target_context','');anchor=scope.get('anchor_quote')
 terms=[x for x in state.get('terms',[state['target']]) if x]
 if not isinstance(anchor,str) and isinstance(review.get('anchor_quote'),str):
  issues.append('字段层级错误：anchor_quote写在了target_scope外。把已有原句移入target_scope.anchor_quote；target_scope对象须同时含reference、anchor_quote、reason，不要改原句或重复写顶层anchor_quote')
 elif not isinstance(anchor,str) or len(anchor)<8 or anchor not in context or not any(t in anchor for t in terms):
  issues.append('target_scope.anchor_quote须为实际局部上下文内含本剧名的至少8字连续原句')
 from rapid_film_workbench import display_excerpt
 if isinstance(anchor,str) and not any(t in display_excerpt(anchor) for t in terms):issues.append('清理标签后的anchor_quote未实际点名本剧，不能只用话题标签')
 if not binding.get('local_target_present'):issues.append('当前选句附近没有本剧名，不能用远处标题补指代')
 # An explicit other-work subject is never rescued by this contextual path.
 if any(not (m.group(1) in ('造型','服装') and re.match(r'指导|设计师|设计总监',selected[m.end():])) for m in re.finditer(r'《[^》]{1,60}》(?:的)?(预告|剧情|演员|角色|人设|表演|镜头|对手戏|打戏|造型|服装)',selected)):issues.append('所选句明确以另一《作品》的剧情/表演/镜头等为对象，不能借本剧上下文放行')
 # The displayed passage itself must refer to the current dramatic work/material.
 if not WORK_REFERENCE_CUE.search(selected):issues.append('所选句自身没有本剧/该剧/剧集/物料/预告等指代，只改target_scope说明无法修复；从给定窗口换有实际作品指代的连续原句，无则reject，禁止添字')
 return issues

def verified_local_reference(selected,binding,review,state):
 return not local_reference_issues(selected,binding,review,state)
