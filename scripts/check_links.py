from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEAD_CODES = {404, 410}
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
CACHE_TTL_HOURS = {"reachable": 72, "confirmed_dead": 24, "blocked": 168, "invalid": 168}


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def blocked_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "woa.com" or host.endswith(".woa.com")


def valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def iri_to_uri(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((
        parts.scheme,
        parts.netloc.encode("idna").decode("ascii"),
        quote(parts.path, safe="/%:@!$&'()*+,;=-._~"),
        quote(parts.query, safe="=&?/%:@!$'()*+,;[-]._~"),
        "",
    ))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def load_cache(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("scope") != "film_brief_link_health_cache":
        return {}
    return {
        clean(item.get("url")): item
        for item in payload.get("results", [])
        if isinstance(item, dict) and clean(item.get("url"))
    }


def fresh_cached_result(item: dict, now_utc: datetime | None = None) -> dict | None:
    # Never let a historical cache bypass the hard host block. The blocked
    # result is regenerated locally by check_one without a network request.
    if blocked_host(clean(item.get("url"))):
        return None
    classification = clean(item.get("classification"))
    ttl_hours = CACHE_TTL_HOURS.get(classification)
    if ttl_hours is None:
        return None
    try:
        checked = datetime.fromisoformat(clean(item.get("checked_at")).replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    now_value = now_utc or datetime.now(timezone.utc)
    if (now_value - checked.astimezone(timezone.utc)).total_seconds() > ttl_hours * 3600:
        return None
    return {key: value for key, value in item.items() if key not in {"references", "cache_hit"}}


def write_cache(path: Path | None, results: list[dict]) -> None:
    if path is None:
        return
    payload = {
        "scope": "film_brief_link_health_cache",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "results": [
            {key: value for key, value in item.items() if key not in {"references", "cache_hit"}}
            for item in results
            if clean(item.get("classification")) in CACHE_TTL_HOURS
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def extract_html_data(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    marker = "const DATA="
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"{path} 中未找到 const DATA")
    value, _ = json.JSONDecoder().raw_decode(text[start + len(marker):])
    return value


def iter_rows(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        data = extract_html_data(path)
        for batch in data.get("batches", []):
            for cluster in batch.get("clusters", []):
                for item in cluster.get("items", []):
                    yield {
                        "source_id": item.get("sourceId") or item.get("id") or item.get("viewId"),
                        "batch": batch.get("name"),
                        "author": item.get("author"),
                        "channel": item.get("channel"),
                        "url": item.get("url"),
                    }
    elif suffix == ".jsonl":
        for item in load_jsonl(path):
            yield {
                "source_id": item.get("id") or item.get("source_id"),
                "batch": item.get("batch"),
                "author": item.get("author"),
                "channel": item.get("channel"),
                "url": item.get("url"),
            }
    elif suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        candidates = payload if isinstance(payload, list) else payload.get("rows", payload.get("results", []))
        for item in candidates:
            if isinstance(item, dict):
                yield item
    else:
        raise ValueError(f"不支持的输入格式：{path}")


def check_one(url: str, timeout: float) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()
    if not valid_http_url(url):
        return {"url": url, "classification": "invalid", "status_code": None, "checked_at": checked_at, "reason": "not_http_url"}
    if blocked_host(url):
        return {"url": url, "classification": "blocked", "status_code": None, "checked_at": checked_at, "reason": "blocked_woa_host"}

    opener = build_opener(NoRedirect())
    current = iri_to_uri(url)
    history = []
    for _ in range(MAX_REDIRECTS + 1):
        if blocked_host(current):
            return {"url": url, "classification": "blocked", "status_code": None, "checked_at": checked_at, "reason": "redirect_to_blocked_woa_host", "redirects": history}
        request = Request(current, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Range": "bytes=0-2047"}, method="GET")
        try:
            with opener.open(request, timeout=timeout) as response:
                code = int(response.getcode() or 0)
                final_url = response.geturl() or current
                response.read(128)
                return {"url": url, "classification": "reachable", "status_code": code, "final_url": final_url, "checked_at": checked_at, "redirects": history}
        except HTTPError as exc:
            code = int(exc.code)
            location = exc.headers.get("Location") if exc.headers else None
            if code in {301, 302, 303, 307, 308} and location:
                target = iri_to_uri(urljoin(current, location))
                history.append({"status_code": code, "from": current, "to": target})
                current = target
                continue
            if code in DEAD_CODES:
                # Confirm once without the Range header. Some platforms use an
                # unusual response for partial or automated requests; one
                # isolated status must not delete a usable sample.
                confirmation = Request(current, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}, method="GET")
                try:
                    with opener.open(confirmation, timeout=timeout) as response:
                        response.read(128)
                        return {"url": url, "classification": "indeterminate", "status_code": code, "confirmation_status_code": int(response.getcode() or 0), "final_url": current, "checked_at": checked_at, "redirects": history, "reason": "dead_status_not_reproduced"}
                except HTTPError as second:
                    second_code = int(second.code)
                    if second_code in DEAD_CODES:
                        return {"url": url, "classification": "confirmed_dead", "status_code": code, "confirmation_status_code": second_code, "final_url": current, "checked_at": checked_at, "redirects": history, "reason": "http_404_or_410_confirmed_twice"}
                    return {"url": url, "classification": "indeterminate", "status_code": code, "confirmation_status_code": second_code, "final_url": current, "checked_at": checked_at, "redirects": history, "reason": "dead_status_not_reproduced"}
                except (URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as second:
                    return {"url": url, "classification": "indeterminate", "status_code": code, "confirmation_status_code": None, "final_url": current, "checked_at": checked_at, "redirects": history, "reason": "dead_status_confirmation_failed", "error": clean(second)[:300]}
            return {"url": url, "classification": "indeterminate", "status_code": code, "final_url": current, "checked_at": checked_at, "redirects": history, "reason": "http_status_not_proof_of_deletion"}
        except (URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as exc:
            return {"url": url, "classification": "indeterminate", "status_code": None, "final_url": current, "checked_at": checked_at, "redirects": history, "reason": type(exc).__name__, "error": clean(exc)[:300]}
    return {"url": url, "classification": "indeterminate", "status_code": None, "final_url": current, "checked_at": checked_at, "redirects": history, "reason": "too_many_redirects"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check film-brief source links without treating login, anti-bot or timeouts as deletion")
    parser.add_argument("--input", required=True, nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()

    references: dict[str, list[dict]] = {}
    for path in args.input:
        for row in iter_rows(path.resolve()):
            url = clean(row.get("url"))
            if url:
                references.setdefault(url, []).append({
                    "source_id": clean(row.get("source_id")), "batch": clean(row.get("batch")),
                    "author": clean(row.get("author")), "channel": clean(row.get("channel")),
                })

    cached = load_cache(args.cache.resolve() if args.cache else None)
    results = []
    pending_urls = []
    for url in references:
        cache_hit = fresh_cached_result(cached.get(url, {}))
        if cache_hit is None:
            pending_urls.append(url)
            continue
        cache_hit["references"] = references[url]
        cache_hit["cache_hit"] = True
        results.append(cache_hit)
    cache_hits = len(results)
    progress_path = args.output.with_suffix(args.output.suffix + ".progress.json")
    started = time.monotonic()

    def write_progress(status: str) -> None:
        payload = {
            "status": status,
            "completed": len(results),
            "total": len(references),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "output": str(args.output.resolve()),
        }
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = progress_path.with_suffix(progress_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(progress_path)

    write_progress("RUNNING")
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 16))) as pool:
        futures = {pool.submit(check_one, url, args.timeout): url for url in pending_urls}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                url = futures[future]
                result = {"url": url, "classification": "indeterminate", "status_code": None, "checked_at": datetime.now(timezone.utc).isoformat(), "reason": type(exc).__name__, "error": clean(exc)[:300]}
            result["references"] = references[result["url"]]
            results.append(result)
            if len(results) == len(references) or len(results) % max(10, len(references) // 20 or 1) == 0:
                write_progress("RUNNING")

    results.sort(key=lambda item: item["url"])
    counts = {}
    for item in results:
        counts[item["classification"]] = counts.get(item["classification"], 0) + 1
    payload = {
        "scope": "current_period_source_link_health",
        "policy": "Only confirmed HTTP 404 or 410 is a deletion gate; 401, 403, 429, 5xx, login walls, anti-bot responses, DNS errors and timeouts remain indeterminate.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "cache_hits": cache_hits,
        "cache_misses": len(pending_urls),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    cache_output = {
        url: fresh
        for url, item in cached.items()
        if (fresh := fresh_cached_result(item)) is not None
    }
    cache_output.update({clean(item.get("url")): item for item in results if clean(item.get("url"))})
    write_cache(args.cache.resolve() if args.cache else None, list(cache_output.values()))
    write_progress("COMPLETE")
    print(json.dumps({"urls": len(results), "counts": counts, "cache_hits": cache_hits, "cache_misses": len(pending_urls), "output": str(args.output.resolve())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
