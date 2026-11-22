import os, re, json, random, requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

SUPA = "https://inzlflseilmmibcqajil.supabase.co/rest/v1/products"
SUPA_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImluemxmbHNlaWxtbWliY3FhamlsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU0NDA4MTcsImV4cCI6MjA5MTAxNjgxN30.hksTxP9V0DGsN-tMCcHWQUVOzYBPApWT18g4fQMqkAg"
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
QUEUE_FILE = os.path.join(BASE, "queue.json")
STORAGE = os.path.join(BASE, "x_storage_state.json")
STORES = ["Mercado Livre", "Shopee", "Amazon"]
PER_STORE = int(os.environ.get("GC_PER_STORE", "10"))
MAX_PER_RUN = 2
HASHTAGS = "#Achadinhos #Ofertas #DescontoReal"


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def load_json(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def fetch_products():
    r = requests.get(SUPA, headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}"},
                     params={"select": "id,title,old_price,new_price,discount_badge,store_name,store_url,created_at",
                             "order": "created_at.desc", "limit": 1000}, timeout=30)
    r.raise_for_status()
    return r.json()


def discount_value(p):
    m = re.search(r"(\d+(?:\.\d+)?)", p.get("discount_badge") or "")
    if m:
        return float(m.group(1))
    old, new = p.get("old_price") or 0, p.get("new_price") or 0
    return round((old - new) / old * 100) if old > new else 0


def normalize_store(name):
    n = (name or "").lower()
    if "shopee" in n:
        return "Shopee"
    if "mercado" in n or "ml" == n:
        return "Mercado Livre"
    if "amazon" in n:
        return "Amazon"
    return name or "Amazon"


def make_tweet(item):
    price = f"R$ {float(item['new_price']):.2f}".replace(".", ",")
    store = item["store_name"]
    badge = item.get("discount_badge") or f"-{int(discount_value(item))}%"
    prefix = "🔎 "
    middle = f"\n\n🏷️ {badge} · 💰 {price} na {store}\n\n{HASHTAGS}\n"
    room = 280 - len(prefix) - len(middle) - 23 - 1
    title = item["title"]
    if len(title) > room:
        title = title[: max(0, room - 1)].rstrip() + "…"
    return prefix + title + middle + item["store_url"]


def build_queue(state):
    products = fetch_products()
    posted = set(state["posted"])
    candidates = []
    for p in products:
        if p.get("id") in posted or not p.get("store_url") or not p.get("title"):
            continue
        if discount_value(p) <= 0:
            continue
        p["store_name"] = normalize_store(p.get("store_name"))
        p["discount_value"] = discount_value(p)
        candidates.append(p)
    picks = []
    for store in STORES:
        pool = sorted([p for p in candidates if p["store_name"] == store],
                      key=lambda x: x["discount_value"], reverse=True)[: max(PER_STORE * 3, PER_STORE)]
        picks.extend(pool[:PER_STORE])
    random.shuffle(picks)
    now = datetime.now(timezone.utc)
    gap = (24 * 60) / max(1, len(picks))
    queue = []
    for i, p in enumerate(picks):
        t = now + timedelta(minutes=2 + i * gap + random.randint(-8, 8))
        queue.append({"id": p["id"], "title": p["title"], "new_price": p["new_price"],
                      "discount_badge": p.get("discount_badge") or f"-{int(p['discount_value'])}%",
                      "store_name": p["store_name"], "store_url": p["store_url"],
                      "time": t.isoformat(), "posted": False})
    save_json(QUEUE_FILE, {"date": now.date().isoformat(), "items": queue})
    log(f"QUEUE_BUILT stores={ {s: sum(1 for i in queue if i['store_name'] == s) for s in STORES} } total={len(queue)}")
    return queue


def main():
    state = load_json(STATE_FILE, {"posted": []})
    queue_data = load_json(QUEUE_FILE, {})
    today = datetime.now(timezone.utc).date().isoformat()
    queue = queue_data.get("items") if queue_data.get("date") == today else None
    if not queue or all(i["posted"] for i in queue):
        queue = build_queue(state)
    now = datetime.now(timezone.utc)
    due = [i for i in queue if not i["posted"] and datetime.fromisoformat(i["time"]) <= now]
    if not due:
        log("NO_DUE_POSTS")
        return
    due = due[:MAX_PER_RUN]
    log(f"DUE {len(due)}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=STORAGE)
        page = ctx.new_page()
        for item in due:
            text = make_tweet(item)
            try:
                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=60000)
                except Exception:
                    page.screenshot(path="fail.png")
                    log("DEBUG_URL " + page.url)
                    log("DEBUG_TEXT " + (page.locator("body").inner_text() or "")[:500].replace("\n", " | "))
                    raise
                page.click('[data-testid="tweetTextarea_0"]')
                page.keyboard.insert_text(text)
                page.wait_for_timeout(800)
                btn = page.locator('[data-testid="tweetButtonInline"]')
                if btn.is_disabled():
                    raise RuntimeError("post button disabled (session expired?)")
                btn.click()
                page.wait_for_timeout(5000)
                left = page.eval_on_selector('[data-testid="tweetTextarea_0"]', "e=>(e.innerText||'').trim().length")
                if left:
                    page.screenshot(path="fail.png")
                    raise RuntimeError("post may not have been sent")
                item["posted"] = True
                if item["id"] not in state["posted"]:
                    state["posted"].append(item["id"])
                save_json(QUEUE_FILE, {"date": today, "items": queue})
                save_json(STATE_FILE, state)
                log(f"POSTED {item['store_name']} {item['id']}")
            except Exception as e:
                log(f"POST_ERROR {item['id']} {e}")
                break
        ctx.close()
        browser.close()
    log("RUN_DONE")


if __name__ == "__main__":
    main()
