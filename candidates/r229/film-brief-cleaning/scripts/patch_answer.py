"""Apply explicit JSON field edits and verify disk contents; never infer judgments."""
from pathlib import Path
import argparse,copy,hashlib,json,os,tempfile

ALLOWED_NAMES={'editorial_plan.json','final_prose_review.json','candidate_closeout_confirmation.json','native_worker_registry.json'}
BINDINGS={'fingerprint','dataset_sha256','plan_sha256','draft_sha256','word_sha256','packet_id','profile','review_state'}

def inside(workspace,value):
    path=(workspace/value).resolve()
    if not path.is_relative_to(workspace):raise ValueError('路径必须位于本工作区内')
    return path

def apply(workspace,answer,patch):
    workspace=Path(workspace).resolve();target=inside(workspace,Path(answer));patchfile=inside(workspace,Path(patch))
    if target.suffix!='.json' or not ('answer' in target.name or target.name in ALLOWED_NAMES):
        raise ValueError('仅可修改答卷、写稿计划、终审或本期任务登记；不能修改原表、规则或控制器状态')
    if target==patchfile:raise ValueError('补丁文件不能就是答卷')
    before=target.read_bytes();original=json.loads(before.decode('utf-8-sig'));working=copy.deepcopy(original)
    payload=json.loads(patchfile.read_text(encoding='utf-8-sig'))
    if not isinstance(original,dict) or not isinstance(payload,dict):raise ValueError('答卷和补丁须为JSON对象')
    changes=payload.get('changes')
    if not isinstance(changes,list) or not changes:raise ValueError('changes须为非空列表')
    paths=[]
    for row in changes:
        if not isinstance(row,dict):raise ValueError('changes各项须为JSON对象')
        pointer=row.get('path')
        if not isinstance(pointer,str) or not pointer.startswith('/') or 'expected' not in row or 'value' not in row:
            raise ValueError('每项须有JSON Pointer path、原值expected及新值value')
        parts=[p.replace('~1','/').replace('~0','~') for p in pointer[1:].split('/')]
        if not parts or parts[0] in BINDINGS:raise ValueError('不能修改指纹、审核契约或发布状态')
        parent=working
        for part in parts[:-1]:parent=parent[int(part)] if isinstance(parent,list) else parent[part]
        key=int(parts[-1]) if isinstance(parent,list) else parts[-1]
        if parent[key]!=row['expected']:raise ValueError(f'{pointer}原值已变化，重新读取该字段；未写入任何修改')
        parent[key]=row['value'];paths.append(pointer)
    if target.read_bytes()!=before:raise ValueError('答卷在操作期间已变化，未写入；等待唯一原写者完成')
    raw=json.dumps(working,ensure_ascii=False,indent=2)+'\n'
    fd,temp=tempfile.mkstemp(prefix=target.stem+'-',suffix='.tmp',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(raw)
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    actual=target.read_bytes()
    if json.loads(actual.decode('utf-8'))!=working:raise ValueError('写后读回不一致，不能假定修改成功')
    return {'status':'WRITTEN_AND_READ_BACK','answer_file':str(target),'changed_paths':paths,'sha256':hashlib.sha256(actual).hexdigest(),'semantic_decisions_inferred':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',required=True);p.add_argument('--answer',required=True);p.add_argument('--patch',required=True);a=p.parse_args()
    try:result=apply(a.workspace,a.answer,a.patch)
    except (ValueError,OSError,KeyError,IndexError,TypeError) as e:p.error(str(e))
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
