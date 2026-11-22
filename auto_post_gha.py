import os, re, json, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

FEED = "https://garimpocerto.shop/feed.xml"
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
STORAGE = os.path.join(BASE, "x_storage_state.json")
MAX_POSTS = int(os.environ.get("GC_MAX_POSTS", "3"))
HASHTAGS = "#Achadinhos #Ofertas #DescontoReal"

def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)

def fetch_items():
    req = urllib.request.Request(FEED, headers={"User-Agent": "Mozilla/5.0 GarimpoCerto-AutoPost/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.findall(".//item"):
        g = (it.findtext("guid") or it.findtext("link") or "").strip()
        t = (it.findtext("title") or "").strip()
        l = (it.findtext("link") or "").strip()
        d = (it.findtext("description") or "").strip()
        if g and t and l:
            out.append({"guid": g, "title": t, "link": l, "desc": d})
    return out

def parse_offer(desc):
    m = re.search(r"Oferta por (R\$\s*[\d.,]+) na (.+?)\s*\.?\s*$", desc, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.search(r"(R\$\s*[\d.,]+)", desc)
    return (m.group(1).strip() if m else "Preço imperdível"), "loja parceira"

def make_tweet(item):
    price, store = parse_offer(item["desc"])
    tag = "#Shopee" if "shopee" in store.lower() else ("#MercadoLivre" if "mercado" in store.lower() else "")
    tags = HASHTAGS + ((" " + tag) if tag else "")
    prefix = "🔎 "
    middle = f"\n\n💰 {price} na {store}\n\n{tags}\n"
    room = 280 - len(prefix) - len(middle) - 23 - 1
    title = item["title"]
    if len(title) > room:
        title = title[: max(0, room - 1)].rstrip() + "…"
    return prefix + title + middle + item["link"]

def main():
    items = fetch_items()
    state = json.load(open(STATE_FILE, encoding="utf-8"))
    known = set(state["posted"])
    new_items = [i for i in items if i["guid"] not in known]
    if not new_items:
        log("NO_NEW_OFFERS")
        return
    to_post = list(reversed(new_items))[:MAX_POSTS]
    log(f"NEW_OFFERS {len(new_items)} -> posting {len(to_post)}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=STORAGE)
        page = ctx.new_page()
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=60000)
        for item in to_post:
            text = make_tweet(item)
            try:
                page.click('[data-testid="tweetTextarea_0"]')
                page.keyboard.insert_text(text)
                page.wait_for_timeout(800)
                btn = page.locator('[data-testid="tweetButtonInline"]')
                if btn.is_disabled():
                    raise RuntimeError("post button disabled (session expired?)")
                btn.click()
                page.wait_for_timeout(3000)
                left = page.eval_on_selector('[data-testid="tweetTextarea_0"]', "e=>(e.innerText||'').trim().length")
                if left:
                    raise RuntimeError("post may not have been sent")
                state["posted"].append(item["guid"])
                json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                log(f"POSTED {item['guid']}")
            except Exception as e:
                log(f"POST_ERROR {item['guid']} {e}")
                break
        ctx.close()
        browser.close()
    log("RUN_DONE")

if __name__ == "__main__":
    main()
