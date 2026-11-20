"""Portable, offline human selection. No raw-file access and no model/API calls.

Hashes detect accidental changes; they are not a publisher signature or a new
semantic review. The original completed review remains separate and unchanged.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json

PROFILE = 'FILM_HUMAN_SELECTION_V1'
POLICY = 'HUMAN_SELECTED_POINTS_V1'
HANDOFF_MESSAGE = ('观点工作台已生成。你可以勾选需要的样本，导出后发给我写稿；'
                   '也可以让我根据这份工作台自动选材并写稿。接下来希望用哪种方式？如有重点或其他要求，也可以一起补充。')
WRITING_CHOICE = {'questions':[{
    'question':'工作台已生成，接下来希望怎么写稿？', 'header':'写稿方式', 'multiSelect':False,
    'options':[{'label':'我来选材料，稍后发文件','description':'在工作台勾选所需样本，导出“写稿选材”文件后发回来写稿。'},
               {'label':'由你自动选材写稿','description':'模型根据当前已审工作台选择观点和样本，直接写稿、审核并生成Word。'}]}]}
MANUAL_HANDOFF = '请在工作台勾选所需样本，点击“导出写稿选材”，再把文件发给我。'

RULE = ('只围绕人工选中的观点成稿，每个非负面组对应一段；选中的负面组统一为唯一末段。'
        '不能增加、拆分、静默删除或合并非负面选点。样本仅从导出清单内择优引用；'
        '正文段数跟随选点，不套用至少6个正面段。证据不足明确说明具体选点，退回选材；'
        '不补原表、不读取原工作台未选内容。所有原有证据、去重、字数、通读返修规则继续执行。')

def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def build_payload(dataset, review):
    """Only a completed workbench can offer an export button."""
    from workbench_completion import review_readiness
    review_readiness(dataset, review)
    from report_writing import check_original
    from source_policy import verify_unique_samples, policy_exclusion
    verify_unique_samples(dataset)
    groups = []
    for index, group in enumerate(dataset['groups']):
        meta = {k:group[k] for k in ('aspect','stance','category','core_mechanism','objective_kind','candidate_count') if k in group}
        # Original group guidance may quote unselected samples. Do not export it.
        guide=group.get('writing_guidance')
        if isinstance(guide,dict):
            meta['writing_guidance']={k:guide[k] for k in ('eligible','standalone_eligible') if k in guide}
        samples = []
        for sample in group['samples']:
            if sample.get('ai_decision') != 'keep' or sample.get('writing_supplement') or policy_exclusion(sample['source']):
                raise ValueError('未审或被排除样本不能进入人工选材')
            check_original(sample)
            snapshot = dumps(sample)
            samples.append({'candidate_id':sample['candidate_id'], 'snapshot':snapshot, 'sha256':digest(snapshot)})
        meta['selection_sample_hashes'] = {item['candidate_id']:item['sha256'] for item in samples}
        snapshot = dumps(meta)
        groups.append({'original_index':index, 'snapshot':snapshot, 'sha256':digest(snapshot), 'samples':samples})
    # Do not export unselected text or the full source dataset in the handoff.
    meta = {k:dataset[k] for k in ('target','writing_contract','single_source_policy') if k in dataset}
    meta['writing_overlap_pairs'] = [{k:p[k] for k in ('left','right')} for p in dataset.get('writing_overlap_pairs', [])]
    snapshot = dumps(meta)
    return {'handoff':{'skill':'film-brief-cleaning','next_action':'WRITE_FROM_HUMAN_SELECTION','user_instruction':'使用影视聚类与写稿skill，按这份选材生成Word，并附原文链接。'},'profile':PROFILE, 'origin_dataset_sha256':digest(dumps(dataset)),
            'origin_review_sha256':digest(dumps(review)),
            'origin_review':{k:review[k] for k in ('status','elapsed_seconds','deadline_seconds','pending_count','structure_verification','semantic_verification','editorial_status','sample_count','group_count','quantity_contract','supported_positive_cluster_count') if k in review}, 'metadata':{'snapshot':snapshot,'sha256':digest(snapshot)}, 'groups':groups}

def unpack(row):
    if not isinstance(row, dict) or not isinstance(row.get('snapshot'), str) or digest(row['snapshot']) != row.get('sha256'):
        raise ValueError('选材快照已改变或不完整，请回工作台重新导出')
    return json.loads(row['snapshot'])

def materialize(package):
    if package.get('profile') != PROFILE:
        raise ValueError('请提供工作台导出的写稿选材文件')
    receipt = package.get('origin_review', {})
    if (receipt.get('status') not in ('FINAL_SELECTED_COMPLETE','POSITIVE_VIEWPOINT_MIN_NOT_MET','TIME_LIMIT_REACHED')
            or receipt.get('pending_count', 0) != 0 or receipt.get('structure_verification') != 'PASS'
            or receipt.get('semantic_verification') != 'FINAL_SELECTED_ONLY'):
        raise ValueError('选材缺少已完成工作台的原审核回执')
    if not isinstance(package.get('origin_dataset_sha256'), str) or len(package['origin_dataset_sha256']) != 64:
        raise ValueError('选材缺少原工作台指纹')
    rows = package.get('groups')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ValueError('至少选择一个观点，最多16个工作台观点')
    metadata = unpack(package['metadata'])
    groups, old_indices, ids = [], [], set()
    for row in rows:
        gi = row.get('original_index')
        if type(gi) is not int or gi < 0 or gi in old_indices:
            raise ValueError('选材观点编号重复或无效')
        old_indices.append(gi)
        group = unpack(row)
        allowed_hashes = group.pop('selection_sample_hashes', {})
        if not row.get('samples'):
            raise ValueError('选中观点至少保留一个样本；独立成段仍须足够证据')
        group['samples'] = []
        for item in row['samples']:
            sample = unpack(item)
            cid = sample.get('candidate_id')
            if allowed_hashes.get(cid) != item.get('sha256'):
                raise ValueError('选材样本不属于所选观点的原审核快照')
            if not cid or cid != item.get('candidate_id') or cid in ids or sample.get('ai_decision') != 'keep':
                raise ValueError('样本编号重复、错配或没有完成审核')
            ids.add(cid)
            group['samples'].append(sample)
        groups.append(group)
    remap = {old:new for new,old in enumerate(old_indices)}
    pairs = [{**p,'left':remap[p['left']], 'right':remap[p['right']]}
             for p in metadata.pop('writing_overlap_pairs', []) if p['left'] in remap and p['right'] in remap]
    return {**metadata, 'groups':groups, 'writing_overlap_pairs':pairs,
            'semantic_verification':'FINAL_SELECTED_ONLY', 'quantity_contract':POLICY,
            'human_selection':{'profile':PROFILE,'origin_dataset_sha256':package['origin_dataset_sha256'],
                               'original_group_indices':old_indices, 'exported_at':package.get('exported_at'),
                               'rule':RULE}}

def validate_dataset(dataset, path):
    package_path = Path(path).with_name('human_selection.json')
    if not package_path.is_file() or materialize(read(package_path)) != dataset:
        raise ValueError('人工选材数据与导出文件不一致，不得增加未选观点或样本')

def paragraph_count(dataset):
    negative = lambda g:g.get('stance') in ('negative','负向词倾向')
    return sum(not negative(g) for g in dataset['groups']) + int(any(negative(g) for g in dataset['groups']))

def validate_points(dataset, points):
    if dataset.get('quantity_contract') != POLICY:
        return
    negative = {i for i,g in enumerate(dataset['groups']) if g.get('stance') in ('negative','负向词倾向')}
    expected = [{i} for i in range(len(dataset['groups'])) if i not in negative]
    if negative: expected.append(negative)
    actual = [p.get('group_indices', []) for p in points]
    if (len(actual) != len(expected) or any(not isinstance(a,list) or len(a)!=len(set(a)) for a in actual)
            or sorted(sorted(a) for a in actual) != sorted(sorted(a) for a in expected)):
        raise ValueError('正文须完整承接人工选点：每个非负面组一段，负面统一末段；不得自行增删、拆分或合并选点。证据不足退回选材。')

def configure_inventory(inventory, dataset):
    count = paragraph_count(dataset)
    inventory['human_selection'] = {**dataset['human_selection'], 'paragraph_count':count}
    inventory['writing_limits']['body_paragraph_count'] = [count,count]
    inventory['writing_limits']['rule'] = RULE
    inventory['editorial_style'] = {**inventory['editorial_style'], 'body_paragraph_count':[count,count], 'rule':RULE}
    inventory['plan_contract']['required_top_level']['points'] = RULE
    inventory['chart_scope_rule'] = '仅统计人工选中的样本；图题明确为人工选材观点分布，不代表原表或全网比例，不为其他造样本。'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from report_writing import check_dataset
    from rapid_film_workbench import render
    dataset = read(args.dataset); check_dataset(dataset, args.dataset)
    if args.output.exists(): parser.error('输出已存在，不覆盖原工作台')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(dataset, read(args.dataset.with_name('final_review_status.json'))), encoding='utf-8')
    print(json.dumps({'workbench':str(args.output),'status':'SELECTION_READY'},ensure_ascii=False))

if __name__ == '__main__': main()
