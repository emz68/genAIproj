"""Build PDF: screenshot each standalone slide at 1920x1080, combine into PDF."""
import os, time
os.environ.pop("PYTHONPATH", None)

from playwright.sync_api import sync_playwright
from PIL import Image

BASE = "/Users/alarionova/genAIproj/docs/demo"
OUT = f"{BASE}/pipeline_presentation.pdf"
W, H = 1920, 1080

SLIDES = [
    "slide_hero", "slide_problem", "slide_research", "slide_ideation",
    "slide_failed", "slide_solution_ba",
    "slide_stage1", "slide_stage2", "slide_stage3", "slide_stage4",
    "slide_testing", "slide_recommend", "slide_risks", "slide_questions",
]

imgs = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": W, "height": H})

    for i, name in enumerate(SLIDES):
        url = f"http://127.0.0.1:9876/{name}.html"
        print(f"[{i+1}/{len(SLIDES)}] {name} ... ", end="", flush=True)

        page.goto(url, wait_until="networkidle", timeout=15000)
        time.sleep(0.3)

        # Verify no scroll overflow
        has_scroll = page.evaluate("document.body.scrollHeight > document.body.clientHeight + 10")
        if has_scroll:
            print(f"OVERFLOW! scrollHeight={page.evaluate('document.body.scrollHeight')}", end=" ", flush=True)

        out = f"/tmp/slide_{i:02d}.png"
        page.screenshot(path=out)
        img = Image.open(out)
        print(f"{img.size[0]}x{img.size[1]}")
        imgs.append(img)

    browser.close()

if imgs:
    imgs[0].save(OUT, "PDF", save_all=True, append_images=imgs[1:], resolution=100.0)
    print(f"\nDone: {OUT} ({len(imgs)} slides)")

# Clean temp
for i in range(len(SLIDES)):
    f = f"/tmp/slide_{i:02d}.png"
    if os.path.exists(f):
        os.remove(f)
