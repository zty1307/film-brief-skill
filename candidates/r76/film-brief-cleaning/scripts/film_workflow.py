from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = Path(__file__).resolve()
PIPELINE = SKILL_ROOT / "scripts" / "film_pipeline.py"
EXTRACTOR = SKILL_ROOT / "scripts" / "extract_sources.py"
LINK_CHECKER = SKILL_ROOT / "scripts" / "check_links.py"
WORKBENCH_TEMPLATE = SKILL_ROOT / "templates" / "workbench.html"
MEDIA_REGISTRY = SKILL_ROOT / "assets" / "media_subject_registry.json"
MANIFEST_NAME = "workflow_manifest.json"
STATUS_NAME = "workflow_status.json"
LOCK_NAME = "workflow.lock"
_PIPELINE_API = None


REVIEW_FILES = {
    "source": "source_reviews.json",
    "clusters": "cluster_definitions.json",
    "overrides": "cluster_overrides.json",
    "set": "cluster_set_reviews.json",
    "excerpts": "excerpt_reviews.json",
    "semantic": "final_excerpt_semantic_reviews.json",
    "post_count": "post_excerpt_cluster_count_reviews.json",
}

LEGACY_CONFIG_KEYS = {
    "cluster_min_independent_sources",
    "cluster_max_sources_before_review",
    "min_samples_per_cluster",
    "max_samples_per_cluster",
    "sample_budget",
    "reading_budget",
    "reading_budget_caps",
    "cluster_target_count",
}

ALLOWED_CLUSTER_EXCLUSION_REASONS = {
    "out_of_scope",
    "no_reportable_viewpoint",
    "cross_work_mismatch",
    "abusive_non_viewpoint",
    "promotion_only",
    "duplicate_or_corrupt",
}

STAGE_GUIDANCE = {
    "period_config": [
        "逐批次确认 serial_drama 或 episodic_variety，并填写真实监测时间窗",
        "strong_terms 只放能独立确认作品的全名或已核验唯一简称；与常见词重合的裸片名放 weak_terms，演员、嘉宾、角色放 auxiliary_terms",
        "同期对比作品放 comparison_terms；不得复制旧期聚类、旧报告点位或样本数量预算",
    ],
    "source_review": [
        "逐条阅读分片中的逐字候选和必要父帖上下文；只有候选不足以判断时才按 source_id 回查完整来源",
        "retain 只需决定和证据编号；读后同意 auto_reason 的 exclude 不重复抄理由，理由不同才另写。优先填写 evidence_candidate_index，候选均不适用时再提供逐字 evidence 或 evidence_position",
        "证据格式错误时只修证据字段，不得为了通过校验把 retain 改成 exclude",
        "相同观点的独立作者均可保留；综艺还须核对最新一期、前一期当周突出话题或节目级讨论",
    ],
    "cluster_discovery": [
        "用文件读取工具按顺序通读 readable_input_files，全部文件都要读；这是完整发现材料的无损阅读副本，不用shell打印或自行写转换脚本。仅在片段语义不清时按 source_id 回查 retained_sources 全文",
        "只依据本期数据归纳一级观点，不读取人工成品反推答案",
        "标题写成可直接理解的报告体判断句，并明确 positive、objective 或 negative",
        "按共同评价机制聚合，人物、角色、段子和单项指标通常作为证据侧面；9至16个仅为颗粒度复查范围",
    ],
    "cluster_assignment_review": [
        "逐条核对目标作品、评价方面、当前片段立场和连续逐字证据",
        "required_any 未穷尽同义表达时，用当前片段中的逐字 anchor_terms 通过初步方面门，最终摘录复核仍须判断完整语义",
        "当前模板只提交本轮残差；控制器用脚本账本合并此前记录，当前同ID提交优先",
        "片段有效但归错簇时应移动、缩窄簇名或建立有数据支持的新簇；明确跨剧、无观点、攻击噪声或纯推广可用 cluster=__exclude__ 并给逐字证据",
        "同一来源进入第二簇时，两段原文必须互不重叠且分别形成完整观点",
    ],
    "cluster_definition_repair": [
        "若 validation_errors 指向 JSON 语法或编码错误，保留已有分类内容，只修报错位置；不清空、不重新归纳整套分类",
        "当前大量来源没有任何观点簇能够承接，先从缺口样本归纳遗漏观点或补充同义方面词",
        "修改 cluster_definitions.json 后重新 advance；不要为几十条缺口逐条写 override",
        "只增加数据中反复出现或虽少量但具有独立报告价值的观点，不建其他综合或人物姓名簇",
    ],
    "cluster_set_review": [
        "根据代表样本检查簇标题是否准确概括共同评价机制；无需重复抄写标题分句和证据ID",
        "正面簇只含正面片段、负面簇只含负面片段，客观簇只含事实、均衡观察或舆情分布",
        "同时检查期次范围、来源角色、过度切碎和大口袋簇；禁止为进入9至16个而机械合并或删除样本",
        "只填写模板中的 decision、report_role、scope_type、issues 和简短 reason；客观簇另填 objective_subtype，通过时 issues 保持空数组",
    ],
    "final_assignment_repair": [
        "只处理pending_view_ids的转簇请求；它们是待办，其他已交最终判断已保存，不重跑成功任务",
        "认可主簇转移请求时，先执行operator_contract.scoped_patch.prepare_command，完整读取其read_now，只填写返回的semantic-patch语义字段，再apply_command和next_command；不先读或批量Edit整张required_file",
        "受影响来源已有副簇时才使用原有allowed_repair_writes路径，按legacy_assignment_read_now读取所需输入；保留无关来源及未受影响主/副簇，显式填写逐字passage、passage_stance和必要目标证据，顺序编辑后advance",
        "写在reason里的已转簇不等于文件已改；子任务建议不是批准，先核对原文和当前簇定义，不能用临时keep/drop占位",
        "若否决请求，在optional_rejection_review_file为该view_id填写完整最终判断，并加reassignment_request_resolution=reject_request及具体reassignment_request_reason；仍需通过正常最终语义校验",
        "输入是无损阅读副本；过长_readable_text按segments读取，分页时继续直到读完，禁止子任务自行修改归类文件",
    ],
    "excerpt_review": [
        "最多选择同一来源中按原顺序出现的两个逐字片段，不改写、不补字、不调换顺序",
        "优先形成70至150字的完整判断和具体依据；清除话题标签、表情、链接、账号标记及分享套话",
        "好看、封神、期待、笑点拉满等泛泛态度没有具体依据时不进入展示层",
    ],
    "excerpt_preflight": [
        "只处理 excerpt_preflight_failures.json 列出的项目，其他摘录和复核结果保持不变",
        "从对应来源全文选择一至两个按原顺序出现的逐字片段，不改写、不补字、不调换顺序",
        "修复空摘录、残缺边界、引号不成对、社交标签残留或超过150字等确定性格式问题",
    ],
    "final_excerpt_review": [
        "审核最终清洗后展示文字本身，常规项只核对方面、局部立场和语义完整性；对象与跨作品字段仅在输入明确标记时填写",
        "常规保留只填 decision 和 evidence_candidate_index；keep 表示已读并确认四项对齐，控制器补齐指纹、立场与确认字段；必要时分填方面/立场编号",
        "判断样本是否支持簇的核心评价方向，不要求每条覆盖标题中全部并列细节；标题概括过窄时调整标题，不因此逐条删样本",
        "target_review_required 时从 target_evidence_segments 选编号，并判断目标锚点与当前观点是否同属一个对象；标签、名单或综合盘点中的顺带出现不能通过",
        "work_consistency_review_required 时核对方面证据究竟评价哪部作品；展示段含购买、抽奖、关注、参与指令或观点前无关八卦时先重截",
        "片段可修复时先回看全文重截；归簇不当时先尝试重归簇或建立真实新簇，再考虑逐来源 drop",
        "不得用文章整体立场否定局部观点；代表选样每簇最多10条不同来源，未选中不是drop，不强凑条数",
        "input_file 的 submission_errors 是当前条目具体错误；词表存疑可用 reason 简短解释真实语义，不得靠校验函数批量生成决定",
    ],
    "render_repair": [
        "逐项读取 render_failures.json，回到对应来源修复摘录、归簇或复核字段",
        "不得删除失败队列、伪造通过字段或直接调用底层 render 绕过门槛",
    ],
    "post_excerpt_count_review": [
        "只审核终审 drop 之后实际仍有展示样本的一级观点簇数量和颗粒度",
        "不要回改终审前 cluster_count，也不要为进入9至16机械合并、拆分或补造观点",
        "模板已预填输入指纹；只填写 decision、issues 和 reason，越界时补充 exception_reason，不重复填写实际簇数",
    ],
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 顶层必须是 JSON 对象")
    return value


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{number} 不是 JSON 对象")
            rows.append(value)
    return rows


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def replace_with_retry(source: Path, destination: Path) -> None:
    """Preserve atomic replacement while tolerating brief Windows file holds."""
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_paths(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted({item.resolve() for item in paths}, key=lambda item: str(item).lower()):
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        if not path.exists():
            digest.update(b"MISSING")
        else:
            digest.update(sha256(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def normalized(value: object) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", clean(value)).lower()


def diverse_review_sample(rows: list[dict], limit: int) -> list[dict]:
    """Choose varied repair examples instead of the first near-duplicates."""
    if len(rows) <= limit:
        return rows

    def grams(row: dict) -> set[str]:
        text = normalized(f"{row.get('title', '')}{row.get('current_passage', '')}")
        return {text[index:index + 3] for index in range(max(1, len(text) - 2))}

    candidates = [(row, grams(row)) for row in sorted(rows, key=lambda item: clean(item.get("source_id")))]
    selected = [candidates.pop(0)]
    while candidates and len(selected) < limit:
        best_index = min(
            range(len(candidates)),
            key=lambda index: (
                max(
                    len(candidates[index][1] & chosen[1]) / max(1, len(candidates[index][1] | chosen[1]))
                    for chosen in selected
                ),
                clean(candidates[index][0].get("source_id")),
            ),
        )
        selected.append(candidates.pop(best_index))
    return [row for row, _ in selected]


def exact_key(value: object) -> str:
    """Trim outer whitespace while preserving the tab separators used by review keys."""
    return str(value or "").strip(" \r\n")


def load_manifest(workspace: Path) -> dict:
    manifest_path = workspace.resolve() / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"缺少 {manifest_path}；先运行 init")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("workflow") != "film-brief-cleaning":
        raise ValueError("workflow_manifest.json 版本或 workflow 不匹配")
    return manifest


def save_manifest(workspace: Path, manifest: dict) -> None:
    manifest["updated_at"] = now()
    write_json(workspace.resolve() / MANIFEST_NAME, manifest)


class WorkflowBusy(RuntimeError):
    pass


@contextlib.contextmanager
def kernel_file_lock(lock_path: Path, busy_message: str):
    """Hold a one-byte operating-system lock for the lifetime of the context."""
    lock_path = lock_path.resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Kernel-owned file locks are released on exit or crash. Never use os.kill
    # to probe PIDs: its Windows semantics can terminate the other process.
    handle = lock_path.open('a+b')
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise WorkflowBusy(f'{busy_message}：{lock_path}') from exc
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextlib.contextmanager
def workflow_lock(workspace: Path):
    """Prevent two agents from mutating the same workflow directory."""
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    with kernel_file_lock(workspace / LOCK_NAME, "该工作区已有任务运行，禁止并发推进"):
        yield


def paths_for(manifest: dict) -> dict[str, Path]:
    workspace = Path(manifest["workspace"]).resolve()
    review_dir = workspace / "reviews"
    return {
        "workspace": workspace,
        "extract": workspace / "extract",
        "run": workspace / "run",
        "config": workspace / "config" / "period_config.json",
        "records": workspace / "extract" / "records.jsonl",
        "reviews": review_dir,
        "templates": workspace / "review_templates",
        "output": Path(manifest["output"]).resolve(),
        "staged_output": workspace / "run" / "workbench.verified-candidate.html",
        "source_validation": workspace / "run" / "source_review_validation.json",
        "link_candidates": workspace / "run" / "source_link_candidates.jsonl",
        "source_ledger": workspace / "run" / "source_reviews.ledger.json",
        "semantic_ledger": workspace / "run" / "final_excerpt_semantic_reviews.ledger.json",
        "override_validation": workspace / "run" / "cluster_override_validation.json",
        "override_ledger": workspace / "run" / "cluster_overrides.ledger.json",
        "override_effective": workspace / "run" / "cluster_overrides.effective.json",
        **{key: review_dir / name for key, name in REVIEW_FILES.items()},
    }


def run_command(command: list[str], cwd: Path | None = None, allow_review_stop: bool = False) -> dict:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    result = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "finished_at": now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    structured = None
    for candidate in (completed.stdout.strip(), completed.stderr.strip()):
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                structured = value
                break
        except json.JSONDecodeError:
            continue
    if structured is not None:
        result["business_payload"] = structured
    if completed.returncode:
        known_business_stop = bool(
            allow_review_stop
            and structured
            and clean(structured.get("status")) in {"REVIEW_REQUIRED", "FAIL", "BLOCKED"}
        )
        if not known_business_stop:
            diagnostic = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
            if structured:
                message = json.dumps(structured, ensure_ascii=False)
            else:
                message = next(
                    (line.strip() for line in reversed(diagnostic.splitlines()) if line.strip()),
                    diagnostic,
                )
            raise RuntimeError(message)
    return result


def binding(workflow_id: str, input_paths: list[Path]) -> dict:
    return {
        "workflow_id": workflow_id,
        "input_sha256": digest_paths(input_paths),
        "input_files": [str(path.resolve()) for path in input_paths],
        "preserve_this_block": True,
    }


def ensure_template(path: Path, payload: dict) -> None:
    if not path.exists() or read_json(path).get('_workflow') != payload.get('_workflow'):
        write_json(path, payload)


def review_payload(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        # Weak models occasionally leave a truncated or non-object JSON file.
        # Treat this as a correctable review submission, not a broken pipeline.
        return {"_review_file_error": f"{type(exc).__name__}: {exc}"}


def binding_issue(payload: dict | None, workflow_id: str, inputs: list[Path]) -> str:
    if payload is None:
        return "missing"
    if clean(payload.get("_review_file_error")):
        return f"invalid_review_file: {clean(payload.get('_review_file_error'))}"
    meta = payload.get("_workflow")
    if not isinstance(meta, dict):
        return "missing_workflow_binding"
    if clean(meta.get("workflow_id")) != workflow_id:
        return "workflow_id_mismatch"
    if clean(meta.get("input_sha256")) != digest_paths(inputs):
        return "input_fingerprint_stale"
    return ""


def verbatim_repair_scope_fingerprints(p: dict, reviews: dict) -> dict[str, str]:
    """Bind raw-cut requests to their own source/definition, not other members.

    This preserves extraction requests only; it never carries keep judgments.
    """
    canonical = p['run'] / 'clustered_items.json'
    if not canonical.exists() or not p['clusters'].exists():
        return {}
    clustered = read_json(canonical)
    definitions = read_json(p['clusters']).get('batches', {})
    config_digest = sha256(p['config']) if p['config'].exists() else 'missing'
    result = {}
    for item in clustered.get('items', []):
        identifier = f"{item.get('source_id', '')}::{item.get('cluster_id', '')}"
        review = reviews.get(identifier)
        if not isinstance(review, dict) or not clean(review.get('reason')):
            continue
        definition = next((value for value in definitions.get(item.get('batch'), [])
                           if str(value.get('id')) == str(item.get('cluster_id'))), None)
        if not isinstance(definition, dict):
            continue
        source = item.get('source') or {}
        body = pipeline_api().source_text(source)
        fragments = review.get('fragments')
        if not isinstance(fragments, list) or not all(isinstance(value, str) for value in fragments):
            continue
        try:
            positions = review.get('positions')
            if positions:
                pipeline_api().validate_fragment_positions(body, fragments, positions)
            else:
                pipeline_api().locate_fragments(body, fragments)
        except (ValueError, TypeError, IndexError):
            continue
        scope = {'view_id': identifier, 'batch': item.get('batch'),
                 'source': {key: source.get(key) for key in
                            ('id', 'batch', 'title', 'body', 'parent_body', 'asr')},
                 'definition': definition, 'config_sha256': config_digest,
                 'repair': review}
        encoded = json.dumps(scope, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')
        result[identifier] = hashlib.sha256(encoded).hexdigest()
    return result


def seal_current_verbatim_repairs(p: dict, payload: dict, workflow_id: str) -> dict:
    if binding_issue(payload, workflow_id, [p['run'] / 'clustered_items.json']):
        return payload
    scopes = verbatim_repair_scope_fingerprints(p, list_review_map(payload, 'view_id'))
    if not scopes and '_verbatim_repair_scopes' not in payload:
        return payload
    return {**payload, '_verbatim_repair_scopes': scopes}


def rebind_scoped_verbatim_repairs(p: dict, payload: dict, workflow_id: str) -> dict:
    reviews = list_review_map(payload, 'view_id')
    scopes = payload.get('_verbatim_repair_scopes') or {}
    if not isinstance(scopes, dict):
        scopes = {}
    meta = payload.get('_workflow')
    same_workflow = isinstance(meta, dict) and meta.get('workflow_id') == workflow_id
    current = verbatim_repair_scope_fingerprints(p, reviews) if same_workflow else {}
    kept = {key: value for key, value in reviews.items()
            if current.get(key) and current[key] == scopes.get(key)}
    return {'scope': 'current_period_verbatim_excerpt_reviews',
            '_workflow': binding(workflow_id, [p['run'] / 'clustered_items.json']),
            'reviews': kept,
            '_verbatim_repair_scopes': {key: current[key] for key in kept},
            'rebind_audit': {'preserved_view_ids': sorted(kept),
                             'not_carried_view_ids': sorted(set(reviews) - set(kept)),
                             'semantic_acceptance': 'not_inferred'}}


def list_review_map(payload: dict | None, key_field: str) -> dict[str, dict]:
    if not payload:
        return {}
    values = payload.get("reviews", [])
    if isinstance(values, dict):
        return {clean(key): value for key, value in values.items() if isinstance(value, dict)}
    result = {}
    if isinstance(values, list):
        for item in values:
            if isinstance(item, dict) and clean(item.get(key_field)):
                result[clean(item[key_field])] = item
    return result


def expand_compact_submission(payload: dict | None, workflow_id: str, inputs: list[Path], items: dict[str, dict], *, final: bool = False) -> dict | None:
    """Backfill mechanical fields only after an explicit AI decision.

    Compact keep means the same semantic assertions as the verbose contract;
    it does not license a script to invent decisions. Stale submissions are
    never rebound to new evidence.
    """
    if not payload or binding_issue(payload, workflow_id, inputs):
        return payload
    key_field = "view_id" if final else "source_id"
    expanded = {}
    for key, original in list_review_map(payload, key_field).items():
        review = dict(original)
        item = items.get(key)
        if item is None:
            expanded[key] = review
            continue
        if final:
            submitted_fingerprint = clean(review.get("review_fingerprint"))
            current_fingerprint = clean(item.get("review_fingerprint"))
            fingerprint_matches = not submitted_fingerprint or submitted_fingerprint == current_fingerprint
            if fingerprint_matches:
                review["review_fingerprint"] = current_fingerprint
                if review.get("decision") == "keep":
                    # Legacy models sometimes put an explicit segment number
                    # in the text field. Normalize only a valid, unambiguous
                    # current-menu choice; never invent or override a choice.
                    legacy_work_evidence = review.get("work_consistency_evidence")
                    legacy_index = (
                        legacy_work_evidence if type(legacy_work_evidence) is int else
                        int(legacy_work_evidence.strip())
                        if isinstance(legacy_work_evidence, str) and re.fullmatch(r"[0-9]{1,9}", legacy_work_evidence.strip()) else None
                    )
                    explicit_work_index = review.get("work_consistency_evidence_candidate_index")
                    matching_work_index = (
                        explicit_work_index in (None, "")
                        or (type(explicit_work_index) is int and explicit_work_index == legacy_index)
                        or (isinstance(explicit_work_index, str) and re.fullmatch(r"[0-9]{1,9}", explicit_work_index.strip())
                            and int(explicit_work_index.strip()) == legacy_index)
                    )
                    if (item.get("work_consistency_review_required") and legacy_index is not None
                            and str(legacy_index) in (item.get("excerpt_segments") or {})
                            and matching_work_index):
                        review.pop("work_consistency_evidence", None)
                        review["work_consistency_evidence_candidate_index"] = legacy_index
                    review.setdefault("stance", item.get("cluster_stance"))
                    review.setdefault("self_contained", True)
                    index = review.pop("evidence_candidate_index", None)
                    for field in ("aspect_evidence", "stance_evidence", "cluster_claim_evidence"):
                        review.setdefault(f"{field}_candidate_index", index)
                    for required, field in (
                        ("target_review_required", "target_relation_passed"),
                        ("work_consistency_review_required", "work_consistency_passed"),
                        ("cluster_claim_review_required", "cluster_claim_passed"),
                        ("promotion_markers", "independent_opinion_passed"),
                    ):
                        if item.get(required):
                            review.setdefault(field, True)
        elif not final and review.get("decision") == "exclude" and item.get("auto_decision") == "exclude":
            if not clean(review.get("reason")):
                review["reason"] = item.get("auto_reason", "")
        if not final and review.get('decision') in {'retain_core', 'retain_consensus', 'exclude'}:
            position = review.get('evidence_position')
            if (isinstance(position, list) and len(position) == 1 and isinstance(position[0], list)
                    and len(position[0]) == 2 and all(type(value) is int for value in position[0])):
                # One explicitly chosen interval has an unambiguous flat form.
                # Bounds and verbatim equality are still checked by the validator.
                review['evidence_position'] = list(position[0])
        expanded[key] = review
    return {**payload, "reviews": expanded if final else [{**value, key_field: key} for key, value in expanded.items()]}


def next_incomplete_chunk(paths: list[Path], key_field: str, missing_ids: set[str]) -> tuple[Path | None, int, int]:
    """Return only the next chunk a weak model needs to read."""
    for index, path in enumerate(paths, start=1):
        ids = {clean(item.get(key_field)) for item in read_jsonl(path)}
        if ids & missing_ids:
            return path, index, len(paths)
    return (paths[0] if paths else None), 1 if paths else 0, len(paths)


def compact_final_review_tail(items: list[dict], missing_ids: set[str], errors: dict,
                              *, max_items: int = 60, max_bytes: int = 120_000) -> list[dict] | None:
    """Pack a bounded residual handoff, never alter canonical input or judgments.

    Initial/full queues and oversized tails keep the ordinary chunk path.
    Current errors count toward the same byte budget as the evidence itself.
    """
    if not missing_ids or len(missing_ids) >= len(items) or len(missing_ids) > max_items:
        return None
    pending = [
        {**item, **({'submission_errors': errors[item['view_id']]}
                   if errors.get(item.get('view_id')) else {})}
        for item in items if clean(item.get('view_id')) in missing_ids
    ]
    if (len(pending) != len(missing_ids)
            or {clean(item.get('view_id')) for item in pending} != missing_ids):
        return None
    size = sum(len((json.dumps(item, ensure_ascii=False) + '\n').encode('utf-8')) for item in pending)
    return pending if size <= max_bytes else None


def cumulative_review_payload(
    workflow_id: str,
    input_paths: list[Path],
    current: dict | None,
    ledger_path: Path,
    key_field: str,
    scope: str,
    reviews_as_dict: bool = False,
) -> dict:
    """Merge small per-chunk submissions into a script-owned cumulative ledger."""
    merged: dict[str, dict] = {}
    if ledger_path.exists():
        ledger = read_json(ledger_path)
        if not binding_issue(ledger, workflow_id, input_paths):
            merged.update(list_review_map(ledger, key_field))
    if current is not None and not binding_issue(current, workflow_id, input_paths):
        merged.update(list_review_map(current, key_field))
    reviews: dict | list
    if reviews_as_dict:
        reviews = merged
    else:
        reviews = [
            {**value, key_field: key}
            for key, value in sorted(merged.items())
        ]
    return {
        "scope": scope,
        "_workflow": binding(workflow_id, input_paths),
        "contract": {
            "script_owned_cumulative_ledger": True,
            "current_submission_only": True,
            "current_same_id_wins": True,
        },
        "reviews": reviews,
    }


def cumulative_fingerprinted_review_payload(
    workflow_id: str,
    input_paths: list[Path],
    current: dict | None,
    ledger_path: Path,
    input_map: dict[str, dict],
) -> dict:
    """Keep final-review answers whose own item fingerprint is still current."""
    merged: dict[str, dict] = {}
    blank_reviews: dict[str, dict] = {}

    def merge_matching(payload: dict | None) -> None:
        if not isinstance(payload, dict):
            return
        meta = payload.get("_workflow")
        if not isinstance(meta, dict) or clean(meta.get("workflow_id")) != workflow_id:
            return
        for view_id, review in list_review_map(payload, "view_id").items():
            current_item = input_map.get(view_id)
            if (
                current_item
                and clean(review.get("review_fingerprint"))
                == clean(current_item.get("review_fingerprint"))
            ):
                if not clean(review.get("decision")):
                    if view_id not in blank_reviews:
                        blank = final_excerpt_review_template_payload(workflow_id, input_paths, [current_item])
                        expanded = expand_compact_submission(blank, workflow_id, input_paths, {view_id: current_item}, final=True)
                        blank_reviews[view_id] = list_review_map(expanded, "view_id").get(view_id, {})
                    if review == blank_reviews[view_id]:
                        # An unchanged form is pending, not a failed answer.
                        # A later explicit reset to the exact blank form must
                        # also invalidate any earlier same-item answer.
                        merged.pop(view_id, None)
                        continue
                merged[view_id] = review

    if ledger_path.exists():
        merge_matching(review_payload(ledger_path))
    merge_matching(current)
    return {
        "scope": "current_period_final_excerpt_semantic_reviews",
        "_workflow": binding(workflow_id, input_paths),
        "contract": {
            "script_owned_cumulative_ledger": True,
            "current_submission_only": True,
            "current_same_id_wins": True,
            "preserve_unchanged_item_fingerprints": True,
        },
        "reviews": merged,
    }


def cluster_override_validation_issues(
    retained_sources: list[dict], cluster_payload: dict, override_payload: dict,
    period_config: dict | None = None,
) -> list[dict]:
    """Validate user-correctable override contract errors before clustering.

    The pipeline still performs the authoritative semantic checks.  This fast
    controller preflight prevents a malformed AI review from surfacing as a
    generic BROKEN state after a full cluster run.
    """
    sources = {clean(row.get("id")): row for row in retained_sources if clean(row.get("id"))}
    known_clusters = {
        clean(batch): {clean(item.get("id")) for item in items if isinstance(item, dict)}
        for batch, items in (cluster_payload.get("batches") or {}).items()
        if isinstance(items, list)
    }
    cluster_stances = {
        (clean(batch), clean(item.get('id'))): clean(item.get('stance'))
        for batch, items in (cluster_payload.get('batches') or {}).items()
        if isinstance(items, list)
        for item in items if isinstance(item, dict)
    }
    overrides = override_payload.get("overrides", {})
    if not isinstance(overrides, dict):
        return [{"source_id": "", "issue": "overrides_not_object", "required_fix": "overrides 必须为对象"}]
    issues: list[dict] = []
    for source_id, review in overrides.items():
        source_id = clean(source_id)
        if source_id not in sources:
            issues.append({"source_id": source_id, "issue": "unknown_source_id", "required_fix": "删除非当前保留池来源，或从最新模板重新填写"})
            continue
        if not isinstance(review, dict):
            issues.append({"source_id": source_id, "issue": "override_not_object", "required_fix": "该来源的归簇修正必须为对象"})
            continue
        if not clean(review.get("reason")):
            issues.append({"source_id": source_id, "issue": "missing_reason", "required_fix": "填写当前来源专属 reason"})
        cluster_id = clean(review.get("cluster"))
        if cluster_id == "__exclude__":
            reason_code = clean(review.get("exclude_reason_code"))
            if reason_code not in ALLOWED_CLUSTER_EXCLUSION_REASONS:
                issues.append({
                    "source_id": source_id,
                    "issue": "invalid_exclude_reason_code",
                    "required_fix": "exclude_reason_code 必须取 allowed_reason_codes 中的一项",
                    "allowed_reason_codes": sorted(ALLOWED_CLUSTER_EXCLUSION_REASONS),
                })
            evidence = clean(review.get("exclusion_evidence") or review.get("evidence"))
            row = sources[source_id]
            own = clean(row.get("body")) or clean(row.get("asr")) or clean(row.get("title"))
            title = clean(row.get("title"))
            source_text = own if row.get("post_type") in {"评论", "转帖"} else clean(
                (title + " " + own) if title and normalized(title) not in normalized(own) else own
            )
            if not evidence:
                issues.append({
                    "source_id": source_id,
                    "issue": "missing_exclusion_evidence",
                    "required_fix": "填写 exclusion_evidence；也兼容 evidence，内容必须是本来源全文的连续逐字子串",
                })
            elif evidence not in source_text:
                issues.append({
                    "source_id": source_id,
                    "issue": "exclusion_evidence_not_verbatim",
                    "required_fix": "从该来源全文原样复制连续文字到 exclusion_evidence，不得概括、改写或规范化标点",
                    "submitted_evidence": evidence,
                })
            continue
        batch = clean(sources[source_id].get("batch"))
        if not cluster_id or cluster_id not in known_clusters.get(batch, set()):
            issues.append({
                "source_id": source_id,
                "issue": "unknown_cluster",
                "required_fix": f"cluster 必须是批次 {batch} 的现有簇 ID，或使用 __exclude__",
                "submitted_cluster": cluster_id,
            })
        secondary = clean(review.get("secondary_cluster"))
        if secondary and (secondary == cluster_id or secondary not in known_clusters.get(batch, set())):
            issues.append({
                "source_id": source_id,
                "issue": "invalid_secondary_cluster",
                "required_fix": "secondary_cluster 必须是同批次中不同于主簇的现有簇 ID",
                "submitted_cluster": secondary,
            })
        for prefix, chosen in (('', cluster_id), ('secondary_', secondary)):
            supplied_stance = clean(review.get(f'{prefix}passage_stance'))
            expected_stance = cluster_stances.get((batch, chosen))
            if supplied_stance and expected_stance and supplied_stance != expected_stance:
                issues.append({
                    'source_id': source_id, 'issue': f'{prefix}passage_stance_conflict',
                    'submitted_stance': supplied_stance, 'cluster_stance': expected_stance,
                    'required_fix': '按原文局部评价重新判断立场并选择相符的簇；不得只为过检改立场标签',
                })
        row = sources[source_id]
        own = clean(row.get("body")) or clean(row.get("asr")) or clean(row.get("title"))
        title = clean(row.get("title"))
        full_text = own if row.get("post_type") in {"评论", "转帖"} else clean(
            (title + " " + own) if title and normalized(title) not in normalized(own) else own
        )
        target = (period_config or {}).get("targets", {}).get(batch, {})
        target_terms = [
            clean(value)
            for field in ("strong_terms", "weak_terms")
            for value in target.get(field, [])
            if clean(value)
        ]
        for prefix in ("", "secondary_"):
            evidence_key = f"{prefix}target_evidence"
            target_evidence = clean(review.get(evidence_key))
            if target_evidence and target_terms and not any(term in target_evidence for term in target_terms):
                issues.append({
                    "source_id": source_id,
                    "issue": f"{prefix}target_evidence_missing_target_anchor",
                    "required_fix": f"{evidence_key} 必须逐字包含目标作品名或已配置简称；不要复制文章开头的无关段落",
                    "accepted_target_terms": target_terms,
                })
        for prefix in ("", "secondary_"):
            fragment_key = f"{prefix}passage_fragments"
            position_key = f"{prefix}passage_positions"
            fragments = review.get(fragment_key)
            positions = review.get(position_key)
            if fragments is None and positions is None:
                continue
            if prefix and not secondary:
                issues.append({
                    "source_id": source_id,
                    "issue": "secondary_passage_without_secondary_cluster",
                    "required_fix": "只有填写 secondary_cluster 时才能提交 secondary_passage_fragments/positions",
                })
                continue
            if not isinstance(fragments, list):
                issues.append({
                    "source_id": source_id,
                    "issue": f"{prefix}passage_fragments_positions_incomplete",
                    "required_fix": f"必须填写 {fragment_key} 数组；{position_key} 可省略并由脚本按原文顺序定位",
                })
                continue
            if positions is not None and not isinstance(positions, list):
                issues.append({
                    "source_id": source_id,
                    "issue": f"{prefix}passage_positions_invalid",
                    "required_fix": f"{position_key} 必须为数组；也可省略并由脚本定位",
                })
                continue
            if not 1 <= len(fragments) <= 2 or (positions is not None and len(fragments) != len(positions)):
                issues.append({
                    "source_id": source_id,
                    "issue": f"{prefix}passage_fragments_positions_count_invalid",
                    "required_fix": "归簇证据只允许一至两段，片段数与位置数必须一致",
                })
                continue
            if positions is None:
                cursor = 0
                for index, fragment in enumerate(fragments):
                    if not isinstance(fragment, str) or not fragment:
                        issues.append({
                            "source_id": source_id,
                            "issue": f"{prefix}passage_fragment_invalid",
                            "fragment_index": index,
                            "required_fix": "片段必须是非空的原文字符串",
                        })
                        continue
                    start = full_text.find(fragment, cursor)
                    if start < 0:
                        issues.append({
                            "source_id": source_id,
                            "issue": f"{prefix}passage_fragment_not_verbatim_or_out_of_order",
                            "fragment_index": index,
                            "required_fix": "片段必须能在本来源全文中按提交顺序逐字定位",
                        })
                        continue
                    cursor = start + len(fragment)
                continue
            previous_end = 0
            for index, (fragment, position) in enumerate(zip(fragments, positions)):
                if not isinstance(fragment, str) or not fragment:
                    issues.append({
                        "source_id": source_id,
                        "issue": f"{prefix}passage_fragment_invalid",
                        "fragment_index": index,
                        "required_fix": "片段必须是非空的原文字符串",
                    })
                    continue
                if not isinstance(position, list) or len(position) != 2 or any(not isinstance(value, int) for value in position):
                    issues.append({
                        "source_id": source_id,
                        "issue": f"{prefix}passage_position_invalid",
                        "fragment_index": index,
                        "required_fix": "每个位置必须是整数 [start, end]",
                    })
                    continue
                start, end = position
                if start < previous_end or start < 0 or end < start or end > len(full_text):
                    issues.append({
                        "source_id": source_id,
                        "issue": f"{prefix}passage_position_out_of_order_or_range",
                        "fragment_index": index,
                        "required_fix": "位置必须在本来源全文内按原顺序排列且互不重叠",
                    })
                    continue
                if full_text[start:end] != fragment:
                    issues.append({
                        "source_id": source_id,
                        "issue": f"{prefix}passage_fragment_not_verbatim_at_position",
                        "fragment_index": index,
                        "required_fix": "按指定 start/end 从本来源全文逐字复制片段，不得改写或改标点",
                    })
                previous_end = end
    return issues


def cluster_override_map(payload: dict | None) -> dict[str, dict]:
    if not payload:
        return {}
    values = payload.get("overrides", {})
    return {
        clean(key): value
        for key, value in values.items()
        if clean(key) and isinstance(value, dict)
    } if isinstance(values, dict) else {}


def cumulative_override_payload(
    workflow_id: str,
    input_paths: list[Path],
    current: dict | None,
    ledger_path: Path,
) -> dict:
    """Merge prior accepted overrides with the current AI submission.

    The current submission wins for duplicate source IDs.  A stale ledger is
    ignored, so records can never leak across changed retained pools or cluster
    definitions.
    """
    merged: dict[str, dict] = {}
    if ledger_path.exists():
        ledger = read_json(ledger_path)
        if not binding_issue(ledger, workflow_id, input_paths):
            merged.update(cluster_override_map(ledger))
    if current is not None and not binding_issue(current, workflow_id, input_paths):
        merged.update(cluster_override_map(current))
    return {
        "scope": "current_period_source_cluster_reviews",
        "_workflow": binding(workflow_id, input_paths),
        "contract": {
            "cumulative_file": True,
            "current_same_id_wins": True,
            "normal_cluster_fields": [
                "cluster", "reason", "anchor_terms", "passage_stance", "target_evidence",
                "passage_fragments", "passage_positions",
            ],
            "two_span_passage": {
                "optional_fields": ["passage_fragments", "passage_positions"],
                "rule": "provide one or two verbatim passage_fragments in source order; passage_positions is optional and is located by script when omitted",
                "secondary_fields": ["secondary_passage_fragments", "secondary_passage_positions"],
            },
            "display_exclusion": {
                "cluster": "__exclude__",
                "required_fields": ["exclude_reason_code", "reason", "exclusion_evidence_or_evidence"],
                "accepted_evidence_fields": ["exclusion_evidence", "evidence"],
                "evidence_rule": "continuous verbatim substring of this source full text",
                "allowed_reason_codes": [
                    "out_of_scope", "no_reportable_viewpoint", "cross_work_mismatch",
                    "abusive_non_viewpoint", "promotion_only", "duplicate_or_corrupt",
                ],
            },
        },
        "overrides": merged,
    }


def cluster_override_submission_template(cumulative: dict, queue: list[dict]) -> dict:
    """Expose only current residual IDs while the script ledger keeps history."""
    existing = cluster_override_map(cumulative)
    overrides: dict[str, dict] = {}
    for item in queue:
        source_id = clean(item.get("source_id"))
        if not source_id:
            continue
        overrides[source_id] = existing.get(source_id, {
            "cluster": clean(item.get("provisional_cluster")),
            "reason": "",
            "anchor_terms": [],
            "passage_stance": "",
            "target_evidence": "",
        })
    return {
        "scope": cumulative.get("scope"),
        "_workflow": cumulative.get("_workflow"),
        "contract": {
            **(cumulative.get("contract") or {}),
            "cumulative_file": False,
            "current_submission_only": True,
            "script_owned_cumulative_ledger": True,
        },
        "overrides": overrides,
    }


def untouched_assignment_packet(
    payload: dict, current_file: Path, queue_file: Path, prepared_template: Path | None = None,
) -> list[dict]:
    """An exact blank controller form is pending work, never a failed review.

    Call only after validating the current retained-source/cluster binding.
    Any user edit, old queue or partial submission takes the normal validator.
    """
    if not current_file.is_file() or not queue_file.is_file():
        return []
    current, queue = read_jsonl(current_file), read_jsonl(queue_file)
    if not current or current != queue[:len(current)]:
        return []
    # A repair packet may intentionally prefill a previously rejected answer.
    # Compare with the exact controller-prepared template so its other blank
    # rows remain pending instead of becoming dozens of missing-field errors.
    if prepared_template and prepared_template.is_file():
        expected_payload = read_json(prepared_template)
        return queue if payload == expected_payload else []
    expected = cluster_override_submission_template({'overrides': {}}, current)['overrides']
    if len(expected) != len(current) or payload.get('overrides') != expected:
        return []
    return queue


def source_review_template_payload(workflow_id: str, inputs: list[Path], queue: list[dict]) -> dict:
    reviews = []
    for item in queue:
        review = {
            "source_id": clean(item.get("id")),
            "decision": "",
            # Candidate 1 is already a verbatim, source-local passage.  It is a
            # safe default that ordinary models may replace when another
            # candidate better supports their semantic decision.
            "evidence_candidate_index": 1 if item.get("review_evidence_candidates") else None,
        }
        if item.get("episode_review_required") is True:
            review.update({
                "episode_scope": "",
                "episode_evidence": "",
                "prominence_basis": "",
            })
        reviews.append(review)
    return {
        "scope": "current_period_source_fulltext_reviews",
        "_workflow": binding(workflow_id, inputs),
        "contract": {
            "instruction": "只填写 workflow_status.input_file 中的当前分片；控制器会自动累计此前分片",
            "allowed_decisions": ["retain_core", "retain_consensus", "exclude"],
            "required_for_retain": ["source_id", "decision", "evidence_candidate_index_or_exact_evidence"],
            "required_for_exclude": ["source_id", "decision", "evidence_candidate_index_or_exact_evidence"],
            "retain_reason": "保留只填写决定和有效证据，不要求另写 reason；已有具体理由仍可保留。",
            "changed_exclude_reason": "若脚本原判不是 exclude，或你排除的理由不同于 auto_reason，必须自行添加具体 reason；控制器不会替你编造。",
            "exclude_reason": "读后同意输入的 auto_reason 时无需抄写，控制器补齐；不同理由必须填写 reason。不得未读即批量同意脚本。",
            "evidence_candidate_index": "candidate 1 is prefilled when available; keep it only if it supports the decision, otherwise choose another candidate_index",
            "exact_evidence_fallback": "候选均不支持决定时，回查全文填写逐字 evidence 或 evidence_position，并将 evidence_candidate_index 清空为 null（或移除）；不能保留默认编号1同时改填另一段文字。不用位置时移除旧 evidence_position。",
            "exact_copy_group": "review the representative once; select propagates the result to copy_group_source_ids",
            "full_source_lookup": "候选不足时按 source_id 到 workflow_status.full_source_file 回查；输入项不重复携带回查说明",
        },
        "reviews": reviews,
    }


def source_review_is_filled(item: dict) -> bool:
    evidence_ready = (
        isinstance(item.get("evidence_candidate_index"), int)
        and not isinstance(item.get("evidence_candidate_index"), bool)
        and item.get("evidence_candidate_index") >= 1
    ) or bool(clean(item.get("evidence"))) or isinstance(item.get("evidence_position"), list)
    return (
        clean(item.get("decision")) in {"retain_core", "retain_consensus", "exclude"}
        and (clean(item.get("decision")) != "exclude" or bool(clean(item.get("reason"))))
        and evidence_ready
    )


def final_excerpt_review_template_payload(
    workflow_id: str, inputs: list[Path], review_items: list[dict]
) -> dict:
    reviews = {}
    api = pipeline_api()
    for item in review_items:
        view_id = clean(item.get("view_id"))
        if not view_id:
            continue
        segments = item.get("excerpt_segments") or {}
        aspect_candidates = [
            int(value) for value in item.get("aspect_candidate_indexes", [])
            if str(value) in segments
        ]
        expected_stance = clean(item.get("cluster_stance"))
        stance_candidates = [
            int(index) for index, segment in segments.items()
            if api.stance_evidence_supports(expected_stance, segment)
        ]
        joint = [index for index in aspect_candidates if index in stance_candidates]
        reviews[view_id] = {"decision": "", "evidence_candidate_index": joint[0] if joint else None}
        if item.get("target_review_required"):
            target_candidates = item.get("target_evidence_segments") or {}
            target_is_roundup = "multi_work_roundup" in set(item.get("target_context_flags") or [])
            reviews[view_id].update({
                "target_evidence_candidate_index": 1 if "1" in target_candidates and not target_is_roundup else None,
            })
        if item.get("work_consistency_review_required"):
            reviews[view_id].update({"work_consistency_evidence_candidate_index": None})
        if item.get("promotion_markers"):
            reviews[view_id].update({"opinion_evidence_candidate_index": None})
        # Surface the existing optional explanation only where it is needed,
        # so the operator need not discover it through a failed submission.
        probe = {**reviews[view_id], "decision": "keep", "stance": expected_stance,
                 "aspect_evidence_candidate_index": joint[0] if joint else None,
                 "stance_evidence_candidate_index": joint[0] if joint else None}
        if not joint or any(error.startswith("reason:") for error in api.final_excerpt_review_errors(probe, item)):
            reviews[view_id]["reason"] = ""
    return {
        "scope": "current_period_final_excerpt_semantic_reviews",
        "_workflow": binding(workflow_id, inputs),
        "contract": {
            "schema_version": 7,
            "representative_selection": any(item.get("representative_selection") is True for item in review_items),
            "representative_selection_rule": "仅在本条 representative_selection=true 时允许 decision=not_selected，填写本条 excerpt_segments 的 evidence_candidate_index 和具体 reason；这是本组不选用，不是drop、来源排除或已转簇，不必为未选材料穷尽重截/转簇。脚本补下一不同来源；每组确认满10条停止，不足10条不硬凑。keep仍须通过原有全部语义门槛。",
            "instruction": (
                "只填写当前分片，逐条读完再决定。keep时须补齐本条模板预留的证据和reason；证据优先选编号，无适用编号时按exact_text_fallback填对应逐字原文。模板没有reason字段才不必另写。"
                "evidence_candidate_index可同时作为方面和立场证据，其他预留证据字段各有用途，不能省略。drop时添加drop_fields规定的理由、失败项和实际尝试情况，不沿用keep空表。"
                "若词表存疑，允许保留并用reason一句话解释原文的真实语义；不能为迎合词表改立场或删来源。跨作品keep的reason说明评价实际属于哪部作品，证据用完整句段，不只摘作品名。"
                "候选编号只是建议，必须读完摘录再判断；不得调用校验函数或脚本批量生成语义决定。cluster_claim_review_required时，确认摘录支持cluster_title的核心评价方向，不必覆盖标题所有并列细节；泛群像、泛竞争、泛特效、名单或物料不能代替具体主张。"
                "目标复核须确认锚点和当前观点属于同一对象；仅在标签、名单或综合盘点中出现不得通过。跨作品复核须确认方面证据评价目标作品。含购买、抽奖、关注、参与指令或观点前无关八卦的展示段先重截。"
            ),
            "full_source_lookup": "仅对需要重截、转簇或核对跨作品的项目按 source_id 回查 retained_sources.jsonl",
            "required_for_normal_keep": ["decision=keep", "evidence_candidate_index", "every additional evidence index and reason reserved in this item's template"],
            "required_for_cross_work_keep": ["decision=keep", "evidence_candidate_index", "work_consistency_evidence_candidate_index", "reason"],
            "cross_work_evidence": "从本条 excerpt_segments 选择能说明评价归属的完整句段，填写 work_consistency_evidence_candidate_index；不是 target_evidence_segments 的编号，也不是把编号当作文字证据。确无适用编号时可填写 work_consistency_evidence 的逐字完整句段。不得只摘作品名。",
            "cross_section_context_review": "出现此提示时，结合前文标题线索、原始分节符核对所有展示句的作品归属。后半句出现目标剧不能证明前半句也属于它；前半段无关则重截。标题线索本身不是错配结论，真实对比可以保留并说明依据。",
            "short_excerpt_specificity": "具体依据必须解释当前方面的评价。演员或角色数量、播出日期等背景事实，不能单独证明剧情老套、演技好坏或视效水平；词表未识别但原文确有依据时，说明实际依据而不是拿其他方面的细节凑数。",
            "keep_means": "AI已读完摘录并确认目标、核心方面、局部立场和完整性。控制器补齐指纹、簇立场和确认字段；不得未读批量keep。两个证据不在同一段时另填 aspect_evidence_candidate_index 与 stance_evidence_candidate_index。",
            "reason": "本条模板预留了reason，或submission_errors要求说明时，keep也必须写一句具体依据；没有预留且没有此类错误的keep不额外写。drop始终需要reason，不能用reassignment_reason替代。",
            "exact_text_fallback": "候选均不适用时，填写对应aspect_evidence、stance_evidence、opinion_evidence、work_consistency_evidence或target_evidence的逐字原文，并清空该证据的编号；这是补齐证据的合法替代，不是为了填满模板乱选编号。",
            "script_owned": ["target when target_review_required=false", "work consistency when work_consistency_review_required=false", "length", "markup", "verbatim positions"],
            "drop_fields": ["reason", "failed_checks", "reexcerpt_attempted", "reassignment_attempted when aspect, stance or cluster_claim fails", "reassignment_reason when aspect, stance or cluster_claim fails", "conflict_evidence when work consistency fails"],
            "drop_reassignment_checks": ["aspect", "stance", "cluster_claim"],
            "drop_reassignment_requirement": "上述任一检查失败时，须实际查当前簇定义；有合适簇就提交reassignment_requests，不得drop。确实无法转簇才填写reassignment_attempted=true和独立reassignment_reason，说明具体原因；不能只在reason中写不能转簇，也不能未检查就确认true。",
            "allowed_drop_failed_checks": sorted(api.ALLOWED_DROP_FAILURE_CHECKS),
        },
        "reviews": reviews,
    }


def selected_excerpt_evidence(review: dict, input_item: dict, field: str) -> str:
    direct = clean(review.get(field))
    if direct:
        return direct
    index = review.get(f"{field}_candidate_index")
    if isinstance(index, bool):
        return ""
    if isinstance(index, int) or (isinstance(index, str) and index.isdigit()):
        return clean((input_item.get("excerpt_segments") or {}).get(str(index)))
    return ""


def pipeline_api():
    """Reuse the pipeline's deterministic semantic checks during each review chunk."""
    global _PIPELINE_API
    if _PIPELINE_API is None:
        spec = importlib.util.spec_from_file_location("film_pipeline_contract_api", PIPELINE)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载最终复核校验器：{PIPELINE}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PIPELINE_API = module
    return _PIPELINE_API


def final_excerpt_review_is_filled(review: dict, input_item: dict | None = None) -> bool:
    return not pipeline_api().final_excerpt_review_errors(review, input_item or {})


def cluster_set_review_template_payload(
    workflow_id: str, inputs: list[Path], cluster_input: dict, count_input: dict
) -> dict:
    reviews = {}
    for item in cluster_input.get("clusters", []):
        review_key = exact_key(item.get("review_key"))
        if not review_key:
            continue
        review = {
            "fingerprint": clean(item.get("fingerprint")),
            "decision": "",
            "report_role": "",
            "scope_type": "",
            "issues": [],
            "reason": "",
        }
        if clean(item.get("stance")) == "objective":
            review.update({"objective_subtype": ""})
        reviews[review_key] = review
    count_reviews = {}
    for item in count_input.get("batches", []):
        review_key = exact_key(item.get("review_key"))
        if not review_key:
            continue
        count_reviews[review_key] = {
            "fingerprint": clean(item.get("fingerprint")),
            "decision": "",
            "issues": [],
            "reason": "",
        }
        if clean(item.get("expected_range_status")) != "within_range":
            count_reviews[review_key]["exception_reason"] = ""
    return {
        "scope": "current_period_cluster_set_reviews",
        "_workflow": binding(workflow_id, inputs),
        "contract": {
            "cluster_review": "按代表样本检查簇标题、立场、范围、来源角色和颗粒度；通过时 issues 保持空数组，不重复抄写证据",
            "required_decision_for_advance": "pass",
            "unresolved_decision": "needs_revision; keep issues and reason, fix actual definitions before passing; do not claim pass to escape the queue",
            "report_roles": sorted(pipeline_api().ALLOWED_CLUSTER_REPORT_ROLES),
            "scope_types_by_content_mode": {"serial_drama": sorted(pipeline_api().DRAMA_CLUSTER_SCOPES), "episodic_variety": sorted(pipeline_api().VARIETY_CLUSTER_SCOPES)},
            "objective_subtypes": ["neutral_fact", "sentiment_distribution", "balanced_observation"],
            "record_title": "客观簇标题含记录时 report_role 使用 data_note；其他客观观察按实际信息角色填写",
            "count_review": "脚本已经计算簇数和范围；AI只判断是否过碎、过宽或被机械调数，通过时 issues 保持空数组",
            "per_member_review": "逐样本语义对齐统一在 final_excerpt_review 完成，此处不复制全部成员证据",
        },
        "reviews": reviews,
        "count_reviews": count_reviews,
    }


def cluster_set_review_is_filled(item: dict) -> bool:
    return (
        clean(item.get("decision")) == "pass"
        and bool(clean(item.get("reason")))
        and item.get("issues") == []
    )


def carry_current_cluster_review_records(template: dict, existing: dict | None) -> dict:
    """Reuse explicit judgments only for identical current per-record fingerprints."""
    existing = existing or {}
    current_binding = template.get("_workflow") or {}
    previous_binding = existing.get("_workflow") or {}
    if (not isinstance(current_binding, dict) or not isinstance(previous_binding, dict)
            or not clean(current_binding.get("workflow_id"))
            or clean(current_binding.get("workflow_id")) != clean(previous_binding.get("workflow_id"))):
        return template
    for section in ("reviews", "count_reviews"):
        previous = existing.get(section, {})
        if not isinstance(previous, dict):
            continue
        for key, current in template.get(section, {}).items():
            old = previous.get(key)
            fingerprint = clean(current.get("fingerprint"))
            if isinstance(old, dict) and fingerprint and clean(old.get("fingerprint")) == fingerprint:
                current.update(old)
    return template


def cluster_count_review_is_filled(item: dict) -> bool:
    return clean(item.get("decision")) == "pass" and bool(clean(item.get("reason"))) and item.get("issues") == []


def post_excerpt_count_review_template_payload(workflow_id: str, input_path: Path) -> dict:
    payload = read_json(input_path)
    reviews = {}
    for item in payload.get("batches", []):
        review_key = clean(item.get("review_key"))
        if not review_key:
            continue
        review = {
            "fingerprint": clean(item.get("fingerprint")),
            "decision": "",
            "issues": [],
            "reason": "",
        }
        if clean(item.get("expected_range_status")) != "within_range":
            review["exception_reason"] = ""
        reviews[review_key] = review
    return {
        "scope": "current_period_post_excerpt_cluster_count_reviews",
        "_workflow": binding(workflow_id, [input_path]),
        "contract": {
            "instruction": "脚本已经计算终审后的簇数和范围；只判断是否过碎、过宽或被机械调数，通过时 issues 保持空数组",
            "required": ["fingerprint", "decision=pass", "issues=[]", "reason"],
            "out_of_range_extra": ["exception_reason"],
        },
        "reviews": reviews,
    }


def stage_fresh(manifest: dict, stage: str, token: str, required: Iterable[Path] = ()) -> bool:
    saved = manifest.get("stages", {}).get(stage, {})
    required_paths = [path.resolve() for path in required]
    if saved.get("input_digest") != token or not all(path.exists() for path in required_paths):
        return False
    output_hashes = saved.get("output_hashes")
    if not isinstance(output_hashes, dict):
        return False
    return all(output_hashes.get(str(path)) == sha256(path) for path in required_paths)


def record_stage(
    workspace: Path,
    manifest: dict,
    stage: str,
    token: str,
    result: dict,
    output_paths: Iterable[Path],
) -> None:
    outputs = [path.resolve() for path in output_paths if path.exists()]
    stage_record = {
        "input_digest": token,
        "returncode": result["returncode"],
        "finished_at": result["finished_at"],
        "command": result["command"],
        "elapsed_seconds": float(result.get("elapsed_seconds", 0.0)),
        "output_hashes": {str(path): sha256(path) for path in outputs},
    }
    if result.get("output_sha256"):
        stage_record["output_sha256"] = result["output_sha256"]
    if result.get('initial_script_snapshot'):
        stage_record['initial_script_snapshot'] = result['initial_script_snapshot']
        stage_record['audit_snapshot_elapsed_seconds'] = result['audit_snapshot_elapsed_seconds']
    manifest.setdefault("stages", {})[stage] = stage_record
    manifest.setdefault("history", []).append({
        "stage": stage,
        "input_digest": token,
        "returncode": result["returncode"],
        "finished_at": result["finished_at"],
        "elapsed_seconds": float(result.get("elapsed_seconds", 0.0)),
    })
    manifest["history"] = manifest["history"][-80:]
    save_manifest(workspace, manifest)


def config_template(records_path: Path) -> dict:
    batches = []
    for row in read_jsonl(records_path):
        batch = clean(row.get("batch"))
        if batch and batch not in batches:
            batches.append(batch)
    return {
        "batch_order": batches,
        "representative_sample_limit": 10,
        "targets": {
            batch: {
                "content_mode": "serial_drama",
                "strong_terms": [],
                "weak_terms": [],
                "auxiliary_terms": [],
                "comparison_terms": [],
            }
            for batch in batches
        },
        "quality_floor": {"long": 12.5, "social": 9.5},
        "media_subject_extensions": [],
        "period_windows": {},
    }


def config_seed(records_path: Path) -> dict:
    """Raw-data orientation only; never a source decision or period answer."""
    groups = {}
    for record in read_jsonl(records_path):
        row = pipeline_api().map_record(record)
        group = groups.setdefault((row['batch'], row['source_file']), {'batch': row['batch'], 'source_file': row['source_file'], 'record_count': 0, 'examples': []})
        group['record_count'] += 1
        if len(group['examples']) < 3:
            group['examples'].append({key: row.get(key, '') for key in ('id', 'channel', 'published', 'title')})
            group['examples'][-1]['body_preview'] = row.get('body', '')[:400]
    return {'purpose': '仅供识别当前作品、数据类型和期次，不是审核样本或准入答案。预览为截断原文，不可作为正式摘录。',
            'raw_records_file': str(records_path), 'files': list(groups.values())}


def title_repair_dependencies(p: dict) -> list[Path]:
    return [p['config'], p['run'] / 'cluster_discovery_seed_input.jsonl',
            SKILL_ROOT / 'SKILL.md', *sorted((SKILL_ROOT / 'scripts').glob('*.py')),
            *sorted((SKILL_ROOT / 'references').glob('*.md'))]


def title_repair_immutable(path: Path, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Title repair context cannot be a symbolic link')
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError('Existing title repair context changed')
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(encoded)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError('Concurrent title repair context changed')
    finally:
        temporary.unlink(missing_ok=True)


def prepare_title_format_repair(manifest: dict, p: dict, payload: dict,
                               errors: list[str]) -> dict | None:
    """Separate title-only edits by definition; never invent a replacement title."""
    if not errors or not all(
        ' 的标题不是可直接理解的报告体观点句：' in error
        or error.endswith(' 存在空标题或兜底簇') for error in errors
    ):
        return None
    api = pipeline_api()
    entries = []
    for batch, definitions in sorted(payload['batches'].items()):
        for index, definition in enumerate(definitions):
            title = api.clean(definition.get('title'))
            if (not title or api.GENERIC_CLUSTER_TITLE.search(title)
                    or len(api.normalized(title)) < 16
                    or not api.CLUSTER_TITLE_PREDICATE.search(title)):
                entries.append({'batch': batch, 'index': index, 'id': definition.get('id'),
                                'definition': definition})
    if not entries:
        return None
    plan = {'version': 1, 'workflow_id': manifest['workflow_id'],
            'parent_sha256': sha256(p['clusters']),
            'dependencies_digest': digest_paths(title_repair_dependencies(p)),
            'entries': entries, 'validation_errors': errors,
            'scope': 'title_fields_only_no_semantic_approval'}
    key = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    folder = p['run'] / 'definition_title_repairs' / key
    if not folder.resolve().is_relative_to(p['workspace']):
        raise ValueError('Title repair directory escaped workspace')
    plan_file = folder / 'plan.json'
    title_repair_immutable(plan_file, plan)
    jobs = []
    for number, entry in enumerate(entries, 1):
        input_file = folder / f'title-{number}.input.json'
        output_file = folder / f'title-{number}.answer.json'
        title_repair_immutable(input_file, {
            'definition': entry['definition'], 'batch': entry['batch'],
            'instruction': '只根据已读本期材料修正此组标题，不改立场、边界、关键词或其他组；填写title和具体reason。不同组写不同answer文件，最后由脚本合并。',
            'title_contract': '明确判断谓语和具体依据；至少16个有效字符。不要猜书名号问题，不以无关细节凑字数。',
            'validation_errors': errors,
        })
        if not output_file.resolve().is_relative_to(folder.resolve()) or output_file.is_symlink():
            raise ValueError('Title repair answer escaped its plan')
        if not output_file.exists():
            title_repair_immutable(output_file, {'title': '', 'reason': ''})
        jobs.append({'batch': entry['batch'], 'id': entry['id'],
                     'readable_input_file': str(input_file), 'output_file': str(output_file)})
    return {'plan_file': str(plan_file), 'plan_sha256': sha256(plan_file), 'jobs': jobs,
            'protected_parent_file': str(p['clusters']),
            'apply_command': [sys.executable, str(CONTROLLER), 'definition-title-repair-apply',
                              '--workspace', str(p['workspace']), '--plan-file', str(plan_file)]}


def apply_title_format_repair(workspace: Path, plan_file: Path) -> dict:
    """Caller holds the workflow lock. All valid explicit edits merge once."""
    manifest = load_manifest(workspace)
    p = paths_for(manifest)
    state = read_json(workspace / STATUS_NAME)
    repair = state.get('title_format_repair') or {}
    expected = Path(str(repair.get('plan_file', ''))).resolve()
    owned = (p['run'] / 'definition_title_repairs').resolve()
    if plan_file.is_symlink() or not owned.is_relative_to(workspace.resolve()):
        raise ValueError('Title repair plan escaped its workspace')
    plan_file = plan_file.resolve()
    if (state.get('stage') != 'cluster_definition_repair'
            or state.get('definition_repair_kind') != 'format_preflight'
            or not repair or plan_file != expected or not plan_file.is_relative_to(owned)
            or plan_file.name != 'plan.json' or sha256(plan_file) != repair.get('plan_sha256')):
        raise ValueError('Title repair plan is not the current guarded owned plan')
    plan = read_json(plan_file)
    if (plan.get('version') != 1 or plan.get('workflow_id') != manifest['workflow_id']
            or plan.get('parent_sha256') != sha256(p['clusters'])
            or plan.get('dependencies_digest') != digest_paths(title_repair_dependencies(p))):
        raise ValueError('Title repair parent, workflow, inputs or rules changed; read current status')
    payload = read_json(p['clusters'])
    candidate = json.loads(json.dumps(payload, ensure_ascii=False))
    errors = []
    for number, entry in enumerate(plan['entries'], 1):
        output_file = plan_file.parent / f'title-{number}.answer.json'
        if output_file.is_symlink() or not output_file.resolve().is_relative_to(plan_file.parent):
            raise ValueError('Title repair answer escaped its owned plan')
        current = candidate['batches'][entry['batch']][entry['index']]
        if current != entry['definition'] or current.get('id') != entry['id']:
            raise ValueError('Title repair definition identity changed')
        try:
            answer = read_json(output_file)
        except (ValueError, OSError) as exc:
            errors.append({'id': entry['id'], 'output_file': str(output_file), 'error': str(exc)})
            continue
        if (set(answer) != {'title', 'reason'} or not isinstance(answer.get('title'), str)
                or not isinstance(answer.get('reason'), str) or not clean(answer['title'])
                or not clean(answer['reason'])):
            errors.append({'id': entry['id'], 'output_file': str(output_file),
                           'error': '只填非空title和具体reason；初始空白不是交卷，不允许附加或修改其他字段'})
            continue
        current['title'] = answer['title']
    if errors:
        return {'status': 'REPAIR_REQUIRED', 'title_patch_errors': errors,
                'parent_not_modified': True, 'all_or_nothing': True}
    batches = {clean(row.get('batch')) for row in
               read_jsonl(p['run'] / 'cluster_discovery_seed_input.jsonl') if clean(row.get('batch'))}
    try:
        pipeline_api().validate_clusters(candidate, batches, read_json(p['config']))
    except ValueError as exc:
        return {'status': 'REPAIR_REQUIRED', 'validation_errors': str(exc).splitlines(),
                'parent_not_modified': True, 'all_or_nothing': True}
    # Do not modify any stance, boundary, keyword, binding, or unrelated title.
    write_json(p['clusters'], candidate)
    return {'status': 'APPLIED', 'titles_changed': len(plan['entries']),
            'parent_sha256': sha256(p['clusters']), 'semantic_approval': False,
            'next_command': [sys.executable, str(CONTROLLER), 'advance', '--workspace', str(workspace)]}


def status_payload(workspace: Path, manifest: dict) -> dict:
    p = paths_for(manifest)
    workflow_id = manifest["workflow_id"]
    base = {
        "workflow": "film-brief-cleaning",
        "workflow_id": workflow_id,
        "workspace": str(p["workspace"]),
        "output": str(p["output"]),
        "display_order": {
            "random": False,
            "keys": [
                "media_authority: central_mainstream > major_mainstream > unclassified",
                "channel: 境内新闻 > 公众文章 > 今日头条 > 微博 > 小红书 > 抖音",
                "decision: retain_core > retain_consensus",
                "quality: high > low",
                "source_text_length: long > short",
                "stable_source_id: descending tie-breaker",
            ],
        },
        "execution_rule": "正常推进只读取 operator_contract.read_now，直接编辑唯一 write_only 文件并执行 next_command；除非状态为 BROKEN，不扫描源码、不遍历产物、不创建临时辅助程序",
    }

    def result(status: str, stage: str, message: str, **extra) -> dict:
        payload = {**base, "status": status, "stage": stage, "message": message, **extra}
        payload['reference'] = str(SKILL_ROOT / 'references' / 'contracts.md')
        if status == "REVIEW_REQUIRED" and stage in STAGE_GUIDANCE:
            payload.setdefault("review_requirements", STAGE_GUIDANCE[stage])
        payload['next_command'] = [sys.executable, str(Path(__file__).resolve()), 'advance', '--workspace', str(workspace)]
        payload['source_text_file'] = str(p['run'] / 'normalized_sources.jsonl')
        if status == "REVIEW_REQUIRED" and payload.get("edit_file_ready"):
            # Keep the audit template on disk, but expose one editable file in
            # the normal operator packet so weak models cannot invent a copy
            # or submission workflow.
            payload.pop("template", None)
            immediate_reads = []
            if stage == "cluster_discovery" and payload.get("readable_input_files"):
                immediate_reads.extend(payload["readable_input_files"])
            else:
                if payload.get("input_file"):
                    immediate_reads.append(payload["input_file"])
                for value in payload.get("input_files", []) or []:
                    if value not in immediate_reads:
                        immediate_reads.append(value)
            immediate_reads.append(payload.get("required_file"))
            payload["operator_contract"] = {
                "mode": "controller_prepared_edit_in_place",
                "read_now": list(dict.fromkeys(value for value in immediate_reads if value)),
                "write_only": payload.get("required_file"),
                "steps": [
                    "读取 read_now 列出的当前输入和已预填提交",
                    "直接编辑控制器已生成的 required_file，只填写预留字段",
                    "保存后原样执行 next_command",
                ],
                "forbidden_in_normal_flow": [
                    "读取 film_pipeline.py 或 film_workflow.py 源码",
                    "创建 Python、PowerShell 或 JavaScript 临时驱动脚本",
                    "遍历 run 目录寻找替代输入",
                    "手工生成、复制或修改 HTML",
                ],
                "full_source_lookup": "仅当需要回查原文时运行 source_lookup_command，将 <source_id> 替换为当前来源ID，可在后面追加其他ID；用文件读取工具完整读取返回的 readable_source_file，直接使用其中 source_text；过长字符串按 _readable_text.segments 的 text 无分隔拼接，不自行拼接标题和正文或写转换脚本",
                "source_lookup_command": [sys.executable, str(Path(__file__).resolve()), "lookup", "--workspace", str(workspace), "--as-file", "--source-id", "<source_id>"],
            }
            if stage == 'cluster_set_review':
                payload['operator_contract']['member_repair'] = {
                    'prepare_command': [sys.executable, str(CONTROLLER), 'assignment-patch-prepare',
                                        '--workspace', str(workspace), '--source-id', '<source_id>'],
                    'instruction': '发现具体成员错放时，将<source_id>替换为当前成员ID（可列多个），先prepare再完整读取返回read_now，只写返回write_only，apply后advance；不为解锁归属修改而微调定义，不改整张归类表、不把needs_revision改为pass逃队列。'}
                payload['operator_contract']['definition_repair'] = {
                    'write_only': str(p['clusters']),
                    'read_before': [str(p['clusters'])],
                    'allowed_when': '仅簇自身标题主张、边界或方面词确实不成立且已有needs_revision证据时，实际修正定义后advance；不是逐成员修复的解锁方法。'}
                payload['review_requirements'] = [*payload['review_requirements'],
                    '具体错放成员走operator_contract.member_repair；簇自身主张不成立才走definition_repair，改说明不是实际改归属']
            if stage == 'final_assignment_repair':
                payload['execution_rule'] = '主簇转移先执行scoped_patch.prepare_command，按返回read_now/write_only/apply_command处理小补丁；仅已有副簇受影响或否决请求时走原有明确路径，不先读整张归类表、不创建辅助程序'
                payload['operator_contract']['mode'] = 'controller_prepared_scoped_repair'
                payload['operator_contract']['legacy_assignment_read_now'] = payload['operator_contract']['read_now']
                payload['operator_contract']['read_now'] = [str(p['clusters'])]
                payload['operator_contract'].pop('write_only', None)
                payload['operator_contract']['allowed_repair_writes'] = [
                    payload['required_file'], payload['optional_rejection_review_file'],
                ]
                payload['operator_contract']['repair_scope'] = '只处理pending_view_ids/pending_source_ids；实际转簇或有依据地明确否决请求，不能用说明代替文件变更'
                payload['operator_contract']['read_if_rejecting_request'] = payload['optional_rejection_review_file']
                payload['operator_contract']['request_context_schema'] = {
                    'root_type': 'array',
                    'item_fields': ['view_id', 'source_id', 'batch', 'current_cluster_id',
                                    'proposed_cluster_id', 'reason', 'evidence_candidate_index',
                                    'evidence', 'review_fingerprint', 'input_item'],
                    'reading_rule': 'input_files中的转簇待办根节点就是数组，不存在requests或reviews包装；直接逐项读source_id、建议目的簇及逐字证据。不得写转换/字段探测/行数统计命令，不能从其他阶段材料猜本文件结构。',
                }
                payload['operator_contract']['assignment_evidence_rules'] = [
                    'passage_fragments是评价观点的原文，target_evidence是对象归属的原文；两类证据分别填写，不互相代替。',
                    'target_evidence须逐字包含本批次period_config.targets中的strong_terms或weak_terms；演员名或不含作品锚点的观点句不能替代。可从input_item.target_evidence_segments选合格原句，仍须核对当前段的归属。',
                    'evidence及input_item.excerpt_segments是展示段候选，不保证清洁后的文字仍是全文逐字子串；无法确认时按source_lookup_command回查原文，复制完整原句，不改空格、标点或HTML转义。',
                    '簇、局部立场和原因由阅读判断填写；不做JSON/行数/子串/位置自检。next_command一次给出全部机械错误，按具体字段修复。',
                ]
                payload['operator_contract']['scoped_patch'] = {
                    'preferred_for_primary_only_reassignments': True,
                    'prepare_command': [sys.executable, str(CONTROLLER), 'assignment-patch-prepare', '--workspace', str(workspace)],
                    'instruction': '主簇转移优先由脚本准备小补丁，读取其read_now，只写semantic-patch，再执行其apply_command和next_command。编号自动还原逐字证据；不批量Edit整张归类表。受影响来源已有副簇或否决请求时，按本状态原有明确路径处理，逐次Edit同一文件，保留无关来源。补丁合并不是终审通过。',
                }
                payload['operator_contract']['steps'] = [
                    '先执行scoped_patch.prepare_command，用Read完整读取其read_now；小输入已含当前待办、候选证据和受影响来源的已有归类，不先读整张required_file',
                    '同意主簇转移：只填写返回write_only的semantic-patch，按当前原文明确簇/立场/原因/证据，再执行apply_command及其next_command；脚本一次核验合并，合并不等于终审通过',
                    '受影响来源已有副簇：才按legacy_assignment_read_now及allowed_repair_writes走原有明确路径；只顺序修改该来源，保留无关来源及未受影响副簇',
                    '否决：先读取read_if_rejecting_request，只为对应pending_view_ids填写完整有效终审判断、reassignment_request_resolution=reject_request和具体reassignment_request_reason',
                    '保存后原样执行next_command；文字声称已转不算处理，变化后的摘录仍需重审',
                ]
            if stage == 'period_config':
                payload.pop('source_text_file', None)
                payload['operator_contract'].pop('source_lookup_command', None)
                payload['operator_contract']['full_source_lookup'] = '标准化原文尚未生成；配置所需的原始数据概览已包含在 read_now。'
            native_packet = stage in {'source_review', 'final_excerpt_review'} or (
                stage == 'cluster_assignment_review'
                and Path(str(payload.get('full_queue_file') or payload.get('input_file') or '')).suffix == '.jsonl'
            )
            if native_packet:
                payload['operator_contract']['native_review'] = {
                    'condition': '当前宿主提供可继承主模型的原生 Agent 时使用；否则按普通逐批流程继续，不启动外部模型',
                    'reference': str(SKILL_ROOT / 'references' / 'native-review.md'),
                    'dispatch_command': [sys.executable, str(CONTROLLER), 'review-dispatch', '--workspace', str(workspace), '--as-file'],
                    'steps': [
                        '读 native-review.md 并运行 dispatch_command，用Read完整读取handoff_file，原样使用其中每份workbuddy_agent_input；不经PowerShell转存或自写转换',
                        '在当前一个主会话中启动所有分配任务后再等待；全部继承主模型，禁止额外 CLI 和孙任务',
                        '子任务只写各自指定文件；主会话运行返回的 collect_command，只修报错项后再 advance',
                    ],
                    'write_boundary': '原生分发是 write_only 的限定例外；主会话不重抄全部子任务答案，最终仍由控制器核验和发布',
                }
                payload['execution_rule'] = '当前阶段优先检查 operator_contract.native_review 的宿主能力条件；可用时按其分发/收卷，不能同时手填主表。不可用时按 read_now/write_only 逐批继续。两种路径都不扫描源码、不遍历产物、不创建临时辅助程序。'
        if payload.get('title_format_repair'):
            repair = payload['title_format_repair']
            jobs = repair['jobs']
            payload.pop('required_file', None)
            payload['protected_parent_file'] = repair['protected_parent_file']
            payload['required_files'] = [job['output_file'] for job in jobs]
            payload['input_files'] = [job['readable_input_file'] for job in jobs]
            payload['input_file'] = payload['input_files'][0]
            payload['next_command'] = repair['apply_command']
            payload['execution_rule'] = '每个分组标题写自己的小answer文件，不直接Edit共同定义文件；全部交齐后脚本一次带锁校验合并。'
            payload['operator_contract'] = {
                'mode': 'independent_title_patches_script_atomic_merge',
                'read_now': [*payload['input_files'], *payload['required_files']],
                'write_only_files': payload['required_files'],
                'forbidden_writes': [repair['protected_parent_file']],
                'forbidden_in_normal_flow': ['读取生产脚本源码', '创建 Python、PowerShell 或 JavaScript 临时驱动脚本',
                                             '手工生成、复制或修改 HTML'],
                'steps': ['按各输入只修对应标题，每组只写自己的answer文件，保留不同文件的独立写边界',
                          '填非空title和具体reason；不改立场、关键词、边界或其他组，不写临时驱动脚本',
                          '所有answer完成后执行next_command由脚本一次合并；仍需普通后续归簇和最终语义验收'],
            }
        write_json(p["workspace"] / STATUS_NAME, payload)
        return payload

    if p["output"].exists():
        published_hash = manifest.get("stages", {}).get("verify", {}).get("output_hashes", {}).get(str(p["output"].resolve()))
        if not published_hash or published_hash != sha256(p["output"]):
            return result(
                "BLOCKED", "unverified_output",
                "正式输出路径出现了非本流程 verify PASS 后发布的 HTML；该文件不得交付，请换新输出路径继续",
                existing_output=str(p["output"]), verified=False,
            )

    if not p["records"].exists():
        return result("BROKEN", "extract", "缺少 extract/records.jsonl，需重新 init")
    if sha256(p["records"]) != manifest.get("records_sha256"):
        return result("BLOCKED", "input_integrity", "抽取后的 records.jsonl 已变化；禁止继续复用旧评审，请新建工作区重跑")
    if not p["config"].exists():
        template = p["templates"] / "period_config.template.json"
        config_payload = config_template(p["records"])
        seed_file = p['templates'] / 'period_config.input.json'
        write_json(seed_file, config_seed(p['records']))
        ensure_template(template, config_payload)
        write_json(p["config"], config_payload)
        return result(
            "REVIEW_REQUIRED", "period_config", "请根据当前数据填写期次配置；不要把演员或角色名写入 strong_terms",
            required_file=str(p["config"]), input_file=str(seed_file), template=str(template), reference=str(SKILL_ROOT / "references" / "contracts.md"), edit_file_ready=True,
        )

    if manifest.get("selection_policy") == "representative_max10":
        current_config = read_json(p["config"])
        try:
            current_limit = pipeline_api().representative_sample_limit(current_config)
        except ValueError:
            current_limit = None
        if current_limit is None:
            return result("REVIEW_REQUIRED", "period_config", "本次是每簇最多10条代表的新规则；保留并填写 representative_sample_limit:10，不得删掉字段退回全量审查", required_file=str(p["config"]), input_file=str(p["config"]), reference=str(SKILL_ROOT / "references" / "contracts.md"), edit_file_ready=True)
    prepare_token = digest_paths([p["records"], p["config"], PIPELINE, MEDIA_REGISTRY, CONTROLLER])
    if not stage_fresh(manifest, "prepare", prepare_token, [p["run"] / "prepare_summary.json", p["run"] / "source_review_queue.jsonl"]):
        return result("READY_TO_ADVANCE", "prepare", "配置已就绪，可以执行标准化与初筛", action="prepare", input_digest=prepare_token)

    source_queue = read_jsonl(p["run"] / "source_review_queue.jsonl")
    source_inputs = [p["run"] / "source_review_queue.jsonl", p['run'] / 'normalized_sources.jsonl', p['config']]
    if source_queue or p['source'].exists():
        source_payload = review_payload(p["source"])
        issue = binding_issue(source_payload, workflow_id, source_inputs)
        source_payload = expand_compact_submission(
            source_payload, workflow_id, source_inputs,
            {clean(item.get("id")): item for item in source_queue},
        )
        source_ledger = cumulative_review_payload(
            workflow_id, source_inputs, source_payload, p["source_ledger"],
            "source_id", "current_period_source_fulltext_reviews",
        )
        write_json(p["source_ledger"], source_ledger)
        reviewed = list_review_map(source_ledger, "source_id")
        required_ids = {clean(item.get("id")) for item in source_queue}
        completed_ids = {
            source_id for source_id, item in reviewed.items()
            if source_review_is_filled(item)
        }
        missing = sorted(required_ids - completed_ids)
        if issue or missing:
            source_chunks = sorted((p["run"] / "source_review_chunks").glob("chunk-*.jsonl"))
            next_chunk, chunk_index, chunk_total = next_incomplete_chunk(source_chunks, "id", set(missing))
            current_ids = {
                clean(item.get("id")) for item in read_jsonl(next_chunk)
            } if next_chunk else set(missing)
            current_queue = [
                item for item in source_queue
                if clean(item.get("id")) in current_ids and clean(item.get("id")) in set(missing)
            ]
            current_input = p["run"] / "source_review.current.jsonl"
            write_jsonl(current_input, current_queue)
            template = p["templates"] / "source_reviews.template.json"
            submission = source_review_template_payload(workflow_id, source_inputs, current_queue)
            for review in submission["reviews"]:
                prior = reviewed.get(clean(review.get("source_id")))
                if isinstance(prior, dict):
                    review.update(prior)
            write_json(template, submission)
            write_json(p["source"], submission)
            return result(
                "REVIEW_REQUIRED", "source_review", "当前分片已写入 required_file；直接补全预留字段并再次 advance",
                required_file=str(p["source"]), template=str(template),
                input_file=str(current_input), source_chunk_file=str(next_chunk or source_inputs[0]),
                chunk_index=chunk_index, chunk_total=chunk_total,
                full_input_file=str(source_inputs[0]),
                full_source_file=str(p["run"] / "normalized_sources.jsonl"),
                binding_issue=issue, required=len(required_ids), completed=len(required_ids & completed_ids), missing=len(missing), edit_file_ready=True,
            )
        write_json(p["source"], source_ledger)

    validation_inputs = source_inputs + [p["source"], PIPELINE, CONTROLLER]
    validation_token = digest_paths(validation_inputs)
    if not stage_fresh(
        manifest,
        "source_review_validation",
        validation_token,
        [p["source_validation"]],
    ):
        return result(
            "READY_TO_ADVANCE", "source_review_validation",
            "来源复核覆盖齐备，可以一次性校验全部决定、证据位置和综艺期次字段",
            action="source_review_validation", input_digest=validation_token,
        )
    validation = read_json(p["source_validation"])
    if clean(validation.get("status")) != "PASS":
        affected_ids = {clean(value) for value in validation.get("affected_source_ids", []) if clean(value)}
        repair_queue = [item for item in source_queue if clean(item.get("id")) in affected_ids]
        repair_queue = [{**item, 'submission_errors': [error for error in validation.get('issues', [])
                        if clean(error.get('source_id')) == clean(item.get('id'))],
                        'previous_submission': reviewed.get(clean(item.get('id')), {})} for item in repair_queue]
        repair_input = p['run'] / 'source_review.repair.jsonl'
        write_jsonl(repair_input, repair_queue)
        repair_submission = source_review_template_payload(workflow_id, source_inputs, repair_queue)
        for review in repair_submission["reviews"]:
            prior = reviewed.get(clean(review.get("source_id")))
            if isinstance(prior, dict):
                review.clear()
                review.update(prior)
        write_json(p["source"], repair_submission)
        return result(
            "REVIEW_REQUIRED", "source_review", "来源复核契约未通过；只修复校验报告列出的受影响来源",
            required_file=str(p["source"]), validation_file=str(p["source_validation"]),
            input_file=str(repair_input), full_input_file=str(repair_input),
            full_source_file=str(p['run'] / 'normalized_sources.jsonl'),
            issue_count=int(validation.get("issue_count", 0)),
            affected_source_ids=validation.get("affected_source_ids", []), edit_file_ready=True,
        )

    if not stage_fresh(manifest, 'source_review_validation', validation_token, [p['link_candidates']]):
        return result('READY_TO_ADVANCE', 'source_review_validation',
                      '来源校验已通过，但链接候选缺失或已变化；重新生成候选后继续',
                      action='source_review_validation', input_digest=validation_token)

    links_token = digest_paths([p["link_candidates"], LINK_CHECKER, CONTROLLER])
    link_health = p["run"] / "source_link_health.json"
    if not stage_fresh(manifest, "link_check", links_token, [link_health]):
        return result(
            "READY_TO_ADVANCE", "link_check",
            "来源语义复核齐备，可以只对仍可能保留的候选来源执行链接健康检查",
            action="link_check", input_digest=links_token,
            progress_file=str(link_health.with_suffix(link_health.suffix + ".progress.json")),
        )

    selection_inputs = [p["run"] / "normalized_sources.jsonl", p["run"] / "source_decisions.auto.jsonl", link_health, PIPELINE, CONTROLLER]
    if p['source'].exists():
        selection_inputs.append(p["source"])
    # Medium-similarity pairs remain independent by default.  The controller
    # intentionally does not block on an AI dedup pass; exact/high-confidence
    # copies are already handled deterministically by select.
    selection_token = digest_paths(selection_inputs)
    if not stage_fresh(manifest, "select", selection_token, [
        p["run"] / "selection_summary.json",
        p["run"] / "retained_sources.jsonl",
        p["run"] / "cluster_discovery_input.jsonl",
        p["run"] / "cluster_discovery_seed_input.jsonl",
        p["run"] / "dedup_audit.json",
        p["run"] / "source_link_health.applied.json",
    ]):
        return result(
            "READY_TO_ADVANCE", "select", "可以执行来源筛选与同稿去重",
            action="select", input_digest=selection_token,
        )

    selection_summary = read_json(p["run"] / "selection_summary.json")
    if int(selection_summary.get("unreviewed_source_queue", 0)):
        return result("BLOCKED", "source_review", "select 检出未完成来源复核；请修复 source_reviews.json 后重跑", unresolved=selection_summary["unreviewed_source_queue"])

    cluster_discovery_input = p["run"] / "cluster_discovery_seed_input.jsonl"
    cluster_discovery_chunks = sorted((p["run"] / "cluster_discovery_chunks").glob("chunk-*.jsonl"))
    cluster_inputs = [cluster_discovery_input]
    cluster_payload = review_payload(p["clusters"])
    if cluster_payload and cluster_payload.get('_review_file_error'):
        parse_error = clean(cluster_payload['_review_file_error'])
        return result(
            'REVIEW_REQUIRED', 'cluster_definition_repair',
            '分类定义文件无法解析；已保留原文件，只修报告中的语法/编码位置，不重新生成分类',
            required_file=str(p['clusters']), input_file=str(p['clusters']),
            validation_error=parse_error, validation_errors=[parse_error],
            definition_repair_kind='json_or_encoding',
            review_requirements=[
                '本次只发现JSON/编码解析错误，不代表观点组或覆盖范围不合格',
                '保留全部已有分类，只修validation_errors指出的位置，不清空、重写或重新发现观点组',
                '修好后直接执行next_command；不读取源码、不编写辅助程序',
            ],
            original_submission_preserved=True, edit_file_ready=True,
        )
    cluster_issue = binding_issue(cluster_payload, workflow_id, cluster_inputs)
    pending_batches = (cluster_payload or {}).get("batches")
    empty_discovery = isinstance(pending_batches, dict) and all(
        isinstance(values, list) and not values for values in pending_batches.values()
    )
    # A status poll must not turn a freshly prepared blank form into a repair
    # error and hide the compact discovery input from the executing model.
    if cluster_issue or empty_discovery:
        batches = []
        for item in read_jsonl(cluster_discovery_input):
            batch = clean(item.get("batch"))
            if batch and batch not in batches:
                batches.append(batch)
        template = p["templates"] / "cluster_definitions.template.json"
        cluster_template = {
            "scope": "current_period_data_derived_clusters",
            "_workflow": binding(workflow_id, cluster_inputs),
            "contract": {
                "required_fields": ["id", "title", "stance", "summary", "keywords", "required_any", "negative_cues", "background", "rare_signal"],
                "field_types": {
                    "id": "string; unique within this batch",
                    "title": "string; current-period report-style judgment",
                    "stance": "positive | objective | negative",
                    "summary": "string; current cluster's semantic boundary",
                    "keywords": "array of current-period strings or [string, numeric_weight] pairs",
                    "required_any": "array of aspect-bearing strings",
                    "negative_cues": "array of negative-expression strings; [] when inapplicable",
                    "background": "boolean: true or false, not a string",
                    "rare_signal": "boolean: true or false, not a string",
                },
                "title": "先读样本再命名：评价对象 + 方向 + 核心依据。第一判断分句以肯定、认可、关注、讨论、质疑、批评、担忧、认为、指出等明确谓语开头；也接受观众、网友、网民、媒体、舆论、公众、影评人、评论者等简短主体后直接接上述判断谓语（如观众认为、媒体指出），时间范围放在谓语之后。至少16个有效字符，不以无关细节凑长度。词法通过不是语义通过，主体与标题主张仍须由本期样本支持。提交前一次检查全部标题，不在报错后逐个试写。",
                "required_any": "填写简洁的方面词及必要同义表达，新增有效同义词均会生效；不得只填作品名、演员名、角色名、武侠、剧情、热度等通用词",
                "coverage": "观点应共同覆盖本期主要正面、客观、负面表达；后续脚本会把大面积无簇可接的来源退回定义修复",
            },
            "batches": {batch: [] for batch in batches},
        }
        if (
            not template.exists()
            or read_json(template).get("_workflow") != cluster_template.get("_workflow")
            or read_json(template).get("contract") != cluster_template.get("contract")
        ):
            write_json(template, cluster_template)
        write_json(p["clusters"], cluster_template)
        discovery_views = discovery_readable_views(
            p["run"], cluster_discovery_chunks or cluster_inputs,
        )
        import native_review
        return result(
            "REVIEW_REQUIRED", "cluster_discovery", "依次读取分层抽样的紧凑观点片段后归纳一级观点簇；未入样来源仍会在全量归簇时接受覆盖检查",
            required_file=str(p["clusters"]), template=str(template),
            input_file=str(cluster_discovery_chunks[0] if cluster_discovery_chunks else cluster_inputs[0]),
            input_files=[str(item) for item in cluster_discovery_chunks] or [str(cluster_inputs[0])],
            readable_input_files=[view["path"] for view in discovery_views],
            readable_input_manifest=discovery_views,
            reading_instruction=native_review.READABLE_CONTEXT_RULE,
            full_input_file=str(p["run"] / "cluster_discovery_input.jsonl"),
            full_source_file=str(p["run"] / "retained_sources.jsonl"), binding_issue=cluster_issue, edit_file_ready=True,
        )
    try:
        pipeline_api().validate_clusters(
            cluster_payload,
            {clean(item.get("batch")) for item in read_jsonl(cluster_discovery_input) if clean(item.get("batch"))},
            read_json(p["config"]),
        )
    except ValueError as exc:
        title_repair = prepare_title_format_repair(manifest, p, cluster_payload, str(exc).splitlines())
        return result(
            "REVIEW_REQUIRED", "cluster_definition_repair",
            "观点簇定义未通过快速预检；修正当前定义后再推进，不执行全量归簇",
            required_file=str(p["clusters"]), input_file=str(p["clusters"]),
            validation_error=str(exc), validation_errors=str(exc).splitlines(),
            definition_repair_kind='format_preflight',
            title_format_repair=title_repair,
            review_requirements=[
                '本次是定义格式/字段快速预检，不是全量材料覆盖审查；不能据此断言大量材料无组可接',
                '一次修正validation_errors列出的标题、字段或方面词，保留无关定义内容，不从头聚类',
                '标题需明确判断谓语与具体依据，可使用契约允许的简短主体；错误不等于书名号有问题',
                '只从已读本期材料补合法方面词，不编造观点，不为格式错误新增组或批量写override',
                '修好后直接执行next_command；不读取源码、不编写辅助程序',
            ],
            full_source_file=str(p["run"] / "retained_sources.jsonl"), edit_file_ready=True,
        )

    override_inputs = [p["run"] / "retained_sources.jsonl", p["clusters"]]
    override_template = p['templates'] / 'cluster_overrides.template.json'
    overrides_payload = review_payload(p["overrides"])
    cumulative_overrides = cumulative_override_payload(
        workflow_id, override_inputs, overrides_payload, p["override_ledger"]
    )
    ensure_template(override_template, cumulative_overrides)
    # Prepare the empty, bound edit surface before computing routing digests.
    # A later parent reassignment must not create a new input merely by polling.
    if overrides_payload is None:
        write_json(p["overrides"], cumulative_overrides)
        overrides_payload = cumulative_overrides
    if overrides_payload is not None:
        override_issue = binding_issue(overrides_payload, workflow_id, override_inputs)
        if override_issue:
            write_json(p["overrides"], cumulative_overrides)
            return result("REVIEW_REQUIRED", "cluster_assignment_review", "归簇提交已按当前输入重新绑定；核对当前文件后继续", required_file=str(p["overrides"]), template=str(override_template), input_file=str(p["overrides"]), binding_issue=override_issue, edit_file_ready=True)
        assignment_input = p['run'] / 'cluster_assignment_review.current.jsonl'
        assignment_queue = p['run'] / 'cluster_review_queue.jsonl'
        pending_assignment = untouched_assignment_packet(
            overrides_payload, assignment_input, assignment_queue, override_template,
        )
        if pending_assignment:
            return result(
                'REVIEW_REQUIRED', 'cluster_assignment_review',
                '当前归簇表尚未填写；保持原待审队列，可按 Skill 分发原生同模型任务',
                required_file=str(p['overrides']), input_file=str(assignment_input),
                full_queue_file=str(assignment_queue), required=len(pending_assignment),
                completed=0, missing=len(pending_assignment), edit_file_ready=True,
            )
        override_validation_issues = cluster_override_validation_issues(
            read_jsonl(p["run"] / "retained_sources.jsonl"),
            read_json(p["clusters"]),
            cumulative_overrides,
            read_json(p["config"]),
        )
        write_json(p["override_validation"], {
            "status": "PASS" if not override_validation_issues else "REVIEW_REQUIRED",
            "issue_count": len(override_validation_issues),
            "issues": override_validation_issues,
        })
        if override_validation_issues:
            return result(
                "REVIEW_REQUIRED", "cluster_assignment_review",
                "归簇修正存在可直接修复的格式或逐字证据问题；已一次列出全部问题",
                required_file=str(p["overrides"]), template=str(override_template),
                input_file=str(p["override_validation"]),
                validation_file=str(p["override_validation"]),
                validation_issue_count=len(override_validation_issues),
                validation_issues=override_validation_issues, edit_file_ready=True,
            )

    cluster_base_inputs = [p["run"] / "retained_sources.jsonl", p["clusters"], p["config"], PIPELINE, CONTROLLER]
    if p["overrides"].exists():
        cluster_base_inputs.append(p["overrides"])
    if p["override_ledger"].exists():
        cluster_base_inputs.append(p["override_ledger"])
    cluster_base_token = digest_paths(cluster_base_inputs)
    if not stage_fresh(manifest, "cluster_base", cluster_base_token, [
        p["run"] / "cluster_set_review_input.json",
        p["run"] / "cluster_count_review_input.json",
        p["run"] / "cluster_review_queue.jsonl",
        p["run"] / "cluster_exclusions.jsonl",
        p["run"] / "cluster_routing_cache_audit.json",
    ]):
        return result("READY_TO_ADVANCE", "cluster_base", "观点簇定义已就绪，可以生成首次归簇和复核队列", action="cluster_base", input_digest=cluster_base_token)

    low_queue = read_jsonl(p["run"] / "cluster_review_queue.jsonl")
    representative_limit = pipeline_api().representative_sample_limit(read_json(p["config"]))
    coverage_queue = read_jsonl(p["run"] / "cluster_review_queue.deferred.jsonl") if representative_limit is not None else low_queue
    if coverage_queue:
        aspect_gaps = [item for item in coverage_queue if clean(item.get("issue")) == "passage_aspect_mismatch"]
        retained_count = len(read_jsonl(p["run"] / "retained_sources.jsonl"))
        gap_threshold = max(12, int(retained_count * 0.08))
        if len(aspect_gaps) >= gap_threshold:
            gap_file = p["run"] / "cluster_definition_gap_samples.jsonl"
            gap_samples = diverse_review_sample(aspect_gaps, 40)
            write_jsonl(gap_file, gap_samples)
            return result(
                "REVIEW_REQUIRED", "cluster_definition_repair",
                "大量来源没有可承接的观点簇；先修正观点定义，再处理少量个别归簇",
                definition_repair_kind='coverage_gap',
                required_file=str(p["clusters"]), input_file=str(gap_file),
                full_queue_file=str(p["run"] / ("cluster_review_queue.deferred.jsonl" if representative_limit is not None else "cluster_review_queue.jsonl")),
                uncovered=len(aspect_gaps), retained_sources=retained_count,
                threshold=gap_threshold, sample_rows=len(gap_samples), sampling="semantic_diversity",
                do_not_create_bulk_overrides=True, edit_file_ready=True,
            )
    if low_queue:
        cumulative_overrides = cumulative_override_payload(
            workflow_id, override_inputs, overrides_payload, p["override_ledger"]
        )
        low_ids = {clean(item.get("source_id")) for item in low_queue}
        rejected_ids = {
            clean(item.get("source_id"))
            for item in low_queue
            if item.get("override_applied")
        }
        template = p["templates"] / "cluster_overrides.template.json"
        current_assignment_input = p["run"] / "cluster_assignment_review.current.jsonl"
        current_assignment_rows = low_queue[:60]
        write_jsonl(current_assignment_input, current_assignment_rows)
        assignment_submission = cluster_override_submission_template(cumulative_overrides, current_assignment_rows)
        write_json(template, assignment_submission)
        write_json(p["overrides"], assignment_submission)
        return result(
            "REVIEW_REQUIRED", "cluster_assignment_review",
            "只处理当前最多60条归簇残差；已提交但仍出现的 override 视为失败，必须按 issue 重新选片段或转簇",
            required_file=str(p["overrides"]), template=str(template), input_file=str(current_assignment_input),
            full_queue_file=str(p["run"] / "cluster_review_queue.jsonl"), current_count=min(len(low_queue), 60),
            required=len(low_ids), completed=0, missing=len(low_ids),
            rejected_submissions=len(rejected_ids), rejected_source_ids=sorted(rejected_ids),
            do_not_rerun_without_changes=True, edit_file_ready=True,
        )

    set_inputs = [p["run"] / "cluster_set_review_input.json", p["run"] / "cluster_count_review_input.json"]
    cluster_set_input = read_json(set_inputs[0])
    cluster_count_input = read_json(set_inputs[1])
    set_payload = review_payload(p["set"])
    set_issue = binding_issue(set_payload, workflow_id, set_inputs)
    required_set = {exact_key(item.get("review_key")) for item in cluster_set_input.get("clusters", [])}
    required_count = {exact_key(item.get("review_key")) for item in cluster_count_input.get("batches", [])}
    set_fingerprints = {exact_key(item.get("review_key")): clean(item.get("fingerprint"))
                        for item in cluster_set_input.get("clusters", [])}
    count_fingerprints = {exact_key(item.get("review_key")): clean(item.get("fingerprint"))
                          for item in cluster_count_input.get("batches", [])}
    set_values = (set_payload or {}).get("reviews", {})
    count_values = (set_payload or {}).get("count_reviews", {})
    set_done = {
        exact_key(key) for key, item in set_values.items()
        if isinstance(set_values, dict) and isinstance(item, dict) and cluster_set_review_is_filled(item)
        and clean(item.get("fingerprint")) == set_fingerprints.get(exact_key(key))
    } if isinstance(set_values, dict) else set()
    count_done = {
        exact_key(key) for key, item in count_values.items()
        if isinstance(count_values, dict) and isinstance(item, dict) and cluster_count_review_is_filled(item)
        and clean(item.get("fingerprint")) == count_fingerprints.get(exact_key(key))
    } if isinstance(count_values, dict) else set()
    if set_issue or required_set - set_done or required_count - count_done:
        template = p["templates"] / "cluster_set_reviews.template.json"
        set_template = cluster_set_review_template_payload(
            workflow_id, set_inputs, cluster_set_input, cluster_count_input
        )
        if (
            not template.exists()
            or read_json(template).get("_workflow") != set_template.get("_workflow")
            or (required_set and not read_json(template).get("reviews"))
        ):
            write_json(template, set_template)
        carry_current_cluster_review_records(set_template, set_payload)
        write_json(template, set_template)
        write_json(p["set"], set_template)
        return result(
            "REVIEW_REQUIRED", "cluster_set_review", "逐簇核对标题主张、成员证据、立场纯度和期次范围，并审查整期簇数",
            required_file=str(p["set"]), template=str(template), input_files=[str(item) for item in set_inputs], binding_issue=set_issue,
            clusters_required=len(required_set), clusters_missing=len(required_set - set_done), batches_required=len(required_count), batches_missing=len(required_count - count_done), edit_file_ready=True,
        )

    cluster_final_inputs = cluster_base_inputs + [p["set"]]
    cluster_final_token = digest_paths(cluster_final_inputs)
    if not stage_fresh(manifest, "cluster_final", cluster_final_token, [
        p["run"] / "cluster_summary.json",
        p["run"] / "clustered_items.json",
        p["run"] / "cluster_review_queue.jsonl",
        p["run"] / "cluster_set_review_queue.unresolved.jsonl",
        p["run"] / "cluster_count_review_queue.unresolved.jsonl",
        p["run"] / "workbench_order_audit.json",
        p["run"] / "cluster_exclusions.jsonl",
        p["run"] / "cluster_routing_cache_audit.json",
    ]):
        return result("READY_TO_ADVANCE", "cluster_final", "簇级审查齐备，可以固化最终归簇并进入逐摘录语义复核", action="cluster_final", input_digest=cluster_final_token)

    unresolved = {
        "assignment": len(read_jsonl(p["run"] / "cluster_review_queue.jsonl")),
        "cluster_set": len(read_jsonl(p["run"] / "cluster_set_review_queue.unresolved.jsonl")),
        "cluster_count": len(read_jsonl(p["run"] / "cluster_count_review_queue.unresolved.jsonl")),
    }
    if any(unresolved.values()):
        if unresolved["assignment"]:
            final_assignment_queue = read_jsonl(p["run"] / "cluster_review_queue.jsonl")
            current_assignment_input = p["run"] / "cluster_assignment_review.current.jsonl"
            current_assignment_rows = final_assignment_queue[:60]
            write_jsonl(current_assignment_input, current_assignment_rows)
            cumulative_overrides = cumulative_override_payload(
                workflow_id, override_inputs, review_payload(p["overrides"]), p["override_ledger"]
            )
            assignment_submission = cluster_override_submission_template(cumulative_overrides, current_assignment_rows)
            write_json(override_template, assignment_submission)
            write_json(p["overrides"], assignment_submission)
            return result(
                "REVIEW_REQUIRED", "cluster_assignment_review",
                "最终归簇仍有对象、方面或立场未对齐项；只处理当前最多60条并修正累计归簇文件",
                required_file=str(p["overrides"]), template=str(override_template),
                input_file=str(current_assignment_input), full_queue_file=str(p["run"] / "cluster_review_queue.jsonl"),
                current_count=min(len(final_assignment_queue), 60), unresolved=unresolved, edit_file_ready=True,
            )
        if unresolved["cluster_set"] or unresolved["cluster_count"]:
            return result(
                "REVIEW_REQUIRED", "cluster_set_review",
                "最终归簇使集合或簇数审查失效；按当前指纹重新完成审查",
                required_file=str(p["set"]),
                input_files=[str(p["run"] / "cluster_set_review_queue.unresolved.jsonl"), str(p["run"] / "cluster_count_review_queue.unresolved.jsonl")],
                unresolved=unresolved, edit_file_ready=True,
            )
        raise RuntimeError(f"未知归簇未解决状态：{unresolved}")

    render_probe_inputs = [p["run"] / "clustered_items.json", WORKBENCH_TEMPLATE, PIPELINE, CONTROLLER]
    excerpt_template = p['templates'] / 'excerpt_reviews.template.json'
    ensure_template(excerpt_template, {'scope': 'current_period_verbatim_excerpt_reviews', '_workflow': binding(workflow_id, [p['run'] / 'clustered_items.json']), 'reviews': {}})
    if p["excerpts"].exists():
        excerpt_payload = review_payload(p["excerpts"])
        excerpt_issue = binding_issue(excerpt_payload, workflow_id, [p["run"] / "clustered_items.json"])
        if excerpt_issue:
            # Archive the old explicit submission before rebinding; never lose
            # unrelated cuts merely because one other source moved groups.
            previous_digest = sha256(p['excerpts'])
            previous = p['run'] / 'verbatim_excerpt_repair_history' / (previous_digest + '.json')
            if not previous.exists():
                write_json(previous, excerpt_payload)
            rebound = rebind_scoped_verbatim_repairs(p, excerpt_payload, workflow_id)
            write_json(p["excerpts"], rebound)
            write_json(excerpt_template, rebound)
            if not rebound['reviews']:
                return result("REVIEW_REQUIRED", "excerpt_review", "摘录提交已按当前最终归簇重新绑定；只填写需要人工指定的逐字片段", required_file=str(p["excerpts"]), template=str(excerpt_template), input_file=str(p['run'] / 'clustered_items.json'), binding_issue=excerpt_issue, edit_file_ready=True)
        else:
            sealed = seal_current_verbatim_repairs(p, excerpt_payload, workflow_id)
            if sealed != excerpt_payload:
                write_json(p['excerpts'], sealed)
        render_probe_inputs.append(p["excerpts"])
    render_probe_token = digest_paths(render_probe_inputs)
    preflight_path = p["run"] / "excerpt_preflight_failures.json"
    if not stage_fresh(manifest, "render_probe", render_probe_token, [
        p["run"] / "excerpt_semantic_review_input.jsonl",
        p["run"] / "excerpt_semantic_auto_accepted.jsonl",
        p["run"] / "final_excerpt_review_contract.json",
        preflight_path,
    ]):
        return result("READY_TO_ADVANCE", "render_probe", "可以生成候选摘录与最终摘录语义复核输入；这一步不会发布 HTML", action="render_probe", input_digest=render_probe_token)

    preflight_failures = read_json(preflight_path).get("failures", [])
    if preflight_failures:
        existing_excerpt_reviews = {}
        excerpt_payload = review_payload(p["excerpts"])
        if not binding_issue(excerpt_payload, workflow_id, [p["run"] / "clustered_items.json"]):
            existing_excerpt_reviews = list_review_map(excerpt_payload, "view_id")
        for failure in preflight_failures:
            existing_excerpt_reviews.setdefault(clean(failure.get("view_id")), {
                "fragments": [], "positions": [], "reason": "",
            })
        write_json(excerpt_template, {
            "scope": "current_period_verbatim_excerpt_reviews",
            "_workflow": binding(workflow_id, [p["run"] / "clustered_items.json"]),
            "contract": {"fix_only_listed_view_ids": True, "one_or_two_verbatim_fragments": True},
            "reviews": existing_excerpt_reviews,
        })
        write_json(p["excerpts"], read_json(excerpt_template))
        return result(
            "REVIEW_REQUIRED", "excerpt_preflight",
            "先修复脚本已定位的摘录格式问题，再进入最终语义复核；未受影响条目无需处理",
            required_file=str(p["excerpts"]), template=str(excerpt_template),
            input_file=str(preflight_path), full_source_file=str(p["run"] / "retained_sources.jsonl"),
            failure_count=len(preflight_failures),
            affected_view_ids=[clean(item.get("view_id")) for item in preflight_failures], edit_file_ready=True,
        )

    semantic_inputs = [
        p["run"] / "excerpt_semantic_review_input.jsonl",
        p["run"] / "final_excerpt_review_contract.json",
    ]
    semantic_payload = review_payload(p["semantic"])
    semantic_issue = binding_issue(semantic_payload, workflow_id, semantic_inputs)
    if semantic_issue == "input_fingerprint_stale":
        semantic_issue = ""
    semantic_review_items = read_jsonl(semantic_inputs[0])
    semantic_input_map = {
        clean(item.get("view_id")): item
        for item in semantic_review_items
        if clean(item.get("view_id"))
    }
    required_views = {clean(item.get("view_id")) for item in semantic_review_items}
    semantic_payload = expand_compact_submission(
        semantic_payload, workflow_id, semantic_inputs, semantic_input_map, final=True,
    )
    if not required_views:
        semantic_issue = ""
    semantic_ledger = cumulative_fingerprinted_review_payload(
        workflow_id, semantic_inputs, semantic_payload, p["semantic_ledger"], semantic_input_map,
    )
    write_json(p["semantic_ledger"], semantic_ledger)
    semantic_review_map = list_review_map(semantic_ledger, "view_id")
    # Read source text in Python only when a cross-work drop needs provenance.
    # This does not send the full file to the model.
    needs_source = any(
        value.get("decision") == "drop" and "work_consistency" in (value.get("failed_checks") or [])
        for value in semantic_review_map.values()
    )
    source_texts = {
        clean(row.get("id")): pipeline_api().source_text(row)
        for row in read_jsonl(p["run"] / "retained_sources.jsonl")
    } if needs_source else {}
    semantic_errors = {
        view_id: pipeline_api().final_excerpt_review_errors(
            review, semantic_input_map.get(view_id, {}),
            source_texts.get(clean(semantic_input_map.get(view_id, {}).get("source_id"))),
        )
        for view_id, review in semantic_review_map.items()
    }
    semantic_done = {view_id for view_id, errors in semantic_errors.items() if not errors}
    import native_review
    parent_requests, request_files = native_review.pending_parent_reassignments(
        sys.modules[__name__], workspace, workflow_id, semantic_input_map, semantic_done, semantic_review_map,
    )
    if parent_requests:
        return result(
            'REVIEW_REQUIRED', 'final_assignment_repair',
            '最终审查有明确转簇待办；其他判断已保存，主窗口实际修改归类或有依据地否决请求后再推进',
            required_file=str(p['overrides']), input_files=request_files + [str(p['clusters'])],
            input_file=request_files[0], optional_rejection_review_file=str(p['semantic']),
            pending_view_ids=[item['view_id'] for item in parent_requests],
            pending_source_ids=sorted({item['source_id'] for item in parent_requests}),
            edit_file_ready=True,
        )
    if semantic_issue or required_views - semantic_done:
        template = p["templates"] / "final_excerpt_semantic_reviews.template.json"
        excerpt_template = p["templates"] / "excerpt_reviews.template.json"
        ensure_template(excerpt_template, {
            "scope": "current_period_verbatim_excerpt_reviews",
            "_workflow": binding(workflow_id, [p["run"] / "clustered_items.json"]),
            "reviews": {},
        })
        semantic_chunks = sorted((p["run"] / "final_excerpt_review_chunks").glob("chunk-*.jsonl"))
        missing_views = required_views - semantic_done
        next_chunk, chunk_index, chunk_total = next_incomplete_chunk(semantic_chunks, "view_id", missing_views)
        current_ids = {
            clean(item.get("view_id")) for item in read_jsonl(next_chunk)
        } if next_chunk else set(missing_views)
        current_items = [
            item for item in semantic_review_items
            if clean(item.get("view_id")) in current_ids and clean(item.get("view_id")) in missing_views
        ]
        current_input = p["run"] / "final_excerpt_review.current.jsonl"
        current_items = [
            {**item, **({"submission_errors": semantic_errors[item["view_id"]]}
                       if semantic_errors.get(item["view_id"]) else {})}
            for item in current_items
        ]
        compact_tail = compact_final_review_tail(semantic_review_items, missing_views, semantic_errors)
        if compact_tail is not None:
            current_items = compact_tail
            chunk_index, chunk_total = 1, 1
        write_jsonl(current_input, current_items)
        semantic_submission = final_excerpt_review_template_payload(workflow_id, semantic_inputs, current_items)
        for view_id in list(semantic_submission["reviews"]):
            prior = semantic_review_map.get(view_id)
            if isinstance(prior, dict):
                semantic_submission["reviews"][view_id].update(prior)
        write_json(template, semantic_submission)
        write_json(p["semantic"], semantic_submission)
        return result(
            "REVIEW_REQUIRED", "final_excerpt_review", "当前分片已写入 required_file；直接核对预填字段并再次 advance",
            required_file=str(p["semantic"]), template=str(template), optional_excerpt_file=str(p["excerpts"]), optional_excerpt_template=str(excerpt_template),
            input_file=str(current_input), source_chunk_file=str(current_input if compact_tail is not None else next_chunk or semantic_inputs[0]),
            chunk_index=chunk_index, chunk_total=chunk_total,
            packet_kind="compacted_pending_tail" if compact_tail is not None else "original_chunk",
            full_input_file=str(semantic_inputs[0]), contract_file=str(semantic_inputs[1]),
            full_source_file=str(p["run"] / "retained_sources.jsonl"), binding_issue=semantic_issue,
            required=len(required_views), completed=len(required_views & semantic_done), missing=len(required_views - semantic_done), edit_file_ready=True,
            rejected_submissions=sum(bool(semantic_errors.get(item["view_id"])) for item in current_items),
            repair_instruction="已提交但未通过的条目，其具体字段错误直接列在 input_file 的 submission_errors；只修这些字段，不要研究源码或按词表生成判断",
        )
    write_json(p["semantic"], semantic_ledger)

    render_final_inputs = render_probe_inputs + [p["semantic"]]
    post_count_input = p["run"] / "post_excerpt_cluster_count_review_input.json"
    if p["post_count"].exists():
        post_count_issue = binding_issue(
            review_payload(p["post_count"]), workflow_id, [post_count_input]
        ) if post_count_input.exists() else "missing_post_excerpt_count_input"
        if post_count_issue:
            template = p["templates"] / "post_excerpt_cluster_count_reviews.template.json"
            if post_count_input.exists():
                post_count_submission = post_excerpt_count_review_template_payload(workflow_id, post_count_input)
                write_json(template, post_count_submission)
                write_json(p["post_count"], post_count_submission)
            return result(
                "REVIEW_REQUIRED", "post_excerpt_count_review",
                "后置簇数复核与当前终审结果不一致",
                required_file=str(p["post_count"]), template=str(template),
                input_file=str(post_count_input), binding_issue=post_count_issue, edit_file_ready=True,
            )
        render_final_inputs.append(p["post_count"])
    render_final_token = digest_paths(render_final_inputs)
    render_summary_path = p["run"] / "render_summary.json"
    if not stage_fresh(manifest, "render_final", render_final_token, [render_summary_path]):
        previous_render = manifest.get("stages", {}).get("render_final", {})
        if p["output"].exists():
            return result(
                "BLOCKED", "output_collision",
                "正式 HTML 在 verify PASS 前已出现，属于未验证文件；禁止覆盖或交付，请换新输出路径",
                existing_output=str(p["output"]),
            )
        return result("READY_TO_ADVANCE", "render_final", "最终摘录复核齐备，可以生成待验收页面；此时不会发布正式 HTML", action="render_final", input_digest=render_final_token)

    render_summary = read_json(render_summary_path)
    if clean(render_summary.get("status")) != "RENDERED" or not p["staged_output"].exists():
        failures = read_json(p["run"] / "render_failures.json").get("failures", []) if (p["run"] / "render_failures.json").exists() else []
        if clean(render_summary.get("stage")) == "post_excerpt_cluster_count_review" and post_count_input.exists():
            template = p["templates"] / "post_excerpt_cluster_count_reviews.template.json"
            post_count_submission = post_excerpt_count_review_template_payload(workflow_id, post_count_input)
            write_json(template, post_count_submission)
            write_json(p["post_count"], post_count_submission)
            return result(
                "REVIEW_REQUIRED", "post_excerpt_count_review",
                "终审改变了实际非空观点簇数，请按终审后结果完成一次后置数量复核",
                required_file=str(p["post_count"]), template=str(template),
                input_file=str(post_count_input), failure_count=len(failures), edit_file_ready=True,
            )
        return result(
            "REVIEW_REQUIRED", "render_repair", "页面未通过最终生成门槛；按失败项重截、重归簇或更新复核后再次 advance",
            render_summary=render_summary, failures_file=str(p["run"] / "render_failures.json"), failure_count=len(failures),
        )
    saved_output_hash = manifest.get("stages", {}).get("render_final", {}).get("output_hashes", {}).get(str(p["staged_output"].resolve()))
    if not saved_output_hash or saved_output_hash != sha256(p["staged_output"]):
        return result(
            "BLOCKED", "output_modified",
            "待验收 HTML 与本轮 render_final 记录不一致；禁止继续验收",
            existing_output=str(p["staged_output"]),
        )

    verify_inputs = [p["staged_output"], p["run"] / "workbench_dataset.json", p["run"] / "excerpt_provenance.json", PIPELINE, CONTROLLER]
    verify_token = digest_paths(verify_inputs)
    if not stage_fresh(manifest, "verify", verify_token, [p["run"] / "verification.json", p["output"]]):
        return result("READY_TO_ADVANCE", "verify", "待验收页面已生成；验收通过后才原子发布正式 HTML", action="verify", input_digest=verify_token)

    verification = read_json(p["run"] / "verification.json")
    if clean(verification.get("status")) != "PASS":
        return result("BLOCKED", "verify", "发布验收失败，禁止交付", verification=verification)
    published_hash = manifest.get("stages", {}).get("verify", {}).get("output_hashes", {}).get(str(p["output"].resolve()))
    if not p["output"].exists() or not published_hash or published_hash != sha256(p["output"]):
        return result("BLOCKED", "publish", "正式 HTML 缺失或与验收发布记录不一致，禁止交付")
    return result(
        "COMPLETE", "complete", "独立工作台已经通过全部机器门槛；交付前仍需由执行者打开页面做视觉抽查",
        verification=verification, visual_inspection_required=True,
    )


def command_init(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    workspace = args.workspace.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    if workspace == source or source in workspace.parents:
        raise ValueError("工作区不能位于原始数据目录内")
    if output.suffix.lower() != '.html' or source == output or source in output.parents:
        raise ValueError('输出须为原始目录以外的 .html 文件')
    if output.exists():
        raise FileExistsError('新运行请使用新的单期 HTML 路径；验证后再通过 merge 合并到已有工作台')
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError('init 需要一个空工作区；已有运行请用 status/advance')
    manifest_path = workspace / MANIFEST_NAME
    if manifest_path.exists():
        raise FileExistsError(f"{manifest_path} 已存在；请改用 status/advance，或换一个新工作区")
    with workflow_lock(workspace):
        (workspace / "reviews").mkdir(exist_ok=True)
        (workspace / "config").mkdir(exist_ok=True)
        (workspace / "review_templates").mkdir(exist_ok=True)
        extraction = run_command([sys.executable, str(EXTRACTOR), "--source", str(source), "--output", str(workspace / "extract")])
        records = workspace / "extract" / "records.jsonl"
        manifest = {
            "schema_version": 1,
            "workflow": "film-brief-cleaning",
            "workflow_id": str(uuid.uuid4()),
            "selection_policy": "representative_max10",
            "created_at": now(),
            "updated_at": now(),
            "source": str(source),
            "workspace": str(workspace),
            "output": str(output),
            "records_sha256": sha256(records),
            "stages": {},
            "history": [{"stage": "extract", "returncode": extraction["returncode"], "finished_at": extraction["finished_at"]}],
        }
        if args.period_config:
            config_source = args.period_config.resolve()
            if not config_source.is_file():
                raise FileNotFoundError(config_source)
            shutil.copy2(config_source, workspace / "config" / "period_config.json")
            new_config_path = workspace / "config" / "period_config.json"
            new_config = read_json(new_config_path)
            if "representative_sample_limit" not in new_config:
                new_config["representative_sample_limit"] = 10
                write_json(new_config_path, new_config)
        else:
            ensure_template(workspace / "review_templates" / "period_config.template.json", config_template(records))
        save_manifest(workspace, manifest)
        advance_loop(workspace, args.max_steps, args.workers, args.timeout)


def save_initial_script_cluster_snapshot(manifest: dict, p: dict, effective_overrides: dict | None) -> Path | None:
    """Archive the first actual script output, never rerun or approve assignments."""
    destination = p['run'] / 'initial_script_cluster_snapshot.json'
    if (not destination.resolve().is_relative_to(p['run'].resolve())
            or not destination.resolve().is_relative_to(p['workspace'].resolve())):
        raise ValueError('Initial script snapshot outside current run')
    inputs = {
        'retained_sources.jsonl': p['run'] / 'retained_sources.jsonl',
        'period_config.json': p['run'] / 'period_config.json',
        'cluster_definitions.json': p['clusters'],
        'cluster_assignment_audit.json': p['run'] / 'cluster_assignment_audit.json',
        'clustered_items.json': p['run'] / 'clustered_items.json',
        'cluster_summary.json': p['run'] / 'cluster_summary.json',
        'cluster_review_queue.jsonl': p['run'] / 'cluster_review_queue.jsonl',
        'cluster_exclusions.jsonl': p['run'] / 'cluster_exclusions.jsonl',
    }
    if pipeline_api().representative_sample_limit(read_json(inputs['period_config.json'])) is not None:
        inputs['provisional_clustered_items.json'] = p['run'] / 'provisional_clustered_items.json'
        inputs['cluster_review_queue.deferred.jsonl'] = p['run'] / 'cluster_review_queue.deferred.jsonl'
    if destination.exists():
        pointer = manifest.get('initial_script_cluster_snapshot')
        if pointer and (pointer.get('path') != str(destination.resolve()) or pointer.get('sha256') != sha256(destination)):
            raise ValueError('Initial script snapshot guarded file changed')
        saved = read_json(destination)
        if saved.get('workflow_id') != manifest['workflow_id'] or saved.get('scope') != 'unreviewed_initial_script_clustering':
            raise ValueError('Initial script snapshot binding mismatch')
        for entry in saved.get('files', {}).values():
            if hashlib.sha256(entry['utf8_text'].encode('utf-8')).hexdigest() != entry['sha256']:
                raise ValueError('Initial script snapshot content changed')
        if set(saved.get('files', {})) != set(inputs):
            raise ValueError('Initial script snapshot file set mismatch')
        if not pointer:
            # Exact crash-after-write recovery only, not adoption of a legacy artifact.
            if ('cluster_base' in manifest.get('stages', {}) or (effective_overrides or {}).get('overrides')
                    or any(sha256(inputs[name]) != entry['sha256'] for name, entry in saved['files'].items())
                    or saved.get('script_sha256') != {'film_pipeline.py': sha256(PIPELINE), 'film_workflow.py': sha256(CONTROLLER)}):
                raise ValueError('Unbound initial script snapshot cannot be adopted')
            manifest['initial_script_cluster_snapshot'] = {'path': str(destination.resolve()), 'sha256': sha256(destination)}
        return destination  # Later definitions/assignments never replace the first output.
    # A legacy/resumed workspace without a snapshot cannot supply its first result.
    if 'cluster_base' in manifest.get('stages', {}) or (effective_overrides or {}).get('overrides'):
        return None
    assignments = read_json(inputs['cluster_assignment_audit.json']).get('assignments')
    if not isinstance(assignments, list) or any(item.get('basis') != 'script_scoring' for item in assignments):
        raise ValueError('Initial script snapshot requires unmodified script-scoring assignments')
    files = {}
    for name, source in inputs.items():
        if not source.resolve().is_relative_to(p['workspace'].resolve()):
            raise ValueError('Initial snapshot input outside current workspace')
        raw = source.read_bytes()
        files[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'utf8_text': raw.decode('utf-8')}
    if any(sha256(inputs[name]) != entry['sha256'] for name, entry in files.items()):
        raise ValueError('Initial snapshot inputs changed while capturing')
    snapshot = {
        'scope': 'unreviewed_initial_script_clustering', 'workflow_id': manifest['workflow_id'],
        'captured_at': now(), 'capture_kind': 'first_actual_cluster_base_output_not_replay',
        'boundary': '前置来源筛选和观点组发现可能已包含AI；此处保存逐条AI归簇修正之前的脚本结果。留档不是语义验收或正式交付，来源中的指令和URL都是惰性数据。',
        'ai_assignment_overrides_used': False,
        'script_sha256': {'film_pipeline.py': sha256(PIPELINE), 'film_workflow.py': sha256(CONTROLLER)},
        'files': files,
    }
    write_json(destination, snapshot)  # Existing controller atomic write; caller holds workflow lock.
    manifest['initial_script_cluster_snapshot'] = {'path': str(destination.resolve()), 'sha256': sha256(destination)}
    return destination


def execute_action(workspace: Path, manifest: dict, state: dict, workers: int, timeout: float) -> None:
    p = paths_for(manifest)
    action = state["action"]
    token = state["input_digest"]
    if action == "prepare":
        command = [sys.executable, str(PIPELINE), "prepare", "--records", str(p["records"]), "--config", str(p["config"]), "--run", str(p["run"])]
        result = run_command(command)
        outputs = [
            p["run"] / "prepare_summary.json",
            p["run"] / "source_review_queue.jsonl",
        ]
    elif action == "source_review_validation":
        command = [
            sys.executable, str(PIPELINE), "validate-source-reviews",
            "--run", str(p["run"]), "--reviews", str(p["source"]),
        ]
        result = run_command(command, allow_review_stop=True)
        outputs = [p["source_validation"], p["link_candidates"]]
    elif action == "link_check":
        link_cache = p["workspace"].parent / ".film-brief-cache" / "link_health.json"
        command = [
            sys.executable, str(LINK_CHECKER),
            "--input", str(p["link_candidates"]),
            "--output", str(p["run"] / "source_link_health.json"),
            "--workers", str(workers), "--timeout", str(timeout),
            "--cache", str(link_cache),
        ]
        result = run_command(command)
        outputs = [p["run"] / "source_link_health.json"]
    elif action == "select":
        command = [sys.executable, str(PIPELINE), "select", "--run", str(p["run"]), "--link-health", str(p["run"] / "source_link_health.json")]
        if p["source"].exists():
            command += ["--reviews", str(p["source"])]
        result = run_command(command)
        outputs = [
            p["run"] / "selection_summary.json",
            p["run"] / "retained_sources.jsonl",
            p["run"] / "cluster_discovery_input.jsonl",
            p["run"] / "cluster_discovery_seed_input.jsonl",
            p["run"] / "dedup_audit.json",
            p["run"] / "source_link_health.applied.json",
        ]
    elif action in {"cluster_base", "cluster_final"}:
        command = [sys.executable, str(PIPELINE), "cluster", "--run", str(p["run"]), "--clusters", str(p["clusters"])]
        effective_overrides = None
        override_inputs = [p["run"] / "retained_sources.jsonl", p["clusters"]]
        if p["overrides"].exists() or p["override_ledger"].exists():
            effective_overrides = cumulative_override_payload(
                manifest["workflow_id"], override_inputs,
                review_payload(p["overrides"]), p["override_ledger"],
            )
            write_json(p["override_effective"], effective_overrides)
            command += ["--overrides", str(p["override_effective"])]
        if action == "cluster_final":
            command += ["--set-reviews", str(p["set"])]
        result = run_command(command)
        audit_started = time.perf_counter()
        initial_snapshot = (save_initial_script_cluster_snapshot(manifest, p, effective_overrides)
                            if action == 'cluster_base' else None)
        if initial_snapshot is not None:
            result['initial_script_snapshot'] = str(initial_snapshot)
            result['audit_snapshot_elapsed_seconds'] = round(time.perf_counter() - audit_started, 6)
        if effective_overrides is not None:
            # Commit only after the cluster command accepts every merged record.
            # Keeping the user-facing file cumulative prevents cross-model
            # replacement of earlier review rounds.
            write_json(p["override_ledger"], effective_overrides)
            write_json(p["overrides"], effective_overrides)
        if action == "cluster_base":
            outputs = [
                p["run"] / "cluster_set_review_input.json",
                p["run"] / "cluster_count_review_input.json",
                p["run"] / "cluster_review_queue.jsonl",
                p["run"] / "cluster_exclusions.jsonl",
                p["run"] / "cluster_routing_cache_audit.json",
            ]
            if initial_snapshot is not None:
                outputs.append(initial_snapshot)
        else:
            outputs = [
                p["run"] / "cluster_summary.json",
                p["run"] / "clustered_items.json",
                p["run"] / "cluster_review_queue.jsonl",
                p["run"] / "cluster_set_review_queue.unresolved.jsonl",
                p["run"] / "cluster_count_review_queue.unresolved.jsonl",
                p["run"] / "workbench_order_audit.json",
                p["run"] / "cluster_exclusions.jsonl",
                p["run"] / "cluster_routing_cache_audit.json",
            ]
    elif action in {"render_probe", "render_final"}:
        command = [sys.executable, str(PIPELINE), "render", "--run", str(p["run"]), "--output", str(p["staged_output"])]
        if p["excerpts"].exists():
            command += ["--excerpt-reviews", str(p["excerpts"])]
        if action == "render_final" or pipeline_api().representative_sample_limit(read_json(p["config"])) is not None and p["semantic_ledger"].exists():
            command += ["--semantic-reviews", str(p["semantic"] if action == "render_final" else p["semantic_ledger"])]
            if p["post_count"].exists():
                command += ["--post-count-reviews", str(p["post_count"])]
        result = run_command(command, allow_review_stop=True)
        if action == "render_final" and p["staged_output"].exists():
            result["output_sha256"] = sha256(p["staged_output"])
        outputs = [
            p["run"] / "excerpt_semantic_review_input.jsonl",
            p["run"] / "excerpt_semantic_auto_accepted.jsonl",
            p["run"] / "final_excerpt_review_contract.json",
            p["run"] / "excerpt_preflight_failures.json",
        ]
        if action == "render_final":
            outputs = [
                p["run"] / "render_summary.json",
                p["run"] / "post_excerpt_cluster_count_review_input.json",
                p["run"] / "post_excerpt_cluster_count_review_audit.json",
                p["staged_output"],
            ]
    elif action == "verify":
        command = [sys.executable, str(PIPELINE), "verify", "--run", str(p["run"]), "--output", str(p["staged_output"])]
        result = run_command(command, allow_review_stop=True)
        verification = read_json(p["run"] / "verification.json")
        if clean(verification.get("status")) == "PASS":
            if p["output"].exists():
                raise FileExistsError("verify PASS 前正式输出路径已存在，拒绝覆盖外部文件")
            p["output"].parent.mkdir(parents=True, exist_ok=True)
            publish_temp = p["output"].with_name(f".{p['output'].name}.{uuid.uuid4().hex}.tmp")
            shutil.copy2(p["staged_output"], publish_temp)
            os.replace(publish_temp, p["output"])
            verification["verified_candidate"] = verification.get("output", str(p["staged_output"]))
            verification["output"] = str(p["output"])
            verification["published_after_pass"] = True
            write_json(p["run"] / "verification.json", verification)
            result["published_output"] = str(p["output"])
            result["output_sha256"] = sha256(p["output"])
        outputs = [p["run"] / "verification.json"]
        if clean(verification.get("status")) == "PASS":
            outputs.append(p["output"])
    else:
        raise ValueError(f"未知 action：{action}")
    record_stage(workspace, manifest, action, token, result, outputs)


def command_status(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    with workflow_lock(workspace):
        manifest = load_manifest(workspace)
        print(json.dumps(status_payload(workspace, manifest), ensure_ascii=False, indent=2))


def unchanged_failure_count(manifest: dict, action: object, token: object) -> int:
    """Count only consecutive failures for the exact same action input."""
    action_value = clean(action)
    token_value = clean(token)
    count = 0
    for event in reversed(manifest.get("history", [])):
        if clean(event.get("stage")) != action_value or clean(event.get("input_digest")) != token_value:
            break
        if int(event.get("returncode", 0)) == 0:
            break
        count += 1
    return count


def advance_loop(workspace: Path, max_steps: int, workers: int, timeout: float) -> None:
    manifest = load_manifest(workspace)
    for _ in range(max(1, max_steps)):
        state = status_payload(workspace, manifest)
        if state.get("status") != "READY_TO_ADVANCE":
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return
        action = clean(state.get("action"))
        token = clean(state.get("input_digest"))
        repeated_failures = unchanged_failure_count(manifest, action, token)
        if repeated_failures >= 2:
            blocked = {
                **state,
                "status": "BLOCKED",
                "stage": "repeated_action_failure",
                "message": "同一输入已连续执行失败两次，控制器停止无效重试；按最近失败报告修改评审文件或输入后再运行 advance",
                "failed_action": action,
                "unchanged_failure_count": repeated_failures,
                "do_not_rerun_without_changes": True,
            }
            blocked.pop("action", None)
            write_json(workspace / STATUS_NAME, blocked)
            print(json.dumps(blocked, ensure_ascii=False, indent=2))
            return
        execute_action(workspace, manifest, state, workers, timeout)
        manifest = load_manifest(workspace)
    state = status_payload(workspace, manifest)
    state["message"] = f"已达到单次最大推进步数 {max_steps}；再次运行 advance 继续"
    write_json(workspace / STATUS_NAME, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def command_advance(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    with workflow_lock(workspace):
        advance_loop(workspace, args.max_steps, args.workers, args.timeout)


def command_doctor(args: argparse.Namespace) -> None:
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("python_version", sys.version_info >= (3, 10), sys.version.split()[0])
    check("openpyxl", importlib.util.find_spec("openpyxl") is not None, "Excel读取依赖")
    for path in (CONTROLLER, PIPELINE, EXTRACTOR, LINK_CHECKER, WORKBENCH_TEMPLATE, MEDIA_REGISTRY,
                 SKILL_ROOT / 'scripts' / 'scoped_assignment_patch.py'):
        check(f"asset:{path.name}", path.is_file(), str(path))
    try:
        registry = read_json(MEDIA_REGISTRY)
        check("media_registry_json", bool(registry), str(MEDIA_REGISTRY))
    except Exception as exc:
        check("media_registry_json", False, str(exc))

    source = args.source.resolve() if args.source else None
    workspace = args.workspace.resolve() if args.workspace else None
    output = args.output.resolve() if args.output else None
    if source:
        check("source_directory", source.is_dir(), str(source))
    if workspace:
        empty = not workspace.exists() or not any(workspace.iterdir())
        check("workspace_empty", empty, str(workspace))
        if source:
            separated = workspace != source and source not in workspace.parents and workspace not in source.parents
            check("workspace_source_separation", separated, f"source={source}; workspace={workspace}")
    if output:
        check("output_html", output.suffix.lower() == ".html", str(output))
        check("output_absent", not output.exists(), str(output))
        if source:
            check("output_outside_source", source not in output.parents, f"source={source}; output={output}")
        ancestor = output.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        check("output_parent_writable", ancestor.is_dir() and os.access(ancestor, os.W_OK), str(ancestor))
    if args.period_config:
        config_path = args.period_config.resolve()
        check("period_config_exists", config_path.is_file(), str(config_path))
        if config_path.is_file():
            try:
                config = read_json(config_path)
                legacy = sorted(LEGACY_CONFIG_KEYS & set(config))
                check("period_config_no_legacy_budget", not legacy, ", ".join(legacy) if legacy else "PASS")
                batches = config.get("batch_order")
                targets = config.get("targets")
                structure_ok = isinstance(batches, list) and bool(batches) and isinstance(targets, dict) and all(batch in targets for batch in batches)
                check("period_config_structure", structure_ok, "batch_order 与 targets")
            except Exception as exc:
                check("period_config_json", False, str(exc))

    failures = [item for item in checks if not item["passed"]]
    payload = {
        "status": "PASS" if not failures else "FAIL",
        "stage": "doctor",
        "python": sys.executable,
        "checks": checks,
        "failure_count": len(failures),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


def command_merge(args: argparse.Namespace) -> None:
    inputs = [path.resolve() for path in args.inputs]
    output = args.output.resolve()
    backup_dir = args.backup_dir.resolve() if args.backup_dir else output.parent / "backups"
    report = args.report.resolve() if args.report else output.with_suffix(output.suffix + ".merge-verification.json")
    if len(inputs) < 2:
        raise ValueError("累计工作台合并至少需要两个输入：现有累计工作台在前，新单期工作台在后")
    if any(not path.is_file() for path in inputs):
        missing = [str(path) for path in inputs if not path.is_file()]
        raise FileNotFoundError(f"合并输入不存在：{missing}")
    if any(path.suffix.lower() != ".html" for path in inputs) or output.suffix.lower() != ".html":
        raise ValueError("合并输入和输出都必须是 .html 文件")
    if report.suffix.lower() != ".json":
        raise ValueError("合并校验报告必须是 .json 文件")
    lock_path = output.with_name(f".{output.name}.merge.lock")
    with kernel_file_lock(lock_path, "该累计工作台已有合并任务运行，禁止并发覆盖"):
        command = [
            sys.executable, str(PIPELINE), "merge", "--inputs", *[str(path) for path in inputs],
            "--output", str(output), "--backup-dir", str(backup_dir), "--report", str(report),
        ]
        result = run_command(command)
        payload = read_json(report)
        if clean(payload.get("status")) != "PASS":
            raise RuntimeError(f"累计工作台合并校验未通过：{report}")
        print(json.dumps({
            "status": "COMPLETE",
            "stage": "merge",
            "output": str(output),
            "report": str(report),
            "verification": payload,
            "command": result["command"],
        }, ensure_ascii=False, indent=2))


def discovery_readable_views(run_dir: Path, canonical_files: list[Path], max_units: int = 16000) -> list[dict]:
    """Lossless, bounded reading copies; canonical discovery/binding stay intact."""
    import native_review
    if max_units < 1600:
        raise ValueError("Discovery reading budget is too small")
    folder = run_dir / "discovery_reading"
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.resolve().is_relative_to(run_dir.resolve()):
        raise ValueError("Discovery reading folder must remain inside run")
    views = []
    def publish(rows):
        encoded = native_review.readable_json_text(rows).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        destination = folder / f"{digest}.json"
        if not destination.resolve().is_relative_to(run_dir.resolve()):
            raise ValueError("Discovery reading file must remain inside run")
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(encoded)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != encoded:
                    raise ValueError("Existing discovery reading file differs from canonical input")
        finally:
            temporary.unlink(missing_ok=True)
        views.append({"path": str(destination), "sha256": digest, "items": len(rows),
                      "read_all_lines": encoded.count(b"\n"),
                      "oversized_single_item": native_review.json_display_units(encoded.decode("utf-8")) > max_units})
    pending = []
    for canonical in canonical_files:
        for row in read_jsonl(canonical):
            proposed = pending + [row]
            if pending and native_review.json_display_units(native_review.readable_json_text(proposed)) > max_units:
                publish(pending)
                pending = []
            pending.append(row)
    if pending:
        publish(pending)
    return views


def command_lookup(args: argparse.Namespace) -> None:
    """Read canonical original text without changing any workflow state."""
    path = args.workspace.resolve() / "run" / "normalized_sources.jsonl"
    requested = list(dict.fromkeys(args.source_id))
    pending = set(requested)
    found = {}
    if path.exists():
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                source_id = clean(row.get("id"))
                if source_id in pending:
                    found[source_id] = {"source_id": source_id, "source_text": pipeline_api().source_text(row)}
                    pending.remove(source_id)
                    if not pending:
                        break
    payload = {
        "status": "FOUND" if not pending else "NOT_FOUND",
        "position_basis": "source_text; zero-based [start, end), no truncation",
        "sources": [found[source_id] for source_id in requested if source_id in found],
        "missing_source_ids": [source_id for source_id in requested if source_id in pending],
    }
    if getattr(args, "as_file", False):
        import native_review
        # Keep original Unicode out of shell decoding; the host reads this UTF-8 file.
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        workspace = args.workspace.resolve()
        output = workspace / "run" / "lookup_contexts" / f"{digest}.json"
        if not output.resolve().is_relative_to(workspace):
            raise ValueError("Lookup context must remain inside its workspace")
        output.parent.mkdir(parents=True, exist_ok=True)
        readable_bytes = native_review.readable_json_text(payload).encode('utf-8')
        # Address the reading view by its own bytes, so a new lossless layout
        # does not overwrite (or conflict with) an older immutable view.
        readable = output.parent / f"{hashlib.sha256(readable_bytes).hexdigest()}.readable.json"
        for destination, expected in ((output, encoded), (readable, readable_bytes)):
            if not destination.resolve().is_relative_to(workspace):
                raise ValueError("Lookup context must remain inside its workspace")
            temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(expected)
                try:
                    # Publish a complete file without overwriting an existing one.
                    os.link(temporary, destination)
                except FileExistsError:
                    if destination.read_bytes() != expected:
                        raise ValueError("Existing lookup context does not match canonical source text")
            finally:
                temporary.unlink(missing_ok=True)
        print(json.dumps({"status": payload["status"], "source_file": str(output),
                          "readable_source_file": str(readable),
                          "reading_instruction": native_review.READABLE_CONTEXT_RULE,
                          "read_with": "host file-reading tool; UTF-8",
                          "source_count": len(payload["sources"]),
                          "missing_source_ids": payload["missing_source_ids"]}, ensure_ascii=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Stateful controller for the film-brief-cleaning Skill")
    commands = root.add_subparsers(dest="command", required=True)
    title_apply = commands.add_parser('definition-title-repair-apply', help='Merge independent explicit title patches once under the workflow lock; does not approve semantics')
    title_apply.add_argument('--workspace', required=True, type=Path)
    title_apply.add_argument('--plan-file', required=True, type=Path)
    title_apply.set_defaults(func=command_title_format_repair)
    patch_prepare = commands.add_parser('assignment-patch-prepare', help='Prepare a guarded semantic-only scoped reassignment patch; does not approve routes')
    patch_prepare.add_argument('--workspace', required=True, type=Path)
    patch_prepare.add_argument('--source-id', nargs='+', action='extend', default=None,
                               help='Cluster-set review only: explicitly identified current member IDs; final repair remains controller-scoped')
    patch_prepare.set_defaults(func=command_assignment_patch)
    patch_apply = commands.add_parser('assignment-patch-apply', help='Validate and merge one explicit scoped patch under the workflow lock')
    patch_apply.add_argument('--workspace', required=True, type=Path)
    patch_apply.add_argument('--plan-file', required=True, type=Path)
    patch_apply.set_defaults(func=command_assignment_patch)
    dispatch = commands.add_parser("review-dispatch", help="Prepare bounded packets for the current host session's native agents; does not launch models")
    dispatch.add_argument("--workspace", required=True, type=Path)
    dispatch.add_argument("--workers", type=int, default=16)
    dispatch.add_argument("--as-file", action="store_true", help="Return an ASCII JSON locator for a guarded complete UTF-8 host handoff; avoids shell recoding")
    dispatch.add_argument("--per-worker", type=int, default=None,
                          help="Optional fixed cap; default source/assignment/final packets adapt within 30-40/12-14/30-35 items to absorb small tails")
    dispatch.set_defaults(func=command_native_review)
    collect = commands.add_parser("review-collect", help="Collect complete native-agent packets for normal controller validation")
    collect.add_argument("--workspace", required=True, type=Path)
    collect.add_argument("--dispatch-file", required=True, type=Path)
    collect.add_argument("--as-file", action="store_true", help="Return an ASCII locator for complete immutable UTF-8 collect feedback; avoids shell recoding")
    collect.set_defaults(func=command_native_review)
    review_status = commands.add_parser("review-status", help="Read native packet response counts without approving or merging answers")
    review_status.add_argument("--workspace", required=True, type=Path)
    review_status.add_argument("--dispatch-file", required=True, type=Path)
    review_status.add_argument("--details", action="store_true", help="Show full pending source IDs; default prints compact counts")
    review_status.set_defaults(func=command_native_review)
    review_wait = commands.add_parser("review-wait", help="Wait up to 45 seconds for native response files; read-only, not task completion or acceptance")
    review_wait.add_argument("--workspace", required=True, type=Path)
    review_wait.add_argument("--dispatch-file", required=True, type=Path)
    review_wait.add_argument("--timeout-seconds", type=float, default=45)
    review_wait.add_argument("--details", action="store_true", help="Show full pending source IDs; default prints compact counts")
    review_wait.set_defaults(func=command_native_review)
    doctor = commands.add_parser("doctor", help="Check runtime, skill assets, paths, and optional period config before production")
    doctor.add_argument("--source", type=Path)
    doctor.add_argument("--period-config", type=Path)
    doctor.add_argument("--workspace", type=Path)
    doctor.add_argument("--output", type=Path)
    doctor.set_defaults(func=command_doctor)
    init = commands.add_parser("init", help="Extract raw workbooks and create an isolated workflow workspace")
    init.add_argument("--source", required=True, type=Path)
    init.add_argument("--workspace", required=True, type=Path)
    init.add_argument("--output", required=True, type=Path)
    init.add_argument("--period-config", type=Path)
    init.add_argument("--max-steps", type=int, default=20)
    init.add_argument("--workers", type=int, default=16)
    init.add_argument("--timeout", type=float, default=10.0)
    init.set_defaults(func=command_init)
    status = commands.add_parser("status", help="Print the current gate and the exact required next artifact")
    status.add_argument("--workspace", required=True, type=Path)
    status.set_defaults(func=command_status)
    lookup = commands.add_parser("lookup", help="Read full canonical source text by ID without changing workflow state")
    lookup.add_argument("--workspace", required=True, type=Path)
    lookup.add_argument("--source-id", required=True, nargs="+", action="extend")
    lookup.add_argument("--as-file", action="store_true", help="Return a UTF-8 evidence file for host Read, avoiding shell Unicode decoding; workflow state is unchanged")
    lookup.set_defaults(func=command_lookup)
    advance = commands.add_parser("advance", help="Run deterministic stages in order until AI review is required or verification passes")
    advance.add_argument("--workspace", required=True, type=Path)
    advance.add_argument("--max-steps", type=int, default=20)
    advance.add_argument("--workers", type=int, default=16)
    advance.add_argument("--timeout", type=float, default=10.0)
    advance.set_defaults(func=command_advance)
    merge = commands.add_parser("merge", help="Merge verified standalone workbenches into one cumulative workbench")
    merge.add_argument("--inputs", required=True, nargs="+", type=Path)
    merge.add_argument("--output", required=True, type=Path)
    merge.add_argument("--backup-dir", type=Path)
    merge.add_argument("--report", type=Path)
    merge.set_defaults(func=command_merge)
    return root


def command_native_review(args: argparse.Namespace) -> None:
    import native_review
    workspace = args.workspace.resolve()
    if args.command == "review-wait":
        # No workflow lock is held while waiting; this path is read-only and
        # the same guards detect any concurrent input change.
        result = native_review.wait_for_responses(sys.modules[__name__], workspace,
                                                 args.dispatch_file.resolve(), args.timeout_seconds)
        if not getattr(args, 'details', False):
            result = native_review.progress_summary(sys.modules[__name__], workspace, args.dispatch_file.resolve(), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    with workflow_lock(workspace):
        api = sys.modules[__name__]
        if args.command == "review-dispatch":
            result = native_review.dispatch(api, workspace, args.workers, args.per_worker)
            if getattr(args, 'as_file', False):
                result = native_review.write_handoff_file(api, workspace, result)
        elif args.command == "review-status":
            result = native_review.progress(api, workspace, args.dispatch_file.resolve())
            if not getattr(args, 'details', False):
                result = native_review.progress_summary(api, workspace, args.dispatch_file.resolve(), result)
        else:
            result = native_review.collect(api, workspace, args.dispatch_file.resolve())
            if getattr(args, 'as_file', False):
                result = native_review.write_collect_result_file(api, workspace, args.dispatch_file.resolve(), result)
        print(json.dumps(result, ensure_ascii=args.command in {'review-dispatch', 'review-collect'} and getattr(args, 'as_file', False), indent=2))


def command_title_format_repair(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    with workflow_lock(workspace):
        try:
            result = apply_title_format_repair(workspace, args.plan_file)
        except ValueError as exc:
            result = {'status': 'INVALID_REPAIR_CONTEXT', 'error': str(exc), 'parent_not_modified': True}
        print(json.dumps(result, ensure_ascii=False, indent=2))


def command_assignment_patch(args: argparse.Namespace) -> None:
    import scoped_assignment_patch
    workspace = args.workspace.resolve()
    with workflow_lock(workspace):
        api = sys.modules[__name__]
        result = (scoped_assignment_patch.prepare(api, workspace, args.source_id) if args.command == 'assignment-patch-prepare'
                  else scoped_assignment_patch.apply(api, workspace, args.plan_file.resolve()))
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        payload = {"status": "BROKEN", "error": type(exc).__name__, "message": str(exc), "failed_at": now()}
        workspace = getattr(args, "workspace", None)
        if isinstance(workspace, Path) and args.command not in {"lookup", "review-dispatch", "review-collect", "review-status", "review-wait"} and not isinstance(exc, (WorkflowBusy, FileExistsError)):
            try:
                write_json(workspace.resolve() / STATUS_NAME, payload)
            except OSError:
                pass
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
