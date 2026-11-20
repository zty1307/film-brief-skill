"""Bound low-yield sample backfill, without changing viewpoint discovery or AI decisions."""
from collections import OrderedDict,Counter
VERSION='r106-low-yield-backfill-v1'
MIN_REVIEWED_SOURCES=12
NO_KEEP_TAIL_SOURCES=8

def sample_stops(state):
 if state.get('coverage_repair_active') or state.get('workbench_positive_preferred_min'):return set()
 if not state.get('ai_viewpoint_discovery'):return set()
 keeps=Counter(v['group_index'] for v in state['judgments'].values() if v['decision']=='keep')
 stops=set();audit=state.setdefault('low_yield_sample_stops',{})
 for i,g in enumerate(state['pool']):
  if not 0<keeps[i]<10:continue
  reviewed=OrderedDict()
  for v in state['judgments'].values():
   if v['group_index']!=i:continue
   sid=v['source_id'];reviewed[sid]=bool(reviewed.get(sid)) or v['decision']=='keep';reviewed.move_to_end(sid)
  tail=list(reviewed.items())[-NO_KEEP_TAIL_SOURCES:]
  if len(reviewed)>=MIN_REVIEWED_SOURCES and len(tail)==NO_KEEP_TAIL_SOURCES and not any(keep for _,keep in tail):
   stops.add(i);audit[str(i)]={'rule_version':VERSION,'accepted_samples':keeps[i],'reviewed_source_count':len(reviewed),
    'last_source_ids':[sid for sid,_ in tail],'reason':'本组已有合格展示句，至少12个来源已审，最近8个独立来源没有新增keep；停止为凑到10条继续补审。',
    'unreviewed_sources_are_not_rejected':True}
 return stops
