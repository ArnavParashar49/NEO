"""Topic images for the HUD visual grid — Google Images for topics, Flipkart for products."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs

import requests

from actions.product_parse import extract_product_names

_PRODUCT_RE = re.compile(
    r"\b(tv|television|laptop|phone|tablet|monitor|camera|headphone|speaker|"
    r"fridge|refrigerator|washing|dryer|ac|air conditioner|product|buy|price|"
    r"rupee|rupees|₹|flipkart|model|inch|oled|qled|bravia|pixel|galaxy|"
    r"iphone|oneplus|vivo|iqoo|motorola|realme|"
    r"table|tables|furniture|sofa|couch|chair|desk|ottoman|console|nesting|"
    r"living\s+room|bedroom|wardrobe|mattress|decor|lamp|bookshelf)\b",
    re.IGNORECASE,
)

_CATEGORY_RE = re.compile(
    r"(?:^\s*\d+[\.\)]\s*)?\*\*([^*:\n]+?)\*\*:?",
    re.IGNORECASE | re.MULTILINE,
)

_FLIPKART_DOMAIN = "flipkart.com"

# Hosts/patterns to skip when parsing Google Images results
_BAD_IMG_RE = re.compile(
    r"(?:google\.com|gstatic\.com|googleusercontent\.com/(?:images|icons)|"
    r"encrypted-tbn0\.gstatic\.com|flipkart\.com|amazon\.|"
    r"logo|icon|favicon|sprite|placeholder)",
    re.IGNORECASE,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
}


def _config_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config"
    return Path(__file__).resolve().parent.parent / "config"


def _read_config() -> dict:
    try:
        return json.loads((_config_dir() / "api_keys.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _google_cse_image_urls(query: str, max_results: int = 6) -> list[str]:
    """Google Custom Search JSON API (image search) when google_cse_id is configured."""
    cfg = _read_config()
    api_key = cfg.get("gemini_api_key") or cfg.get("google_api_key") or ""
    cse_id = cfg.get("google_cse_id") or cfg.get("google_search_cx") or ""
    if not api_key or not cse_id:
        return []

    urls: list[str] = []
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cse_id,
                "q": query,
                "searchType": "image",
                "num": min(max_results, 10),
                "safe": "active",
            },
            timeout=12,
        )
        r.raise_for_status()
        for item in r.json().get("items") or []:
            u = item.get("link") or ""
            if _is_usable_image_url(u):
                urls.append(u)
    except Exception as e:
        print(f"[VisualFeed] ⚠️ Google CSE: {e}")
    return urls[:max_results]


def _ddgs_class():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        return DDGS


def _clean_redirect_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "duckduckgo.com/l/" in u:
        qs = parse_qs(urlparse(u).query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return u


def _normalize_product_url(url: str, domain: str) -> str:
    url = _clean_redirect_url(url)
    if "amazon.in" in domain or "amazon.in" in url:
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url, re.I)
        if m:
            return f"https://www.amazon.in/dp/{m.group(1).upper()}"
        return ""
    if "flipkart.com" in domain or "flipkart.com" in url:
        if "/p/" in url and "flipkart.com" in url:
            base = url.split("?")[0]
            return base if base.startswith("http") else ""
        return ""
    parsed = urlparse(url)
    if domain in (parsed.netloc or ""):
        return url.split("?")[0] if url.startswith("http") else ""
    return ""


def _is_product_url(url: str, domain: str) -> bool:
    return bool(_normalize_product_url(url, domain))


def _ddg_text(query: str, max_results: int = 8) -> list[dict]:
    DDGS = _ddgs_class()
    out: list[dict] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            out.append({
                "title":   r.get("title", ""),
                "snippet": r.get("body", r.get("snippet", "")),
                "url":     _clean_redirect_url(r.get("href", r.get("url", ""))),
            })
    return out


def _ddg_images(query: str, max_results: int = 5) -> list[str]:
    DDGS = _ddgs_class()
    urls: list[str] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.images(query, max_results=max_results):
                u = r.get("image") or r.get("thumbnail") or ""
                if u.startswith("http") and not _BAD_IMG_RE.search(u):
                    urls.append(u)
    except Exception as e:
        print(f"[VisualFeed] ⚠️ Image search: {e}")
    return urls


def _is_usable_image_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    if _BAD_IMG_RE.search(url):
        return False
    if len(url) > 2048:
        return False
    return True


def _google_image_urls(query: str, max_results: int = 6) -> list[str]:
    """Topic images: Google CSE when configured, else filtered web image search."""
    q = re.sub(r"\s+buy india$", "", query.strip(), flags=re.I)
    if not q:
        return []

    urls = _google_cse_image_urls(q, max_results)
    if urls:
        return urls

    # Google Images HTML requires JS — use open image index, exclude shopping hosts
    _SHOP_HOST = re.compile(r"flipkart|amazon\.|croma\.|reliancedigital|ebay|aliexpress", re.I)
    for u in _ddg_images(q, max_results=max_results * 3):
        if u in urls or _SHOP_HOST.search(u):
            continue
        urls.append(u)
        if len(urls) >= max_results:
            break

    return urls


def _scrape_product_image(page_url: str) -> str:
    headers = dict(_HEADERS)
    headers["Referer"] = page_url
    try:
        from bs4 import BeautifulSoup

        r = requests.get(page_url, headers=headers, timeout=14)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        for prop in ("og:image", "twitter:image"):
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content", "").startswith("http"):
                return tag["content"]
            tag = soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content", "").startswith("http"):
                return tag["content"]
        if "flipkart" in page_url:
            for sel in ("img[class*='_396cs4']", "img[class*='DByuf']", "img"):
                img = soup.select_one(sel)
                if img and img.get("src", "").startswith("http"):
                    return img["src"]
    except Exception as e:
        print(f"[VisualFeed] ⚠️ Page scrape {page_url[:50]}: {e}")
    return ""


def _flipkart_product_panel(product_name: str) -> dict | None:
    """Find product on Flipkart only — image + buy link."""
    title_base = product_name if len(product_name) <= 36 else product_name[:33] + "..."
    clean_name = re.sub(r"\s+buy india$", "", product_name.strip(), flags=re.I)

    query = f"site:{_FLIPKART_DOMAIN} {clean_name}"
    try:
        results = _ddg_text(query, max_results=10)
    except Exception as e:
        print(f"[VisualFeed] ⚠️ Flipkart search: {e}")
        return None

    for r in results:
        raw = (r.get("url") or "").strip()
        page = _normalize_product_url(raw, _FLIPKART_DOMAIN)
        if not page:
            continue
        img = _scrape_product_image(page)
        if not img:
            continue
        name = (r.get("title") or title_base).strip()
        name = re.sub(r"\s*-\s*Buy.*$", "", name, flags=re.I)
        name = re.sub(r"\s*\|\s*Flipkart.*$", "", name, flags=re.I)
        if len(name) > 44:
            name = name[:41] + "..."
        return {
            "type": "image",
            "title": f"{name} · Flipkart",
            "store": "Flipkart",
            "image_url": img,
            "thumbnail_url": img,
            "page_url": page,
        }

    return None


def _list_item_head(line: str) -> str:
    """First meaningful token from a numbered list line."""
    m = re.match(r"^\s*\d+[\.\)]\s*(.+)$", line.strip())
    if not m:
        return ""
    item = m.group(1).strip()
    for sep in (":", " — ", " – ", " - ", "|"):
        if sep in item:
            item = item.split(sep, 1)[0].strip()
    item = re.sub(r"[*#]+", "", item).strip()
    return item


def extract_shopping_queries(summary: str, main_query: str, n: int = 8) -> list[str]:
    """Pull product search terms from markdown categories or product names."""
    queries: list[str] = []
    seen: set[str] = set()

    def _add(raw: str):
        name = re.sub(r"\s+", " ", raw.strip(" :*#"))
        if not name or len(name) < 3:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        queries.append(name)

    for m in _CATEGORY_RE.finditer(summary):
        _add(m.group(1))

    for name in extract_product_names(f"{summary}\n{main_query}", n):
        _add(name)

    if not queries and main_query:
        q = main_query.strip()
        for junk in ("image", "images", "photo", "show me", "pictures of", "buy india"):
            q = re.sub(junk, "", q, flags=re.I)
        q = q.strip()
        if len(q) > 5:
            _add(q)

    return queries[:n]


def is_product_context(summary: str, main_query: str) -> bool:
    blob = f"{main_query} {summary}".lower()
    if re.search(
        r"\b(inhabitants?|ethnic|population|tribes?|people of|history of|"
        r"who lives|demographics|culture of|language of|religion of|"
        r"mountain range|geography|climate of|news|war|politics|election|"
        r"weather|scientist|discovery|disease|country|capital city)\b",
        blob,
    ):
        return False
    if re.search(r"\b(buy|price|₹|rupees?|flipkart|best laptop|best phone|"
                 r"compare|vs\.?|under \d+|budget)\b", blob):
        return True
    if _PRODUCT_RE.search(blob):
        return True
    if _CATEGORY_RE.search(summary) and extract_product_names(f"{summary}\n{main_query}", 2):
        return True
    return len(extract_product_names(f"{summary}\n{main_query}", 2)) >= 2


def extract_product_models(summary: str, n: int = 4) -> list[str]:
    return extract_product_names(summary, n)


def extract_visual_queries(summary: str, main_query: str, n: int = 8) -> list[str]:
    product_mode = is_product_context(summary, main_query)
    queries: list[str] = []

    if product_mode:
        queries = extract_shopping_queries(summary, main_query, n)
        if not queries:
            base = (main_query or "product").strip()
            for junk in ("image", "images", "photo", "show me", "pictures of"):
                base = re.sub(junk, "", base, flags=re.I).strip()
            if base and base.lower() not in ("news", "search"):
                queries.append(base)

    if not product_mode:
        ctx = " ".join((main_query or "").split()[:5])
        for line in summary.splitlines():
            if len(queries) >= n:
                break
            head = _list_item_head(line)
            if head and len(head) >= 2:
                q = f"{head} {ctx}".strip() if ctx else head
                if q not in queries:
                    queries.append(q)

        lower = summary.lower()
        topic_map = [
            (r"middle east|iran\b", "Middle East Iran news"),
            (r"russia|ukraine", "Russia Ukraine war"),
            (r"artificial intelligence|pope francis", "Pope Francis AI warning"),
            (r"europe|heatwave", "Europe heatwave"),
        ]
        for pattern, q in topic_map:
            if len(queries) >= n:
                break
            if re.search(pattern, lower) and q not in queries:
                queries.append(q)

        if len(queries) < n:
            for sent in re.split(r"(?<=[.!?])\s+", summary):
                if len(queries) >= n:
                    break
                sent = sent.strip()
                if len(sent) < 25:
                    continue
                words = re.sub(r"[^\w\s'-]", " ", sent).split()[:6]
                chunk = " ".join(words).strip()
                if chunk and chunk not in queries:
                    queries.append(chunk)

    if not product_mode and len(queries) < n:
        base = (main_query or "").strip()
        for junk in ("image", "images", "photo", "show me", "pictures of"):
            base = re.sub(junk, "", base, flags=re.I).strip()
        if base and base not in queries:
            queries.append(base)

    return queries[:n]


def _topic_image_panel(q: str) -> dict | None:
    """Topic / news / culture images from Google Images."""
    clean = re.sub(r"\s+buy india$", "", q.strip(), flags=re.I)
    if not clean:
        return None

    imgs = _google_image_urls(clean, max_results=6)
    if not imgs:
        imgs = _ddg_images(clean, max_results=4)
    if not imgs:
        return None

    img = imgs[0]
    t = clean if len(clean) <= 42 else clean[:39] + "..."
    search_url = f"https://www.google.com/search?tbm=isch&q={quote(clean)}"
    return {
        "type": "image",
        "title": t,
        "store": "Google",
        "image_url": img,
        "thumbnail_url": img,
        "page_url": search_url,
    }


def _panel_for_query(q: str, product_mode: bool) -> dict | None:
    if product_mode:
        return _flipkart_product_panel(q)
    return _topic_image_panel(q)


def fetch_visual_panels(summary: str, query: str) -> list[dict]:
    """Products → Flipkart panels. Everything else → Google Images."""
    product_mode = is_product_context(summary, query)
    panels: list[dict] = []
    seen_urls: set[str] = set()

    for i, q in enumerate(extract_visual_queries(summary, query, 8)):
        if i:
            time.sleep(0.35)
        panel = _panel_for_query(q, product_mode)
        if not panel:
            print(f"[VisualFeed] — skip: {q!r}")
            continue
        img_key = panel.get("image_url") or panel.get("thumbnail_url") or ""
        page = panel.get("page_url") or img_key
        if img_key in seen_urls or page in seen_urls:
            continue
        seen_urls.add(img_key)
        seen_urls.add(page)
        panels.append(panel)
        store = panel.get("store", "Google")
        print(f"[VisualFeed] ✅ {store}: {q!r} → {str(page)[:60]}")

    return panels
