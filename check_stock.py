"""
Stock checker for the Tesla Universal Wall Connector on Georgia Power Marketplace.

Looks at the add-to-cart button:
  - "Out of stock" / disabled (gray)  -> not in stock, exit quietly
  - "Add to cart" and enabled (blue)  -> IN STOCK, send a push via ntfy.sh
  - Button can't be found             -> exit with an error so GitHub emails you
                                         that the checker needs fixing

Env vars:
  NTFY_TOPIC   your private ntfy topic name (required)
  TEST_NOTIFY  set to "true" to send a test push and exit
"""

import os
import re
import sys
import urllib.request

PRODUCT_URL = "https://georgiapowermarketplace.com/electric-vehicle-chargers/P-TSLAUNIWALLCON.html"
PRODUCT_NAME = "Tesla Universal Wall Connector"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def notify(title: str, message: str, priority: str = "urgent", tags: str = "zap") -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set; can't send notification.")
        sys.exit(1)
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Click": PRODUCT_URL,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20)
    print(f"Notification sent: {title}")


def fetch_page() -> str:
    req = urllib.request.Request(PRODUCT_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip().lower()


def check_status(html: str) -> str:
    """Returns 'in_stock', 'out_of_stock', or 'unknown'."""
    # Find buttons that look like the add-to-cart button (SFCC uses class "add-to-cart")
    buttons = re.findall(r"<button\b([^>]*)>(.*?)</button>", html, flags=re.S | re.I)
    cart_buttons = [
        (attrs, strip_tags(inner))
        for attrs, inner in buttons
        if "add-to-cart" in attrs.lower()
        or "add to cart" in strip_tags(inner)
        or "out of stock" in strip_tags(inner)
    ]

    for attrs, text in cart_buttons:
        attrs_l = attrs.lower()
        disabled = re.search(r"\bdisabled\b", attrs_l) is not None
        if "out of stock" in text or disabled:
            return "out_of_stock"
        if "add to cart" in text:
            return "in_stock"

    # Fallback: plain text search of the whole page
    page_text = strip_tags(html)
    if "out of stock" in page_text:
        return "out_of_stock"
    if "add to cart" in page_text:
        return "in_stock"
    return "unknown"


def main() -> None:
    if os.environ.get("TEST_NOTIFY", "").lower() == "true":
        notify("Test: stock checker is working", f"You'll get an alert like this when the {PRODUCT_NAME} is back.",
               priority="default", tags="white_check_mark")
        return

    html = fetch_page()
    status = check_status(html)
    print(f"Status: {status}")

    if status == "in_stock":
        notify(f"{PRODUCT_NAME} is IN STOCK", "Blue Add to cart button is live. Tap to open the page.")
    elif status == "unknown":
        print("Couldn't find the add-to-cart button. The page layout may have changed.")
        sys.exit(1)  # failed run -> GitHub emails you


if __name__ == "__main__":
    main()
