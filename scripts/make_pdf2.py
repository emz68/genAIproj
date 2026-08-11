"""Generate clean slides by screenshotting individual HTML pages."""
import os, time
from playwright.sync_api import sync_playwright
from PIL import Image

SERVER = "http://127.0.0.1:9876"
OUT_PDF = "/Users/alarionova/genAIproj/docs/demo/pipeline_presentation.pdf"
W, H = 1920, 1080

# Each slide: URL to load, and optional JS to run before screenshot
SLIDES = [
    ("/pipeline_demo_copy_2.html", None),
    ("/pipeline_demo_copy_2.html#problem", None),
    ("/pipeline_demo_copy_2.html#research", None),
    ("/pipeline_demo_copy_2.html#ideation", None),
    ("/pipeline_demo_copy_2.html#failed", None),
    ("/pipeline_demo_copy_2.html#solution", "document.querySelector('.stepper-track')?.remove(); document.querySelector('.stepper-controls')?.remove(); document.querySelectorAll('.stepper-panel').forEach(p => p.remove()); var b = document.querySelector('#solution p[style*=\"text-align:center\"]'); if(b) b.remove();"),
    ("/slide_stage1.html", None),
    ("/slide_stage2.html", None),
    ("/slide_stage3.html", None),
    ("/slide_stage4.html", None),
    ("/pipeline_demo_copy_2.html#testing", None),
    ("/pipeline_demo_copy_2.html#recommendation", None),
    ("/pipeline_demo_copy_2.html#risks", None),
    ("/pipeline_demo_copy_2.html#questions", None),
]

slides = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": W, "height": H})

    for i, (url_path, js) in enumerate(SLIDES):
        url = f"{SERVER}{url_path}"
        label = url_path.replace("/pipeline_demo_copy_2.html","").replace("#","").replace("/","") or "hero"
        print(f"[{i+1}/{len(SLIDES)}] {label} ... ", end="", flush=True)

        page.goto(url, wait_until="networkidle", timeout=15000)
        time.sleep(0.6)
        # Force reveals
        page.evaluate("document.querySelectorAll('.reveal').forEach(el => el.classList.add('on'))")
        time.sleep(0.2)

        if js:
            page.evaluate(js)
            time.sleep(0.2)

        out = f"/tmp/slide_{i:02d}.png"
        page.screenshot(path=out)
        img = Image.open(out)
        print(f"{img.size[0]}x{img.size[1]}")
        slides.append(img)

    browser.close()

if slides:
    slides[0].save(OUT_PDF, "PDF", save_all=True, append_images=slides[1:], resolution=100.0)
    print(f"\nPDF: {OUT_PDF} ({len(slides)} slides)")
