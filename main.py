import os
import sys
import time
import json
import requests
import urllib3
from threading import Thread
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from flask import Flask, request
from html import escape as html_escape

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- Configuration (edit via env vars if needed) ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))  # seconds
FORCE_TOKEN = os.getenv("FORCE_TOKEN", None)
STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")

# ---------- Keywords & Sites ----------
KEYWORDS = [
    "dental", "dentist", "assistant surgeon",
    "neet mds", "neet mds 2025", "neet mds 2026"
]
SITES = {
    "mrb": {
        "url": "https://www.mrb.tn.gov.in/notifications.html",
        "base_url": "https://www.mrb.tn.gov.in/"
    },
    "neetmds": {
        "url": "https://natboard.edu.in/viewnbeexam?exam=neetmds",
        "base_url": "https://natboard.edu.in/"
    },
    "nbehome": {
        "url": "https://natboard.edu.in/index",
        "base_url": "https://natboard.edu.in/"
    },
    "mcc_mds_counselling": {
        "url": "https://mcc.nic.in/mds-counselling/",
        "base_url": "https://mcc.nic.in/"
    },
    "tnmedicalselection": {
        "url": "https://tnmedicalselection.net/Notification.aspx",
        "base_url": "https://tnmedicalselection.net/"
    }
}

# ---------- Helpers: persistence ----------
def links_file_for(site_name):
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
    except Exception:
        pass
    return os.path.join(STORAGE_DIR, f"seen_links_{site_name}.json")

def load_seen_links(site_name):
    fpath = links_file_for(site_name)
    if not os.path.exists(fpath):
        return None  # first run
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception as e:
        print(f"⚠️ Failed to load {fpath}: {e}")
        return set()

def save_seen_links(site_name, links_set):
    fpath = links_file_for(site_name)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(list(links_set), f)
    except Exception as e:
        print(f"⚠️ Failed to save {fpath}: {e}")

# ---------- Telegram ----------
def send_telegram_message(html_message):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN or CHAT_ID not set!")
        return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": html_message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("✅ Telegram alert sent.")
            return True
        else:
            print(f"⚠️ Telegram send error: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"❌ Telegram exception: {e}")
        return False

# ---------- Fetch with retry ----------
def fetch_with_retry(url, retries=3, delay=5):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, verify=False, timeout=20, headers=headers)
            resp.raise_for_status()
            return resp
        except Exception as e:
            print(f"⚠️ Attempt {attempt} failed for {url}: {e}")
            if attempt < retries:
                time.sleep(delay)
    return None

# ---------- Core scraping / compare logic ----------
def check_site(site_key, site_info):
    print(f"🔍 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking {site_key}...")
    last_links = load_seen_links(site_key)
    resp = fetch_with_retry(site_info["url"])
    if not resp:
        print(f"❌ Failed to fetch {site_key} after retries.")
        return
    soup = BeautifulSoup(resp.content, "html.parser")
    anchors = soup.find_all("a", href=True)
    current_links = set()
    for a in anchors:
        title_raw = " ".join(a.get_text(separator=" ").split())
        title_lower = title_raw.lower()
        if any(kw in title_lower for kw in KEYWORDS):
            full_url = urljoin(site_info["base_url"], a['href'].strip())
            current_links.add(f"{title_raw}::{full_url}")
    if last_links is None:
        save_seen_links(site_key, current_links)
        print(f"ℹ️ Initialized {site_key} list — skipped sending.")
        return
    new_items = current_links - last_links
    if new_items:
        print(f"🚨 {len(new_items)} new update(s) on {site_key}.")
        send_in_chunks(site_key, new_items)
        last_links.update(new_items)
        save_seen_links(site_key, last_links)
    else:
        print(f"ℹ️ No new updates for {site_key}.")

def send_in_chunks(site_key, items):
    batch = []
    total_len = 0
    for itm in sorted(items):
        try:
            heading, url = itm.split("::", 1)
        except ValueError:
            heading, url = itm, itm
        part = f'<a href="{html_escape(url, quote=True)}">{html_escape(heading)}</a>'
        if total_len + len(part) > 3500:
            message = f"🚨 <b>{html_escape(site_key.upper())} Update(s)</b>\n\n" + "\n\n".join(batch)
            send_telegram_message(message)
            batch, total_len = [], 0
        batch.append(part)
        total_len += len(part)
    if batch:
        message = f"🚨 <b>{html_escape(site_key.upper())} Update(s)</b>\n\n" + "\n\n".join(batch)
        send_telegram_message(message)

# ---------- Worker loop ----------
def worker_loop():
    print(f"🟢 Worker starting (interval {POLL_INTERVAL}s)...")
    while True:
        try:
            for k, info in SITES.items():
                check_site(k, info)
        except Exception as e:
            print("❌ Unhandled exception in worker loop:", e)
        print(f"✅ Sleeping {POLL_INTERVAL} seconds...\n")
        time.sleep(POLL_INTERVAL)

# ---------- Flask app (health + optional trigger) ----------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    return "OK"

@app.route("/health", methods=["GET"])
def health():
    return "healthy"

@app.route("/force-check", methods=["GET"])
def force_check():
    token = request.args.get("token", None)
    if FORCE_TOKEN and token != FORCE_TOKEN:
        return "Unauthorized", 403
    def run_once():
        print("🔁 /force-check triggered — running one immediate pass.")
        try:
            for k, info in SITES.items():
                check_site(k, info)
        except Exception as e:
            print("❌ Error in force-check:", e)
    Thread(target=run_once, daemon=True).start()
    return "Triggered", 200

# ---------- Entrypoint ----------
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        send_telegram_message("🚨 <b>TEST ALERT</b> — This is a test from your bot ✅")
        sys.exit(0)
    t = Thread(target=worker_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", "10000"))
    print(f"🌐 Starting web server on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
