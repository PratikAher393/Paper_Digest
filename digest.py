"""
Weekly paper digest
===================
1. FETCH  - last week's papers from arXiv (by category) and from journals
            (via the Crossref API, by ISSN).
2. SCORE  - each paper against the weighted keywords in topics.yaml.
3. SEND   - an HTML email grouped by topic, best matches first.

Run locally without sending any mail:
    python digest.py --dry-run        # writes digest.html to open in a browser
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests
import yaml

HERE = Path(__file__).parent
CONFIG = yaml.safe_load((HERE / "topics.yaml").read_text(encoding="utf-8"))
SEEN_FILE = HERE / "seen.json"          # IDs already emailed (avoids repeats)

TODAY = dt.date.today()
START = TODAY - dt.timedelta(days=CONFIG["days_back"])


# ------------------------------------------------------------------
# 1. FETCH
# ------------------------------------------------------------------
def fetch_arxiv(category):
    """All papers submitted to one arXiv category inside the date window."""
    window = f"[{START:%Y%m%d}0000 TO {TODAY:%Y%m%d}2359]"
    params = {
        "search_query": f"cat:{category} AND submittedDate:{window}",
        "max_results": 2000,
        "sortBy": "submittedDate",
    }
    resp = requests.get("https://export.arxiv.org/api/query", params=params, timeout=90)
    feed = feedparser.parse(resp.text)

    papers = []
    for e in feed.entries:
        arxiv_id = e.id.split("/abs/")[-1].rsplit("v", 1)[0]   # drop version "v2"
        papers.append({
            "id": "arxiv:" + arxiv_id,
            "title": " ".join(e.title.split()),                 # collapse line breaks
            "abstract": " ".join(e.summary.split()),
            "authors": ", ".join(a.name for a in e.get("authors", [])),
            "source": f"arXiv · {category}",
            "url": e.link,
        })
    return papers


def fetch_journals():
    """Recent papers from every journal in topics.yaml, in one Crossref call."""
    filters = [f"from-pub-date:{START}", f"until-pub-date:{TODAY}"]
    filters += [f"issn:{issn}" for issn in CONFIG["journals"].values()]  # ISSNs are OR-ed
    params = {
        "filter": ",".join(filters),
        "rows": 1000,
        "select": "DOI,title,abstract,author,container-title,URL",
    }
    headers = {"User-Agent": f"paper-digest (mailto:{CONFIG['contact_email']})"}
    msg = requests.get("https://api.crossref.org/works", params=params,
                       headers=headers, timeout=90).json()["message"]

    if msg["total-results"] > 1000:
        print(f"Warning: {msg['total-results']} journal papers, only first 1000 scored")

    papers = []
    for item in msg["items"]:
        authors = [f"{a.get('given', '')} {a.get('family', '')}".strip()
                   for a in item.get("author", [])]
        abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", ""))  # strip JATS tags
        papers.append({
            "id": "doi:" + item["DOI"].lower(),
            "title": " ".join((item.get("title") or ["(untitled)"])[0].split()),
            "abstract": " ".join(abstract.split()),
            "authors": ", ".join(authors),
            "source": (item.get("container-title") or ["Journal"])[0],
            "url": item["URL"],
        })
    return papers


def fetch_all():
    """Run every fetcher; one failing source should not kill the whole digest."""
    papers = []
    for cat in CONFIG["arxiv_categories"]:
        try:
            papers += fetch_arxiv(cat)
        except Exception as err:
            print(f"arXiv {cat} failed: {err}")
        time.sleep(3)                     # arXiv asks for >= 3 s between API calls
    try:
        papers += fetch_journals()
    except Exception as err:
        print(f"Crossref failed: {err}")

    unique = {p["id"]: p for p in papers}   # de-duplicate cross-listed papers
    return list(unique.values())


# ------------------------------------------------------------------
# 2. SCORE
# ------------------------------------------------------------------
def topic_score(title, text, terms):
    """Sum of keyword weights found in the text; title hits count double."""
    return sum(w * (2 if t.lower() in title else 1)
               for t, w in terms.items() if t.lower() in text)


def score(paper):
    """Return {topic: score} for every topic this paper matches."""
    title = paper["title"].lower()
    text = title + " " + paper["abstract"].lower()

    hits = {topic: topic_score(title, text, terms)
            for topic, terms in CONFIG["topics"].items()}
    hits = {t: s for t, s in hits.items() if s > 0}

    authors = paper["authors"].lower()
    if any(name.lower() in authors for name in CONFIG.get("watch_authors", [])):
        hits["Watched authors"] = 5
    return hits


# ------------------------------------------------------------------
# 3. BUILD + SEND
# ------------------------------------------------------------------
def build_html(by_topic):
    """Simple, email-safe HTML (inline styles only)."""
    total = sum(len(v) for v in by_topic.values())
    parts = [f"<div style='font-family:Georgia,serif;max-width:720px'>"
             f"<h2>Papers {START:%b %d} – {TODAY:%b %d}: {total} matches</h2>"]

    for topic, papers in by_topic.items():
        parts.append(f"<h3 style='border-bottom:1px solid #ccc'>{html.escape(topic)} "
                     f"({len(papers)})</h3>")
        for p in papers:
            abstract = p["abstract"][:400] + ("…" if len(p["abstract"]) > 400 else "")
            parts.append(
                f"<p><a href='{p['url']}' style='font-size:15px'><b>{html.escape(p['title'])}</b></a><br>"
                f"<span style='color:#555;font-size:12px'>{html.escape(p['authors'][:200])}"
                f" · <i>{html.escape(p['source'])}</i> · score {p['score']}</span><br>"
                f"<span style='font-size:13px'>{html.escape(abstract)}</span></p>")
    parts.append("</div>")
    return "\n".join(parts), total


def send_email(body, total):
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = f"Paper digest {START:%b %d}–{TODAY:%b %d}: {total} papers"
    msg["From"] = os.environ["EMAIL_USER"]
    msg["To"] = os.environ["EMAIL_TO"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["EMAIL_USER"], os.environ["EMAIL_APP_PASSWORD"])
        server.send_message(msg)


# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="write digest.html, don't email")
    args = parser.parse_args()

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    papers = [p for p in fetch_all() if p["id"] not in seen]
    print(f"Fetched {len(papers)} new papers")

    # Score, then file each paper under its single best-matching topic
    by_topic = {t: [] for t in list(CONFIG["topics"]) + ["Watched authors"]}
    for p in papers:
        hits = score(p)
        p["score"] = sum(hits.values())
        if p["score"] >= CONFIG["min_score"] or "Watched authors" in hits:
            best = "Watched authors" if "Watched authors" in hits else max(hits, key=hits.get)
            by_topic[best].append(p)

    # Best first, capped per topic, empty topics removed
    by_topic = {t: sorted(v, key=lambda p: -p["score"])[:CONFIG["max_per_topic"]]
                for t, v in by_topic.items() if v}

    body, total = build_html(by_topic)
    if args.dry_run:
        (HERE / "digest.html").write_text(body, encoding="utf-8")
        print(f"Wrote digest.html with {total} papers")
        return

    if total:
        send_email(body, total)
        print(f"Emailed {total} papers")
    seen |= {p["id"] for v in by_topic.values() for p in v}
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
