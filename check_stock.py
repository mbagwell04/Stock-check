"""
Stock checker v2 for the Tesla Universal Wall Connector on Georgia Power Marketplace.

The add-to-cart button is drawn by JavaScript, so this version reads the
site's underlying stock data instead, trying several sources in order:
  1. Salesforce Commerce "Product-Variation" JSON (the data the button is built from)
  2. data-available / data-ready-to-order attributes in the page
  3. schema.org availability (InStock / OutOfStock) embedded in the page
  4. Button text, as a last resort

If none work, it prints diagnostics and fails, so GitHub emails you.

Env vars:
  NTFY_TOPIC   your private ntfy topic name (required)
  TEST_NOTIFY  set to "true" to send a test push and exit
"""

import html as htmllib
import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE = "https://georgiapowermarketplace.com"
PAGES = [
    BASE + "/electric-vehicle-chargers/I-TSLAUNIWALLCON-01-WHTE-1772-V1.html",  # the specific variant
    BASE + "/electric-vehicle-chargers/P-TSLAUNIWALLCON.html",                  # the product page you shared
]
PRODUCT_URL = PAGES[1]
PRODUCT_NAME = "Tesla Universal Wall Connector"
VARIANT_ID = "I-TSLAUNIWALLCON-01-WHTE-1772-V1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------- helpers ----------

def notify(title, message, priority="urgent", tags="zap"):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set; can't send notification.")
        sys.exit(1)
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags, "Click": PRODUCT_URL},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20)
    print(f"Notification sent: {title}")


def fetch(url, accept_json=False):
    headers = dict(HEADERS)
    if accept_json:
        headers["Accept"] = "application/json, text/javascript, */*;q=0.01"
        headers["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip().lower()


# ---------- signal readers (each returns 'in_stock', 'out_of_stock', or None) ----------

def from_variation_json(page_html):
    """Find the Product-Variation / Product-Show endpoint in the page and read its JSON."""
    raw_urls = re.findall(r'["\']([^"\']*Product-(?:Variation|Show)[^"\']*)["\']', page_html)
    candidates = []
    for u in raw_urls:
        u = htmllib.unescape(u)
        if u.startswith("/"):
            u = BASE + u
        if not u.startswith("http"):
            continue
        candidates.append(u)

    # Also build the standard endpoint from any Sites-XXX-Site path we can see
    site = re.search(r"/on/demandware\.store/(Sites-[^/]+-Site)/([^/]+)/", page_html)
    if site:
        candidates.append(
            f"{BASE}/on/demandware.store/{site.group(1)}/{site.group(2)}/Product-Variation?"
            + urllib.parse.urlencode({"pid": VARIANT_ID, "quantity": 1})
        )

    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if "pid=" not in url:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode({"pid": VARIANT_ID})
        try:
            _, body = fetch(url, accept_json=True)
            data = json.loads(body)
        except Exception as e:
            print(f"  variation endpoint failed: {url[:120]} ({e.__class__.__name__})")
            continue
        product = data.get("product", data)
        print(f"  variation endpoint OK: {url[:120]}")
        print(f"    available={product.get('available')} readyToOrder={product.get('readyToOrder')} "
              f"messages={(product.get('availability') or {}).get('messages')}")
        if product.get("readyToOrder") is True and product.get("available") is True:
            return "in_stock"
        if product.get("available") is False or product.get("readyToOrder") is False:
            return "out_of_stock"
    return None


def from_data_attributes(page_html):
    avail = re.findall(r'data-(?:available|ready-to-order)\s*=\s*["\'](true|false)["\']', page_html, re.I)
    if avail:
        print(f"  data attributes: {avail}")
        vals = [a.lower() for a in avail]
        if "false" in vals:
            return "out_of_stock"
        return "in_stock"
    return None


def from_schema(page_html):
    hits = re.findall(r'schema\.org/(InStock|OutOfStock|PreOrder|BackOrder|SoldOut|Discontinued|LimitedAvailability)',
                      page_html, re.I)
    if hits:
        print(f"  schema.org availability: {hits}")
        vals = [h.lower() for h in hits]
        if any(v in ("instock", "limitedavailability") for v in vals) and not any(
            v in ("outofstock", "soldout", "discontinued") for v in vals
        ):
            return "in_stock"
        return "out_of_stock"
    return None


def from_button(page_html):
    for attrs, inner in re.findall(r"<button\b([^>]*)>(.*?)</button>", page_html, flags=re.S | re.I):
        text = strip_tags(inner)
        if "add-to-cart" in attrs.lower() or "add to cart" in text or "out of stock" in text:
            print(f"  button: '{text}' disabled={'disabled' in attrs.lower()}")
            if "out of stock" in text or re.search(r"\bdisabled\b", attrs.lower()):
                return "out_of_stock"
            if "add to cart" in text:
                return "in_stock"
    return None


def diagnostics(page_html):
    print("  --- diagnostics ---")
    print(f"  length={len(page_html)}")
    title = re.search(r"<title>(.*?)</title>", page_html, re.S | re.I)
    print(f"  title={strip_tags(title.group(1)) if title else None}")
    buttons = [strip_tags(b)[:60] for b in re.findall(r"<button\b[^>]*>(.*?)</button>", page_html, re.S | re.I)]
    print(f"  buttons={buttons[:15]}")
    for kw in ("stock", "availab", "cart", "demandware.store", "Product-"):
        lines = [l.strip()[:160] for l in page_html.splitlines() if kw.lower() in l.lower()]
        print(f"  lines with '{kw}' ({len(lines)}):")
        for l in lines[:8]:
            print(f"    {l}")


# ---------- main ----------

def main():
    if os.environ.get("TEST_NOTIFY", "").lower() == "true":
        notify("Test: stock checker is working",
               f"You'll get an alert like this when the {PRODUCT_NAME} is back.",
               priority="default", tags="white_check_mark")
        return

    status = None
    pages = {}
    for url in PAGES:
        try:
            code, page_html = fetch(url)
        except Exception as e:
            print(f"Fetch failed for {url}: {e}")
            continue
        pages[url] = page_html
        print(f"Fetched {url} (HTTP {code}, {len(page_html)} chars)")
        for reader in (from_variation_json, from_data_attributes, from_schema, from_button):
            status = reader(page_html)
            if status:
                print(f"  -> {reader.__name__}: {status}")
                break
        if status:
            break

    print(f"Status: {status or 'unknown'}")

    if status == "in_stock":
        notify(f"{PRODUCT_NAME} is IN STOCK", "Add to cart is live. Tap to open the page.")
    elif status is None:
        for url, page_html in pages.items():
            print(f"\nDiagnostics for {url}")
            diagnostics(page_html)
        print("\nCouldn't determine stock status. Send these logs to Claude.")
        sys.exit(1)


if __name__ == "__main__":
    main()
