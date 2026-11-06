#!/usr/bin/env python
"""构建「程序内下载」用的 torch 环境包（wheels ZIP 分卷）+ 覆写 downloads_catalog.json。

产线口径与 Image-DuoModel 的 build_desktop_bundle.ps1 -Mode runtime 一致：
  - 用 pip download 拉指定 CUDA 版本的 torch/torchvision 轮子（强制 cp312/win_amd64）
  - ZIP（level 0，wheel 已压缩）按体积切分卷
  - 逐卷 SHA256，写回 app/downloads_catalog.json 的 runtime.volumes + packages
  - 输出 SHA256SUMS.txt / runtime-manifest.json

用法（在目标仓根目录执行）：
  python scripts/build_runtime_bundle.py \
      --torch 2.10.0 --torchvision 0.25.0 --cuda cu128 \
      --index-url https://download.pytorch.org/whl/cu128 \
      --out D:\\_build\\xxx\\runtime-release \
      --base-url https://github.com/<org>/<repo>/releases/download/runtime-v1 \
      --min-app-version 1.7.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd: list[str], env: dict | None = None) -> None:
    print("[run]", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if proc.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {proc.returncode}）：{' '.join(cmd)}")


def download_wheels(
    torch: str, torchvision: str, index_url: str, wheels: Path, python_tag: str = "312", extra: list[str] | None = None
) -> list[Path]:
    if wheels.exists():
        shutil.rmtree(wheels)
    wheels.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PIP_CONFIG_FILE="NUL")  # 屏蔽用户镜像配置，防止顶成 +cpu 轮子
    reqs = [f"torch=={torch}", f"torchvision=={torchvision}", *(extra or [])]
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            *reqs,
            "--index-url",
            index_url,
            "--only-binary=:all:",
            "--implementation",
            "cp",
            "--python-version",
            python_tag,
            "--abi",
            f"cp{python_tag}",
            "--platform",
            "win_amd64",
            "-d",
            str(wheels),
        ],
        env=env,
    )
    result = sorted(wheels.glob("*.whl"))
    if not result:
        raise SystemExit(f"wheels 目录为空：{wheels}")
    total = sum(p.stat().st_size for p in result)
    print(f"[ok] {len(result)} 个 wheel，合计 {total / 1e9:.2f} GB")
    return result


def zip_and_split(wheels: Path, out: Path, base_name: str, max_volume_bytes: int) -> list[dict]:
    """ZIP level 0 后按体积切分卷（直接写分卷，避免再拷贝一份大文件）。"""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob(f"{base_name}*"):
        old.unlink()
    volumes: list[dict] = []
    index = 0
    handle = None
    written = 0

    def open_next():
        nonlocal handle, index, written
        if handle:
            handle.close()
        index += 1
        path = out / f"{base_name}.{index:03d}"
        handle = open(path, "wb")  # noqa: SIM115 —— 分卷写入需要跨调用保持文件句柄
        written = 0
        return path

    current = open_next()
    # 用一个临时 zip 写入内存不可行（GB 级），改为：逐条目 zip → 边压边切
    import io

    class SplitWriter(io.RawIOBase):
        def write(self, b):  # noqa: D401
            nonlocal written, current
            view = memoryview(b)
            while view:
                room = max_volume_bytes - written
                if room <= 0:
                    current = open_next()
                    room = max_volume_bytes
                take = min(room, len(view))
                handle.write(view[:take])
                written += take
                view = view[take:]
            return len(b)

    split = SplitWriter()
    with zipfile.ZipFile(split, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for whl in sorted(wheels.glob("*.whl")):
            print(f"[zip] {whl.name}")
            zf.write(whl, arcname=whl.name)
    handle.close()

    for i in range(1, index + 1):
        path = out / f"{base_name}.{i:03d}"
        volumes.append({"name": path.name, "size": path.stat().st_size, "sha256": sha256_of(path)})
        print(f"[vol] {path.name}  {path.stat().st_size / 1e6:.0f} MB  {volumes[-1]['sha256'][:12]}…")
    return volumes


def write_catalog(
    out: Path,
    tag: str,
    base_url: str,
    torch: str,
    torchvision: str,
    cuda: str,
    python_ver: str,
    volumes: list[dict],
    min_app_version: str,
    extra: list[str] | None = None,
) -> Path:
    catalog_path = ROOT / "app" / "downloads_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"schema": 1}
    total = sum(v["size"] for v in volumes)
    catalog["schema"] = 1
    catalog["runtime"] = {
        "version": "1",
        "tag": tag,
        "base_url": base_url.rstrip("/"),
        "python": python_ver,
        "torch": f"{torch}+{cuda}",
        "cuda": cuda,
        "packages": [f"torch=={torch}", f"torchvision=={torchvision}", *(extra or [])],
        "size_bytes": total,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "min_app_version": min_app_version,
        "volumes": volumes,
    }
    catalog.setdefault("models", [])
    text = json.dumps(catalog, ensure_ascii=False, indent=2)
    target = out / "downloads_catalog.json"
    target.write_text(text + "\n", encoding="utf-8")
    real = ROOT / "app" / "downloads_catalog.json"
    real.write_text(text + "\n", encoding="utf-8")
    print(f"[ok] catalog 已写：{target}（并回写仓库基线 {real}）")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--torch", required=True)
    parser.add_argument("--torchvision", required=True)
    parser.add_argument("--cuda", required=True, help="如 cu128")
    parser.add_argument("--index-url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tag", default="runtime-v1")
    parser.add_argument("--min-app-version", default="0.0.0")
    parser.add_argument("--python-version", default="3.12.10")
    parser.add_argument("--python-tag", default="312")
    parser.add_argument(
        "--extra-packages", default="", help="逗号分隔的额外轮子（如 torchaudio==2.5.1），同时写入下载与 packages 清单"
    )
    parser.add_argument("--max-volume-mb", type=int, default=1900)
    parser.add_argument("--staging", default="")
    args = parser.parse_args()

    out = Path(args.out)
    staging = Path(args.staging) if args.staging else out / "_wheels"
    wheels = staging / "wheels"
    base_name = f"{ROOT.name}-Runtime-{args.cuda}.zip"

    print(f"[1/4] 下载 wheels（torch=={args.torch} {args.cuda}）→ {wheels}")
    extras = [x.strip() for x in args.extra_packages.split(",") if x.strip()]
    download_wheels(args.torch, args.torchvision, args.index_url, wheels, args.python_tag, extras)

    print(f"[2/4] 打包分卷（≤{args.max_volume_mb} MB/卷）")
    volumes = zip_and_split(wheels, out, base_name, args.max_volume_mb * 1024 * 1024)

    print("[3/4] 写 catalog")
    write_catalog(
        out,
        args.tag,
        args.base_url,
        args.torch,
        args.torchvision,
        args.cuda,
        args.python_version,
        volumes,
        args.min_app_version,
        extras,
    )

    print("[4/4] SHA256SUMS / manifest")
    sums = [(v["sha256"], v["name"]) for v in volumes]
    catalog_file = out / "downloads_catalog.json"
    sums.append((sha256_of(catalog_file), catalog_file.name))
    (out / "SHA256SUMS.txt").write_text("\n".join(f"{h}  {n}" for h, n in sums) + "\n", encoding="utf-8")
    manifest = {
        "product": ROOT.name,
        "kind": "runtime-bundle",
        "tag": args.tag,
        "torch": f"{args.torch}+{args.cuda}",
        "cuda": args.cuda,
        "python": args.python_version,
        "volumes_bytes": sum(v["size"] for v in volumes),
        "volumes": volumes,
        "base_url": args.base_url,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out / "runtime-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.rmtree(staging, ignore_errors=True)
    print("[done] 产物目录：", out)
    for p in sorted(out.glob("*")):
        print(f"  {p.name}  {p.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
