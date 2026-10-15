#!/usr/bin/env python3
"""坏案例 → 金标准用例回流工具（LLM 应用工程评估 P1-2）。

回流流程（GOLDEN_REFLOW）：
  1. 真机/线上发现坏案例（退化输出、误拦、显著劣化）→ 记录 text/voice/engine/model；
  2. 本工具把坏案例追加为金标准用例（默认 configs/golden_cases.multimodel.json；
     idx 自动取现有最大值 +1；与既有用例 text+voice+engine 重复时拒绝）；
  3. 下一次 GPU 基线运行带 --include-multimodel（扩展集 idx 统一 +100 偏移，
     音色未授权声明记 env_skip 不冒充质量退化）；
  4. 对比判定走 scripts/gpu_golden_compare.py --ref-multimodel（建立参考基线后
     扩展集与主集同口径红黄判定；缺省维持 measure-only）。

用法：
  python scripts/add_golden_case.py --text "坏案例文本" --voice 韩立 \
      [--engine voxcpm2] [--model tts-1] [--note "线上退化"] [--dry-run]

退出码：0 成功；1 拒绝（重复/非法输入/文件结构不符）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "configs" / "golden_cases.multimodel.json"


def load_cases_file(path: Path) -> dict:
    """读取并校验用例文件结构（必须含 cases 列表）。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"用例文件结构不符（缺少 cases 列表）: {path}")
    return payload


def next_idx(cases: list[dict]) -> int:
    """下一个可用 idx（取现有最大 idx + 1；无 idx 视为 0）。"""
    idxs = [int(c["idx"]) for c in cases if isinstance(c.get("idx"), int)]
    return (max(idxs) + 1) if idxs else 1


def append_case(
    path: Path,
    case: dict,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """把一条坏案例追加进用例文件。

    Args:
        path: 用例 JSON 路径。
        case: 至少含 text；voice/model/engine 缺省时由调用方补齐。
        dry_run: True 时不写文件，只做校验。

    Returns:
        (ok, message)：ok=False 时文件保持原样。
    """
    text = (case.get("text") or "").strip()
    if not text:
        return False, "text 为空，拒绝回流空用例"
    voice = case.get("voice") or "alloy"
    engine = case.get("engine") or "voxcpm2"
    payload = load_cases_file(path)
    cases = payload["cases"]
    for c in cases:
        if (
            str(c.get("text", "")).strip() == text
            and str(c.get("voice", "alloy")) == voice
            and str(c.get("engine", "")) == engine
        ):
            return False, f"重复用例（text+voice+engine 已存在，idx={c.get('idx')}），拒绝重复回流"
    new_case = {
        "idx": next_idx(cases),
        "text": text,
        "voice": voice,
        "model": case.get("model") or "tts-1",
        "engine": engine,
    }
    if case.get("note"):
        new_case["note"] = str(case["note"])
    if dry_run:
        return True, f"[dry-run] 将追加 idx={new_case['idx']}: {text[:40]}"
    cases.append(new_case)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, f"已追加 idx={new_case['idx']} -> {path}"


def main() -> int:
    parser = argparse.ArgumentParser(description="坏案例 → 金标准用例回流（GOLDEN_REFLOW 步骤 2）")
    parser.add_argument("--text", required=True, help="坏案例合成文本（必填，非空）")
    parser.add_argument("--voice", default="alloy", help="音色名（默认 alloy 预设音色）")
    parser.add_argument("--engine", default="voxcpm2", help="引擎名（默认 voxcpm2）")
    parser.add_argument("--model", default="tts-1", help="OpenAI 兼容模型名（默认 tts-1）")
    parser.add_argument("--note", default="", help="回流备注（如坏案例现象/工单号）")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="目标用例文件（默认 multimodel 扩展集）")
    parser.add_argument("--dry-run", action="store_true", help="只校验不写文件")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[FAIL] 用例文件不存在: {path}")
        return 1
    try:
        ok, msg = append_case(
            path,
            {"text": args.text, "voice": args.voice, "model": args.model, "engine": args.engine, "note": args.note},
            dry_run=args.dry_run,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(("[OK] " if ok else "[FAIL] ") + msg)
    if not ok:
        # 拒绝路径以 SystemExit(1) 终止（与 argparse 失败语义一致，测试按退出码校验）
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
