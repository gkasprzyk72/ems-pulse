from flask import Flask, jsonify
from flask_cors import CORS
import feedparser
import datetime
import re
import time
import os
import threading
import requests
from dateutil import parser as dateparser

app = Flask(__name__)
CORS(app)

FEEDS = [
    {"name": "EMS1", "url": "https://www.ems1.com/rss.xml"},
    {"name": "JEMS", "url": "https://www.jems.com/feed/"},
    {"name": "FireRescue1", "url": "https://www.firerescue1.com/rss.xml"},
    {"name": "Firehouse", "url": "https://www.firehouse.com/rss.xml"},
    {"name": "EMS World", "url": "https://www.emsworld.com/rss.xml"},
]

# ---------- OIG Scanner ----------

OIG_BASE = "https://oig.hhs.gov/fraud/enforcement/"
OIG_TYPES = [
    "fraud-self-disclosures",
    "cmp-and-affirmative-exclusions",
    "criminal-and-civil-actions",
]
OIG_PAGES_PER_TYPE = 2

OIG_KEYWORDS = [
    "ambulance", "paramedic", "emt", "emergency medical",
    "fire department", "fire district", "ground ambulance",
]

OIG_TYPE_LABELS = [
    "CMP and Affirmative Exclusions",
    "Fraud Self-Disclosures",
    "Grant and Contractor Fraud Self-Disclosures",
    "Criminal and Civil Actions",
    "State Enforcement Agencies",
    "EMTALA/Patient Dumping",
    "Child Support",
    "CIA Reportable Events",
    "COVID-19",
    "Stipulated Penalties and Material Breaches",
]
OIG_DATE_RE = re.compile(
    r'(January|February|March|April|May|June|July|August|September|October|November|December|'
    r'Jan\.|Feb\.|Mar\.|Apr\.|Jun\.|Jul\.|Aug\.|Sep\.|Sept\.|Oct\.|Nov\.|Dec\.)\s+\d{1,2},?\s+\d{4}'
)
OIG_LINK_RE = re.compile(r'^/fraud/enforcement/[a-z0-9\-]+/?$')

_oig_cache = {"data": None, "timestamp": 0}
_oig_lock = threading.Lock()
OIG_CACHE_TTL = 6 * 60 * 60


def _list_oig_entries():
    from bs4 import BeautifulSoup
    entries = {}
    for page in range(1, OIG_PAGES_PER_TYPE + 1):
        try:
            params = [("type", t) for t in OIG_TYPES]
            params.append(("page", page))
            resp = requests.get(
                OIG_BASE, params=params,
                headers={"User-Agent": "Mozilla/5.0 (compatible; EMSPulseBot/1.0)"},
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all("a", href=OIG_LINK_RE)
            for a in links:
                href = a.get("href", "")
                title = a.get_text(strip=True)
                if not href or not title:
                    continue
                url = href if href.startswith("http") else f"https://oig.hhs.gov{href}"
                if url in entries:
                    continue
                container = a
                context_text = ""
                for _ in range(5):
                    if container.parent is None:
                        break
                    container = container.parent
                    context_text = container.get_text(" ", strip=True)
                    if OIG_DATE_RE.search(context_text):
                        break
                date_match = OIG_DATE_RE.search(context_text)
                date_str = date_match.group(0) if date_match else ""
                try:
                    parsed_date = dateparser.parse(date_str, fuzzy=True).isoformat() if date_str else ""
                except Exception:
                    parsed_date = ""
                found_types = [t for t in OIG_TYPE_LABELS if t in context_text]
                entries[url] = {
                    "title": title, "url": url, "date": date_str,
                    "publishedAt": parsed_date, "types": found_types,
                }
        except Exception as e:
            print(f"Error listing OIG page {page}: {e}")
    return list(entries.values())


def _matches_keywords(text):
    t = text.lower()
    return any(kw in t for kw in OIG_KEYWORDS)


def _fetch_detail_snippet(url):
    try:
        resp = requests.get(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; EMSPulseBot/1.0)"}, timeout=15,
        )
        if resp.status_code != 200:
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        main = soup.find("main") or soup
        paragraphs = [p.get_text(" ", strip=True) for p in main.find_all("p")]
        return " ".join(paragraphs)
    except Exception as e:
        print(f"Error fetching OIG detail {url}: {e}")
        return ""


def _fetch_oig_articles():
    entries = _list_oig_entries()
    matched = []
    for entry in entries:
        if _matches_keywords(entry["title"]):
            entry["summary"] = ""
            entry["matched_on"] = "title"
            matched.append(entry)
            continue
        body = _fetch_detail_snippet(entry["url"])
        if body and _matches_keywords(body):
            entry["summary"] = body[:300]
            entry["matched_on"] = "body"
            matched.append(entry)
    matched.sort(key=lambda x: x["publishedAt"] or "", reverse=True)
    return matched


@app.route("/oig")
def get_oig():
    now = time.time()
    with _oig_lock:
        stale = _oig_cache["data"] is None or (now - _oig_cache["timestamp"]) > OIG_CACHE_TTL
        if stale:
            _oig_cache["data"] = _fetch_oig_articles()
            _oig_cache["timestamp"] = now
        articles = _oig_cache["data"]
        cached_at = _oig_cache["timestamp"]
    return jsonify({"articles": articles, "count": len(articles), "cached_at": cached_at})

# ---------- Congress Tracker ----------

CONGRESS_API_KEY = os.environ.get("CONGRESS_API_KEY", "")
CONGRESS_BASE = "https://api.congress.gov/v3"
CONGRESS_NUM = "119"

WATCHLIST = [
    {"congress": "119", "type": "hr", "number": "9970", "label": "RESCUE Act of 2026 (H.R. 9970)"},
    {"congress": "119", "type": "hr", "number": "3443", "label": "When Minutes Count for Emergency Medical Patients Act (H.R. 3443)"},
    {"congress": "119", "type": "hr", "number": "4792", "label": "Protecting Air Ambulance Services for Americans Act of 2025 (H.R. 4792)"},
    {"congress": "119", "type": "hr", "number": "2232", "label": "Protecting Access to Ground Ambulance Medical Services Act of 2025 (H.R. 2232)"},
    {"congress": "119", "type": "hr", "number": "9212", "label": "VA Emergency Transportation Act (H.R. 9212)"},
    {"congress": "119", "type": "hr", "number": "7277", "label": "EMS Reimbursement for On-Scene and Support Act (H.R. 7277)"},
    {"congress": "119", "type": "s", "number": "3730", "label": "EMS Reimbursement for On-Scene Care and Support Act (S. 3730)"},
    {"congress": "119", "type": "s", "number": "1643", "label": "Protecting Access to Ground Ambulance Medical Services Act of 2025 (S. 1643)"},
]

CONGRESS_KEYWORDS = [
    "ambulance", "paramedic", "emt", "emergency medical",
    "ground ambulance", "air ambulance", "gemt", "ems provider",
    "ems agency", "fire department",
]

_congress_cache = {"data": None, "timestamp": 0}
_congress_lock = threading.Lock()
CONGRESS_CACHE_TTL = 12 * 60 * 60  # 12 hours


def _congress_get(path, params=None):
    params = dict(params or {})
    params["api_key"] = CONGRESS_API_KEY
    params["format"] = "json"
    resp = requests.get(f"{CONGRESS_BASE}{path}", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _bill_congress_url(congress, btype, number):
    chamber_slug = "house-bill" if btype.lower() == "hr" else "senate-bill" if btype.lower() == "s" else btype.lower()
    return f"https://www.congress.gov/bill/{congress}th-congress/{chamber_slug}/{number}"


def _fetch_watchlist():
    items = []
    for w in WATCHLIST:
        try:
            data = _congress_get(f"/bill/{w['congress']}/{w['type']}/{w['number']}")
            bill = data.get("bill", {})
            latest = bill.get("latestAction", {}) or {}
            sponsors = bill.get("sponsors") or []
            sponsor_name = sponsors[0].get("fullName", "") if sponsors else ""
            cosponsors = bill.get("cosponsors") or {}
            items.append({
                "label": w["label"],
                "title": bill.get("title", w["label"]),
                "congress": w["congress"],
                "type": w["type"].upper(),
                "number": w["number"],
                "introducedDate": bill.get("introducedDate", ""),
                "latestActionDate": latest.get("actionDate", ""),
                "latestActionText": latest.get("text", ""),
                "sponsor": sponsor_name,
                "cosponsorCount": cosponsors.get("count"),
                "url": _bill_congress_url(w["congress"], w["type"], w["number"]),
                "matched_on": "watchlist",
            })
        except Exception as e:
            print(f"Error fetching watchlist bill {w}: {e}")
    return items


def _matches_congress_keywords(text):
    t = (text or "").lower()
    return any(kw in t for kw in CONGRESS_KEYWORDS)


def _fetch_recent_scan(limit=100):
    items = []
    try:
        data = _congress_get(f"/bill/{CONGRESS_NUM}", params={"sort": "updateDate desc", "limit": limit})
        bills = data.get("bills", [])
        for b in bills:
            title = b.get("title", "") or ""
            if not _matches_congress_keywords(title):
                continue
            btype = (b.get("type") or "").lower()
            bnum = str(b.get("number", ""))
            latest = b.get("latestAction") or {}
            items.append({
                "label": f"{b.get('type','')} {bnum}",
                "title": title,
                "congress": str(b.get("congress", CONGRESS_NUM)),
                "type": b.get("type", ""),
                "number": bnum,
                "introducedDate": b.get("introducedDate", ""),
                "latestActionDate": latest.get("actionDate", ""),
                "latestActionText": latest.get("text", ""),
                "sponsor": "",
                "cosponsorCount": None,
                "url": _bill_congress_url(b.get("congress", CONGRESS_NUM), btype, bnum),
                "matched_on": "keyword-scan",
            })
    except Exception as e:
        print(f"Error scanning recent bills: {e}")
    return items


def _fetch_congress_data():
    if not CONGRESS_API_KEY:
        print("CONGRESS_API_KEY not set — skipping congress fetch")
        return []
    watchlist_items = _fetch_watchlist()
    scan_items = _fetch_recent_scan()
    watch_keys = {(w["congress"], w["type"].lower(), w["number"]) for w in watchlist_items}
    scan_items = [s for s in scan_items if (s["congress"], s["type"].lower(), s["number"]) not in watch_keys]
    all_items = watchlist_items + scan_items
    all_items.sort(key=lambda x: x.get("latestActionDate") or x.get("introducedDate") or "", reverse=True)
    return all_items


@app.route("/congress")
def get_congress():
    now = time.time()
    with _congress_lock:
        stale = _congress_cache["data"] is None or (now - _congress_cache["timestamp"]) > CONGRESS_CACHE_TTL
        if stale:
            _congress_cache["data"] = _fetch_congress_data()
            _congress_cache["timestamp"] = now
        bills = _congress_cache["data"]
        cached_at = _congress_cache["timestamp"]
    return jsonify({"bills": bills, "count": len(bills), "cached_at": cached_at})

# ---------- News Scanner (existing) ----------

EMS_KEYWORDS = [
    "ems", "ambulance", "paramedic", "emt", "emergency medical",
    "pre-hospital", "prehospital", "medic", "rescue", "first responder",
    "dispatch", "cardiac arrest", "air ambulance", "medevac"
]
def is_ems(title, summary):
    text = (title + " " + summary).lower()
    return any(kw in text for kw in EMS_KEYWORDS)
def classify(title, summary):
    t = (title + " " + summary).lower()
    if any(w in t for w in ["legislat","policy","law","bill","fund","budget","govern","contract"]):
        return "Policy & Law"
    if any(w in t for w in ["tech","drone","device","software","app","digital","sensor","telemedicine","ultrasound","ai "]):
        return "Technology"
    if any(w in t for w in ["train","certif","educat","course","protocol","simul","graduate","academy"]):
        return "Training"
    if any(w in t for w in ["community","volunt","rural","urban","equity","outreach","celebrat","honor","award"]):
        return "Community"
    return "Operations"
@app.route("/news")
def get_news():
    articles = []
    seen = set()
    for feed in FEEDS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries[:20]:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                url = entry.get("link", "")
                published = entry.get("published", entry.get("updated", ""))
                if not title or title in seen:
                    continue
                if not is_ems(title, summary):
                    continue
                seen.add(title)
                articles.append({
                    "id": str(hash(title)),
                    "title": title,
                    "url": url,
                    "source": feed["name"],
                    "publishedAt": published,
                    "summary": summary[:200] if summary else "",
                    "category": classify(title, summary),
                })
        except Exception as e:
            print(f"Error fetching {feed['name']}: {e}")
    articles.sort(key=lambda x: x["publishedAt"], reverse=True)
    return jsonify({"articles": articles, "count": len(articles)})
@app.route("/")
def index():
    return "EMS Pulse API is running."
if __name__ == "__main__":
    app.run(debug=True)
