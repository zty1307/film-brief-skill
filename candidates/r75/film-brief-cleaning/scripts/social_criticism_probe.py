"""Bounded discovery probes for criticism in existing social source rows.

These are recall hints, never semantic approvals. The discovery model reviews each
literal excerpt, and the final sample reviewer applies the usual keep/reject gate.
"""
from __future__ import annotations

import hashlib
import re

from rapid_film_workbench import sentence_spans, source_exclusion, source_identity, text_field


SOCIAL_CHANNELS = ("微博", "小红书")
PROBE_LIMIT = 6
PROBE_CUES = {
    "改编争议": re.compile(r"爆改|魔改|切割.{0,8}原著|原著.{0,8}(?:切割|不提|没提)|戏份.{0,8}挪给"),
    "节奏与注水": re.compile(r"有点儿?水|注水|节奏.{0,8}(?:偏慢|太慢|有点慢|缓慢|拖沓)|(?:重复|反复|三波|两波|多波).{0,8}(?:三遍|几遍|多遍)"),
    "表演与动作": re.compile(r"舞蹈痕迹.{0,8}重|(?:演技|表演|动作|打戏).{0,10}(?:僵硬|尴尬|出戏|别扭|拉胯|差(?!感|异|别|距))"),
    "其他具体不满": re.compile(r"不太满意|不满意|看不下去|坚持不下去|劝退|(?:滤镜|服化道|剧情|剪辑|台词).{0,8}(?:过于(?:饱和|夸张|复杂)|不够|失望|糟糕)"),
}
NEGATED_PRAISE = re.compile(
    r"(?:没有|不|无|没|零)(?:会|至于|算|多余|甜宠)?(?:拖沓|尴尬|失望|差|出戏|拉胯|注水)"
    r"|(?:没有|没)让人失望"
)


def _span_around_cue(text, cue_start):
    spans = list(sentence_spans(text))
    index = next((i for i, (start, end, _) in enumerate(spans) if start <= cue_start < end), None)
    if index is None:
        return None
    start, end, _ = spans[index]
    if end - start < 50 and index and end - spans[index - 1][0] <= 300:
        start = spans[index - 1][0]
    if end - start < 50 and index + 1 < len(spans) and spans[index + 1][1] - start <= 300:
        end = spans[index + 1][1]
    if end - start > 360:
        start = max(start, cue_start - 120)
        end = min(end, start + 300)
    return start, end


def social_criticism_probe_group(sources, terms):
    """Expose at most six distinct original posts across criticism mechanisms.

    Scan only local extracted rows. Ignore raw sentiment and the rough aspect
    classifier, which can miss mixed-polarity posts and adaptation vocabulary.
    A channel/category slot is for review coverage, not a final sample quota.
    """
    proposals = []
    for record in sources.values():
        if record.get("channel") not in SOCIAL_CHANNELS or source_exclusion(record):
            continue
        data = record.get("data", {})
        field = next((key for key in ("正文内容", "正文", "内容") if text_field(data, key)), None)
        if not field:
            continue
        body = str(data[field])
        if not any(term and (term in body or term in text_field(data, "标题")) for term in terms):
            continue
        screened = NEGATED_PRAISE.sub(lambda match: " " * len(match.group()), body)
        screened = re.sub(r"#[^#\n]{1,100}#", lambda match: " " * len(match.group()), screened)
        best = None
        for category, pattern in PROBE_CUES.items():
            for match in pattern.finditer(screened):
                bounds = _span_around_cue(body, match.start())
                if bounds is None:
                    continue
                start, end = bounds
                quote = body[start:end]
                if not 24 <= len(quote) <= 360:
                    continue
                score = (80 + min(len(match.group()), 12) * 2
                         + (12 if any(term in quote for term in terms if term) else 0)
                         + (8 if re.search(r"我|个人|看完|看了|追剧", quote) else 0)
                         + min(10, len(quote) // 40))
                proposal = {"source_id": record["id"], "start": start, "end": end,
                            "score": score, "text_field": field,
                            "identity": source_identity(record),
                            "excerpt_hash": hashlib.sha256(re.sub(r"\s+", "", quote).encode("utf-8")).hexdigest(),
                            "probe_category": category, "probe_channel": record["channel"]}
                if best is None or (score, -start) > (best["score"], -best["start"]):
                    best = proposal
        if best is not None:
            proposals.append(best)

    # First give different complaint types on both social platforms a chance,
    # then fill spare slots by score. One original row/author is used at most once.
    proposals.sort(key=lambda item: (-item["score"], item["source_id"], item["start"]))
    picked, identities, hashes, slots = [], set(), set(), set()
    def accept(item):
        if item["identity"] in identities or item["excerpt_hash"] in hashes:
            return False
        picked.append(item)
        identities.add(item["identity"])
        hashes.add(item["excerpt_hash"])
        slots.add((item["probe_category"], item["probe_channel"]))
        return True

    for item in proposals:
        if len(picked) >= PROBE_LIMIT:
            break
        if (item["probe_category"], item["probe_channel"]) not in slots:
            accept(item)
    for item in proposals:
        if len(picked) >= PROBE_LIMIT:
            break
        accept(item)
    if not picked:
        return None
    return {"aspect": "社交平台具体批评线索（待核）", "stance": "负向词倾向",
            "candidate_count": len(proposals),
            "candidates": [{key: value for key, value in item.items() if not key.startswith("probe_")}
                           for item in picked]}
