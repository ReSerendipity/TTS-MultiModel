#!/usr/bin/env python3
"""golden WAV 集离线 MOS 评分（NEXT 评估 P0-1：感知音质口径进 golden）。

对 ``scripts/gpu_golden_baseline.py --save-wav-dir`` 落盘的 ``golden_*.wav`` 用
UTMOS2 打分（0~4 量纲的客观 MOS），输出均值与逐条分数；可选 ``--merge`` 把分数
回写进 measured JSON（``summary.mean_mos`` + 逐条 ``mos_score``），供
``gpu_golden_compare.py`` 的 MOS 口径消费。

退出码：
  0  打分成功并写出（或合并）
  3  UTMOS2 未安装 → SKIP（workflow 按黄处理，不阻断；与 ENV_PROBLEM 约定一致）
  2  用法/文件错误
  1  打分过程失败

UTMOS2 是可选依赖（report 点名的客观 MOS 方案）：缺它不装、不影响核心门禁，
评分只在真机合成出 wav 后才有意义（GPU 真跑按既有 skip 台账约定记受阻）。

用法：
  python scripts/gpu_golden_mos.py --wav-dir golden_wavs --output golden_mos.json
  python scripts/gpu_golden_mos.py --wav-dir golden_wavs --merge golden_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_scorer() -> tuple | None:
    """懒加载 UTMOS2 打分函数：优先 ``utmos2_score``，回退 ``utmos22_score``。

    Returns:
        (func, backend_name) 或 None（两者均不可导入 = 可选依赖缺失）。
    """
    try:
        from utmos2_score import utmos2_score  # type: ignore[import-not-found]

        return utmos2_score, "utmos2_score"
    except Exception:
        pass
    try:
        from utmos22_score import utmos22_score  # type: ignore[import-not-found]

        return utmos22_score, "utmos22_score"
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="golden wav 离线 MOS 评分（UTMOS2 可选）")
    parser.add_argument("--wav-dir", required=True, help="gpu_golden_baseline.py --save-wav-dir 落盘目录")
    parser.add_argument("--output", default="golden_mos.json", help="评分输出 JSON 路径")
    parser.add_argument("--merge", default=None, help="把 mean_mos / 逐条 mos_score 回写进该 measured JSON（原地）")
    args = parser.parse_args(argv)

    wav_dir = Path(args.wav_dir)
    if not wav_dir.is_dir():
        print(f"ERROR: wav-dir 不存在: {wav_dir}", file=sys.stderr)
        return 2
    wavs = sorted(wav_dir.glob("golden_*.wav"))
    if not wavs:
        print("ERROR: wav-dir 内没有 golden_*.wav（检查 --save-wav-dir 是否生效）", file=sys.stderr)
        return 2

    scorer = load_scorer()
    if scorer is None:
        print("MOS SKIPPED: UTMOS2 未安装（可选依赖，pip install utmos2_score 可启用）；本轮未做感知评分")
        return 3

    func, backend = scorer
    try:
        scores = func([str(p) for p in wavs])
    except Exception as exc:  # noqa: BLE001 - 打分器内部异常统一归类
        print(f"ERROR: UTMOS2 打分失败: {exc}", file=sys.stderr)
        return 1

    per_file = {p.name: round(float(s), 3) for p, s in zip(wavs, scores)}
    payload = {
        "backend": backend,
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "n": len(per_file),
        "mean_mos": round(sum(per_file.values()) / len(per_file), 3),
        "per_file": per_file,
    }

    if args.merge:
        merge_path = Path(args.merge)
        try:
            with merge_path.open(encoding="utf-8") as f:
                meas = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"ERROR: 读取 --merge 目标失败: {exc}", file=sys.stderr)
            return 2
        meas["mean_mos"] = payload["mean_mos"]
        for item in meas.get("results", []):
            idx = item.get("idx")
            if idx is not None:
                item["mos_score"] = per_file.get(f"golden_{idx:02d}.wav")
        with merge_path.open("w", encoding="utf-8") as f:
            json.dump(meas, f, ensure_ascii=False, indent=2)
        print(f"[mos] 已合并 mean_mos={payload['mean_mos']} 进 {merge_path}")

    out = Path(args.output)
    with out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[mos] backend={backend} mean_mos={payload['mean_mos']} n={payload['n']} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
