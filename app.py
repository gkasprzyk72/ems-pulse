from flask import Flask, jsonify
from flask_cors import CORS
import feedparser
import datetime

app = Flask(__name__)
CORS(app)

FEEDS = [
    {"name": "EMS1", "url": "https://www.ems1.com/rss.xml"},
    {"name": "JEMS", "url": "https://www.jems.com/feed/"},
    {"name": "FireRescue1", "url": "https://www.firerescue1.com/rss.xml"},
    {"name": "Firehouse", "url": "https://www.firehouse.com/rss.xml"},
    {"name": "EMS World", "url": "https://www.emsworld.com/rss.xml"},
]

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
