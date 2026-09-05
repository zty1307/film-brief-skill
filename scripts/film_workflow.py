from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
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


REVIEW_FILES = {
    "source": "source_reviews.json",
    "dedup": "dedup_reviews.json",
    "clusters": "cluster_definitions.json",
    "overrides": "cluster_overrides.json",
    "set": "cluster_set_reviews.json",
    "members": "cluster_member_semantic_reviews.json",
    "excerpts": "excerpt_reviews.json",
    "semantic": "final_excerpt_semantic_reviews.json",
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

STAGE_GUIDANCE = {
    "period_config": [
        "逐批次确认 serial_drama 或 episodic_variety，并填写真实监测时间窗",
        "strong_terms 只放能独立确认作品的全名或已核验唯一简称；演员、嘉宾、角色放 auxiliary_terms",
        "同期对比作品放 comparison_terms；不得复制旧期聚类、旧报告点位或样本数量预算",
    ],
    "source_review": [
        "逐条阅读队列中的完整标题、正文、ASR及必要父帖上下文，不凭标题或关键词单独判断",
        "保留必须同时有目标作品、可报告判断和具体依据；用连续逐字证据或候选字符位置",
        "相同观点的独立作者均可保留；综艺还须核对最新一期、前一期当周突出话题或节目级讨论",
    ],
    "dedup_review": [
        "只有转载、同稿跨平台分发、洗稿或共享明确稿件骨架才标 same_copy",
        "不同作者独立表达相近观点标 independent，不能因为观点相似而删除",
    ],
    "cluster_discovery": [
        "只依据本期 retained_sources 从数据中归纳一级观点，不读取人工成品反推答案",
        "标题写成可直接理解的报告体判断句，并明确 positive、objective 或 negative",
        "按共同评价机制聚合，人物、角色、段子和单项指标通常作为证据侧面；9至16个仅为颗粒度复查范围",
    ],
    "cluster_assignment_review": [
        "逐条核对目标作品、评价方面、当前片段立场和连续逐字证据",
        "片段有效但归错簇时应移动、缩窄簇名或建立有数据支持的新簇；不得因初次归错直接删除",
        "同一来源进入第二簇时，两段原文必须互不重叠且分别形成完整观点",
    ],
    "cluster_set_review": [
        "拆开检查簇标题中的每项主张，确保全部成员至少支持其中一项且每项均有成员证据",
        "正面簇只含正面片段、负面簇只含负面片段，客观簇只含事实、均衡观察或舆情分布",
        "同时检查期次范围、来源角色、过度切碎和大口袋簇；禁止为进入9至16个而机械合并或删除样本",
    ],
    "cluster_member_review": [
        "必须由未参与本轮簇命名的第二个AI或清空前轮上下文后的独立轮次完成",
        "只判断最终簇名、最终立场与当前分配片段是否一致，并提供片段中的连续逐字证据",
        "只有人物名或题材词重合、没有表达同一判断时必须退回，不能勉强填写 pass",
    ],
    "excerpt_review": [
        "最多选择同一来源中按原顺序出现的两个逐字片段，不改写、不补字、不调换顺序",
        "优先形成70至150字的完整判断和具体依据；清除话题标签、表情、链接、账号标记及分享套话",
        "好看、封神、期待、笑点拉满等泛泛态度没有具体依据时不进入展示层",
    ],
    "final_excerpt_review": [
        "审核最终清洗后展示文字本身，分别核对目标、方面、立场、作品一致性和语义完整性",
        "片段可修复时先回看全文重截；归簇不当时先尝试重归簇或建立真实新簇，再考虑逐来源 drop",
        "不得用文章整体立场否定其中可独立成立的局部观点，也不得为了缩量删除合格独立表达",
    ],
    "render_repair": [
        "逐项读取 render_failures.json，回到对应来源修复摘录、归簇或复核字段",
        "不得删除失败队列、伪造通过字段或直接调用底层 render 绕过门槛",
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
        **{key: review_dir / name for key, name in REVIEW_FILES.items()},
    }


def run_command(command: list[str], cwd: Path | None = None, allow_review_stop: bool = False) -> dict:
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
    }
    if completed.returncode and not allow_review_stop:
        diagnostic = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        message = next((line.strip() for line in reversed(diagnostic.splitlines()) if line.strip()), diagnostic)
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
    return read_json(path) if path.exists() else None


def binding_issue(payload: dict | None, workflow_id: str, inputs: list[Path]) -> str:
    if payload is None:
        return "missing"
    meta = payload.get("_workflow")
    if not isinstance(meta, dict):
        return "missing_workflow_binding"
    if clean(meta.get("workflow_id")) != workflow_id:
        return "workflow_id_mismatch"
    if clean(meta.get("input_sha256")) != digest_paths(inputs):
        return "input_fingerprint_stale"
    return ""


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


def pair_key(left: object, right: object) -> str:
    return "\t".join(sorted((clean(left), clean(right))))


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
        "output_hashes": {str(path): sha256(path) for path in outputs},
    }
    if result.get("output_sha256"):
        stage_record["output_sha256"] = result["output_sha256"]
    manifest.setdefault("stages", {})[stage] = stage_record
    manifest.setdefault("history", []).append({
        "stage": stage,
        "returncode": result["returncode"],
        "finished_at": result["finished_at"],
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
    }

    def result(status: str, stage: str, message: str, **extra) -> dict:
        payload = {**base, "status": status, "stage": stage, "message": message, **extra}
        payload['reference'] = str(SKILL_ROOT / 'references' / 'contracts.md')
        if status == "REVIEW_REQUIRED" and stage in STAGE_GUIDANCE:
            payload["review_requirements"] = STAGE_GUIDANCE[stage]
        payload['next_command'] = [sys.executable, str(Path(__file__).resolve()), 'advance', '--workspace', str(workspace)]
        payload['source_text_file'] = str(p['run'] / 'normalized_sources.jsonl')
        write_json(p["workspace"] / STATUS_NAME, payload)
        return payload

    if not p["records"].exists():
        return result("BROKEN", "extract", "缺少 extract/records.jsonl，需重新 init")
    if sha256(p["records"]) != manifest.get("records_sha256"):
        return result("BLOCKED", "input_integrity", "抽取后的 records.jsonl 已变化；禁止继续复用旧评审，请新建工作区重跑")
    if not p["config"].exists():
        template = p["templates"] / "period_config.template.json"
        ensure_template(template, config_template(p["records"]))
        return result(
            "REVIEW_REQUIRED", "period_config", "请根据当前数据填写期次配置；不要把演员或角色名写入 strong_terms",
            required_file=str(p["config"]), template=str(template), reference=str(SKILL_ROOT / "references" / "contracts.md"),
        )

    prepare_token = digest_paths([p["records"], p["config"], PIPELINE, MEDIA_REGISTRY, CONTROLLER])
    if not stage_fresh(manifest, "prepare", prepare_token, [p["run"] / "prepare_summary.json", p["run"] / "source_review_queue.jsonl"]):
        return result("READY_TO_ADVANCE", "prepare", "配置已就绪，可以执行标准化与初筛", action="prepare", input_digest=prepare_token)

    source_queue = read_jsonl(p["run"] / "source_review_queue.jsonl")
    if source_queue or p['source'].exists():
        source_inputs = [p["run"] / "source_review_queue.jsonl", p['run'] / 'normalized_sources.jsonl', p['config']]
        source_payload = review_payload(p["source"])
        issue = binding_issue(source_payload, workflow_id, source_inputs)
        reviewed = list_review_map(source_payload, "source_id")
        required_ids = {clean(item.get("id")) for item in source_queue}
        missing = sorted(required_ids - set(reviewed))
        template = p["templates"] / "source_reviews.template.json"
        ensure_template(template, {
            "scope": "current_period_source_fulltext_reviews",
            "_workflow": binding(workflow_id, source_inputs),
            "reviews": [],
        })
        if issue or missing:
            return result(
                "REVIEW_REQUIRED", "source_review", "逐条阅读全文并覆盖全部来源语义队列",
                required_file=str(p["source"]), template=str(template), input_file=str(source_inputs[0]),
                binding_issue=issue, required=len(required_ids), completed=len(required_ids & set(reviewed)), missing=len(missing),
            )

    links_token = digest_paths([p["run"] / "normalized_sources.jsonl", LINK_CHECKER, CONTROLLER])
    link_health = p["run"] / "source_link_health.json"
    if not stage_fresh(manifest, "link_check", links_token, [link_health]):
        return result("READY_TO_ADVANCE", "link_check", "来源语义复核齐备，可以执行链接健康检查", action="link_check", input_digest=links_token)

    selection_inputs = [p["run"] / "normalized_sources.jsonl", p["run"] / "source_decisions.auto.jsonl", link_health, PIPELINE, CONTROLLER]
    if p['source'].exists():
        selection_inputs.append(p["source"])
    dedup_inputs = [p['run'] / 'normalized_sources.jsonl', p['run'] / 'source_decisions.auto.jsonl', p['config'], link_health]
    if p['source'].exists():
        dedup_inputs.append(p['source'])
    if p["dedup"].exists():
        issue = binding_issue(review_payload(p['dedup']), workflow_id, dedup_inputs)
        if issue:
            template = p['templates'] / 'dedup_reviews.template.json'
            ensure_template(template, {'scope': 'current_period_copy_reviews', '_workflow': binding(workflow_id, dedup_inputs), 'reviews': []})
            return result('REVIEW_REQUIRED', 'dedup_review', '去重评审输入已变化，请按新模板重新审核后提交', required_file=str(p['dedup']), template=str(template), binding_issue=issue, input_file=str(p['run'] / 'normalized_sources.jsonl'))
        selection_inputs.append(p["dedup"])
    selection_token = digest_paths(selection_inputs)
    if not stage_fresh(manifest, "select", selection_token, [
        p["run"] / "selection_summary.json",
        p["run"] / "retained_sources.jsonl",
        p["run"] / "dedup_audit.json",
        p["run"] / "source_link_health.applied.json",
    ]):
        return result("READY_TO_ADVANCE", "select", "可以执行来源筛选与同稿去重", action="select", input_digest=selection_token)

    selection_summary = read_json(p["run"] / "selection_summary.json")
    if int(selection_summary.get("unreviewed_source_queue", 0)):
        return result("BLOCKED", "source_review", "select 检出未完成来源复核；请修复 source_reviews.json 后重跑", unresolved=selection_summary["unreviewed_source_queue"])

    dedup_audit = read_json(p["run"] / "dedup_audit.json")
    candidates = dedup_audit.get("medium_similarity_candidates", [])
    dedup_payload = review_payload(p["dedup"])
    dedup_issue = binding_issue(dedup_payload, workflow_id, dedup_inputs) if candidates else ""
    dedup_reviews = {
        pair_key(item.get("left_id"), item.get("right_id")): item
        for item in (dedup_payload or {}).get("reviews", [])
        if isinstance(item, dict)
    }
    required_pairs = {pair_key(item.get("left_id"), item.get("right_id")) for item in candidates}
    pending_pairs = sorted(required_pairs - set(dedup_reviews))
    write_jsonl(p["run"] / "dedup_review_queue.unresolved.jsonl", [
        item for item in candidates if pair_key(item.get("left_id"), item.get("right_id")) in pending_pairs
    ])
    if candidates and (dedup_issue or pending_pairs):
        template = p["templates"] / "dedup_reviews.template.json"
        ensure_template(template, {
            "scope": "current_period_copy_reviews",
            "_workflow": binding(workflow_id, dedup_inputs),
            "reviews": [],
        })
        return result(
            "REVIEW_REQUIRED", "dedup_review", "复核中等相似候选；相近观点的独立作者仍须保留",
            required_file=str(p["dedup"]), template=str(template), input_file=str(p["run"] / "dedup_audit.json"),
            binding_issue=dedup_issue, required=len(required_pairs), completed=len(required_pairs & set(dedup_reviews)), missing=len(pending_pairs),
        )

    cluster_inputs = [p["run"] / "retained_sources.jsonl"]
    cluster_payload = review_payload(p["clusters"])
    cluster_issue = binding_issue(cluster_payload, workflow_id, cluster_inputs)
    if cluster_issue:
        batches = []
        for item in read_jsonl(p["run"] / "retained_sources.jsonl"):
            batch = clean(item.get("batch"))
            if batch and batch not in batches:
                batches.append(batch)
        template = p["templates"] / "cluster_definitions.template.json"
        ensure_template(template, {
            "scope": "current_period_data_derived_clusters",
            "_workflow": binding(workflow_id, cluster_inputs),
            "batches": {batch: [] for batch in batches},
        })
        return result(
            "REVIEW_REQUIRED", "cluster_discovery", "通读本期保留样本后从数据中归纳一级观点簇，并先标明正面、客观、负面",
            required_file=str(p["clusters"]), template=str(template), input_file=str(cluster_inputs[0]), binding_issue=cluster_issue,
        )

    override_inputs = [p["run"] / "retained_sources.jsonl", p["clusters"]]
    override_template = p['templates'] / 'cluster_overrides.template.json'
    ensure_template(override_template, {'scope': 'current_period_source_cluster_reviews', '_workflow': binding(workflow_id, override_inputs), 'overrides': {}})
    overrides_payload = review_payload(p["overrides"])
    if overrides_payload is not None:
        override_issue = binding_issue(overrides_payload, workflow_id, override_inputs)
        if override_issue:
            return result("REVIEW_REQUIRED", "cluster_assignment_review", "cluster_overrides.json 与当前保留池或簇定义不一致", required_file=str(p["overrides"]), template=str(override_template), input_files=[str(x) for x in override_inputs], binding_issue=override_issue)

    cluster_base_inputs = [p["run"] / "retained_sources.jsonl", p["clusters"], PIPELINE, CONTROLLER]
    if p["overrides"].exists():
        cluster_base_inputs.append(p["overrides"])
    cluster_base_token = digest_paths(cluster_base_inputs)
    if not stage_fresh(manifest, "cluster_base", cluster_base_token, [
        p["run"] / "cluster_set_review_input.json",
        p["run"] / "cluster_count_review_input.json",
        p["run"] / "cluster_review_queue.jsonl",
    ]):
        return result("READY_TO_ADVANCE", "cluster_base", "观点簇定义已就绪，可以生成首次归簇和复核队列", action="cluster_base", input_digest=cluster_base_token)

    low_queue = read_jsonl(p["run"] / "cluster_review_queue.jsonl")
    if low_queue:
        overrides = (overrides_payload or {}).get("overrides", {})
        override_ids = set(overrides) if isinstance(overrides, dict) else set()
        low_ids = {clean(item.get("source_id")) for item in low_queue}
        template = p["templates"] / "cluster_overrides.template.json"
        ensure_template(template, {
            "scope": "current_period_source_cluster_reviews",
            "_workflow": binding(workflow_id, override_inputs),
            "overrides": {},
        })
        return result(
            "REVIEW_REQUIRED", "cluster_assignment_review", "逐条修正低置信度归簇；对象、方面、立场和逐字证据必须同时对齐",
            required_file=str(p["overrides"]), template=str(template), input_file=str(p["run"] / "cluster_review_queue.jsonl"),
            required=len(low_ids), completed=len(low_ids & override_ids), missing=len(low_ids - override_ids),
        )

    set_inputs = [p["run"] / "cluster_set_review_input.json", p["run"] / "cluster_count_review_input.json"]
    set_payload = review_payload(p["set"])
    set_issue = binding_issue(set_payload, workflow_id, set_inputs)
    required_set = {exact_key(item.get("review_key")) for item in read_json(p["run"] / "cluster_set_review_input.json").get("clusters", [])}
    required_count = {exact_key(item.get("review_key")) for item in read_json(p["run"] / "cluster_count_review_input.json").get("batches", [])}
    set_done = {exact_key(key) for key in (set_payload or {}).get("reviews", {})} if isinstance((set_payload or {}).get("reviews"), dict) else set()
    count_done = {exact_key(key) for key in (set_payload or {}).get("count_reviews", {})} if isinstance((set_payload or {}).get("count_reviews"), dict) else set()
    if set_issue or required_set - set_done or required_count - count_done:
        template = p["templates"] / "cluster_set_reviews.template.json"
        ensure_template(template, {
            "scope": "current_period_cluster_set_reviews",
            "_workflow": binding(workflow_id, set_inputs),
            "reviews": {},
            "count_reviews": {},
        })
        return result(
            "REVIEW_REQUIRED", "cluster_set_review", "逐簇核对标题主张、成员证据、立场纯度和期次范围，并审查整期簇数",
            required_file=str(p["set"]), template=str(template), input_files=[str(item) for item in set_inputs], binding_issue=set_issue,
            clusters_required=len(required_set), clusters_missing=len(required_set - set_done), batches_required=len(required_count), batches_missing=len(required_count - count_done),
        )

    cluster_set_inputs = cluster_base_inputs + [p["set"]]
    cluster_set_token = digest_paths(cluster_set_inputs)
    if not stage_fresh(manifest, "cluster_set", cluster_set_token, [
        p["run"] / "cluster_member_semantic_review_input.json",
        p["run"] / "cluster_set_review_queue.unresolved.jsonl",
        p["run"] / "cluster_count_review_queue.unresolved.jsonl",
    ]):
        return result("READY_TO_ADVANCE", "cluster_set", "集合审查齐备，可以生成独立成员级语义复核输入", action="cluster_set", input_digest=cluster_set_token)

    set_unresolved = read_jsonl(p["run"] / "cluster_set_review_queue.unresolved.jsonl")
    count_unresolved = read_jsonl(p["run"] / "cluster_count_review_queue.unresolved.jsonl")
    if set_unresolved or count_unresolved:
        return result(
            "REVIEW_REQUIRED", "cluster_set_review", "集合审查未通过当前指纹或字段校验；按 unresolved 队列修复",
            required_file=str(p["set"]), cluster_unresolved=len(set_unresolved), count_unresolved=len(count_unresolved),
        )

    member_inputs = [p["run"] / "cluster_member_semantic_review_input.json"]
    member_payload = review_payload(p["members"])
    member_issue = binding_issue(member_payload, workflow_id, member_inputs)
    member_required = {exact_key(item.get("review_key")) for item in read_json(member_inputs[0]).get("members", [])}
    member_done = {exact_key(key) for key in (member_payload or {}).get("reviews", {})} if isinstance((member_payload or {}).get("reviews"), dict) else set()
    if member_issue or member_required - member_done:
        template = p["templates"] / "cluster_member_semantic_reviews.template.json"
        ensure_template(template, {
            "scope": "current_period_cluster_member_semantic_reviews",
            "_workflow": binding(workflow_id, member_inputs),
            "reviews": {},
        })
        return result(
            "REVIEW_REQUIRED", "cluster_member_review", "由未参与簇命名的第二个 AI 或清空上下文后的独立轮次逐成员复核",
            required_file=str(p["members"]), template=str(template), input_file=str(member_inputs[0]), binding_issue=member_issue,
            required=len(member_required), completed=len(member_required & member_done), missing=len(member_required - member_done), independent_pass_required=True,
        )

    cluster_final_inputs = cluster_set_inputs + [p["members"]]
    cluster_final_token = digest_paths(cluster_final_inputs)
    if not stage_fresh(manifest, "cluster_final", cluster_final_token, [
        p["run"] / "cluster_summary.json",
        p["run"] / "clustered_items.json",
        p["run"] / "cluster_review_queue.jsonl",
        p["run"] / "cluster_set_review_queue.unresolved.jsonl",
        p["run"] / "cluster_count_review_queue.unresolved.jsonl",
        p["run"] / "cluster_member_semantic_review_queue.unresolved.jsonl",
        p["run"] / "workbench_order_audit.json",
    ]):
        return result("READY_TO_ADVANCE", "cluster_final", "成员复核齐备，可以固化最终归簇", action="cluster_final", input_digest=cluster_final_token)

    unresolved = {
        "assignment": len(read_jsonl(p["run"] / "cluster_review_queue.jsonl")),
        "cluster_set": len(read_jsonl(p["run"] / "cluster_set_review_queue.unresolved.jsonl")),
        "cluster_count": len(read_jsonl(p["run"] / "cluster_count_review_queue.unresolved.jsonl")),
        "cluster_members": len(read_jsonl(p["run"] / "cluster_member_semantic_review_queue.unresolved.jsonl")),
    }
    if any(unresolved.values()):
        return result("BLOCKED", "cluster_final", "最终归簇仍有未决项，禁止进入页面生成", unresolved=unresolved)

    render_probe_inputs = [p["run"] / "clustered_items.json", WORKBENCH_TEMPLATE, PIPELINE, CONTROLLER]
    excerpt_template = p['templates'] / 'excerpt_reviews.template.json'
    ensure_template(excerpt_template, {'scope': 'current_period_verbatim_excerpt_reviews', '_workflow': binding(workflow_id, [p['run'] / 'clustered_items.json']), 'reviews': {}})
    if p["excerpts"].exists():
        excerpt_payload = review_payload(p["excerpts"])
        excerpt_issue = binding_issue(excerpt_payload, workflow_id, [p["run"] / "clustered_items.json"])
        if excerpt_issue:
            return result("REVIEW_REQUIRED", "excerpt_review", "excerpt_reviews.json 与当前最终归簇不一致", required_file=str(p["excerpts"]), template=str(excerpt_template), input_file=str(p['run'] / 'clustered_items.json'), binding_issue=excerpt_issue)
        render_probe_inputs.append(p["excerpts"])
    render_probe_token = digest_paths(render_probe_inputs)
    if not stage_fresh(manifest, "render_probe", render_probe_token, [p["run"] / "excerpt_semantic_review_input.jsonl"]):
        return result("READY_TO_ADVANCE", "render_probe", "可以生成候选摘录与最终摘录语义复核输入；这一步不会发布 HTML", action="render_probe", input_digest=render_probe_token)

    semantic_inputs = [p["run"] / "excerpt_semantic_review_input.jsonl"]
    semantic_payload = review_payload(p["semantic"])
    semantic_issue = binding_issue(semantic_payload, workflow_id, semantic_inputs)
    required_views = {clean(item.get("view_id")) for item in read_jsonl(semantic_inputs[0])}
    semantic_done = set(list_review_map(semantic_payload, "view_id"))
    if semantic_issue or required_views - semantic_done:
        template = p["templates"] / "final_excerpt_semantic_reviews.template.json"
        ensure_template(template, {
            "scope": "current_period_final_excerpt_semantic_reviews",
            "_workflow": binding(workflow_id, semantic_inputs),
            "reviews": {},
        })
        excerpt_template = p["templates"] / "excerpt_reviews.template.json"
        ensure_template(excerpt_template, {
            "scope": "current_period_verbatim_excerpt_reviews",
            "_workflow": binding(workflow_id, [p["run"] / "clustered_items.json"]),
            "reviews": {},
        })
        return result(
            "REVIEW_REQUIRED", "final_excerpt_review", "逐条审核最终展示摘录；先重截可修复的片段，再对最终文字做独立语义复核",
            required_file=str(p["semantic"]), template=str(template), optional_excerpt_file=str(p["excerpts"]), optional_excerpt_template=str(excerpt_template),
            input_file=str(semantic_inputs[0]), binding_issue=semantic_issue,
            required=len(required_views), completed=len(required_views & semantic_done), missing=len(required_views - semantic_done),
        )

    render_final_inputs = render_probe_inputs + [p["semantic"]]
    render_final_token = digest_paths(render_final_inputs)
    if not stage_fresh(manifest, "render_final", render_final_token, [p["run"] / "render_summary.json", p["output"]]):
        previous_render = manifest.get("stages", {}).get("render_final", {})
        previous_output_sha = clean(previous_render.get("output_sha256"))
        if p["output"].exists() and not previous_output_sha:
            return result(
                "BLOCKED", "output_collision",
                "目标 HTML 在本工作流首次发布前已存在；禁止覆盖，请更换输出路径或人工确认后移走该文件",
                existing_output=str(p["output"]),
            )
        if p["output"].exists() and previous_output_sha and sha256(p["output"]) != previous_output_sha:
            return result(
                "BLOCKED", "output_modified",
                "目标 HTML 在本工作流生成后被外部修改；禁止自动覆盖，请另存修改稿或换新输出路径",
                existing_output=str(p["output"]),
            )
        return result("READY_TO_ADVANCE", "render_final", "最终摘录复核齐备，可以生成独立工作台", action="render_final", input_digest=render_final_token)

    render_summary = read_json(p["run"] / "render_summary.json")
    if clean(render_summary.get("status")) != "RENDERED" or not p["output"].exists():
        failures = read_json(p["run"] / "render_failures.json").get("failures", []) if (p["run"] / "render_failures.json").exists() else []
        return result(
            "REVIEW_REQUIRED", "render_repair", "页面未通过最终生成门槛；按失败项重截、重归簇或更新复核后再次 advance",
            render_summary=render_summary, failures_file=str(p["run"] / "render_failures.json"), failure_count=len(failures),
        )

    verify_inputs = [p["output"], p["run"] / "workbench_dataset.json", p["run"] / "excerpt_provenance.json", PIPELINE, CONTROLLER]
    verify_token = digest_paths(verify_inputs)
    if not stage_fresh(manifest, "verify", verify_token, [p["run"] / "verification.json"]):
        return result("READY_TO_ADVANCE", "verify", "工作台已生成，可以执行发布验收", action="verify", input_digest=verify_token)

    verification = read_json(p["run"] / "verification.json")
    if clean(verification.get("status")) != "PASS":
        return result("BLOCKED", "verify", "发布验收失败，禁止交付", verification=verification)
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
        else:
            ensure_template(workspace / "review_templates" / "period_config.template.json", config_template(records))
        save_manifest(workspace, manifest)
        advance_loop(workspace, args.max_steps, args.workers, args.timeout)


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
    elif action == "link_check":
        command = [sys.executable, str(LINK_CHECKER), "--input", str(p["run"] / "normalized_sources.jsonl"), "--output", str(p["run"] / "source_link_health.json"), "--workers", str(workers), "--timeout", str(timeout)]
        result = run_command(command)
        outputs = [p["run"] / "source_link_health.json"]
    elif action == "select":
        command = [sys.executable, str(PIPELINE), "select", "--run", str(p["run"]), "--link-health", str(p["run"] / "source_link_health.json")]
        if p["source"].exists():
            command += ["--reviews", str(p["source"])]
        if p["dedup"].exists():
            command += ["--dedup-reviews", str(p["dedup"])]
        result = run_command(command)
        outputs = [
            p["run"] / "selection_summary.json",
            p["run"] / "retained_sources.jsonl",
            p["run"] / "dedup_audit.json",
            p["run"] / "source_link_health.applied.json",
        ]
    elif action in {"cluster_base", "cluster_set", "cluster_final"}:
        command = [sys.executable, str(PIPELINE), "cluster", "--run", str(p["run"]), "--clusters", str(p["clusters"])]
        if p["overrides"].exists():
            command += ["--overrides", str(p["overrides"])]
        if action in {"cluster_set", "cluster_final"}:
            command += ["--set-reviews", str(p["set"])]
        if action == "cluster_final":
            command += ["--member-reviews", str(p["members"])]
        result = run_command(command)
        if action == "cluster_base":
            outputs = [
                p["run"] / "cluster_set_review_input.json",
                p["run"] / "cluster_count_review_input.json",
                p["run"] / "cluster_review_queue.jsonl",
            ]
        elif action == "cluster_set":
            outputs = [
                p["run"] / "cluster_member_semantic_review_input.json",
                p["run"] / "cluster_set_review_queue.unresolved.jsonl",
                p["run"] / "cluster_count_review_queue.unresolved.jsonl",
            ]
        else:
            outputs = [
                p["run"] / "cluster_summary.json",
                p["run"] / "clustered_items.json",
                p["run"] / "cluster_review_queue.jsonl",
                p["run"] / "cluster_set_review_queue.unresolved.jsonl",
                p["run"] / "cluster_count_review_queue.unresolved.jsonl",
                p["run"] / "cluster_member_semantic_review_queue.unresolved.jsonl",
                p["run"] / "workbench_order_audit.json",
            ]
    elif action in {"render_probe", "render_final"}:
        command = [sys.executable, str(PIPELINE), "render", "--run", str(p["run"]), "--output", str(p["output"])]
        if p["excerpts"].exists():
            command += ["--excerpt-reviews", str(p["excerpts"])]
        if action == "render_final":
            command += ["--semantic-reviews", str(p["semantic"])]
        result = run_command(command, allow_review_stop=(action == "render_probe"))
        if action == "render_final" and p["output"].exists():
            result["output_sha256"] = sha256(p["output"])
        outputs = [p["run"] / "excerpt_semantic_review_input.jsonl"]
        if action == "render_final":
            outputs = [p["run"] / "render_summary.json", p["output"]]
    elif action == "verify":
        command = [sys.executable, str(PIPELINE), "verify", "--run", str(p["run"]), "--output", str(p["output"])]
        result = run_command(command)
        outputs = [p["run"] / "verification.json"]
    else:
        raise ValueError(f"未知 action：{action}")
    record_stage(workspace, manifest, action, token, result, outputs)


def command_status(args: argparse.Namespace) -> None:
    workspace = args.workspace.resolve()
    with workflow_lock(workspace):
        manifest = load_manifest(workspace)
        print(json.dumps(status_payload(workspace, manifest), ensure_ascii=False, indent=2))


def advance_loop(workspace: Path, max_steps: int, workers: int, timeout: float) -> None:
    manifest = load_manifest(workspace)
    for _ in range(max(1, max_steps)):
        state = status_payload(workspace, manifest)
        if state.get("status") != "READY_TO_ADVANCE":
            print(json.dumps(state, ensure_ascii=False, indent=2))
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
    for path in (CONTROLLER, PIPELINE, EXTRACTOR, LINK_CHECKER, WORKBENCH_TEMPLATE, MEDIA_REGISTRY):
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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Stateful controller for the film-brief-cleaning Skill")
    commands = root.add_subparsers(dest="command", required=True)
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
    init.add_argument("--workers", type=int, default=8)
    init.add_argument("--timeout", type=float, default=10.0)
    init.set_defaults(func=command_init)
    status = commands.add_parser("status", help="Print the current gate and the exact required next artifact")
    status.add_argument("--workspace", required=True, type=Path)
    status.set_defaults(func=command_status)
    advance = commands.add_parser("advance", help="Run deterministic stages in order until AI review is required or verification passes")
    advance.add_argument("--workspace", required=True, type=Path)
    advance.add_argument("--max-steps", type=int, default=20)
    advance.add_argument("--workers", type=int, default=8)
    advance.add_argument("--timeout", type=float, default=10.0)
    advance.set_defaults(func=command_advance)
    merge = commands.add_parser("merge", help="Merge verified standalone workbenches into one cumulative workbench")
    merge.add_argument("--inputs", required=True, nargs="+", type=Path)
    merge.add_argument("--output", required=True, type=Path)
    merge.add_argument("--backup-dir", type=Path)
    merge.add_argument("--report", type=Path)
    merge.set_defaults(func=command_merge)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        payload = {"status": "BROKEN", "error": type(exc).__name__, "message": str(exc), "failed_at": now()}
        workspace = getattr(args, "workspace", None)
        if isinstance(workspace, Path) and not isinstance(exc, (WorkflowBusy, FileExistsError)):
            try:
                write_json(workspace.resolve() / STATUS_NAME, payload)
            except OSError:
                pass
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
