#!/usr/bin/env python3
"""Self-host the 14 title families that templates/base.html's font menu advertises.

Three explicit I/O tiers — nothing touches the network until you ask for it:

  --list (default)  0 requests, 0 bytes. Prints what would be fetched and where it lands.
  --resolve         14 HTTPS GETs of small CSS documents (fonts.googleapis.com/css2), no font
                    bytes. Writes static/fonts/manifest.json = the exact woff2 URL list + counts.
  --apply           does --resolve, then GETs every woff2/ttf and writes static/css/fonts.local.css.
  --licenses        14 GETs of OFL.txt from raw.githubusercontent.com into static/fonts/licenses/
                    (OFL 再分发义务要求授权全文随包，缺它算发布阻断项)。

Why a local copy is the only option: security_headers.py pins
``style-src 'self' 'unsafe-inline'`` and ``font-src 'self' data:``, so a Google Fonts
``<link>`` is CSP-blocked and the 14 families in the menu could never render — the menu
used to advertise them anyway (see docs/agents/GOTCHAS.md).

Sizes matter: a CJK family is split into ~100 unicode-range subsets totalling several MB.
Use --only to install a subset of the families.

本脚本不会删除或覆盖任何既有字体文件；已下载且字节数一致的文件会跳过。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
STATIC = HERE / "app" / "integrated_app" / "static"
FONT_DIR = STATIC / "fonts"
CSS_PATH = STATIC / "css" / "fonts.local.css"
MANIFEST_PATH = FONT_DIR / "manifest.json"

CSS_HOST = "fonts.googleapis.com"
#: 字体字节的允许域名：只信 Google 自己的 gstatic 与常见国内镜像。
#: 本机实测 css2 返回的 URL 域名是 ``fonts.gstatic.font.im``（被镜像改写），
#: 所以只硬编码 fonts.gstatic.com 会让 --apply 把每个文件都拒掉。
#: 需要再多一个镜像请显式传 --allow-host，不要放开成任意 host。
FILE_HOSTS: set[str] = {
    "fonts.gstatic.com",
    "fonts.gstatic.cn",
    "fonts.gstatic.font.im",
    "fonts.gstatic.loli.net",
}
# Chrome's UA is required: without it Google serves TTF instead of WOFF2 (~3x the bytes).
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"

# (family as written in the CSS font-family, is_cjk) — must match base.html's FONTS list.
FAMILIES: list[tuple[str, bool]] = [
    ("Noto Sans SC", True),
    ("Noto Serif SC", True),
    ("ZCOOL XiaoWei", True),
    ("ZCOOL QingKe HuangYou", True),
    ("ZCOOL KuaiLe", True),
    ("Ma Shan Zheng", True),
    ("Long Cang", True),
    ("Zhi Mang Xing", True),
    ("Liu Jian Mao Cao", True),
    ("Playfair Display", False),
    ("Cinzel", False),
    ("Great Vibes", False),
    ("Pacifico", False),
    ("Dancing Script", False),
]

_FACE_RE = re.compile(r"@font-face\s*\{([^}]*)\}")
# 不是每个家族都有 woff2 —— 志莽行书（Zhi Mang Xing）Google 只给 TTF。
# 只匹配 .woff2 会让它解析出 0 个子集，--apply 静默什么都不下。
_URL_RE = re.compile(r"url\((https://[^)]+\.woff2)\)\s*format\(['\"]woff2['\"]\)")
_TTF_RE = re.compile(r"url\((https://[^)]+\.ttf)\)\s*format\(['\"]truetype['\"]\)")


def _field(block: str, name: str) -> str:
    m = re.search(rf"{name}\s*:\s*([^;]+);", block)
    return m.group(1).strip() if m else ""


def css_url(family: str) -> str:
    return f"https://{CSS_HOST}/css2?family={family.replace(' ', '+')}&display=swap"


def _get(url: str, timeout: int = 60) -> bytes:
    host = url.split("/")[2]
    if host != CSS_HOST and host not in FILE_HOSTS:
        raise ValueError(f"refusing to fetch non-allowlisted host: {url}（要放行请加 --allow-host {host}）")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def resolve(family: str) -> list[dict[str, str]]:
    """Return one entry per @font-face subset Google serves for this family."""
    faces = []
    for i, block in enumerate(_FACE_RE.findall(_get(css_url(family)).decode("utf-8"))):
        hit = _URL_RE.search(block) or _TTF_RE.search(block)
        if not hit:
            continue
        faces.append(
            {
                "family": family,
                "index": str(i),
                "url": hit.group(1),
                "format": "woff2" if hit.re is _URL_RE else "truetype",
                "weight": _field(block, "font-weight") or "400",
                "style": _field(block, "font-style") or "normal",
                "unicode_range": _field(block, "unicode-range"),
            }
        )
    return faces


def slug(family: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", family)


def filename_for(face: dict[str, str]) -> str:
    ext = "ttf" if face["url"].endswith(".ttf") else "woff2"
    return f"{slug(face['family'])}-{face['index']}.{ext}"


LICENSE_DIR = FONT_DIR / "licenses"
#: 授权全文与字体字节是两回事：OFL.txt 只在上游仓库里，不在 gstatic 上。
#: 单独一个 host、单独一个档位，不与 --apply 混在一起，避免"顺手放开任意域名"。
LICENSE_HOST = "raw.githubusercontent.com"


def _license_url(family: str) -> str:
    return f"https://{LICENSE_HOST}/google/fonts/main/ofl/{family.lower().replace(' ', '')}/OFL.txt"


def fetch_licenses(families: list[tuple[str, bool]]) -> int:
    """把每个家族的 OFL 1.1 全文抓到 static/fonts/licenses/，缺一个就返回非 0。"""
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for family, _cjk in families:
        target = LICENSE_DIR / f"{slug(family)}.OFL.txt"
        try:
            req = urllib.request.Request(_license_url(family), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                payload = resp.read()
        except OSError as e:
            failed.append(f"{family} ({e})")
            print(f"  [FAIL] {family:<22} {e}")
            continue
        if b"Copyright" not in payload:
            failed.append(f"{family} (内容里没有 Copyright 行，疑似不是授权全文)")
            print(f"  [FAIL] {family:<22} 内容可疑，已跳过写入")
            continue
        target.write_bytes(payload)
        print(f"  [OK]   {family:<22} -> {target.name} ({len(payload) // 1024} KB)")
    if failed:
        print(f"\n{len(failed)} 个家族的 OFL 全文没拿到：{', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"授权全文 -> {LICENSE_DIR.relative_to(HERE)}")
    return 0


def write_css(all_faces: list[dict[str, str]]) -> None:
    lines = [
        "/* Generated by scripts/fetch_title_fonts.py --apply. Do not edit by hand.",
        " * Self-hosted because CSP font-src is 'self' data:. See static/fonts/README.md. */",
    ]
    for face in all_faces:
        lines += [
            "@font-face {",
            f"  font-family: '{face['family']}';",
            f"  font-style: {face['style']};",
            f"  font-weight: {face['weight']};",
        ]
        if face["unicode_range"]:
            lines.append(f"  unicode-range: {face['unicode_range']};")
        lines += [
            "  font-display: swap;",
            f"  src: url('/static/fonts/{filename_for(face)}') format('{face.get('format', 'woff2')}');",
            "}",
            "",
        ]
    CSS_PATH.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tier = parser.add_mutually_exclusive_group()
    tier.add_argument("--list", action="store_true", help="0 network calls (default)")
    tier.add_argument("--resolve", action="store_true", help="fetch CSS only, write manifest.json")
    tier.add_argument("--apply", action="store_true", help="fetch CSS + every woff2, write fonts.local.css")
    parser.add_argument("--only", default="", help="comma-separated subset of families, e.g. --only 'Cinzel,Pacifico'")
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="HOST",
        help="额外允许下载字体字节的域名（可重复）。默认只放行 gstatic 与其常见镜像",
    )
    parser.add_argument("--licenses", action="store_true", help="只抓各家族的 OFL 1.1 全文到 static/fonts/licenses/")
    args = parser.parse_args(argv)
    FILE_HOSTS.update(args.allow_host)

    wanted = [f.strip() for f in args.only.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in {name for name, _ in FAMILIES}]
    if unknown:
        print(f"unknown families: {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(n for n, _ in FAMILIES)}", file=sys.stderr)
        return 2
    selected = [entry for entry in FAMILIES if not wanted or entry[0] in wanted]

    if args.licenses:
        return fetch_licenses(selected)

    if args.apply or args.resolve:
        faces: list[dict[str, str]] = []
        for family, _cjk in selected:
            found = resolve(family)
            faces += found
            print(f"  resolve {family:<22} {len(found):>4} 个子集")
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(faces, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"manifest -> {MANIFEST_PATH.relative_to(HERE)} ({len(faces)} files)")
        if args.resolve:
            print("no font bytes transferred; re-run with --apply to download")
            return 0

        FONT_DIR.mkdir(parents=True, exist_ok=True)
        downloaded = skipped = 0
        total = 0
        for face in faces:
            target = FONT_DIR / filename_for(face)
            payload = _get(face["url"])
            if target.exists() and target.stat().st_size == len(payload):
                skipped += 1
                continue
            target.write_bytes(payload)
            downloaded += 1
            total += len(payload)
        write_css(faces)
        print(f"wrote {downloaded} new / {skipped} unchanged files, {total / 1024 / 1024:.1f} MB this run")
        print(f"css -> {CSS_PATH.relative_to(HERE)}")
        print("now uncomment the fonts.local.css <link> in templates/base.html")
        return 0

    cjk = [n for n, is_cjk in selected if is_cjk]
    latin = [n for n, is_cjk in selected if not is_cjk]
    print(f"plan (0 requests): {len(selected)} families -> {FONT_DIR.relative_to(HERE)}/")
    print(f"  CJK   ({len(cjk)}): {', '.join(cjk) or '-'}")
    print(f"  Latin ({len(latin)}): {', '.join(latin) or '-'}")
    print(f"  css   -> {CSS_PATH.relative_to(HERE)}")
    print("\nnext: python scripts/fetch_title_fonts.py --resolve   (14 small GETs, no font bytes)")
    print("then: python scripts/fetch_title_fonts.py --apply     (transfers the woff2 files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
