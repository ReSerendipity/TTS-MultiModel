#!/usr/bin/env python3
"""为「水印到底听不听得出来」准备同源自 A/B 对照素材。

为什么需要脚本而不是直接听 outputs/ 里的成品：产出的成品**都已经带水印**，
拿不出「同一段语音的无水印版」。这里用未加过水印的真人参考音频做源，
A/B 两段来自同一份内存数据，唯一差异就是 ``watermark_audio()`` 嵌进去的那层载波。

三档 I/O：0 次网络请求、不加载任何 TTS 引擎、不写 model//outputs/ 禁区目录，
只在 --out 目录里新建 2N 个 wav + 1 个 README.md（已存在的同名文件会被覆盖，
因为它们是本脚本自己产出的衍生物）。

    python scripts/make_watermark_ab.py                 # 默认 personas/ 里挑 3 段（24k/44.1k/48k）
    python scripts/make_watermark_ab.py --seconds 12 --out docs/reports/watermark_ab

注意：默认产物目录 ``docs/reports/`` 被 ``.gitignore`` 第 245 行整目录忽略，所以这批 wav
只存在于本机，**要留在版本库里就得换 --out 到一个未忽略的路径**。判定命令本身（§5.2）
不依赖它存在，重跑一次即可再生成。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, welch

_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE / "app"))

from integrated_app.watermark import (  # noqa: E402
    _WATERMARK_FREQ_HIGH,
    _WATERMARK_FREQ_LOW,
    detect_watermark,
    watermark_audio,
)

_trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 2.0 改了名


def _mono(data: np.ndarray) -> np.ndarray:
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def _resample(data: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return data
    from math import gcd

    g = gcd(int(sr_in), int(sr_out))
    return resample_poly(data, sr_out // g, sr_in // g).astype(np.float32)


def _band_energy(data: np.ndarray, sr: int, lo: float, hi: float) -> float:
    """给定频段内的 RMS 谱能量（Welch），用来量化「B 比 A 多了多少高频内容」。"""
    n = min(len(data), 8192)
    freqs, psd = welch(data[:n], fs=sr, nperseg=min(2048, n))
    band = (freqs >= lo) & (freqs <= hi)
    return float(np.sqrt(_trapz(psd[band], freqs[band]))) if band.any() else 0.0


def _pick_sources(personas_dir: Path, limit: int) -> list[tuple[str, Path]]:
    """按采样率分档挑源，保证 24k（上采样路径）/44.1k/48k（原生路径）都被覆盖。"""
    buckets: dict[str, tuple[str, Path]] = {}
    for path in sorted(personas_dir.glob("*.wav")):
        if path.stat().st_size < 40_000:
            continue
        try:
            sr = sf.info(str(path)).samplerate
        except Exception:  # noqa: BLE001 — 读不动的样本直接跳过，不是本脚本的职责
            continue
        tier = "low" if sr < 40000 else ("mid" if sr < 48000 else "native")
        if tier not in buckets:
            buckets[tier] = (path.stem, path)
    order = [v for k, v in sorted(buckets.items())]
    return [(name, p) for name, p in order][:limit]


def build_pairs(sources: list[tuple[str, Path]], out_dir: Path, seconds: float) -> list[dict[str, object]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for idx, (_stem, path) in enumerate(sources, start=1):
        audio, sr = sf.read(str(path), dtype="float32")
        data = _mono(audio)[: int(seconds * sr)]
        # 文件名不带源人名：personas/ 里是中文人名，本仓有过中文文件名在打包/CI 链路上
        # 被编码规则坑掉的先例（GOTCHAS 里 bsdtar 那条），产物一律用纯 ASCII 名。
        tag = f"s{idx}_{sr}hz"

        watermarked, meta = watermark_audio(data.copy(), sr, source_id="tts-multimodel", output_path=None)
        embedded = bool(meta.get("watermarked"))
        sr_out = int(meta.get("sample_rate_out") or sr)

        # A 也重采样到 B 的采样率：否则两段唯一的差异就不只是水印，还有带宽。
        a = _resample(data, sr, sr_out)
        b = _mono(np.asarray(watermarked))
        n = min(len(a), len(b))
        a, b = a[:n], b[:n]

        a_path, b_path = out_dir / f"{tag}_A_clean.wav", out_dir / f"{tag}_B_watermark.wav"
        sf.write(str(a_path), a, sr_out, subtype="PCM_16")
        sf.write(str(b_path), b, sr_out, subtype="PCM_16")

        det = detect_watermark(b, sr_out) if embedded else None
        diff = (b - a).astype(np.float64)
        peak = float(np.abs(diff).max()) if n else 0.0
        rms_a = float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) or 1e-9
        rows.append(
            {
                "tag": tag,
                "src": os.path.relpath(path, _HERE).replace("\\", "/"),
                "sr_in": sr,
                "sr_out": sr_out,
                "seconds": round(n / sr_out, 2),
                "embedded": embedded,
                "snr_db": meta.get("snr_db"),
                "detect_ok": None if det is None else bool(det.success),
                "detect_msg": None if det is None else str(det.message)[:60],
                "peak_diff_pct": round(peak / (float(np.abs(a).max()) or 1.0) * 100, 2),
                "rms_diff_db": round(20 * np.log10(float(np.sqrt(np.mean(diff**2))) / rms_a), 1),
                "band_a": round(_band_energy(a, sr_out, _WATERMARK_FREQ_LOW, _WATERMARK_FREQ_HIGH), 8),
                "band_b": round(_band_energy(b, sr_out, _WATERMARK_FREQ_LOW, _WATERMARK_FREQ_HIGH), 8),
            }
        )
    return rows


def write_report(rows: list[dict[str, object]], out_dir: Path) -> Path:
    lines = [
        "# 水印听感 A/B 对照素材",
        "",
        "由 `python scripts/make_watermark_ab.py` 生成。每一对的 A、B 两段来自**同一份内存数据**，",
        "采样率与长度都对齐，唯一差异就是 `watermark_audio()` 嵌进 16–20kHz 的那层载波。",
        "",
        "> 源是 `personas/` 里未经水印的真人参考音频，不是新生成的 TTS 产物——成品音频全都已经带水印，",
        "> 拿不出无水印对照。要听真实引擎输出上的差异，需按 README 末尾的开关各生成一次。",
        "",
        "| 编号 | 源 | 输入 sr | 输出 sr | 秒 | 已嵌入 | SNR(dB) | 解码检测 | 峰值差(%) | 差值 RMS(dB) | 16–20k 能量 A→B |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| `{r['tag']}` | {r['src']} | {r['sr_in']} | {r['sr_out']} | {r['seconds']} "
            f"| {r['embedded']} | {r['snr_db']} | 成功={r['detect_ok']}（{r['detect_msg']}） "
            f"| {r['peak_diff_pct']} | {r['rms_diff_db']} | {r['band_a']} → {r['band_b']} |"
        )
    lines += [
        "",
        "## 怎么听",
        "",
        "1. 用同一条输出链路（同一副耳机、同一音量），A→B→A→B 来回切，注意力放在齿音/空气感那一层。",
        "2. 判据：**能否稳定分辨出哪一段是 B**。听不出但表里 `已嵌入=True`、`解码检测 成功=True` 是理想结果。",
        "3. 16–20kHz 是人耳阈值以上的窄带能量，B 列应高于 A 列；若两列接近而检测仍成功，说明载波被削——属于缺陷。",
        "",
        "## 若要听真实引擎输出",
        "先关总开关生成 A，再开回来生成同一段文本得到 B：",
        "",
        "```bash",
        "# config.yaml: security.audio_watermark_enabled: false → 生成 A",
        "# 改回 true → 生成 B（同文本、同音色、同 seed，否则差异不唯一）",
        "```",
        "",
    ]
    report = out_dir / "README.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personas", default=str(_HERE / "personas"), help="源音频目录（只读）")
    parser.add_argument("--out", default=str(_HERE / "docs/reports/watermark_ab"), help="产物目录")
    parser.add_argument("--seconds", type=float, default=8.0, help="每段取前 N 秒")
    parser.add_argument("--limit", type=int, default=3, help="最多做几对（按 sr 分档挑源）")
    args = parser.parse_args(argv)

    sources = _pick_sources(Path(args.personas), args.limit)
    if not sources:
        print(f"no usable wav under {args.personas}", file=sys.stderr)
        return 1
    rows = build_pairs(sources, Path(args.out), args.seconds)
    report = write_report(rows, Path(args.out))
    for r in rows:
        print(
            f"{r['tag']}: sr {r['sr_in']}->{r['sr_out']} embedded={r['embedded']} snr={r['snr_db']} detect={r['detect_ok']}"
        )
    print(f"report -> {report.relative_to(_HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
