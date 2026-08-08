# -*- coding: utf-8 -*-
"""Pixel-level visual diff between actual and replica screenshots."""
import os
import sys
from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(ROOT, "verification_output", "diff")
ACTUAL_DIR = os.environ.get("TTS_ACTUAL_DIR", os.path.join(ROOT, "verification_output", "actual"))
REPLICA_DIR = os.environ.get("TTS_REPLICA_DIR", os.path.join(ROOT, "verification_output", "replica"))

TABS = [
    "voice_design", "voice_clone", "ultimate_clone", "script",
    "prompt_continue", "lora", "lora_training", "indextts2_clone",
    "indextts2_emotion", "indextts2_duration", "settings", "history",
    "persona", "help",
]


def compare_images(actual_path, replica_path, diff_path):
    a = Image.open(actual_path).convert("RGB")
    r = Image.open(replica_path).convert("RGB")
    # resize to same dimensions
    target_size = (min(a.width, r.width), min(a.height, r.height))
    a = a.resize(target_size, Image.Resampling.LANCZOS)
    r = r.resize(target_size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(a, r)
    # highlight differences in red
    diff_highlight = diff.copy()
    pixels = diff_highlight.load()
    for y in range(diff_highlight.height):
        for x in range(diff_highlight.width):
            if pixels[x, y] != (0, 0, 0):
                pixels[x, y] = (255, 0, 0)
    diff_highlight.save(diff_path)
    # compute diff ratio
    diff_data = list(diff.getdata())
    diff_pixels = sum(1 for p in diff_data if p != (0, 0, 0))
    ratio = diff_pixels / len(diff_data)
    return ratio, target_size


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    results = []
    for tab_id in TABS:
        actual_path = os.path.join(ACTUAL_DIR, f"tab_{tab_id}.png")
        replica_path = os.path.join(REPLICA_DIR, f"tab_{tab_id}.png")
        if not os.path.exists(actual_path) or not os.path.exists(replica_path):
            results.append((tab_id, None, "missing screenshot"))
            continue
        diff_path = os.path.join(OUTDIR, f"tab_{tab_id}_diff.png")
        try:
            ratio, size = compare_images(actual_path, replica_path, diff_path)
            results.append((tab_id, ratio, size, diff_path))
        except Exception as e:
            results.append((tab_id, None, str(e)))

    # also compare sidebar states
    for name in ["00_initial", "01_collapsed", "02_expanded"]:
        actual_path = os.path.join(ACTUAL_DIR, f"{name}.png")
        replica_path = os.path.join(REPLICA_DIR, f"{name}.png")
        if os.path.exists(actual_path) and os.path.exists(replica_path):
            diff_path = os.path.join(OUTDIR, f"{name}_diff.png")
            try:
                ratio, size = compare_images(actual_path, replica_path, diff_path)
                results.append((f"sidebar_{name}", ratio, size, diff_path))
            except Exception as e:
                results.append((f"sidebar_{name}", None, str(e)))

    report_path = os.path.join(OUTDIR, "visual_diff_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 像素级视觉差异报告\n\n")
        f.write("| 页面/状态 | 差异比例 | 尺寸 | diff 图片 | 说明 |\n")
        f.write("|-----------|----------|------|-----------|------|\n")
        for item in results:
            if len(item) == 3:
                tab, ratio, note = item
                f.write(f"| {tab} | - | - | - | {note} |\n")
            else:
                tab, ratio, size, path = item
                pct = f"{ratio * 100:.2f}%"
                note = "高度一致" if ratio < 0.01 else "中度差异" if ratio < 0.1 else "显著差异"
                f.write(f"| {tab} | {pct} | {size[0]}x{size[1]} | `{path}` | {note} |\n")

    print(f"视觉差异报告已保存: {report_path}")
    for item in results:
        if len(item) == 4:
            tab, ratio, size, path = item
            print(f"  {tab}: {ratio * 100:.2f}% diff")

