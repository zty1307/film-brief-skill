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
        "retain 与 exclude 都必须填写具体理由；优先填写 evidence_candidate_index 选择脚本候选，候选均不适用时再提供逐字 evidence 或 evidence_position",
        "证据格式错误时只修证据字段，不得为了通过校验把 retain 改成 exclude",
        "相同观点的独立作者均可保留；综艺还须核对最新一期、前一期当周突出话题或节目级讨论",
    ],
    "cluster_discovery": [
        "按顺序通读 cluster_discovery_chunks 中的分层抽样逐字片段；仅在片段语义不清时按 source_id 回查 retained_sources 全文",
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
        "当前大量来源没有任何观点簇能够承接，先从缺口样本归纳遗漏观点或补充同义方面词",
        "修改 cluster_definitions.json 后重新 advance；不要为几十条缺口逐条写 override",
        "只增加数据中反复出现或虽少量但具有独立报告价值的观点，不建其他综合或人物姓名簇",
    ],
    "cluster_set_review": [
        "拆开检查簇标题中的每项主张，确保全部成员至少支持其中一项且每项均有成员证据",
        "正面簇只含正面片段、负面簇只含负面片段，客观簇只含事实、均衡观察或舆情分布",
        "同时检查期次范围、来源角色、过度切碎和大口袋簇；禁止为进入9至16个而机械合并或删除样本",
    ],
    "excerpt_review": [
        "最多选择同一来源中按原顺序出现的两个逐字片段，不改写、不补字、不调换顺序",
        "优先形成70至150字的完整判断和具体依据；清除话题标签、表情、链接、账号标记及分享套话",
        "好看、封神、期待、笑点拉满等泛泛态度没有具体依据时不进入展示层",
    ],
    "final_excerpt_review": [
        "审核最终清洗后展示文字本身，常规项只核对方面、局部立场和语义完整性；对象与跨作品字段仅在输入明确标记时填写",
        "片段可修复时先回看全文重截；归簇不当时先尝试重归簇或建立真实新簇，再考虑逐来源 drop",
        "不得用文章整体立场否定其中可独立成立的局部观点，也不得为了缩量删除合格独立表达",
    ],
    "render_repair": [
        "逐项读取 render_failures.json，回到对应来源修复摘录、归簇或复核字段",
        "不得删除失败队列、伪造通过字段或直接调用底层 render 绕过门槛",
    ],
    "post_excerpt_count_review": [
        "只审核终审 drop 之后实际仍有展示样本的一级观点簇数量和颗粒度",
        "不要回改终审前 cluster_count，也不要为进入9至16机械合并、拆分或补造观点",
        "按输入指纹填写实际簇数；越界时如实批准并说明继续调整会损害哪项语义质量",
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


def next_incomplete_chunk(paths: list[Path], key_field: str, missing_ids: set[str]) -> tuple[Path | None, int, int]:
    """Return only the next chunk a weak model needs to read."""
    for index, path in enumerate(paths, start=1):
        ids = {clean(item.get(key_field)) for item in read_jsonl(path)}
        if ids & missing_ids:
            return path, index, len(paths)
    return (paths[0] if paths else None), 1 if paths else 0, len(paths)


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


def source_review_template_payload(workflow_id: str, inputs: list[Path], queue: list[dict]) -> dict:
    reviews = []
    for item in queue:
        review = {
            "source_id": clean(item.get("id")),
            "decision": "",
            "reason": "",
            # Candidate 1 is already a verbatim, source-local passage.  It is a
            # safe default that ordinary models may replace when another
            # candidate better supports their semantic decision.
            "evidence_candidate_index": 1 if item.get("review_evidence_candidates") else None,
        }
        if clean(item.get("episode_review_instruction")):
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
            "required_for_every_decision": ["source_id", "decision", "reason", "evidence_candidate_index_or_exact_evidence"],
            "evidence_candidate_index": "candidate 1 is prefilled when available; keep it only if it supports the decision, otherwise choose another candidate_index",
            "exact_evidence_fallback": "use evidence or evidence_position only when no generated candidate supports the decision",
            "exact_copy_group": "review the representative once; select propagates the result to copy_group_source_ids",
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
        and bool(clean(item.get("reason")))
        and evidence_ready
    )


def final_excerpt_review_template_payload(
    workflow_id: str, inputs: list[Path], review_items: list[dict]
) -> dict:
    reviews = {}
    for item in review_items:
        view_id = clean(item.get("view_id"))
        if not view_id:
            continue
        reviews[view_id] = {
            "decision": "",
            "reason": "",
            "aspect_evidence": "",
            "stance": "",
            "stance_evidence": "",
            "self_contained": None,
        }
        if item.get("target_review_required"):
            reviews[view_id].update({"target_passed": None, "target_evidence": ""})
        if item.get("work_consistency_review_required"):
            reviews[view_id].update({"work_consistency_passed": None, "work_consistency_evidence": ""})
        if item.get("short_excerpt"):
            reviews[view_id].update({
                "short_excerpt_justified": None,
                "short_excerpt_reason": "",
                "specific_support_evidence": "",
            })
        if item.get("promotion_markers"):
            reviews[view_id].update({"independent_opinion_passed": None, "opinion_evidence": ""})
    return {
        "scope": "current_period_final_excerpt_semantic_reviews",
        "_workflow": binding(workflow_id, inputs),
        "contract": {
            "schema_version": 3,
            "instruction": "只填写 workflow_status.input_file 中的当前分片；控制器会自动累计此前分片。常规 keep 只需 decision、reason、stance、aspect_evidence、stance_evidence、self_contained",
            "full_source_lookup": "仅对需要重截、转簇或核对跨作品的项目按 source_id 回查 retained_sources.jsonl",
            "evidence_scopes_are_in_contract": True,
            "aspect_evidence": "cleaned_excerpt",
            "stance_evidence": "cleaned_excerpt",
            "specific_support_evidence": "cleaned_excerpt",
            "script_owned": ["target when target_review_required=false", "work consistency when work_consistency_review_required=false", "length", "markup", "verbatim positions"],
            "drop_fields": ["failed_checks", "reexcerpt_attempted", "reassignment_attempted when aspect or stance fails", "reassignment_reason", "conflict_evidence when work consistency fails"],
        },
        "reviews": reviews,
    }


def final_excerpt_review_is_filled(review: dict, input_item: dict | None = None) -> bool:
    decision = clean(review.get("decision"))
    if decision not in {"keep", "drop"} or not clean(review.get("reason")):
        return False
    if decision == "keep":
        if clean(review.get("stance")) not in {"positive", "objective", "negative"}:
            return False
        if review.get("self_contained") is not True:
            return False
        if any(not clean(review.get(field)) for field in ("aspect_evidence", "stance_evidence")):
            return False
        if (input_item or {}).get("target_review_required") and (
            review.get("target_passed") is not True or not clean(review.get("target_evidence"))
        ):
            return False
        if (input_item or {}).get("work_consistency_review_required") and (
            review.get("work_consistency_passed") is not True or not clean(review.get("work_consistency_evidence"))
        ):
            return False
        if (input_item or {}).get("short_excerpt"):
            if review.get("short_excerpt_justified") is not True:
                return False
            if not clean(review.get("short_excerpt_reason")) or not clean(review.get("specific_support_evidence")):
                return False
        if (input_item or {}).get("promotion_markers"):
            if review.get("independent_opinion_passed") is not True or not clean(review.get("opinion_evidence")):
                return False
        return True
    failed_checks = review.get("failed_checks")
    if not isinstance(failed_checks, list) or not failed_checks or review.get("reexcerpt_attempted") is not True:
        return False
    if {clean(value) for value in failed_checks} & {"aspect", "stance"}:
        return review.get("reassignment_attempted") is True and bool(clean(review.get("reassignment_reason")))
    if "work_consistency" in {clean(value) for value in failed_checks}:
        return bool(clean(review.get("conflict_evidence")))
    return True


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
            "title_claims_passed": None,
            "title_claims": [],
            "stance_purity_passed": None,
            "scope_purity_passed": None,
            "scope_reason": "",
            "granularity_passed": None,
            "granularity_reason": "",
            "source_role_checked": None,
            "source_role_reason": "",
            "reason": "",
        }
        if clean(item.get("stance")) == "objective":
            review.update({"objective_purity_passed": None, "objective_subtype": ""})
        reviews[review_key] = review
    count_reviews = {}
    for item in count_input.get("batches", []):
        review_key = exact_key(item.get("review_key"))
        if not review_key:
            continue
        count_reviews[review_key] = {
            "fingerprint": clean(item.get("fingerprint")),
            "decision": "",
            "cluster_count": item.get("cluster_count"),
            "range_status": clean(item.get("expected_range_status")),
            "reader_load_reviewed": None,
            "overfragmentation_checked": None,
            "overbreadth_checked": None,
            "no_forced_merge_or_split": None,
            "exception_approved": None,
            "exception_reason": "",
            "reason": "",
        }
    return {
        "scope": "current_period_cluster_set_reviews",
        "_workflow": binding(workflow_id, inputs),
        "contract": {
            "cluster_review": "按代表样本检查簇标题、立场、范围和颗粒度；每个标题分句至少列一个 supporting_source_id",
            "per_member_review": "逐样本语义对齐统一在 final_excerpt_review 完成，此处不复制全部成员证据",
        },
        "reviews": reviews,
        "count_reviews": count_reviews,
    }


def cluster_set_review_is_filled(item: dict) -> bool:
    return (
        clean(item.get("decision")) == "pass"
        and bool(clean(item.get("reason")))
        and isinstance(item.get("title_claims"), list)
        and bool(item.get("title_claims"))
    )


def cluster_count_review_is_filled(item: dict) -> bool:
    return clean(item.get("decision")) == "pass" and bool(clean(item.get("reason")))


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
        ensure_template(template, config_template(p["records"]))
        return result(
            "REVIEW_REQUIRED", "period_config", "请根据当前数据填写期次配置；不要把演员或角色名写入 strong_terms",
            required_file=str(p["config"]), template=str(template), reference=str(SKILL_ROOT / "references" / "contracts.md"),
        )

    prepare_token = digest_paths([p["records"], p["config"], PIPELINE, MEDIA_REGISTRY, CONTROLLER])
    if not stage_fresh(manifest, "prepare", prepare_token, [p["run"] / "prepare_summary.json", p["run"] / "source_review_queue.jsonl"]):
        return result("READY_TO_ADVANCE", "prepare", "配置已就绪，可以执行标准化与初筛", action="prepare", input_digest=prepare_token)

    source_queue = read_jsonl(p["run"] / "source_review_queue.jsonl")
    source_inputs = [p["run"] / "source_review_queue.jsonl", p['run'] / 'normalized_sources.jsonl', p['config']]
    if source_queue or p['source'].exists():
        source_payload = review_payload(p["source"])
        issue = binding_issue(source_payload, workflow_id, source_inputs)
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
            write_json(template, source_review_template_payload(workflow_id, source_inputs, current_queue))
            return result(
                "REVIEW_REQUIRED", "source_review", "只提交当前分片；控制器会自动累计历史答案，完成后再次advance获取下一分片",
                required_file=str(p["source"]), template=str(template),
                input_file=str(current_input), source_chunk_file=str(next_chunk or source_inputs[0]),
                chunk_index=chunk_index, chunk_total=chunk_total,
                full_input_file=str(source_inputs[0]),
                binding_issue=issue, required=len(required_ids), completed=len(required_ids & completed_ids), missing=len(missing),
            )
        write_json(p["source"], source_ledger)

    validation_inputs = source_inputs + [PIPELINE, CONTROLLER]
    validation_token = digest_paths(validation_inputs)
    if not stage_fresh(
        manifest,
        "source_review_validation",
        validation_token,
        [p["source_validation"], p["link_candidates"]],
    ):
        return result(
            "READY_TO_ADVANCE", "source_review_validation",
            "来源复核覆盖齐备，可以一次性校验全部决定、证据位置和综艺期次字段",
            action="source_review_validation", input_digest=validation_token,
        )
    validation = read_json(p["source_validation"])
    if clean(validation.get("status")) != "PASS":
        return result(
            "REVIEW_REQUIRED", "source_review", "来源复核契约未通过；只修复校验报告列出的受影响来源",
            required_file=str(p["source"]), validation_file=str(p["source_validation"]),
            input_file=str(p["source_validation"]),
            issue_count=int(validation.get("issue_count", 0)),
            affected_source_ids=validation.get("affected_source_ids", []),
        )

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
    cluster_issue = binding_issue(cluster_payload, workflow_id, cluster_inputs)
    if cluster_issue:
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
                "required_any": "填写1至4个方面词或短语；不得只填作品名、演员名、角色名、武侠、剧情、热度等通用词",
                "coverage": "观点应共同覆盖本期主要正面、客观、负面表达；后续脚本会把大面积无簇可接的来源退回定义修复",
            },
            "batches": {batch: [] for batch in batches},
        }
        if (
            not template.exists()
            or read_json(template).get("_workflow") != cluster_template.get("_workflow")
            or not read_json(template).get("contract")
        ):
            write_json(template, cluster_template)
        return result(
            "REVIEW_REQUIRED", "cluster_discovery", "依次读取分层抽样的紧凑观点片段后归纳一级观点簇；未入样来源仍会在全量归簇时接受覆盖检查",
            required_file=str(p["clusters"]), template=str(template),
            input_file=str(cluster_discovery_chunks[0] if cluster_discovery_chunks else cluster_inputs[0]),
            input_files=[str(item) for item in cluster_discovery_chunks] or [str(cluster_inputs[0])],
            full_input_file=str(p["run"] / "cluster_discovery_input.jsonl"),
            full_source_file=str(p["run"] / "retained_sources.jsonl"), binding_issue=cluster_issue,
        )

    override_inputs = [p["run"] / "retained_sources.jsonl", p["clusters"]]
    override_template = p['templates'] / 'cluster_overrides.template.json'
    overrides_payload = review_payload(p["overrides"])
    cumulative_overrides = cumulative_override_payload(
        workflow_id, override_inputs, overrides_payload, p["override_ledger"]
    )
    ensure_template(override_template, cumulative_overrides)
    if overrides_payload is not None:
        override_issue = binding_issue(overrides_payload, workflow_id, override_inputs)
        if override_issue:
            return result("REVIEW_REQUIRED", "cluster_assignment_review", "cluster_overrides.json 与当前保留池或簇定义不一致；从当前模板恢复绑定，不要读取整份保留池", required_file=str(p["overrides"]), template=str(override_template), input_file=str(override_template), binding_issue=override_issue)
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
                validation_issues=override_validation_issues,
            )

    cluster_base_inputs = [p["run"] / "retained_sources.jsonl", p["clusters"], PIPELINE, CONTROLLER]
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
    ]):
        return result("READY_TO_ADVANCE", "cluster_base", "观点簇定义已就绪，可以生成首次归簇和复核队列", action="cluster_base", input_digest=cluster_base_token)

    low_queue = read_jsonl(p["run"] / "cluster_review_queue.jsonl")
    if low_queue:
        aspect_gaps = [item for item in low_queue if clean(item.get("issue")) == "passage_aspect_mismatch"]
        retained_count = len(read_jsonl(p["run"] / "retained_sources.jsonl"))
        gap_threshold = max(12, int(retained_count * 0.08))
        if len(aspect_gaps) >= gap_threshold:
            gap_file = p["run"] / "cluster_definition_gap_samples.jsonl"
            write_jsonl(gap_file, aspect_gaps[:40])
            return result(
                "REVIEW_REQUIRED", "cluster_definition_repair",
                "大量来源没有可承接的观点簇；先修正观点定义，再处理少量个别归簇",
                required_file=str(p["clusters"]), input_file=str(gap_file),
                full_queue_file=str(p["run"] / "cluster_review_queue.jsonl"),
                uncovered=len(aspect_gaps), retained_sources=retained_count,
                threshold=gap_threshold, sample_rows=min(len(aspect_gaps), 40),
                do_not_create_bulk_overrides=True,
            )
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
        write_json(template, cluster_override_submission_template(cumulative_overrides, current_assignment_rows))
        return result(
            "REVIEW_REQUIRED", "cluster_assignment_review",
            "只处理当前最多60条归簇残差；已提交但仍出现的 override 视为失败，必须按 issue 重新选片段或转簇",
            required_file=str(p["overrides"]), template=str(template), input_file=str(current_assignment_input),
            full_queue_file=str(p["run"] / "cluster_review_queue.jsonl"), current_count=min(len(low_queue), 60),
            required=len(low_ids), completed=0, missing=len(low_ids),
            rejected_submissions=len(rejected_ids), rejected_source_ids=sorted(rejected_ids),
            do_not_rerun_without_changes=True,
        )

    set_inputs = [p["run"] / "cluster_set_review_input.json", p["run"] / "cluster_count_review_input.json"]
    cluster_set_input = read_json(set_inputs[0])
    cluster_count_input = read_json(set_inputs[1])
    set_payload = review_payload(p["set"])
    set_issue = binding_issue(set_payload, workflow_id, set_inputs)
    required_set = {exact_key(item.get("review_key")) for item in cluster_set_input.get("clusters", [])}
    required_count = {exact_key(item.get("review_key")) for item in cluster_count_input.get("batches", [])}
    set_values = (set_payload or {}).get("reviews", {})
    count_values = (set_payload or {}).get("count_reviews", {})
    set_done = {
        exact_key(key) for key, item in set_values.items()
        if isinstance(set_values, dict) and isinstance(item, dict) and cluster_set_review_is_filled(item)
    } if isinstance(set_values, dict) else set()
    count_done = {
        exact_key(key) for key, item in count_values.items()
        if isinstance(count_values, dict) and isinstance(item, dict) and cluster_count_review_is_filled(item)
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
        return result(
            "REVIEW_REQUIRED", "cluster_set_review", "逐簇核对标题主张、成员证据、立场纯度和期次范围，并审查整期簇数",
            required_file=str(p["set"]), template=str(template), input_files=[str(item) for item in set_inputs], binding_issue=set_issue,
            clusters_required=len(required_set), clusters_missing=len(required_set - set_done), batches_required=len(required_count), batches_missing=len(required_count - count_done),
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
            write_json(override_template, cluster_override_submission_template(cumulative_overrides, current_assignment_rows))
            return result(
                "REVIEW_REQUIRED", "cluster_assignment_review",
                "最终归簇仍有对象、方面或立场未对齐项；只处理当前最多60条并修正累计归簇文件",
                required_file=str(p["overrides"]), template=str(override_template),
                input_file=str(current_assignment_input), full_queue_file=str(p["run"] / "cluster_review_queue.jsonl"),
                current_count=min(len(final_assignment_queue), 60), unresolved=unresolved,
            )
        if unresolved["cluster_set"] or unresolved["cluster_count"]:
            return result(
                "REVIEW_REQUIRED", "cluster_set_review",
                "最终归簇使集合或簇数审查失效；按当前指纹重新完成审查",
                required_file=str(p["set"]),
                input_files=[str(p["run"] / "cluster_set_review_queue.unresolved.jsonl"), str(p["run"] / "cluster_count_review_queue.unresolved.jsonl")],
                unresolved=unresolved,
            )
        raise RuntimeError(f"未知归簇未解决状态：{unresolved}")

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
    if not stage_fresh(manifest, "render_probe", render_probe_token, [
        p["run"] / "excerpt_semantic_review_input.jsonl",
        p["run"] / "final_excerpt_review_contract.json",
    ]):
        return result("READY_TO_ADVANCE", "render_probe", "可以生成候选摘录与最终摘录语义复核输入；这一步不会发布 HTML", action="render_probe", input_digest=render_probe_token)

    semantic_inputs = [
        p["run"] / "excerpt_semantic_review_input.jsonl",
        p["run"] / "final_excerpt_review_contract.json",
    ]
    semantic_payload = review_payload(p["semantic"])
    semantic_issue = binding_issue(semantic_payload, workflow_id, semantic_inputs)
    semantic_review_items = read_jsonl(semantic_inputs[0])
    semantic_input_map = {
        clean(item.get("view_id")): item
        for item in semantic_review_items
        if clean(item.get("view_id"))
    }
    required_views = {clean(item.get("view_id")) for item in semantic_review_items}
    semantic_ledger = cumulative_review_payload(
        workflow_id, semantic_inputs, semantic_payload, p["semantic_ledger"],
        "view_id", "current_period_final_excerpt_semantic_reviews", reviews_as_dict=True,
    )
    write_json(p["semantic_ledger"], semantic_ledger)
    semantic_review_map = list_review_map(semantic_ledger, "view_id")
    semantic_done = {
        view_id for view_id, item in semantic_review_map.items()
        if final_excerpt_review_is_filled(item, semantic_input_map.get(view_id))
    }
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
        write_jsonl(current_input, current_items)
        write_json(template, final_excerpt_review_template_payload(workflow_id, semantic_inputs, current_items))
        return result(
            "REVIEW_REQUIRED", "final_excerpt_review", "只提交当前分片；控制器会自动累计历史答案，完成后再次advance获取下一分片",
            required_file=str(p["semantic"]), template=str(template), optional_excerpt_file=str(p["excerpts"]), optional_excerpt_template=str(excerpt_template),
            input_file=str(current_input), source_chunk_file=str(next_chunk or semantic_inputs[0]),
            chunk_index=chunk_index, chunk_total=chunk_total,
            full_input_file=str(semantic_inputs[0]), contract_file=str(semantic_inputs[1]),
            full_source_file=str(p["run"] / "retained_sources.jsonl"), binding_issue=semantic_issue,
            required=len(required_views), completed=len(required_views & semantic_done), missing=len(required_views - semantic_done),
            incomplete_view_ids=sorted(required_views - semantic_done)[:50],
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
            ensure_template(template, {
                "scope": "current_period_post_excerpt_cluster_count_reviews",
                "_workflow": binding(workflow_id, [post_count_input]) if post_count_input.exists() else {},
                "reviews": {},
            })
            return result(
                "REVIEW_REQUIRED", "post_excerpt_count_review",
                "后置簇数复核与当前终审结果不一致",
                required_file=str(p["post_count"]), template=str(template),
                input_file=str(post_count_input), binding_issue=post_count_issue,
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
            template_payload = {
                "scope": "current_period_post_excerpt_cluster_count_reviews",
                "_workflow": binding(workflow_id, [post_count_input]),
                "contract": {
                    "required": ["fingerprint", "decision=pass", "cluster_count", "range_status", "reader_load_reviewed=true", "no_forced_merge_or_split=true", "reason"],
                    "out_of_range_extra": ["exception_approved=true", "exception_reason"],
                },
                "reviews": {},
            }
            ensure_template(template, template_payload)
            return result(
                "REVIEW_REQUIRED", "post_excerpt_count_review",
                "终审改变了实际非空观点簇数，请按终审后结果完成一次后置数量复核",
                required_file=str(p["post_count"]), template=str(template),
                input_file=str(post_count_input), failure_count=len(failures),
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
    elif action == "source_review_validation":
        command = [
            sys.executable, str(PIPELINE), "validate-source-reviews",
            "--run", str(p["run"]), "--reviews", str(p["source"]),
        ]
        result = run_command(command, allow_review_stop=True)
        outputs = [p["source_validation"], p["link_candidates"]]
    elif action == "link_check":
        command = [
            sys.executable, str(LINK_CHECKER),
            "--input", str(p["link_candidates"]),
            "--output", str(p["run"] / "source_link_health.json"),
            "--workers", str(workers), "--timeout", str(timeout),
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
            ]
        else:
            outputs = [
                p["run"] / "cluster_summary.json",
                p["run"] / "clustered_items.json",
                p["run"] / "cluster_review_queue.jsonl",
                p["run"] / "cluster_set_review_queue.unresolved.jsonl",
                p["run"] / "cluster_count_review_queue.unresolved.jsonl",
                p["run"] / "workbench_order_audit.json",
                p["run"] / "cluster_exclusions.jsonl",
            ]
    elif action in {"render_probe", "render_final"}:
        command = [sys.executable, str(PIPELINE), "render", "--run", str(p["run"]), "--output", str(p["staged_output"])]
        if p["excerpts"].exists():
            command += ["--excerpt-reviews", str(p["excerpts"])]
        if action == "render_final":
            command += ["--semantic-reviews", str(p["semantic"])]
            if p["post_count"].exists():
                command += ["--post-count-reviews", str(p["post_count"])]
        result = run_command(command, allow_review_stop=True)
        if action == "render_final" and p["staged_output"].exists():
            result["output_sha256"] = sha256(p["staged_output"])
        outputs = [
            p["run"] / "excerpt_semantic_review_input.jsonl",
            p["run"] / "final_excerpt_review_contract.json",
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
