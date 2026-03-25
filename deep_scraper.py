#!/usr/bin/env python3
"""
Hybrid Scraper v3 — OPTIMIZED
==============================
KEY INSIGHT: DDoS-Guard solves once per browser tab. After the initial solve,
navigating the SAME TAB to subsequent pages takes ~4-5s instead of 13s.

Architecture:
  Stage 1  [Browser] — Each worker = 1 long-lived tab that:
            1. Opens first page → solves DDoS-Guard once (~13s)
            2. Navigates same tab through all its assigned pages (~4s each)
            → Result: album name + URL per album

  Stage 2  [Pure aiohttp, 100 concurrent] — bunkr.cr album pages need NO browser!
            → Searches filenames for TARGET_FILENAME

Performance estimate (20 workers):
  Stage 1: 24,397 pages × 4.5s avg ÷ 20 workers ≈ 1.5 hours
  Stage 2: ~360k albums × 0.3s ÷ 100 concurrency ≈ 18 minutes
  Total:   ~1.7 hours  (vs ~55h with the original all-browser approach)

Output files:
  deep_matches.csv        page_number, folder_name, album_url, filename
  deep_match_pages.txt    plain list of matched page numbers
  deep_all_albums.csv     Stage 1 checkpoint
  deep_scraper.log        live log
"""

import asyncio
import aiohttp
import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# ─── CONFIG ───────────────────────────────────────────────────────────────────
START_PAGE          = 7
END_PAGE            = 24403
TARGET_FILENAME     = "screenrecord294934"

# Stage 1 — browser (each worker = 1 persistent tab)
S1_WORKERS          = 20        # number of parallel browser tabs
S1_FIRST_WAIT_MS    = 12_000    # wait after FIRST page load (DDoS-Guard solve)
S1_NEXT_WAIT_MS     = 2_000     # wait after subsequent pages (already solved)
S1_GOTO_TIMEOUT     = 90_000    # ms for playwright goto

# Stage 2 — pure HTTP (bunkr.cr has no DDoS-Guard)
S2_HTTP_CONCURRENCY = 150       # concurrent aiohttp connections
S2_REQUEST_TIMEOUT  = 20        # seconds per request
S2_RETRIES          = 3
MIN_FILES           = 3         # only run Stage 2 on albums with MORE than this many files

PROGRESS_EVERY      = 200       # log every N items
OUTPUT_CSV          = Path(__file__).parent / "deep_matches.csv"
PAGES_TXT           = Path(__file__).parent / "deep_match_pages.txt"
ALBUMS_CSV          = Path(__file__).parent / "deep_all_albums.csv"
LOG_FILE            = Path(__file__).parent / "deep_scraper.log"
# ──────────────────────────────────────────────────────────────────────────────

LIST_URL = "https://bunkr-albums.io/?search=&page={page}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


# ════════════════════════════════════════════════════════════════
# STAGE 1 — Persistent single-tab browser workers
# ════════════════════════════════════════════════════════════════

def extract_albums(html: str, page_num: int) -> list[dict]:
    """
    Parse HTML and return list of {page, name, url, file_count}.
    File count comes from: <p class="text-xs"><span class="font-semibold">N files</span></p>
    """
    soup = BeautifulSoup(html, "html.parser")
    names = [el.get_text(strip=True) for el in soup.find_all(class_="truncate") if el.get_text(strip=True)]

    # Parse file counts — each album card has a <p class="text-xs"> with "N files"
    file_counts = []
    for p in soup.find_all("p", class_="text-xs"):
        span = p.find("span", class_="font-semibold")
        if span:
            txt = span.get_text(strip=True)          # e.g. "5 files" or "1 file"
            try:
                file_counts.append(int(txt.split()[0]))
            except (ValueError, IndexError):
                file_counts.append(0)

    anchors = soup.find_all("a", href=lambda h: h and "/a/" in h)
    urls = []
    seen = set()
    for a in anchors:
        href = a.get("href", "")
        if href not in seen:
            seen.add(href)
            urls.append(href if href.startswith("http") else "https://bunkr.cr" + href)

    albums = []
    for i, (n, u) in enumerate(zip(names, urls)):
        fc = file_counts[i] if i < len(file_counts) else 0
        albums.append({"page": page_num, "name": n, "url": u, "file_count": fc})
    return albums


async def s1_tab_worker(wid: int, pages: list[int], results: list,
                        browser: Browser, progress: dict, lock: asyncio.Lock):
    """
    Each worker owns ONE persistent browser tab.
    Solves DDoS-Guard on the first page, then navigates sequentially.
    """
    ctx: BrowserContext = await browser.new_context(
        user_agent=BROWSER_UA,
        locale="en-US",
        viewport={"width": 1280, "height": 800},
    )
    tab: Page = await ctx.new_page()
    first_page = True

    try:
        for page_num in pages:
            url = LIST_URL.format(page=page_num)
            try:
                if first_page:
                    # Full solve — wait for networkidle + extra time for JS challenge
                    await tab.goto(url, wait_until="networkidle", timeout=S1_GOTO_TIMEOUT)
                    await tab.wait_for_timeout(S1_FIRST_WAIT_MS)
                    first_page = False
                    title = await tab.title()
                    if "ddos" in title.lower():
                        # Challenge not solved — try longer wait
                        await tab.wait_for_timeout(8000)
                else:
                    # Subsequent pages — DDoS-Guard already solved, much faster
                    await tab.goto(url, wait_until="networkidle", timeout=S1_GOTO_TIMEOUT)
                    await tab.wait_for_timeout(S1_NEXT_WAIT_MS)

                html = await tab.content()
                albums = extract_albums(html, page_num)

                async with lock:
                    results.extend(albums)
                    progress["done"] += 1
                    if progress["done"] % PROGRESS_EVERY == 0:
                        d, t = progress["done"], progress["total"]
                        log.info(f"[S1 W{wid:02d}] {d}/{t} pages ({d/t:.1%}) | {len(results)} albums")

            except Exception as e:
                log.warning(f"[S1 W{wid}] page={page_num} error: {e}")
                async with lock:
                    progress["done"] += 1
                first_page = True  # reset so next page gets a fresh solve attempt

    finally:
        await tab.close()
        await ctx.close()


def assign_pages(start: int, end: int, n_workers: int) -> list[list[int]]:
    """Split page range into n_workers chunks."""
    pages = list(range(start, end + 1))
    k = max(1, (len(pages) + n_workers - 1) // n_workers)
    return [pages[i:i + k] for i in range(0, len(pages), k)]


# ════════════════════════════════════════════════════════════════
# STAGE 2 — Pure aiohttp album scraper
# ════════════════════════════════════════════════════════════════

async def scrape_album_http(session: aiohttp.ClientSession, album: dict) -> list[dict]:
    hits = []
    for attempt in range(1, S2_RETRIES + 1):
        try:
            async with session.get(album["url"],
                                   timeout=aiohttp.ClientTimeout(total=S2_REQUEST_TIMEOUT)) as resp:
                if resp.status != 200:
                    return hits
                soup = BeautifulSoup(await resp.text(), "html.parser")
                for el in soup.select("p.truncate.theName"):
                    fname = el.get_text(strip=True)
                    fname_base = fname.rsplit(".", 1)[0].lower()
                    target = TARGET_FILENAME.lower()
                    if target == fname_base or target == fname.lower() or target in fname.lower():
                        hits.append({
                            "page_number": album["page"],
                            "folder_name": album["name"],
                            "album_url":   album["url"],
                            "filename":    fname,
                        })
                        log.info(f"  ★ FOUND  page={album['page']}  folder='{album['name']}'  file='{fname}'")
                return hits
        except Exception:
            await asyncio.sleep(1)
    return hits


async def s2_runner(albums: list[dict], results: list, lock: asyncio.Lock):
    sem = asyncio.Semaphore(S2_HTTP_CONCURRENCY)
    progress = {"done": 0}
    total = len(albums)
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def fetch_one(album: dict):
        async with sem:
            hits = await scrape_album_http(session, album)
            async with lock:
                results.extend(hits)
                progress["done"] += 1
                if progress["done"] % PROGRESS_EVERY == 0:
                    d = progress["done"]
                    log.info(f"[S2] {d}/{total} albums ({d/total:.1%}) | {len(results)} matches")

    async with aiohttp.ClientSession(headers=headers) as session:
        await asyncio.gather(*[fetch_one(a) for a in albums])


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════

async def main():
    log.info("=" * 65)
    log.info(f"Hybrid scraper v3 (optimized)  started  {datetime.now()}")
    log.info(f"Pages {START_PAGE}–{END_PAGE}  ({END_PAGE - START_PAGE + 1:,} total)")
    log.info(f"S1 tab workers: {S1_WORKERS}  |  S2 HTTP concurrency: {S2_HTTP_CONCURRENCY}")
    log.info(f"Target filename: '{TARGET_FILENAME}'")
    log.info("=" * 65)

    t0 = time.time()

    # ── STAGE 1 ───────────────────────────────────────────────────
    log.info(f"\n▶ STAGE 1: Collecting album links (persistent-tab browser workers)...")
    chunks = assign_pages(START_PAGE, END_PAGE, S1_WORKERS)
    log.info(f"  Distributing {END_PAGE - START_PAGE + 1:,} pages across {len(chunks)} workers")
    for i, c in enumerate(chunks):
        log.info(f"  Worker {i+1:02d}: pages {c[0]}–{c[-1]} ({len(c)} pages)")

    all_albums: list[dict] = []
    s1_lock = asyncio.Lock()
    s1_progress = {"done": 0, "total": END_PAGE - START_PAGE + 1}

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        tasks = [
            asyncio.create_task(
                s1_tab_worker(i + 1, chunks[i], all_albums, browser, s1_progress, s1_lock)
            )
            for i in range(len(chunks))
        ]
        await asyncio.gather(*tasks)
        await browser.close()

    t_s1 = time.time() - t0
    log.info(f"Stage 1 done in {t_s1:.0f}s ({t_s1/60:.1f} min): {len(all_albums):,} albums collected")

    # Checkpoint (all albums, with file counts)
    with open(ALBUMS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["page", "name", "url", "file_count"])
        w.writeheader()
        w.writerows(all_albums)
    log.info(f"Albums checkpoint → {ALBUMS_CSV}")

    # Filter: only scan albums with > MIN_FILES files in Stage 2
    albums_to_scan = [a for a in all_albums if a["file_count"] > MIN_FILES]
    skipped = len(all_albums) - len(albums_to_scan)
    log.info(f"  Skipping {skipped:,} albums with ≤{MIN_FILES} files | Scanning {len(albums_to_scan):,} albums")

    # ── STAGE 2 ───────────────────────────────────────────────────
    log.info(f"\n▶ STAGE 2: Scanning {len(albums_to_scan):,} album pages via pure HTTP (file_count > {MIN_FILES})...")
    all_hits: list[dict] = []
    s2_lock = asyncio.Lock()
    await s2_runner(albums_to_scan, all_hits, s2_lock)

    elapsed = time.time() - t0
    log.info(f"\nAll done in {elapsed:.0f}s ({elapsed/3600:.2f}h)")

    # ── Output ───────────────────────────────────────────────────
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["page_number", "folder_name", "album_url", "filename"])
        w.writeheader()
        w.writerows(all_hits)

    pages_only = sorted(set(h["page_number"] for h in all_hits))
    PAGES_TXT.write_text("\n".join(str(p) for p in pages_only), encoding="utf-8")

    log.info(f"Matches  → {OUTPUT_CSV}  ({len(all_hits)} rows)")
    log.info(f"Pages    → {PAGES_TXT}  ({len(pages_only)} unique pages)")

    if all_hits:
        print("\n── Matches Found ──────────────────────────────────────────────")
        print(f"{'Page':>8}  {'Folder':<40}  {'File'}")
        print("-" * 90)
        for h in all_hits:
            print(f"{h['page_number']:>8}  {h['folder_name']:<40}  {h['filename']}")
    else:
        print(f"\nNo exact matches for '{TARGET_FILENAME}' found.")


if __name__ == "__main__":
    asyncio.run(main())
