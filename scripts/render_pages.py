"""Render Jinja2 templates to static HTML for jsdom smoke testing.

Usage: python scripts/render_pages.py
Output: tests/frontend/_rendered/*.html
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = PROJECT_ROOT / "app" / "integrated_app" / "templates"
OUTPUT_DIR = PROJECT_ROOT / "tests" / "frontend" / "_rendered"
PAGES = ["base", "download_guide"]

def _t(key: str, lang: str = "zh-CN", **kw) -> str:
    """Minimal i18n filter: return key as-is (smoke test only checks structure)."""
    return str(key)

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    env.filters["t"] = _t

    context = {
        "title": "TTS MultiModel",
        "theme": "dark",
        "lang": "zh-CN",
        "language": "zh-CN",
        "current_year": 2026,
    }

    for page in PAGES:
        try:
            template = env.get_template(f"{page}.html")
            html = template.render(**context)
            out = OUTPUT_DIR / f"{page}.html"
            out.write_text(html, encoding="utf-8")
            print(f"  rendered: {page}.html ({len(html)} bytes)")
        except Exception as e:
            print(f"  skip {page}: {e}")


if __name__ == "__main__":
    main()
