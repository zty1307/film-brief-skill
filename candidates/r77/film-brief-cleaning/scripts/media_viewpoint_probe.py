"""Recall viewpoint passages from every locally identified mainstream source.

Identification and shortlisting are deterministic. Neither a media identity nor
a lexical score approves a viewpoint; the discovery and final reviewers do that.
"""
from __future__ import annotations

import hashlib
import re

from rapid_film_workbench import (candidates, sentence_spans, source_exclusion,
                                  source_priority, text_field)
from social_criticism_probe import _target_grounded


DISCOVERY_LIMIT = 24
GROUP_SIZE = 6
BODY_FIELDS = ("正文内容", "正文", "内容", "视频ASR", "标题")
PROMOTION = re.compile(r"抽奖|扫码|转发有礼|点击链接|预约入口|云包场|购票链接|关注我们")
OPINION_CUES = re.compile(
    r"观众|网友|评论|评价|认为|指出|口碑|反响|讨论|共鸣|"
    r"细腻|自然|扎实|鲜活|出色|精彩|真实|克制|有层次|有张力|新意|创新|"
    r"叙事|节奏|镜头|质感|塑造|刻画|呈现|表达|聚焦|反思"
)
BULLETIN_CUES = re.compile(r"定档|今日开播|官宣|上线|即将播出|敬请关注|锁定.*频道|演员阵容")


def _window(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand a source sentence to a readable, contiguous local excerpt."""
    spans = list(sentence_spans(text))
    index = next((i for i, (a, b, _) in enumerate(spans) if a <= start < b), None)
    if index is None:
        return start, end
    left, right = index, index
    while spans[right][1] - spans[left][0] < 70:
        if right + 1 < len(spans) and spans[right + 1][1] - spans[left][0] <= 360:
            right += 1
        elif left and spans[right][1] - spans[left - 1][0] <= 360:
            left -= 1
        else:
            break
    a, b = spans[left][0], spans[right][1]
    if b - a > 360:
        a, b = max(a, start - 100), min(b, start + 260)
    return a, b


def _best_passage(record: dict, terms: list[str]):
    data = record.get("data", {})
    field = next((name for name in BODY_FIELDS if text_field(data, name)), None)
    if not field:
        return None
    body = str(data[field])
    if not _target_grounded(data, body, terms):
        return None
    options = candidates(body, terms)
    # A factual media observation may have no generic aspect word. Retain one
    # target-bearing sentence as a review hint in that case.
    if not options:
        positions = [body.find(term) for term in terms if term and term in body]
        anchor = min(positions) if positions else 0
        spans = list(sentence_spans(body))
        containing = next(((a, b) for a, b, _ in spans if a <= anchor < b), None)
        if containing is None:
            return None
        options = [{"start": containing[0], "end": containing[1], "score": 0,
                    "aspects": [], "stance": "中性或混合"}]
    def rank(item):
        fragment = body[item["start"]:item["end"]]
        opinion = len(OPINION_CUES.findall(fragment))
        return (PROMOTION.search(fragment) is not None,
                item["stance"] == "负向词倾向",
                -(20 * opinion - 10 * len(BULLETIN_CUES.findall(fragment)) + item["score"]),
                item["start"])
    ranked = sorted(options, key=rank)
    for item in ranked:
        start, end = _window(body, item["start"], item["end"])
        quote = body[start:end]
        if len(quote.strip()) >= 24:
            return {"source_id": record["id"], "start": start, "end": end,
                    "text_field": field, "score": 100 + item["score"],
                    "excerpt_hash": hashlib.sha256(re.sub(r"\s+", "", quote).encode("utf-8")).hexdigest(),
                    "identity": "media-row:" + record["id"],
                    "probe_aspect": item["aspects"][0] if item["aspects"] else "其他观察",
                    "probe_tier": source_priority(record)["media_authority_rank"]}
    return None


def media_viewpoint_probe_groups(sources: dict, terms: list[str]):
    """Index all verified media rows; send at most 24 varied local excerpts.

    The audit counts every identified source, including off-topic, excluded,
    duplicate and overflow rows. A group slot is a discovery opportunity, not
    a media quota in final samples.
    """
    identified, proposals, source_status = [], [], {}
    for record in sources.values():
        if not source_priority(record)["media_authority_rank"]:
            continue
        identified.append(record["id"])
        if source_exclusion(record):
            source_status[record["id"]] = "excluded_ai_draft"
            continue
        proposal = _best_passage(record, terms)
        if proposal:
            proposals.append(proposal)
            source_status[record["id"]] = "proposed"
        else:
            source_status[record["id"]] = "no_target_passage"
    proposals.sort(key=lambda item: (-item["probe_tier"], -item["score"], item["source_id"]))
    picked, hashes, aspects = [], set(), set()
    for distinct_first in (True, False):
        for item in proposals:
            if len(picked) >= DISCOVERY_LIMIT:
                break
            if item["excerpt_hash"] in hashes:
                continue
            if distinct_first and item["probe_aspect"] in aspects:
                continue
            picked.append(item)
            hashes.add(item["excerpt_hash"])
            aspects.add(item["probe_aspect"])
            source_status[item["source_id"]] = "sent_to_discovery"
    for item in proposals:
        if source_status[item["source_id"]] == "proposed":
            source_status[item["source_id"]] = (
                "duplicate_excerpt" if item["excerpt_hash"] in hashes else "discovery_limit")
    groups = []
    for offset in range(0, len(picked), GROUP_SIZE):
        chunk = picked[offset:offset + GROUP_SIZE]
        groups.append({"aspect": "已核实主流媒体观点线索（待核）", "stance": "中性或混合",
                       "candidate_count": len(chunk),
                       "candidates": [{key: value for key, value in item.items()
                                       if not key.startswith("probe_")} for item in chunk]})
    audit = {"identified_count": len(identified), "identified_source_ids": identified,
             "target_passage_count": len(proposals), "discovery_count": len(picked),
             "duplicate_or_limit_count": len(proposals) - len(picked),
             "discovery_limit": DISCOVERY_LIMIT, "source_status": source_status}
    return groups, audit
