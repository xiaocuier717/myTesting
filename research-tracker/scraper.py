#!/usr/bin/env python3
"""Daily scraper for the latest AI + CAD testing research.

Sources: arXiv API and OpenAlex (journals / conferences). Standard library only.
Results are merged into site/data/papers.json, which the static page renders.

Usage:
    python scraper.py                  # fetch and update site/data/papers.json
    python scraper.py --dry-run        # fetch and print, don't write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_PATH = ROOT / "site" / "data" / "papers.json"
USER_AGENT = "ai-cad-research-tracker/1.0 (https://github.com/xiaocuier717/myTesting)"
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def http_get(url: str, retries: int = 4) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - network errors of all kinds
            if attempt == retries - 1:
                raise
            wait = 5 * 2**attempt
            # Rate limited (arXiv often 429s shared CI IPs): back off much longer.
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 503):
                retry_after = exc.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 30 * 2**attempt
            print(f"  retry in {wait}s after error: {exc}", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


# ---------------------------------------------------------------- arXiv
def fetch_arxiv(query: str, max_results: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    })
    root = ET.fromstring(http_get(f"https://export.arxiv.org/api/query?{params}"))
    papers = []
    for entry in root.findall("a:entry", ATOM):
        abs_url = clean(entry.findtext("a:id", namespaces=ATOM))
        arxiv_id = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
        pdf = next((l.get("href") for l in entry.findall("a:link", ATOM) if l.get("title") == "pdf"), None)
        papers.append({
            "id": f"arxiv:{arxiv_id}",
            "title": clean(entry.findtext("a:title", namespaces=ATOM)),
            "abstract": clean(entry.findtext("a:summary", namespaces=ATOM)),
            "authors": [clean(a.findtext("a:name", namespaces=ATOM)) for a in entry.findall("a:author", ATOM)],
            "published": clean(entry.findtext("a:published", namespaces=ATOM))[:10],
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf": pdf,
            "venue": "arXiv " + ", ".join(c.get("term", "") for c in entry.findall("a:category", ATOM)[:3]),
            "source": "arXiv",
        })
    return papers


# ---------------------------------------------------------------- OpenAlex
def openalex_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    words = sorted((pos, word) for word, positions in inverted.items() for pos in positions)
    return " ".join(w for _, w in words)


def fetch_openalex(query: str, since: str, max_results: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "search": query,
        "filter": f"from_publication_date:{since}",
        "sort": "publication_date:desc",
        "per-page": min(max_results, 200),
        "mailto": "research-tracker@users.noreply.github.com",
    })
    data = json.loads(http_get(f"https://api.openalex.org/works?{params}"))
    papers = []
    for w in data.get("results", []):
        title = clean(w.get("title"))
        if not title:
            continue
        doi = w.get("doi")
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        papers.append({
            "id": f"doi:{doi.lower().removeprefix('https://doi.org/')}" if doi else w["id"],
            "title": title,
            "abstract": clean(openalex_abstract(w.get("abstract_inverted_index"))),
            "authors": [clean((a.get("author") or {}).get("display_name")) for a in w.get("authorships", [])],
            "published": w.get("publication_date") or "",
            "url": doi or loc.get("landing_page_url") or w["id"],
            "pdf": (w.get("open_access") or {}).get("oa_url"),
            "venue": src.get("display_name") or "",
            "source": "OpenAlex",
        })
    return papers


# ---------------------------------------------------------------- relevance / tagging
def compile_terms(terms: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")s?\b", re.I)


def classify(papers: list[dict], cfg: dict) -> list[dict]:
    ai_re = compile_terms(cfg["relevance"]["ai_terms"])
    cad_re = compile_terms(cfg["relevance"]["cad_terms"])
    topic_res = {name: compile_terms(terms) for name, terms in cfg["topics"].items()}
    kept = []
    for p in papers:
        text = f"{p['title']} {p['abstract']}"
        if not (ai_re.search(text) and cad_re.search(text)):
            continue
        p["tags"] = [name for name, rx in topic_res.items() if rx.search(text)]
        # Score: testing-related work ranks first, then by topic coverage.
        p["score"] = (3 if "测试/评测" in p["tags"] else 0) + len(p["tags"])
        kept.append(p)
    return kept


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    since = (now - timedelta(days=cfg["lookback_days"])).date().isoformat()

    fetched: list[dict] = []
    errors: list[str] = []
    # One combined arXiv request: separate requests get rate limited (HTTP 429).
    arxiv_query = " OR ".join(f"({q})" for q in cfg["arxiv_queries"])
    print(f"arXiv: {len(cfg['arxiv_queries'])} queries combined")
    try:
        max_results = cfg["max_results_per_query"] * len(cfg["arxiv_queries"])
        fetched += [p for p in fetch_arxiv(arxiv_query, max_results) if p["published"] >= since]
    except Exception as exc:  # noqa: BLE001
        errors.append(f"arXiv: {exc}")
    for q in cfg["openalex_queries"]:
        print(f"OpenAlex: {q}")
        try:
            fetched += fetch_openalex(q, since, cfg["max_results_per_query"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"OpenAlex: {exc}")

    relevant = classify(fetched, cfg)
    print(f"fetched {len(fetched)}, relevant {len(relevant)}, errors {len(errors)}")
    for e in errors:
        print(f"  ERROR {e}", file=sys.stderr)

    if args.dry_run:
        for p in sorted(relevant, key=lambda p: p["published"], reverse=True)[:20]:
            print(f"{p['published']} [{', '.join(p['tags'])}] {p['title']}")
        return 0
    if not fetched and errors:
        print("all sources failed; keeping existing data", file=sys.stderr)
        return 1

    existing = {}
    if DATA_PATH.exists():
        existing = {p["id"]: p for p in json.loads(DATA_PATH.read_text(encoding="utf-8")).get("papers", [])}
    seen_titles = {norm_title(p["title"]): pid for pid, p in existing.items()}

    new_count = 0
    for p in relevant:
        key = norm_title(p["title"])
        pid = p["id"] if p["id"] in existing else seen_titles.get(key)
        if pid:
            # Keep the original discovery date; prefer arXiv record when both exist.
            p["first_seen"] = existing[pid]["first_seen"]
            if existing[pid]["source"] == "arXiv" and p["source"] != "arXiv":
                continue
            if pid != p["id"]:
                del existing[pid]
        else:
            p["first_seen"] = today
            new_count += 1
        existing[p["id"]] = p
        seen_titles[key] = p["id"]

    cutoff = (now - timedelta(days=cfg["keep_days"])).date().isoformat()
    papers = [p for p in existing.values() if p["first_seen"] >= cutoff]
    papers.sort(key=lambda p: (p["first_seen"], p["published"], p["score"]), reverse=True)

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({
        "updated": now.isoformat(timespec="seconds"),
        "new_today": new_count,
        "errors": errors,
        "papers": papers,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(papers)} papers ({new_count} new) to {DATA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
