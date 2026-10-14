"""
Weekly paper digest
===================
1. FETCH  - last week's papers from arXiv (by category) and from journals
            (via the Crossref API, by ISSN).
2. SCORE  - each paper against the weighted keywords in topics.yaml.
3. SAVE   - docs/digests/<date>.json, which the website in docs/ displays.

Run locally without saving anything:
    python digest.py --dry-run        # writes digest.html to open in a browser
"""
import argparse
import datetime as dt
import html
import json
import re
import time
import unicodedata
from pathlib import Path

import feedparser
import requests
import yaml

HERE = Path(__file__).parent
CONFIG = yaml.safe_load((HERE / "topics.yaml").read_text(encoding="utf-8"))
SEEN_FILE = HERE / "seen.json"          # IDs already saved (avoids repeats)
DOCS = HERE / "docs"

TODAY = dt.date.today()
START = TODAY - dt.timedelta(days=CONFIG["days_back"])
FEATURED_SCORE = CONFIG.get("featured_score", 8)   # score that makes a "top pick"
WATCHED = "Watched authors"                        # section for watched-author papers


# ------------------------------------------------------------------
# 1. FETCH
# ------------------------------------------------------------------
def clean(text, sep=" "):
    """Remove the XML/HTML tags and entities Crossref leaves in titles and abstracts."""
    text = html.unescape(re.sub(r"<[^>]+>", sep, text or ""))
    return " ".join(text.split())


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
            "source": "arXiv",
            "category": category,
            "kind": "arxiv",
            "published": e.published[:10],                      # first submission date
            "url": e.link,
        })
    return papers


def crossref_date(item):
    """Publication date as 'YYYY-MM-DD' (or 'YYYY-MM' when Crossref has no day)."""
    parts = (item.get("published") or item.get("issued") or {}).get("date-parts", [[]])[0]
    parts = [p for p in parts if p]
    return "-".join(f"{p:04d}" if i == 0 else f"{p:02d}" for i, p in enumerate(parts))


def fetch_journals():
    """Recent papers from every journal in topics.yaml, in one Crossref call."""
    filters = [f"from-pub-date:{START}", f"until-pub-date:{TODAY}"]
    filters += [f"issn:{issn}" for issn in CONFIG["journals"].values()]  # ISSNs are OR-ed
    params = {
        "filter": ",".join(filters),
        "rows": 1000,
        "select": "DOI,title,abstract,author,container-title,URL,published,issued",
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
        abstract = clean(item.get("abstract", ""))
        abstract = re.sub(r"^Abstract\s*[:.]?\s*", "", abstract, flags=re.I)  # leading label
        papers.append({
            "id": "doi:" + item["DOI"].lower(),
            "title": clean((item.get("title") or ["(untitled)"])[0], sep=""),
            "abstract": abstract,
            "authors": ", ".join(authors),
            "source": (item.get("container-title") or ["Journal"])[0],
            "category": "",
            "kind": "journal",
            "published": crossref_date(item),
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
def found(term, text):
    """True if `term` starts at a word boundary in `text`.

    Stems still work ("superconduct" matches "superconductor"), but a short
    keyword no longer matches in the middle of another word
    ("inas" used to match "kinase").
    """
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()), text) is not None


def plain(text):
    """Lower-case and strip accents, so 'Schäpers' matches 'Schapers'."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def topic_match(title, text, terms):
    """Return (score, matched keywords) for one topic. Title hits count double."""
    matched = [t for t in terms if found(t, text)]
    # A keyword written without a number in topics.yaml counts as weight 1
    score = sum((terms[t] or 1) * (2 if found(t, title) else 1) for t in matched)
    return score, matched


def classify(paper):
    """Score one paper. Returns None if it is not relevant, else the paper plus
    topic, score, matched keywords, watched authors and a top-pick flag."""
    title = paper["title"].lower()
    text = title + " " + paper["abstract"].lower()

    hits = {}                                    # topic -> (score, matched keywords)
    for topic, terms in CONFIG["topics"].items():
        score, matched = topic_match(title, text, terms)
        if score > 0:
            hits[topic] = (score, matched)

    authors = plain(paper["authors"])
    watched = [name for name in CONFIG.get("watch_authors", []) if plain(name) in authors]

    total = sum(s for s, _ in hits.values()) + (5 if watched else 0)
    if total < CONFIG["min_score"] and not watched:
        return None

    # File the paper under its best topic; watched-author papers with no topic
    # match get their own section.
    topic = max(hits, key=lambda t: hits[t][0]) if hits else WATCHED
    keywords = list(dict.fromkeys(k for _, matched in hits.values() for k in matched))
    paper.update(topic=topic, score=total, matched=keywords, watched=watched,
                 featured=bool(watched) or total >= FEATURED_SCORE)
    return paper


def group_by_topic(papers):
    """Group by topic, best first, capped per topic. Empty topics are dropped."""
    groups = {t: [] for t in list(CONFIG["topics"]) + [WATCHED]}
    for p in papers:
        groups.setdefault(p["topic"], []).append(p)

    result = {}
    for topic, items in groups.items():
        items.sort(key=lambda p: p.get("published", ""), reverse=True)   # newest first
        items.sort(key=lambda p: -p["score"])                            # then best first
        if items:
            result[topic] = items[:CONFIG["max_per_topic"]]
    return result


# ------------------------------------------------------------------
# 3. SAVE
# ------------------------------------------------------------------
def build_html(by_topic):
    """Simple HTML preview for --dry-run (the real site is docs/index.html)."""
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
                f" · <i>{html.escape(p['source'])}</i> · {p['published']} · score {p['score']}</span><br>"
                f"<span style='font-size:13px'>{html.escape(abstract)}</span></p>")
    parts.append("</div>")
    return "\n".join(parts), total


def load_existing(path):
    """Papers already saved today, so a second run adds to the digest
    instead of replacing it."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    papers = []
    for topic, items in data.get("topics", {}).items():
        for p in items:
            p.setdefault("topic", topic)
            p.setdefault("published", "")
            papers.append(p)
    return papers


FIELDS = ["id", "title", "authors", "abstract", "source", "category", "kind",
          "url", "published", "score", "matched", "watched", "featured"]


def save_digest(path, by_topic):
    """Write the digest JSON and update the archive index docs/digests.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(len(v) for v in by_topic.values())
    data = {
        "date": str(TODAY),
        "period": f"{START} to {TODAY}",
        "total": total,
        "topic_order": list(CONFIG["topics"]) + [WATCHED],   # keeps topic colours stable
        "topics": {topic: [{k: p.get(k) for k in FIELDS if k in p} for p in papers]
                   for topic, papers in by_topic.items()},
    }
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    # The index lists every digest; replace today's entry so re-runs stay accurate.
    index_file = DOCS / "digests.json"
    index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else []
    index = [d for d in index if d["date"] != str(TODAY)]
    index.append({"date": str(TODAY), "count": total})
    index.sort(key=lambda d: d["date"], reverse=True)
    index_file.write_text(json.dumps(index, indent=2), encoding="utf-8")

    print(f"Saved {total} papers to {path}")


# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="write digest.html, save nothing")
    args = parser.parse_args()

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    fresh = [p for p in fetch_all() if p["id"] not in seen]
    print(f"Fetched {len(fresh)} new papers")

    picked = [p for p in map(classify, fresh) if p]
    print(f"{len(picked)} match your topics")

    if args.dry_run:
        body, total = build_html(group_by_topic(picked))
        (HERE / "digest.html").write_text(body, encoding="utf-8")
        print(f"Wrote digest.html with {total} papers")
        return

    path = DOCS / "digests" / f"{TODAY}.json"
    merged = {p["url"]: p for p in load_existing(path) + picked}   # same-day re-run: keep both
    by_topic = group_by_topic(list(merged.values()))
    save_digest(path, by_topic)

    # Remember only what was actually saved
    seen |= {p["id"] for items in by_topic.values() for p in items if "id" in p}
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
