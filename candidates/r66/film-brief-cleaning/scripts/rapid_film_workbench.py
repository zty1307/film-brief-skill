"""Time-first, offline rule buckets. No model calls or semantic approval."""
from __future__ import annotations

import time
ENTRY_STARTED = time.monotonic()

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path

from extract_sources import extract

ASPECTS = {
    "剧情与叙事": ("剧情", "故事", "叙事", "节奏", "逻辑", "改编", "原著", "套路", "悬念"),
    "演员与表演": ("演技", "表演", "台词", "演员", "主演", "配角", "表情", "角色", "人设", "人物", "塑造", "群像"),
    "动作与打戏": ("打戏", "打斗", "动作", "武打", "武术", "招式"),
    "画面与制作": ("制作", "质感", "特效", "画面", "摄影", "镜头", "服装", "服化", "置景", "滤镜"),
    "音乐与声音": ("音乐", "配乐", "主题曲", "插曲", "音效", "配音", "原声"),
    "情感与主题": ("情感", "感情", "共鸣", "情怀", "价值观", "主题", "亲情", "友情", "爱情", "成长", "责任", "奉献", "现实", "职业"),
    "播出与市场": ("播出", "收视", "热度", "播放", "开播", "定档", "平台", "排播", "排名", "预约"),
    "宣传与争议": ("宣传", "营销", "粉丝", "争议", "炒作", "热搜", "预告", "海报", "包场"),
}
POSITIVE = ("精彩", "好看", "出色", "惊艳", "优秀", "喜欢", "满意", "赞", "细腻", "流畅", "带感", "期待", "精良", "扎实", "自然", "创新", "创造性", "突破", "升级", "优质", "质感在线", "感染力", "水准非常之高", "美感", "力量感")
NEGATIVE = ("难看", "失望", "尴尬", "烂", "糟", "差", "僵硬", "呆板", "单一", "拖沓", "无聊", "套路", "敷衍", "不行", "雷同", "混乱", "质疑", "吐槽")
POSITIVE += ("鲜活", "生动", "生活感", "烟火气", "稳健", "太稳", "专业感", "温暖", "真实感", "可信", "耐看")
NOTICE = "纯脚本粗分｜未经AI或人工内容复核；关键词分组和倾向可能误判，不代表确定支持观点。"
URL_FIELDS = ("链接", "发文链接", "原文链接", "内容链接", "文章链接", "URL", "url")


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


WORK_SCOPE_RULE = " 当前任务对象是本期目标影视剧，同名原著小说不等于剧版。原著、作者、演员及旧作品只在原句直接说明本剧改编、制作、表演或传播时纳入；明确比较原著与剧版的改动可以保留。纯原著书评、作者履历、旧作评价、与本剧无关的艺人信息不能独立充作本剧观点，不得由背景资料推断本剧质量。不能仅靠同名、同演员或所在文章提到本剧就纳入；判断的是实际摘录与本剧的关系，不按是否出现小说/原著等词一刀切。若本组标题本身超出这个任务范围，按当前答卷格式排除样本，不自行改组；发现者在首次分组时就排除这种独立议题。"


def text_field(data: dict, *names: str) -> str:
    return next((str(data[name]).strip() for name in names if data.get(name) is not None and str(data[name]).strip()), "")


def resolve_verbatim_span(window: str, quote: object):
    """Restore only unambiguous NBSP transport changes; return actual source bytes/text."""
    if not isinstance(quote, str) or not 12 <= len(quote) <= 360:
        return None
    offset = window.find(quote)
    if offset >= 0:
        return quote, offset, False
    spaces = str.maketrans({"\u00a0": " ", "\u202f": " "})
    normalized_window, normalized_quote = window.translate(spaces), quote.translate(spaces)
    offset = normalized_window.find(normalized_quote)
    if offset < 0 or normalized_window.find(normalized_quote, offset + 1) >= 0:
        return None
    # Translation is one codepoint to one codepoint: positions remain exact.
    literal = window[offset:offset + len(quote)]
    return literal, offset, True


def blocked_host(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        return host == "ydata.woa.com" or host.endswith(".ydata.woa.com")
    except ValueError:
        return True


def source_identity(record: dict) -> str:
    data = record["data"]
    author = text_field(data, "作者", "作者名称", "媒体名称", "账号名称", "账号昵称", "帐号昵称", "发文账号", "来源", "发布者", "昵称")
    url = text_field(data, *URL_FIELDS)
    # Unknown authors are not treated as one author. Independence is heuristic, not verified.
    return ("author:" + author) if author and author not in ("未署名", "未知", "匿名") else ("url:" + url if url else "row:" + record["id"])


def lexical_features(text: str) -> set[str]:
    normalized = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', text).lower()
    return {normalized[i:i+2] for i in range(len(normalized)-1)}


def text_overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / max(1, len(left | right))


def sentiment_counts(text: str) -> tuple[int, int]:
    """Local lexical routing hint with negation; not semantic approval."""
    basis = re.sub(r"不同于[^，。！？]{0,18}的套路", "", text)
    positive = negative = 0
    for words, original_positive in ((POSITIVE, True), (NEGATIVE, False)):
        pattern = '|'.join(re.escape(word) for word in sorted(words, key=len, reverse=True))
        for match in re.finditer(pattern, basis):
            word = match.group()
            if word == '差' and (basis[max(0,match.start()-1):match.end()] == '反差' or basis[match.start():match.end()+1] in ('差异','差别','差距')):
                continue
            prefix = re.split(r'[，,。；;！？!?\n]', basis[max(0,match.start()-10):match.start()])[-1]
            double = re.search(r'(?:不是|并非|不能|不得)不$', prefix)
            negated = not double and bool(re.search(r'(?:没有|并非|不是|未见|毫无|不|没|无|未)(?:算|太|很|够|再|会|是|觉得|显得|那么|特别|过于)?$', prefix))
            if word == '套路' and prefix.endswith('反'):
                negated = True
            is_positive = original_positive != negated
            positive += int(is_positive)
            negative += int(not is_positive)
    return positive, negative


def sentence_spans(text: str):
    """Cover long punctuation-poor sources instead of losing their later parts."""
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
        start, stop = match.span()
        while start < stop:
            end = stop if stop-start <= 240 else start + 200
            if end < stop:
                boundaries = list(re.finditer(r'[，,；;]', text[start+80:end]))
                if boundaries:
                    end = start + 80 + boundaries[-1].end()
            cut_inside_clause = ((start > match.start() and text[start-1] not in '，,；;') or
                                 (end < stop and text[end-1] not in '，,；;'))
            yield start, end, cut_inside_clause
            start = end


def candidates(text: str, terms: list[str]) -> list[dict]:
    hits = [match.start() for term in terms for match in re.finditer(re.escape(term), text)]
    result = []
    for start, end, truncated in sentence_spans(text):
        fragment = text[start:end]
        if len(fragment.strip()) < 12:
            continue
        near = min((abs(hit - start) for hit in hits), default=len(text))
        fragment = text[start:end]
        ranking_text = re.sub(r"#[^#\n]{1,100}#", "", fragment)
        aspects = [name for name, words in ASPECTS.items() if any(word in ranking_text for word in words)]
        if not aspects:
            continue
        pos, neg = sentiment_counts(ranking_text)
        stance = "正向词倾向" if pos > neg else "负向词倾向" if neg > pos else "中性或混合"
        score = min(8, sum(ranking_text.count(word) for name in aspects for word in ASPECTS[name]))
        score += 24 if any(term in ranking_text for term in terms) else max(0, 4 - near // 200)
        score += 10 if 30 <= len(fragment) <= 200 and not truncated else -8
        score += 8 if not (pos and neg) else -12
        score -= 25 if any(word in ranking_text for word in ("抽奖", "转发", "云包场", "买票", "扫码", "关注我", "预约", "点击链接", "不见不散", "呼朋唤友", "大爆特爆", "收视长虹", "必精品", "坐等开播")) else 0
        result.append({"start": start, "end": end, "excerpt": text[start:end], "aspects": aspects, "stance": stance, "score": score})
    return result


def workbench_data(dataset: dict) -> dict:
    """Adapt selected data to the unchanged, established workbench template."""
    from urllib.parse import urlsplit
    stances = {"正向词倾向": "positive", "中性或混合": "objective", "负向词倾向": "negative"}
    clusters = []
    for index, group in enumerate(dataset["groups"]):
        items = []
        for sample in group["samples"]:
            if dataset.get("semantic_verification") == "FINAL_SELECTED_ONLY" and sample.get("ai_decision") != "keep":
                raise ValueError("末端审样页面只能展示实际AI keep条目")
            record, data = sample["source"], sample["source"]["data"]
            raw_url = text_field(data, *URL_FIELDS)
            try:
                parsed = urlsplit(raw_url)
                host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
                # Inert company portal addresses; ordinary links require a human click.
                url = raw_url if parsed.scheme.lower() in ("http", "https") and host and "%" not in host and not re.search(r"[\x00-\x20\\]", raw_url) and not (host == "woa.com" or host.endswith(".woa.com")) else ""
            except (ValueError, UnicodeError):
                url = ""
            provenance = f'来源ID：{record["id"]}\n原始文件：{record["source_file"]}\n工作表：{record["sheet"]}，第{record["row"]}行\n原文字段：{sample["text_field"]}，位置 [{sample["start"]}, {sample["end"]})\n来源地址（未联网检查）：{raw_url}'
            items.append({
                "sourceId": record["id"], "author": text_field(data, "作者", "作者名称", "媒体名称", "账号名称", "账号昵称", "帐号昵称", "发文账号", "来源", "发布者", "昵称") or "未署名",
                "title": sample["title"], "excerpt": sample["excerpt"],
                "body": sample["source_text"] + "\n\n【追溯信息】\n" + provenance,
                "sourceText": sample["source_text"], "channel": record["channel"],
                "publishedAt": text_field(data, "发表时间", "发布时间", "日期"),
                "url": url, "sourceUrlText": raw_url, "sourceFile": record["source_file"],
                "sourceSheet": record["sheet"], "sourceRow": record["row"],
                "textField": sample["text_field"], "excerptStart": sample["start"], "excerptEnd": sample["end"],
                "semanticReview": {"decision": "keep" if dataset.get("semantic_verification") == "FINAL_SELECTED_ONLY" else "not_reviewed",
                                   "scope": dataset.get("semantic_verification", "NOT_PERFORMED")},
            })
        clusters.append({"id": f'rule-{index}', "title": group["aspect"], "stance": stances[group["stance"]],
                         "count": len(items), "items": items, "candidateCount": group["candidate_count"]})
    return {"batches": [{"name": dataset["target"], "count": sum(c["count"] for c in clusters),
                          "clusterCount": len(clusters), "clusters": clusters}],
            "reviewScope": dataset.get("semantic_verification", "NOT_PERFORMED"),
            "notice": dataset.get("notice", NOTICE), "footer": dataset.get("footer", ""),
            "sourceIndependence": "author_or_url_heuristic_not_verified"}


def render(dataset: dict) -> str:
    template = (Path(__file__).resolve().parents[1] / "templates" / "workbench.html").read_text(encoding="utf-8")
    marker = "@@FILM_BRIEF_DATA@@"
    if template.count(marker) != 1:
        raise ValueError("原工作台模板数据占位符必须恰好一个")
    data = json.dumps(workbench_data(dataset), ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    # No CSS, DOM layout, tabs, card styling or interaction changes.
    page = template.replace(marker, data)
    return page.replace('<div class="side-foot">观点工作台</div>',
                        '<div class="side-foot">' + html.escape(dataset.get("notice", NOTICE)) + '</div>')


def worker(args: argparse.Namespace) -> None:
    started = time.monotonic()
    workspace = args.workspace.resolve()
    timings = {}
    extraction = extract(args.source, workspace / "raw_extraction")
    timings["extract_seconds"] = round(time.monotonic() - started, 3)
    # JSONL is separated by physical newlines, not Unicode U+2028 inside source strings.
    with Path(extraction["records_path"]).open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    phase = time.monotonic()
    terms = [args.target, *args.alias]
    seen = set()
    buckets: dict[tuple[str, str], list[dict]] = {}
    ledger = []
    retained = 0
    for record in records:
        routes = []
        data = record["data"]
        title = text_field(data, "标题")
        field = next((name for name in ("正文内容", "正文", "视频ASR", "内容") if data.get(name) and str(data[name]).strip()), "标题")
        text = str(data.get(field) or "")
        status = "retained"
        if not any(term in title or term in text for term in terms):
            status = "no_literal_target"
        elif len(text.strip()) < 12:
            status = "too_short"
        normalized = re.sub(r"\s+", "", text)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if status == "retained" and digest in seen:
            status = "exact_copy"
        if status == "retained":
            seen.add(digest)
            choices = candidates(text, terms)
            if not choices:
                status = "no_rule_aspect"
            else:
                retained += 1
                best: dict[tuple[str, str], list[dict]] = {}
                for choice in choices:
                    for aspect in choice["aspects"]:
                        key = aspect, choice["stance"]
                        best.setdefault(key, []).append(choice)
                for key, options in best.items():
                    selected_features = []
                    for choice in sorted(options, key=lambda item: (-item['score'], item['start'])):
                        features = lexical_features(choice['excerpt'])
                        if any(text_overlap(features, previous) >= .6 for previous in selected_features):
                            continue
                        selected_features.append(features)
                        routes.append({"aspect": key[0], "stance": key[1], "start": choice["start"], "end": choice["end"], "score": choice["score"]})
                        buckets.setdefault(key, []).append({**choice, "source": record, "title": title, "source_text": text, "text_field": field, "identity": source_identity(record)})
                        if len(selected_features) == 3:
                            break
        ledger.append({"source_id": record["id"], "status": status, "rule_routes": routes})
    groups = []
    for (aspect, stance), items in sorted(buckets.items()):
        selected, identities = [], set()
        excerpt_hashes = set()
        for item in sorted(items, key=lambda item: (-item["score"], item["source"]["id"])):
            excerpt_hash = hashlib.sha256(re.sub(r"\s+", "", item["excerpt"]).encode("utf-8")).hexdigest()
            if item["identity"] in identities or excerpt_hash in excerpt_hashes:
                continue
            identities.add(item["identity"])
            excerpt_hashes.add(excerpt_hash)
            selected.append(item)
            if len(selected) >= args.per_group:
                break
        groups.append({"aspect": aspect, "stance": stance, "candidate_count": len(items), "samples": selected})
    timings["filter_bucket_select_seconds"] = round(time.monotonic() - phase, 3)
    dataset = {"mode": "SCRIPT_FAST", "semantic_verification": "NOT_PERFORMED", "notice": NOTICE, "target": args.target, "raw_count": len(records), "retained_count": retained, "per_group": args.per_group, "groups": groups}
    phase = time.monotonic()
    save(workspace / "dataset.json", dataset)
    save(workspace / "processing_ledger.json", ledger)
    save(workspace / "candidate_pool.json", [{"aspect": aspect, "stance": stance, "candidates": [{"source_id": item["source"]["id"], "start": item["start"], "end": item["end"], "score": item["score"], "text_field": item["text_field"], "identity": item["identity"], "excerpt_hash": hashlib.sha256(re.sub(r"\s+", "", item["excerpt"]).encode("utf-8")).hexdigest()} for item in sorted(items, key=lambda item: (-item["score"], item["source"]["id"]))]} for (aspect, stance), items in sorted(buckets.items())])
    output = workspace / "workbench.html"
    page = render(dataset)
    output.write_text(page, encoding="utf-8")
    for group in groups:
        assert 0 < len(group["samples"]) <= args.per_group
        for sample in group["samples"]:
            assert sample["source_text"][sample["start"]:sample["end"]] == sample["excerpt"]
    assert not re.search(r'<(?:script|link|img|iframe)\b[^>]*(?:src|href)\s*=', page, re.I)
    if not groups:
        raise RuntimeError("无可展示规则组；请检查目标剧名/别名和原始数据，不冒充成功")
    timings["render_structure_check_seconds"] = round(time.monotonic() - phase, 3)
    save(workspace / "worker_result.json", {"status": "SCRIPT_COMPLETED_FAST", "structure_verification": "PASS", "semantic_verification": "NOT_PERFORMED", "output": str(output), "raw_count": len(records), "retained_count": retained, "group_count": len(groups), "sample_count": sum(len(group["samples"]) for group in groups), "timings": timings})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--target", required=True)
    result.add_argument("--alias", action="append", default=[])
    result.add_argument("--per-group", type=int, choices=range(1, 11), default=10)
    result.add_argument("--deadline-seconds", type=float, default=1800)
    result.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return result


def main() -> int:
    started = ENTRY_STARTED
    args = parser().parse_args()
    if args.worker:
        worker(args)
        return 0
    if not 0 < args.deadline_seconds <= 1800 or not args.target.strip():
        raise ValueError("deadline必须在(0,1800]秒；目标剧名不能为空")
    workspace, source = args.workspace.resolve(), args.source.resolve()
    if workspace == source or source in workspace.parents or workspace in source.parents:
        raise ValueError("工作区与原始数据目录必须互不包含")
    workspace.mkdir(parents=True, exist_ok=False)
    started_at = dt.datetime.now().astimezone().isoformat()
    outcome = {"status": "RUNNING", "started_at": started_at, "deadline_seconds": args.deadline_seconds, "model_calls": 0, "network_requests": 0, "semantic_verification": "NOT_PERFORMED"}
    save(workspace / "run_status.json", outcome)
    argv = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    try:
        remaining = args.deadline_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, args.deadline_seconds)
        with (workspace / "execution.log").open("w", encoding="utf-8") as log:
            child = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, timeout=remaining, check=False)
        if child.returncode != 0:
            outcome.update(status="FAILED", exit_code=child.returncode, log=str(workspace / "execution.log"))
        else:
            outcome.update(json.loads((workspace / "worker_result.json").read_text(encoding="utf-8")))
    except subprocess.TimeoutExpired:
        # subprocess.run terminates and reaps only its own direct deterministic child.
        outcome.update(status="TIME_LIMIT_REACHED", structure_verification="NOT_ACCEPTED")
    elapsed = time.monotonic() - started
    outcome.update(elapsed_seconds=round(elapsed, 3), finished_at=dt.datetime.now().astimezone().isoformat(), completed_within_30_minutes=outcome["status"] == "SCRIPT_COMPLETED_FAST" and elapsed <= 1800)
    if elapsed > args.deadline_seconds:
        outcome.update(status="TIME_LIMIT_REACHED", completed_within_30_minutes=False, structure_verification="NOT_ACCEPTED")
    save(workspace / "run_status.json", outcome)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if outcome["status"] == "SCRIPT_COMPLETED_FAST" else 1


if __name__ == "__main__":
    raise SystemExit(main())
