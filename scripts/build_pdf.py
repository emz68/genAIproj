"""Generate clean standalone slide HTMLs, then screenshot and combine into PDF."""
import os, time
from playwright.sync_api import sync_playwright
from PIL import Image

BASE = "/Users/alarionova/genAIproj/docs/demo"
OUT_PDF = f"{BASE}/pipeline_presentation.pdf"
W, H = 1920, 1080

SLIDES = [
    ("slide_hero",        "EV Charger Data Platform", "", True),
    ("slide_problem",     "§1 — The Problem", "", False),
    ("slide_research",    "§2 — Background Research", "", False),
    ("slide_ideation",    "§3 — Ideation Process", "", False),
    ("slide_failed",      "§4 — What Failed", "", False),
    ("slide_solution_ba", "§5 — Before vs After", "", False),
    ("slide_stage1",      "§5 — Stage 1: Ingestion", "", False),
    ("slide_stage2",      "§5 — Stage 2: Validation", "", False),
    ("slide_stage3",      "§5 — Stage 3: Reconciliation", "", False),
    ("slide_stage4",      "§5 — Stage 4: Reporting", "", False),
    ("slide_testing",     "§6 — Testing", "", False),
    ("slide_recommend",   "§7 — Recommendation", "", False),
    ("slide_risks",       "§8 — Risks", "", False),
    ("slide_questions",   "§9 — Questions for Con Edison", "", False),
]

imgs = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": W, "height": H})
    for i, (name, label, _, _) in enumerate(SLIDES):
        url = f"http://127.0.0.1:9876/{name}.html"
        print(f"[{i+1}/{len(SLIDES)}] {label} ... ", end="", flush=True)
        page.goto(url, wait_until="networkidle", timeout=15000)
        time.sleep(0.3)
        out = f"/tmp/slide_{i:02d}.png"
        page.screenshot(path=out)
        img = Image.open(out)
        print(f"{img.size[0]}x{img.size[1]}")
        imgs.append(img)
    browser.close()

imgs[0].save(OUT_PDF, "PDF", save_all=True, append_images=imgs[1:], resolution=100.0)
print(f"\nDone: {OUT_PDF} ({len(imgs)} slides)")
