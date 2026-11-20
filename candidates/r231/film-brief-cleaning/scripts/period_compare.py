"""Compare audited viewpoints with preceding workbenches; never recall raw data."""
from pathlib import Path
import argparse,hashlib,json,re
from period_history import load_history

COMPARISON_RULE_VERSION='r102-selected-scope-v2'
RELATIONS={'continuing':'延续关注','developed':'更具体展开','opposing':'不同方向评价','unseen_angle':'前期未展示角度'}
RULES='''只读本包中的本期已审工作台观点和前期已展示观点，比较评价机制，不按剧名、人名或相同方面词机械判断。对本期每簇记录：continuing=同一核心评价延续；developed=在同一问题上有更具体的判断或机制展开；opposing=对同一问题出现不同方向评价；unseen_angle=前期给出的已展示材料里未出现的独立评价。新作者、新文章、换说法不等于新观点。须核对前期给出的全部展示句，不能只看标题，也不能把它原已包含的具体判断误说成首次出现。
每簇填写匹配的previous_group_ids和简短但具体的reason，绑定本期candidate_id与连续逐字quote_snippet。前期材料只能用于比较，不能作为本期证据；不能查原表、联网或补入旧句。unseen_angle表示前期工作台没展示，不代表全网新出现；opposing不代表声量反转。没有实际变化就标continuing。
从本期已审观点中选0—5个daily_focus作为今天值得展开的取材重点，具体判断是否重要和有代表性。优先证据扎实的新角度与具体展开，重要的延续观点也可保留。不要强造每日差异，也不能为避重复把证据弱的边角料抬为重点。每点reason说明为什么选它；不改本期原有簇标题、立场、样本或数量。取材理由只说明这些展示句的具体价值，不凭展示条数宣称整体声量最多、口碑主面或最集中；不能用‘坐实、证实’把来源评价变成已核实事实，也不用‘首次’作超出前期给定材料的判断。应明确表述为‘来源给出更具体的批评’或‘前期给定材料未展示这一具体判断’。这里只给取材建议，不重新聚类、不声称前期每个工作台观点都实际写进了报告。'''

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def validate_reason_scope(reason):
    text=str(reason or '')
    patterns=[r'(?:本期|当天|当日|当前).{0,8}(?:最集中|最多|主流|主面)',r'(?:口碑|舆论|负向|负面|正向|正面).{0,4}(?:主面|最集中|最多|主流)',r'(?:把|将).{0,12}(?:担忧|猜测|传闻).{0,6}(?:坐实|证实)',r'(?:已|得到|被)(?:经)?(?:坐实|证实)',r'首次(?:出现|进入|展示|成为)']
    if any(re.search(pattern,text) for pattern in patterns):
        raise ValueError('取材理由超出展示材料范围：不得凭条数概括总体声量、把评价证实为事实或宣称首次出现；请改为材料范围内具体判断')


def validate_comparison(dataset,result):
    groups=dataset['groups'];rows=result.get('group_reviews')
    if not isinstance(rows,list) or len(rows)!=len(groups):raise ValueError('须逐簇比较本期全部已审观点')
    indices=[row.get('group_index') for row in rows if isinstance(row,dict)]
    if len(indices)!=len(rows) or set(indices)!=set(range(len(groups))):raise ValueError('group_index须覆盖当前每簇且不重复')
    allowed=set(result.get('previous_group_ids',[]))
    for row in rows:
        validate_reason_scope(row.get('reason'))
        gi=row['group_index'];relation=row.get('relation');prior=row.get('previous_group_ids')
        if relation not in RELATIONS or len(str(row.get('reason') or '').strip())<15:raise ValueError('每簇须有明确关系及至少15字的具体比较依据')
        if not isinstance(prior,list) or len(set(prior))!=len(prior) or any(p not in allowed for p in prior):raise ValueError('只能引用本包的前期观点ID')
        if (relation=='unseen_angle') != (not prior):raise ValueError('前期未展示角度不引用前簇，其他关系必须指出比较对象')
        samples={s['candidate_id']:s for s in groups[gi]['samples']}
        evidence=row.get('evidence')
        if not isinstance(evidence,list) or not evidence:raise ValueError('比较依据至少绑定一条本簇当前已审原句')
        for ev in evidence:
            sample=samples.get(ev.get('candidate_id'));quote=ev.get('quote_snippet')
            if sample is None or not isinstance(quote,str) or len(quote)<12 or quote not in (sample.get('display_excerpt') or sample['excerpt']):
                raise ValueError('比较证据须来自本簇当前已审展示句的连续原文，不能引用历史样本')
    focus=result.get('daily_focus')
    if not isinstance(focus,list) or len(focus)>5:raise ValueError('本日重点选择0—5个，有证据才选，不凑数量')
    if not focus and len(str(result.get('no_focus_reason') or '').strip())<15:
        raise ValueError('未单列本日重点时须说明具体原因，不自动把空列表解释为没有新舆情')
    seen=set()
    for item in focus:
        gi=item.get('group_index')
        if not isinstance(gi,int) or isinstance(gi,bool) or gi not in indices or gi in seen or len(str(item.get('reason') or '').strip())<15:
            raise ValueError('本日重点须为不重复的当前簇编号，并具体说明取材价值')
        validate_reason_scope(item.get('reason'))
        seen.add(gi)

def prepare(args):
    path=Path(args.dataset);dataset=read(path)
    status=path.with_name('final_review_status.json')
    if path.name!='final_dataset.json' or not status.exists():
        raise ValueError('连续取材比较须承接完整已审工作台，不接受原表或未完成结果')
    from report_writing import check_dataset
    check_dataset(dataset,path)
    if dataset.get('semantic_verification')!='FINAL_SELECTED_ONLY' or any(s.get('ai_decision')!='keep' for g in dataset['groups'] for s in g['samples']):
        raise ValueError('连续比较只接受实际已审展示样本')
    history=load_history(args.history,dataset['target'],args.history_plan)
    if not history['entries']:raise ValueError('没有前期工作台时按独立一期处理，不生成虚假的差异比较')
    previous=[]
    from period_history import read_history_dataset
    for hi,entry in enumerate(history['entries']):
        prior=read_history_dataset(Path(entry['path']),dataset['target'])
        for gi,g in enumerate(prior['groups']):
            previous.append({'id':f'H{hi+1}-G{gi}','title':g['aspect'],'stance':g['stance'],
                'used_in_report':entry['displayed_groups'][gi]['used_in_report'],
                'displayed_excerpts':[(s.get('display_excerpt') or s['excerpt']) for s in g['samples']]})
    current=[{'group_index':i,'title':g['aspect'],'category':g.get('category',''),'stance':g['stance'],
        'samples':[{'candidate_id':s['candidate_id'],'excerpt':s.get('display_excerpt') or s['excerpt']} for s in g['samples']]}
        for i,g in enumerate(dataset['groups'])]
    out=Path(args.outdir);out.mkdir(parents=True,exist_ok=True)
    packet={'comparison_rule_version':COMPARISON_RULE_VERSION,'target':dataset['target'],'dataset_path':str(path.resolve()),'dataset_sha256':sha(path),'rules':RULES,
        'history_inputs':[item for e in history['entries'] for item in ([{'path':e['path'],'sha256':e['sha256']}]+([e['actual_plan']] if e['actual_plan'] else []))],
        'current':current,'previous':previous,'answer_file':str((out/'comparison_answer.json').resolve()),
        'answer_schema':{'dataset_sha256':'照抄','group_reviews':[{'group_index':0,'relation':'continuing/developed/opposing/unseen_angle',
        'previous_group_ids':['H1-G0'],'reason':'至少15字，说明机制关系','evidence':[{'candidate_id':'本簇当前ID','quote_snippet':'本期连续原句'}]}],
        'daily_focus':[{'group_index':0,'reason':'至少15字，说明本日取材价值'}],
        'no_focus_reason':'daily_focus为空时解释为什么不单列重点，至少15字'}}
    write(out/'comparison_input.json',packet)
    write(out/'comparison_answer.json',{'dataset_sha256':packet['dataset_sha256'],'group_reviews':[],'daily_focus':[]})
    print(json.dumps({'status':'COMPARISON_REVIEW_REQUIRED','input':str(out/'comparison_input.json'),'answer':packet['answer_file']},ensure_ascii=False))

def collect(args):
    out=Path(args.outdir);packet=read(out/'comparison_input.json');result=read(out/'comparison_answer.json')
    if result.get('dataset_sha256')!=packet['dataset_sha256'] or sha(packet['dataset_path'])!=packet['dataset_sha256']:
        raise ValueError('本期工作台指纹已改变，不能套用旧比较')
    for item in packet['history_inputs']:
        if sha(item['path'])!=item['sha256']:raise ValueError('历史比较输入已改变，须重新准备')
    result['previous_group_ids']=[p['id'] for p in packet['previous']]
    dataset=read(packet['dataset_path']);validate_comparison(dataset,result)
    result.update(comparison_rule_version=packet['comparison_rule_version'],target=packet['target'],history_inputs=packet['history_inputs'],scope='AUDITED_DISPLAYED_VIEWPOINT_COMPARISON_ONLY',
        population_novelty_claim=False,previous_groups=[{k:v for k,v in g.items() if k!='displayed_excerpts'} for g in packet['previous']],status='AI_COMPARISON_COMPLETED')
    write(out/'period_comparison.json',result)
    lines=[f"# 《{packet['target']}》本日取材重点",'',
        '依据本期已审工作台与前期已展示材料比较；不代表全网新增观点或声量变化。','']
    reviews={r['group_index']:r for r in result['group_reviews']}
    for item in result['daily_focus']:
        gi=item['group_index'];row=reviews[gi]
        lines.extend([f"## {dataset['groups'][gi]['aspect']}",f"比较关系：{RELATIONS[row['relation']]}",
            f"取材理由：{item['reason']}",f"与前期的区别：{row['reason']}",''])
    if not result['daily_focus']:lines.append('本次未单列本日重点：'+result['no_focus_reason'])
    (out/'daily_focus.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'comparison':str(out/'period_comparison.json'),'focus_count':len(result['daily_focus'])},ensure_ascii=False))

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--dataset',required=True);p.add_argument('--outdir',required=True)
    p.add_argument('--history',action='append',default=[]);p.add_argument('--history-plan',action='append',default=[])
    p=sub.add_parser('collect');p.add_argument('--outdir',required=True)
    args=parser.parse_args()
    try:prepare(args) if args.command=='prepare' else collect(args)
    except (KeyError,ValueError) as exc:parser.error(str(exc))
if __name__=='__main__':main()
