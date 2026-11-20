"""Deterministic briefing layout and chart; never reads historical reports."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from chart_coverage import THEME_POLICY, validate_body_label


def validate_outline(dataset: dict, plan: dict) -> dict:
    """Require explicit editorial distinctions, without pretending to judge semantics."""
    points = plan["points"]
    from human_selection import validate_points
    validate_points(dataset, points)
    from workbench_completion import SUPPORTED_POSITIVE_POLICY, MIN_POSITIVE
    if dataset.get('quantity_contract') == SUPPORTED_POSITIVE_POLICY:
        positive_points = sum(any(dataset['groups'][gi].get('stance') in ('positive', '正向词倾向')
                                  for gi in point.get('group_indices', [])) for point in points)
        if positive_points < MIN_POSITIVE:
            raise ValueError(f'正文实际只有{positive_points}个正面点，至少须有{MIN_POSITIVE}个；'
                             '不能用工作台中已被判重复或缺证据的组充数，也不能以批评段补足正面数')
    if not dataset.get("human_selection") and len(points) == 8 and len(str(plan.get("extra_point_reason", "")).strip()) < 24:
        raise ValueError("eight-point brief needs extra_point_reason explaining the independent value of the eighth point")
    for i, point in enumerate(points):
        if plan.get("report_style")!="MANUAL_BRIEF_V1" and len(point.get("samples", [])) == 3 and len(str(point.get("third_sample_reason", "")).strip()) < 12:
            raise ValueError(f"point {i}: use two strong samples or explain the third sample's added information in third_sample_reason")
    review = plan.get("outline_review", {})
    boundaries = review.get("point_boundaries", [])
    if len(boundaries) != len(points):
        raise ValueError("outline_review.point_boundaries needs one entry per point")
    owners = {}
    body_chart = plan.get("chart_theme_policy") == THEME_POLICY
    for i, (p, boundary) in enumerate(zip(points, boundaries)):
        if boundary.get("point_index") != i:
            raise ValueError("point_boundaries must follow zero-based point order")
        for field in ("core_question", "boundary"):
            if len(str(boundary.get(field, "")).strip()) < 12:
                raise ValueError(f"point {i} needs a concrete {field} explaining its editorial boundary")
        label = p.get("chart_label", "")
        if body_chart:
            validate_body_label(p, i)
        elif not isinstance(label, str) or not 4 <= len(label.strip()) <= 30:
            raise ValueError(f"point {i} needs a concise chart_label of 4–30 characters")
        for gi in p.get("group_indices", []):
            if gi in owners:
                raise ValueError(f"group {gi} reused by points {owners[gi]} and {i}; assign each cluster once")
            owners[gi] = i
    pairs = review.get("pairwise_review", [])
    from evidence_quality import similarity
    def categories(point):return {dataset['groups'][gi].get('category') for gi in point.get('group_indices',[]) if dataset['groups'][gi].get('category')}
    possible = {(i,j) for i in range(len(points)) for j in range(i+1,len(points))}
    expected = {(i,j) for i,j in possible if categories(points[i]) & categories(points[j]) or similarity(points[i]['claim'],points[j]['claim'])>=.18}
    inherited = {
        tuple(sorted((owners[row['left']], owners[row['right']])))
        for row in dataset.get('writing_overlap_pairs',[])
        if row['left'] in owners and row['right'] in owners and owners[row['left']]!=owners[row['right']]
    }
    expected.update(inherited)
    actual = set()
    for pair in pairs:
        key = (pair.get("left"), pair.get("right"))
        if key in actual or key not in possible or len(str(pair.get("distinction", "")).strip()) < 12:
            raise ValueError(f"pairwise_review项={pair!r}无效；left/right须为0起始points下标且left<right，不重复；distinction至少12字。有效组合={sorted(possible)}；不独立的点先合并")
        if key in inherited:
            current_quotes={i:{'claim':points[i]['claim'],'samples':[
                {'candidate_id':row.get('candidate_id'),'quote':row.get('quote','')}
                for row in points[i].get('samples',[])]} for i in key}
            proofs = pair.get('evidence',[])
            if not isinstance(proofs,list) or len(proofs)!=2 or not all(isinstance(row,dict) for row in proofs):
                raise ValueError(f'pairwise_review{key}须绑定这两段实际引用各一条不同评价原句；当前实际选样={current_quotes}')
            matched=set()
            for proof in proofs:
                cid=proof.get('candidate_id');quote=proof.get('quote_snippet')
                if isinstance(cid,str) and isinstance(quote,str) and len(quote)>=8:
                    matched.update(i for i in key if any(
                        row.get('candidate_id')==cid and quote in row.get('quote','')
                        for row in points[i].get('samples',[])))
            if matched!=set(key):
                raise ValueError(f'pairwise_review{key}的两条区别证据须分别逐字来自本段当前选样；当前实际选样={current_quotes}')
        actual.add(key)
    if not expected <= actual:
        raise ValueError(f"missing pairwise editorial distinctions: {sorted(expected - actual)}")
    omissions = review.get("omitted_groups", [])
    omitted = [entry.get("group_index") for entry in omissions]
    if (len(set(omitted)) != len(omitted) or set(omitted) != set(range(len(dataset["groups"]))) - set(owners)
            or any(len(str(entry.get("reason", "")).strip()) < 12 for entry in omissions)):
        raise ValueError("omitted_groups must explain every unused cluster without omitting independent evidence by accident")
    return review


def chart_data(dataset: dict, plan: dict, dataset_hash: str) -> dict:
    """A selected-source distribution, NOT a monitoring-population estimate."""
    validate_outline(dataset, plan)
    if plan.get('chart_profile')=='WORKBENCH_CHART_COVERAGE_V1' or 'chart_review' in plan:
        from chart_coverage import coverage_data
        return coverage_data(dataset,plan,dataset_hash)
    owners = {gi: i for i, p in enumerate(plan["points"]) for gi in p["group_indices"]}
    memberships = {}
    for gi, g in enumerate(dataset["groups"]):
        for s in g["samples"]:
            sid = s["source_id"]
            memberships.setdefault(sid, set()).add(owners.get(gi, len(plan["points"])))
    counts = [0] * (len(plan["points"]) + 1)
    allocation = []
    for sid, choices in sorted(memberships.items()):
        bucket = min(choices)  # Stable editorial order; never double-count a source.
        counts[bucket] += 1
        allocation.append({"source_id": sid, "bucket": bucket, "eligible_buckets": sorted(choices)})
    total = sum(counts)
    if not total:
        raise ValueError("no reviewed sources for chart")
    exact = [c * 100 / total for c in counts]
    percentages = [math.floor(v) for v in exact]
    for i in sorted(range(len(counts)), key=lambda i: (-(exact[i] - percentages[i]), i))[:100 - sum(percentages)]:
        percentages[i] += 1
    rows = [{"point_index": i, "label": p["chart_label"], "count": counts[i], "percent": percentages[i]}
            for i, p in enumerate(plan["points"]) if counts[i]]
    rows.sort(key=lambda r: (-r["count"], r["point_index"]))
    if counts[-1]:
        rows.append({"point_index": None, "label": "其他", "count": counts[-1], "percent": percentages[-1]})
    work_label = plan.get("work_label", "电视剧")
    if work_label not in ("电视剧", "网络剧", "电影", "综艺", ""):
        raise ValueError("work_label must describe the current work type")
    return {"title": f"{work_label}《{dataset['target']}》相关话题舆论观点分布", "rows": rows,
            "basis": "本期工作台已审展示来源；按来源ID去重，每条来源只计一次；多观点来源归入正文顺序最靠前的所属点，未入稿观点归其他。",
            "population_claim": False, "denominator": total, "dataset_sha256": dataset_hash,
            "percentage_rounding": "largest_remainder_sum_100", "allocation": allocation}


def draw_chart(data: dict, path: Path) -> dict:
    """Recreate the supplied white/navy horizontal-bar visual with local fonts."""
    regular = Path(os.environ.get("FILM_CHART_FONT", "C:/Windows/Fonts/msyh.ttc"))
    bold = Path(os.environ.get("FILM_CHART_BOLD_FONT", "C:/Windows/Fonts/msyhbd.ttc"))
    if not regular.exists() or not bold.exists():
        raise ValueError("Set FILM_CHART_FONT and FILM_CHART_BOLD_FONT to installed Microsoft YaHei fonts")
    scale = 2
    n = len(data["rows"])
    width, height = 856, max(349, 95 + n * 63)
    image = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(image)
    def font(size, heavy=False):
        return ImageFont.truetype(str(bold if heavy else regular), round(size * scale))
    def text(x, y, value, size, anchor="lt", heavy=False):
        draw.text((x * scale, y * scale), value, font=font(size, heavy), fill="black", anchor=anchor)
    title_size = 32
    while draw.textlength(data["title"], font=font(title_size, True)) > 810 * scale:
        title_size -= 1
    text(428, 14, data["title"], title_size, "mt", True)
    axis_x, first_y, row_height = 402, 106, 63
    top, bottom = first_y - 31, first_y + (n - 1) * row_height + 31
    draw.line((axis_x*scale, top*scale, axis_x*scale, bottom*scale), fill="#d9d9d9", width=2*scale)
    for y in (top, bottom):
        draw.ellipse(((axis_x-6)*scale,(y-6)*scale,(axis_x+6)*scale,(y+6)*scale), fill="#d9d9d9")
    max_count = max(row["count"] for row in data["rows"])
    for i, row in enumerate(data["rows"]):
        y = first_y + i * row_height
        label = row["label"]
        size = 22
        if draw.textlength(label, font=font(size)) > 375*scale:
            # Wrap the full label; do not truncate meaning or shrink other labels.
            cut = len(label)//2
            lines = [label[:cut], label[cut:]]
            for j, line in enumerate(lines):
                text(axis_x-14, y-24+j*25, line, size, "rt")
        else:
            text(axis_x-14, y-15, label, size, "rt")
        length = 390 * row["count"] / max_count
        draw.rectangle((axis_x*scale,(y-18)*scale,(axis_x+length)*scale,(y+18)*scale), fill="#003366")
        percentage = "<1%" if row["count"] and not row["percent"] else f"{row['percent']}%"
        text(axis_x+length+10,y-15,percentage,22)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return {"image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "font_sha256": hashlib.sha256(regular.read_bytes()).hexdigest(),
            "bold_font_sha256": hashlib.sha256(bold.read_bytes()).hexdigest(),
            "size": list(image.size), "bar_color": "#003366", "axis_color": "#d9d9d9"}
