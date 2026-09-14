# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""TTS_MultiModel — 统一模型下载器（框架-only 分发配套）。

设计目标（对应分发审计「第 2 章 / 缺口 #1」）：
    把零散的 per-engine 下载脚本（仅 indextts2 / indextts20 有）收敛为一个
    统一入口，覆盖全部 7 个模型 / 权重集合，并固化「国内社区优先」策略：
        1. **优先 Hugging Face 镜像社区** https://hf-mirror.com （国内通畅）；
        2. **缺失 / 失败时回退魔搭社区** ModelScope （国内通畅）；
        3. 个别权重仅 ModelScope 有（如 speech_zipenhancer）→ 直连 ModelScope。
    下载均走官方 SDK 的 snapshot_download，**天然断点续传**（缓存目录 + 本地目录
    分离，重复运行只补缺失分片，不重下）。

安全：
    - 下载完成后按 docs/SHA256SUMS.models 做 SHA256 比对（若该模型已有登记哈希）；
      清单为空时退化为「文件存在 + 权重文件体积预检」，不阻断（与 verify_model_checksums.py 的 no-op 行为一致）。
    - 框架本身（Apache-2.0）只做编排，权重指向官方仓库、由用户自取——
      本脚本不打包任何权重，符合「发框架、用户自取模型」的分发策略。

用法：
    python scripts/download_models.py --all            # 拉全部 7 个
    python scripts/download_models.py --model voxcpm2  # 只拉某一个
    python scripts/download_models.py --all --source hf         # 强制只用 HF 镜像
    python scripts/download_models.py --all --source modelscope # 强制只用魔搭
    python scripts/download_models.py --all --no-verify        # 跳过哈希校验

依赖：
    pip install huggingface_hub modelscope
    （HF 镜像无需额外依赖；ModelScope 回退需要 modelscope）
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("download_models")

#: HF 镜像社区（国内首选）
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "model"

#: 每个模型集合的完整描述。
#:   local_dir  : 落盘到 model/<local_dir>
#:   hf_repo    : Hugging Face 仓库 ID（None 表示 HF 无源，直连 ModelScope）
#:   ms_repo    : ModelScope 仓库 ID（None 表示 ModelScope 无源）
#:   license    : 许可提示（仅提示，不做强制校验）
#:   note       : 落盘后的摆放注意 / 引擎期望目录结构
#:   required   : 期望存在的核心文件（相对 local_dir）；缺失则告警
MODEL_REGISTRY: list[dict] = [
    {
        "key": "voxcpm2",
        "title": "VoxCPM2（核心多模态 TTS 引擎）",
        "local_dir": "VoxCPM2",
        "hf_repo": "openbmb/VoxCPM2",
        "ms_repo": "OpenBMB/VoxCPM2",
        "license": "Apache-2.0（可商用）",
        "note": "由 voxcpm 库在加载时 from_pretrained 读取；整个仓库落到 model/VoxCPM2 即可。",
        "required": [],
    },
    {
        "key": "sensevoice",
        "title": "SenseVoiceSmall（VoxCPM2 的 ASR 辅助）",
        "local_dir": "SenseVoiceSmall",
        "hf_repo": "FunAudioLLM/SenseVoiceSmall",
        "ms_repo": "iic/SenseVoiceSmall",
        "license": "FunAudioLLM 许可（研究为主，商用需确认）",
        "note": "落盘到 model/SenseVoiceSmall。",
        "required": [],
    },
    {
        "key": "zipenhancer",
        "title": "speech_zipenhancer（VoxCPM2 的降噪辅助）",
        "local_dir": "speech_zipenhancer",
        "hf_repo": None,  # 仅 ModelScope 有
        "ms_repo": "iic/speech_zipenhancer_ans_multiloss_16k_base",
        "license": "需确认（iic 社区权重）",
        "note": "HF 无官方源，直连 ModelScope。落盘到 model/speech_zipenhancer。",
        "required": [],
    },
    {
        "key": "indextts2",
        "title": "IndexTTS 2.5（情感控制引擎）",
        "local_dir": "IndexTTS-2.5",
        "hf_repo": "IndexTeam/IndexTTS-2.5",
        "ms_repo": "IndexTeam/IndexTTS-2.5",
        "license": "bilibili Model Use License（自定义，商用需确认）",
        "note": "落盘到 model/IndexTTS-2.5。辅助模型（w2v-bert-2.0 / MaskGCT / CAMPPlus / BigVGAN）"
        "首次运行由 index-tts 库自动拉到 hf_cache/，需联网。",
        "required": ["config.yaml", "gpt.pth", "s2mel.pth"],
    },
    {
        "key": "indextts20",
        "title": "IndexTTS 2.0（2.5 的旧版本变体，权重不通用）",
        "local_dir": "IndexTTS-2.0",
        "hf_repo": "IndexTeam/IndexTTS-2",
        "ms_repo": "IndexTeam/IndexTTS-2",
        "license": "bilibili Model Use License（自定义，商用需确认）",
        "note": "落盘到 model/IndexTTS-2.0（与 2.5 同名文件但权重不同，必须分开目录）。",
        "required": ["config.yaml", "gpt.pth"],
    },
    {
        "key": "openvoice",
        "title": "OpenVoice（Voicebox 语音转换引擎权重）",
        "local_dir": "OpenVoice",
        "hf_repo": "myshell-ai/OpenVoice",
        "ms_repo": "myshell-ai/OpenVoice",
        "license": "MIT（代码）；权重许可需确认",
        "note": "引擎期望 model/OpenVoice/config.json + checkpoint.pth（ToneColorConverter）。"
        "HF 仓库文件在 checkpoints/converter/ 下，下载后脚本会自动把 converter 目录的"
        "config.json / checkpoint.pth 软链/复制到 model/OpenVoice 根目录。",
        "required": ["config.json", "checkpoint.pth"],
    },
    {
        "key": "step-audio-editx",
        "title": "Step-Audio-EditX（音频编辑引擎权重）",
        "local_dir": "Step-Audio-EditX",
        "hf_repo": "stepfun-ai/Step-Audio-EditX",
        "ms_repo": "stepfun-ai/Step-Audio-EditX",
        "license": "代码 Apache-2.0；权重许可未明示",
        "note": "引擎期望 model/Step-Audio-EditX/（含 LLM 权重 + CosyVoice-300M-25Hz），"
        "同级的 Step-Audio-Tokenizer 会被自动检测。整仓落盘到 model/Step-Audio-EditX 即可。",
        "required": [],
    },
]


def _set_hf_mirror() -> None:
    """把 huggingface_hub 的下载端点切到国内镜像 hf-mirror.com。"""
    os.environ.setdefault("HF_ENDPOINT", HF_MIRROR_ENDPOINT)
    # 允许通过环境变量覆盖（例如用户想直连官方 HF 时设 HF_ENDPOINT=https://huggingface.co）
    logger.info("HF_ENDPOINT = %s", os.environ.get("HF_ENDPOINT"))


def _download_hf(repo_id: str, local_dir: Path) -> Path:
    """从 Hugging Face（经 HF_ENDPOINT，默认 hf-mirror.com）下载整个仓库。

    断点续传：huggingface_hub.snapshot_download 会复用本地缓存并只补缺失分片。
    落盘：local_dir_use_symlinks=False 让 model/ 下是真实文件（非指向缓存的软链），
    方便引擎与 SHA256 校验直接读取。

    Raises:
        Exception: 任何下载 / 导入异常（由调用方决定是否回退 ModelScope）。
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:  # 没装 huggingface_hub
        raise RuntimeError(
            "huggingface_hub 未安装，请先 `pip install huggingface_hub`；或改用 --source modelscope 直连魔搭"
        ) from e

    logger.info("[HF] 从 %s 下载到 %s", repo_id, local_dir)
    return Path(
        snapshot_download(
            repo_id,
            repo_type="model",
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
            # resume_download=True 确保断点续传（老版本默认 True，新版本显式声明更安全）
            resume_download=True,
        )
    )


def _download_modelscope(repo_id: str, local_dir: Path) -> Path:
    """从 ModelScope 下载整个仓库（断点续传 + 本地目录落盘）。"""
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError as e:
        raise RuntimeError(
            "modelscope 未安装，请先 `pip install modelscope`；或改用 --source hf 走 Hugging Face 镜像"
        ) from e

    logger.info("[ModelScope] 从 %s 下载到 %s", repo_id, local_dir)
    cache_dir = local_dir.parent / ".cache" / local_dir.name
    return Path(snapshot_download(repo_id, cache_dir=str(cache_dir), local_dir=str(local_dir)))


def _arrange_openvoice(local_dir: Path) -> None:
    """OpenVoice 引擎特例：把 checkpoints/converter/{config.json,checkpoint.pth}
    摆到 model/OpenVoice 根目录（引擎期望的位置）。幂等、不删原文件。"""
    converter = local_dir / "checkpoints" / "converter"
    if not converter.exists():
        return
    for name in ("config.json", "checkpoint.pth"):
        src = converter / name
        dst = local_dir / name
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
                logger.info("[OpenVoice] 已复制 %s → %s", src, dst)
            except OSError as e:
                logger.warning("[OpenVoice] 复制 %s 失败（可手动处理）: %s", name, e)


def _verify_model(name: str, entry: dict, local_dir: Path, do_verify: bool) -> bool:
    """下载后校验：优先 SHA256 清单比对；清单无该模型条目时退化为体积预检。

    Returns:
        True 通过 / False 失败（失败时调用方据此返回非零退出码）。
    """
    # 1) 核心文件存在性
    missing = [f for f in entry["required"] if not (local_dir / f).exists()]
    if missing:
        logger.warning("[%s] 缺少核心文件: %s —— 请核对仓库实际文件布局", name, missing)

    if not do_verify:
        logger.info("[%s] 已跳过哈希校验（--no-verify）", name)
        return not missing

    # 2) SHA256 比对（仅当清单里有该模型的条目）
    sums_file = PROJECT_ROOT / "docs" / "SHA256SUMS.models"
    if sums_file.exists():
        import hashlib

        entries: list[tuple[str, str]] = []
        with open(sums_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2:
                    entries.append((parts[0].lower().strip(), parts[1].strip()))

        prefix = f"{entry['local_dir']}/"
        relevant = [(h, p) for h, p in entries if p.startswith(prefix)]
        if relevant:
            logger.info("[%s] 比对 %d 个已登记哈希…", name, len(relevant))
            ok = True
            for expected, rel in relevant:
                fp = PROJECT_ROOT / "model" / rel
                if not fp.exists():
                    logger.error("[%s][FAIL] 文件缺失: %s", name, rel)
                    ok = False
                    continue
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    for c in iter(lambda: f.read(1 << 20), b""):
                        h.update(c)
                if h.hexdigest() == expected:
                    logger.info("[%s][OK]   %s", name, rel)
                else:
                    logger.error("[%s][FAIL] %s 哈希不匹配", name, rel)
                    ok = False
            return ok and not missing
        logger.info("[%s] 清单中暂无该模型哈希，退化为体积预检", name)

    # 3) 体积预检（清单为空时的 no-op 等价物）
    weight_exts = {".pth", ".pt", ".safetensors", ".bin"}
    weights = [f for f in local_dir.rglob("*") if f.suffix in weight_exts]
    if not weights:
        logger.warning("[%s] 未在 %s 找到任何权重文件（可能下载不完整）", name, local_dir)
        return False
    tiny = [w for w in weights if w.stat().st_size < 1024 * 1024]
    for w in weights:
        logger.info("[%s][OK] %s (%.1f MB)", name, w.name, w.stat().st_size / 1e6)
    if tiny:
        logger.warning("[%s] 存在过小权重文件（<1MB，可能损坏）: %s", name, tiny)
        return False
    return not missing


def download_one(entry: dict, source: str, do_verify: bool) -> bool:
    """按「HF 镜像优先、ModelScope 回退」策略下载单个模型集合。

    Args:
        entry: MODEL_REGISTRY 中的一项。
        source: "auto"（镜像优先）/ "hf" / "modelscope"。
        do_verify: 是否做哈希/体积校验。

    Returns:
        True 成功，False 失败。
    """
    name = entry["key"]
    local_dir = MODELS_DIR / entry["local_dir"]
    local_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("开始处理: %s", entry["title"])
    logger.info("  许可: %s", entry["license"])
    logger.info("  落盘: %s", local_dir)
    if entry["note"]:
        logger.info("  注意: %s", entry["note"])

    success = False
    # 决定尝试顺序
    want_hf = source in ("auto", "hf") and entry["hf_repo"]
    want_ms = source in ("auto", "modelscope") and entry["ms_repo"]

    if source == "hf":
        want_ms = False
    if source == "modelscope":
        want_hf = False

    if want_hf:
        _set_hf_mirror()
        try:
            _download_hf(entry["hf_repo"], local_dir)
            success = True
        except Exception as e:  # 镜像缺失 / 网络失败 / 依赖缺失 → 回退
            logger.warning("[HF] 下载失败: %s", e)
            if not want_ms:
                logger.error("[%s] HF 下载失败且无 ModelScope 回退，终止", name)
                return False
            logger.info("[%s] 回退到 ModelScope…", name)

    if not success and want_ms:
        try:
            _download_modelscope(entry["ms_repo"], local_dir)
            success = True
        except Exception as e:
            logger.error("[ModelScope] 下载失败: %s", e)
            return False

    if not success:
        logger.error("[%s] 无可用下载源（HF=%s / MS=%s）", name, bool(entry["hf_repo"]), bool(entry["ms_repo"]))
        return False

    # OpenVoice 特例摆放
    if name == "openvoice":
        _arrange_openvoice(local_dir)

    # 校验
    if not _verify_model(name, entry, local_dir, do_verify):
        logger.warning("[%s] 校验未完全通过（见上方日志）", name)
        return False

    logger.info("[%s] 下载完成 ✅", name)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="TTS_MultiModel 统一模型下载器（HF 镜像优先，ModelScope 回退）")
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="KEY",
        help="只下载指定模型（可重复）。KEY 见 MODEL_REGISTRY：" + ", ".join(m["key"] for m in MODEL_REGISTRY),
    )
    parser.add_argument("--all", action="store_true", help="下载全部 7 个模型集合")
    parser.add_argument(
        "--source",
        choices=["auto", "hf", "modelscope"],
        default="auto",
        help="下载源策略：auto=HF 镜像优先、失败回退魔搭；hf=只用镜像；modelscope=只用魔搭",
    )
    parser.add_argument("--no-verify", action="store_true", help="跳过 SHA256 / 体积校验")
    args = parser.parse_args()

    if not args.all and not args.model:
        parser.error("请指定 --all 或至少一个 --model KEY")

    selected = []
    if args.all:
        selected = list(MODEL_REGISTRY)
    else:
        by_key = {m["key"]: m for m in MODEL_REGISTRY}
        for k in args.model:
            if k not in by_key:
                parser.error(f"未知模型 KEY: {k}（可选：{', '.join(by_key)}）")
            selected.append(by_key[k])

    logger.info("下载源策略: %s", args.source)
    logger.info("待下载模型数: %d", len(selected))

    results = {m["key"]: download_one(m, args.source, not args.no_verify) for m in selected}

    print()
    print("=" * 60)
    print("下载结果汇总：")
    ok = all(results.values())
    for k, v in results.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("=" * 60)
    if not ok:
        failed = [k for k, v in results.items() if not v]
        logger.error("以下模型下载失败: %s", failed)
        return 1
    logger.info("全部模型下载完成 🎉")
    return 0


if __name__ == "__main__":
    sys.exit(main())
