#!/usr/bin/env python3
"""GPU 训练最小冒烟测试（1 步 LoRA 微调 + checkpoint 非空校验）。

在自托管 GPU runner 上运行，验证训练链路端到端可用：
  1. 生成最小训练数据集（合成音频 + 文本，3 样本）
  2. 调用 scripts/train_voxcpm_finetune.py 跑 1 步训练
  3. 校验输出目录存在非空 .safetensors checkpoint
  4. 清理临时文件

退出码：0 = 通过，非 0 = 失败（含具体原因）。

用法：
    python scripts/gpu_training_smoke.py [--pretrained model/VoxCPM2] [--workdir tests/_tmp_smoke]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def create_minimal_dataset(data_dir: Path, sample_count: int = 3) -> Path:
    """生成最小训练数据集（合成正弦波音频 + 中文文本）。

    Returns:
        manifest.jsonl 的路径。
    """
    import numpy as np

    data_dir.mkdir(parents=True, exist_ok=True)
    sr = 16000
    manifest: list[dict[str, str]] = []

    for i in range(sample_count):
        wav_path = data_dir / f"sample{i:03d}.wav"
        txt_path = data_dir / f"sample{i:03d}.txt"
        # 合成 2 秒低幅正弦波（避免真实音频依赖）
        t = np.linspace(0, 2.0, sr * 2, endpoint=False)
        audio = (0.01 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        try:
            import soundfile as sf

            sf.write(str(wav_path), audio, sr)
        except ImportError:
            import torch
            import torchaudio

            torchaudio.save(str(wav_path), torch.from_numpy(audio).unsqueeze(0), sr)
        txt_path.write_text(f"测试文本样本编号 {i}，用于训练冒烟验证。", encoding="utf-8")
        manifest.append({"audio_file": wav_path.name, "text": f"测试文本样本编号 {i}"})

    manifest_path = data_dir / "manifest.jsonl"
    with open(manifest_path, "w", encoding="utf-8") as f:
        for item in manifest:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return manifest_path


def run_one_step_training(
    pretrained_path: Path,
    manifest_path: Path,
    output_dir: Path,
    timeout_sec: int = 600,
) -> int:
    """调用训练脚本执行 1 步 LoRA 微调。

    Returns:
        训练子进程退出码。
    """
    project_root = _project_root()
    train_script = project_root / "scripts" / "train_voxcpm_finetune.py"
    if not train_script.is_file():
        print(f"[FATAL] 训练脚本不存在: {train_script}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(train_script),
        "--pretrained_path",
        str(pretrained_path),
        "--train_manifest",
        str(manifest_path),
        "--save_path",
        str(output_dir),
        "--num_iters",
        "1",
        "--batch_size",
        "1",
        "--log_interval",
        "1",
        "--save_interval",
        "1",
        "--warmup_steps",
        "0",
    ]
    print(f"[RUN] {' '.join(cmd)}")
    env = {**os.environ, "TOKENIZERS_PARALLELISM": "false", "PYTHONUNBUFFERED": "1"}
    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            env=env,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print(f"[FATAL] 训练超时（{timeout_sec}s）", file=sys.stderr)
        return 3

    if result.stdout:
        print("--- training stdout (tail) ---")
        print("\n".join(result.stdout.splitlines()[-50:]))
    if result.stderr:
        print("--- training stderr (tail) ---", file=sys.stderr)
        print("\n".join(result.stderr.splitlines()[-50:]), file=sys.stderr)
    return result.returncode


def verify_checkpoint(output_dir: Path) -> tuple[bool, str]:
    """校验训练输出目录存在非空 checkpoint 文件。

    Returns:
        (passed: bool, message: str)
    """
    if not output_dir.is_dir():
        return False, f"输出目录不存在: {output_dir}"
    checkpoints = list(output_dir.rglob("*.safetensors"))
    if not checkpoints:
        # 也接受 .pt（全参数模式）
        checkpoints = list(output_dir.rglob("*.pt"))
    if not checkpoints:
        all_files = list(output_dir.rglob("*"))
        return False, f"未找到 checkpoint 文件（输出目录共 {len(all_files)} 个文件）"
    ckpt = checkpoints[0]
    size = ckpt.stat().st_size
    if size < 1024:
        return False, f"checkpoint 过小（{size} bytes），疑似空文件: {ckpt}"
    return True, f"checkpoint 校验通过: {ckpt.relative_to(output_dir)} ({size / 1024:.1f} KB)"


def main() -> int:
    parser = argparse.ArgumentParser(description="GPU 训练最小冒烟测试")
    parser.add_argument("--pretrained", default="model/VoxCPM2", help="预训练模型路径")
    parser.add_argument("--workdir", default=None, help="临时工作目录（默认系统 temp）")
    parser.add_argument("--timeout", type=int, default=600, help="训练超时秒数")
    parser.add_argument("--keep", action="store_true", help="保留临时文件（不清理）")
    args = parser.parse_args()

    project_root = _project_root()
    pretrained_path = (project_root / args.pretrained).resolve()
    if not pretrained_path.is_dir():
        print(f"[FATAL] 预训练模型目录不存在: {pretrained_path}", file=sys.stderr)
        return 2

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="tts_train_smoke_"))
    data_dir = workdir / "data"
    output_dir = workdir / "output"

    try:
        print(f"[1/3] 生成最小训练数据集 -> {data_dir}")
        manifest_path = create_minimal_dataset(data_dir)
        print(f"      manifest: {manifest_path}")

        print(f"[2/3] 执行 1 步训练（超时 {args.timeout}s）...")
        rc = run_one_step_training(pretrained_path, manifest_path, output_dir, args.timeout)
        if rc != 0:
            print(f"[FAIL] 训练进程退出码 {rc}", file=sys.stderr)
            return 1

        print(f"[3/3] 校验 checkpoint -> {output_dir}")
        ok, msg = verify_checkpoint(output_dir)
        if not ok:
            print(f"[FAIL] {msg}", file=sys.stderr)
            return 1
        print(f"[PASS] {msg}")
        print("\n=== 训练冒烟全部通过 ===")
        return 0
    finally:
        if not args.keep and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
