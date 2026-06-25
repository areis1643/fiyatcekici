#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Akakce arama servisi - FastAPI + Playwright (Coolify / keyubu server)
Endpoint:
    GET /ara?q=<sorgu>&max_pages=<n>&filtre=<opsiyonel>
    GET /health
Donus: {"sorgu","sayfa","adet","urunler":[...]}
n8n bu servisi cagirir; ham urun listesini alir.
"""
import asyncio, re, time, os
from urllib.parse import quote
from typing import Optional

from fastapi import FastAPI, Query
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE = "https://www.akakce.com/arama/"
PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*)\s*,\s*(\d{2})\s*TL")
UP_RE    = re.compile(r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?)\s*TL\s*/\s*([^\s<]+)", re.I)
SIZE_RE  = re.compile(r"(\d{2,4})\s*[xX]\s*(\d{2,4})")

CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))  # 1 saat
MAX_PAGES_HARD = int(os.getenv("MAX_PAGES_HARD", "15"))
WAIT_MS = int(os.getenv("WAIT_MS", "2500"))
_cache: dict = {}

app = FastAPI(title="Akakce Arama Servisi")


def to_float(s):
    return float(s.replace(".", "").replace(",", ".")) if s else None

def price_from(t):
    m = PRICE_RE.search(t or "")
    return float(m.group(1).replace(".", "") + "." + m.group(2)) if m else None

def parse_page(html, page_no):
    soup = BeautifulSoup(html, "lxml")
    rows, seen = [], set()
    for li in soup.select("li.w[data-pr]"):
        pr = li.get("data-pr")
        if not pr or pr in seen:
            continue
        seen.add(pr)
        a = li.select_one("a.pw_v8")
        if not a:
            continue
        h3 = li.select_one("h3.pn_v8")
        name = a.get("title") or (h3.get_text(" ", strip=True) if h3 else "")
        url = a.get("href", "")
        pt = a.select_one("span.pt_v9")
        fiyat = price_from(pt.get_text(" ", strip=True)) if pt else None
        up = li.select_one("span.up_v8")
        b_deger = b_birim = None
        if up:
            m = UP_RE.search(up.get_text(" ", strip=True))
            if m:
                b_deger, b_birim = to_float(m.group(1)), m.group(2)
        m = SIZE_RE.search(url) or SIZE_RE.search(name)
        beden = f"{m.group(1)}x{m.group(2)}" if m else None
        rows.append({
            "pr": pr, "marka": li.get("data-mk"), "ad": name, "beden": beden,
            "fiyat_tl": fiyat,
            "akakce_birim_deger": b_deger, "akakce_birim": b_birim,
            "sayfa": page_no, "url": url,
        })
    return rows

def detect_max_page(html):
    soup = BeautifulSoup(html, "lxml")
    pg = soup.select_one("p.pager_v9, div.pager_w_v9")
    nums = [int(a.get_text(strip=True)) for a in pg.find_all("a")
            if a.get_text(strip=True).isdigit()] if pg else []
    return max(nums) if nums else 1

def build_url(query, page_no):
    return BASE + "?q=" + quote(query) + ("&p=" + str(page_no) if page_no > 1 else "")

async def goto(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(WAIT_MS)
    return await page.content()

async def scrape(query, max_pages):
    rows = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            locale="tr-TR", timezone_id="Europe/Istanbul",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900})
        page = await ctx.new_page()
        html = await goto(page, build_url(query, 1))
        low = html.lower()
        if "just a moment" in low or "checking your browser" in low:
            await browser.close()
            return {"error": "cloudflare_challenge", "sayfa": 0, "urunler": []}
        total = detect_max_page(html)
        if max_pages > 0:
            total = min(total, max_pages)
        total = min(total, MAX_PAGES_HARD)
        rows.extend(parse_page(html, 1))
        for n in range(2, total + 1):
            html = await goto(page, build_url(query, n))
            rows.extend(parse_page(html, n))
        await browser.close()
    return {"sayfa": total, "urunler": rows}


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/ara")
async def ara(
    q: str = Query(..., description="arama sorgusu"),
    max_pages: int = Query(0, description="0=otomatik tum sayfalar"),
    filtre: Optional[str] = Query(None, description="ada/bedene gore alt-dize filtre"),
):
    key = f"{q}|{max_pages}"
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        res = _cache[key][1]
    else:
        res = await scrape(q, max_pages)
        _cache[key] = (now, res)

    urunler = res.get("urunler", [])
    if filtre:
        fl = filtre.lower()
        urunler = [r for r in urunler
                   if fl in (r.get("ad") or "").lower()
                   or fl in (r.get("beden") or "").lower()]
    out = {"sorgu": q, "sayfa": res.get("sayfa", 0),
           "adet": len(urunler), "urunler": urunler}
    if "error" in res:
        out["error"] = res["error"]
    return out


@app.get("/debug")
async def debug(q: str = Query("ssd")):
    """Teshis: sunucunun akakce'den gercekte aldigi sayfayi inceler."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            locale="tr-TR", timezone_id="Europe/Istanbul",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900})
        page = await ctx.new_page()
        resp = await page.goto(build_url(q, 1), wait_until="domcontentloaded", timeout=60000)
        status = resp.status if resp else None
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(WAIT_MS)
        html = await page.content()
        final_url = page.url
        title = await page.title()
        await browser.close()

    low = html.lower()
    markers = [k for k in ["just a moment", "checking your browser", "cf-challenge",
                           "cloudflare", "captcha", "erisim engellendi", "cerez",
                           "çerez", "kvkk", "robot", "access denied"] if k in low]
    soup = BeautifulSoup(html, "lxml")
    return {
        "http_status": status,
        "final_url": final_url,
        "title": title,
        "html_uzunluk": len(html),
        "li_w_data_pr_sayisi": len(soup.select("li.w[data-pr]")),
        "pager_var_mi": bool(soup.select_one("p.pager_v9, div.pager_w_v9")),
        "bulunan_isaretler": markers,
        "ilk_600_karakter": html[:600],
    }
