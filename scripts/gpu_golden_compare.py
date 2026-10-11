#!/usr/bin/env python3
"""scripts/gpu_golden_compare.py — 对比 golden 实测与参考基线，产出 junit。

退出码：
  0  内容通过（可带 sha 软警告）
  2  内容漂移（红）：有非 200、或某条时长漂移超阈值、或 mean RTF 较基线劣化超阈值
  1  用法/文件错误

环境问题（CUDA 不可用、服务起不来、OOM）由上层 workflow 的启动步骤判定，
本脚本只在 golden 已经跑出 all_ok 结果后运行，不负责环境分类。

用法：
  python scripts/gpu_golden_compare.py \
      --measured golden_baseline.json \
      --ref configs/golden_baseline.voxcpm2.ref.json \
      --junit golden_baseline.junit.xml
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measured", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--junit", default="golden_baseline.junit.xml")
    args = ap.parse_args()

    with open(args.measured, encoding="utf-8") as f:
        meas = json.load(f)
    with open(args.ref, encoding="utf-8") as f:
        ref = json.load(f)

    by_idx = {r["idx"]: r for r in meas.get("results", [])}
    hard_failures: list[str] = []
    soft_warnings: list[str] = []

    if not meas.get("all_ok"):
        hard_failures.append("golden 并非全部 200（见 measured results status）")

    dur_tol = ref.get("duration_drift_hard_pct", 20.0) / 100.0
    rtf_base = ref.get("mean_rtf_baseline", 0.0)
    rtf_warn = ref.get("rtf_regression_warn_pct", 30.0) / 100.0

    for item in ref["items"]:
        i = item["idx"]
        m = by_idx.get(i)
        if m is None:
            hard_failures.append(f"#{i} 缺失实测记录")
            continue
        if m.get("status") != 200:
            hard_failures.append(f"#{i} status={m.get('status')}")
            continue
        ref_dur = item["audio_duration_sec"]
        m_dur = m.get("audio_duration_sec") or 0.0
        if ref_dur > 0:
            drift = abs(m_dur - ref_dur) / ref_dur
            if drift > dur_tol:
                hard_failures.append(
                    f"#{i} 时长漂移 {drift * 100:.1f}%（ref={ref_dur}s measured={m_dur}s，阈值 {dur_tol * 100:.0f}%）"
                )
        # sha/bytes 软信号（CUDA/driver 浮点会微动，不红）
        if m.get("audio_sha256") != item["audio_sha256"]:
            soft_warnings.append(f"#{i} sha256 漂移（ref={item['audio_sha256']} measured={m.get('audio_sha256')}）")

    m_rtf = meas.get("mean_rtf", 0.0)
    if rtf_base > 0 and m_rtf > rtf_base * (1 + rtf_warn):
        hard_failures.append(f"mean RTF 劣化 {m_rtf:.3f} vs 基线 {rtf_base}（阈值 +{rtf_warn * 100:.0f}%）")

    # MOS 感知评分（可选口径，NEXT 评估 P0-1）：ref 声明 mean_mos_baseline 且
    # measured 提供 mean_mos 时才硬判定；任一侧缺失只给软警告，不编造数值。
    # MOS 基线由 gpu_golden_mos.py 对落盘 wav 打分积累（机制已就绪，基线待积累）。
    mos_base = ref.get("mean_mos_baseline")
    mos_hard = ref.get("mos_regression_hard_pct", 10.0) / 100.0
    m_mos = meas.get("mean_mos")
    if mos_base is not None and m_mos is not None:
        if m_mos < mos_base * (1 - mos_hard):
            hard_failures.append(f"mean MOS 劣化 {m_mos:.3f} vs 基线 {mos_base}（阈值 -{mos_hard * 100:.0f}%）")
        elif m_mos < mos_base:
            soft_warnings.append(f"mean MOS 略降 {m_mos:.3f} vs 基线 {mos_base}")
    elif mos_base is not None and m_mos is None:
        soft_warnings.append("ref 声明了 mean_mos_baseline 但 measured 无 mean_mos（MOS 评分未跑或缺失）")
    for item in ref["items"]:
        ref_mos = item.get("mos_score")
        m = by_idx.get(item["idx"])
        m_mos_i = (m or {}).get("mos_score")
        if ref_mos is not None and m_mos_i is not None and m_mos_i < ref_mos * (1 - mos_hard):
            soft_warnings.append(f"#{item['idx']} MOS 下降 {m_mos_i:.3f} vs ref {ref_mos}（软警告，均值口径才硬）")

    # junit
    ts = ET.Element(
        "testsuite",
        name="golden-baseline",
        tests=str(len(ref["items"]) + 1),
        failures=str(len(hard_failures)),
        warnings=str(len(soft_warnings)),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    tc = ET.SubElement(ts, "testcase", name="content-drift", classname="golden")
    if hard_failures:
        ET.SubElement(tc, "failure", message="content drift").text = "\n".join(hard_failures)
    if soft_warnings:
        ET.SubElement(tc, "system-out").text = "WARNINGS:\n" + "\n".join(soft_warnings)
    ET.ElementTree(ts).write(args.junit, encoding="utf-8", xml_declaration=True)

    print(f"[compare] mean_rtf={m_rtf} baseline={rtf_base}")
    for w in soft_warnings:
        print(f"[warn] {w}")
    if hard_failures:
        print("[RED] 内容漂移：")
        for h in hard_failures:
            print(f"  - {h}")
        return 2
    print("[GREEN] 内容无硬漂移（sha 软警告见上）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
