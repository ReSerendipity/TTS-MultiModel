"""
TTS MultiModel - IndexTTS 2.0 模型下载脚本
===========================================

项目名称: TTS MultiModel (多引擎语音合成平台)
主要功能: 从 ModelScope 下载 IndexTTS 2.0 语音合成引擎的模型文件
核心技术栈: Python + modelscope SDK

说明:
    IndexTTS 2.0 与 2.5 是同一 index-tts 代码包的两个版本变体（依赖共用，
    安装一次即可）；但**权重不通用**，2.0 需单独下载到 model/IndexTTS-2.0/。
    2.0 仅支持中/英双语、无显式时长控制（语速走后处理），相较 2.5 语种更少、
    推理更慢，保留用于版本对比。

依赖要求:
    pip install modelscope

使用方法:
    python scripts/download_indextts20.py

下载目标目录:
    model/IndexTTS-2.0/

下载完成后:
    1. 确认已安装 index-tts 代码包（2.0/2.5 共用，见 README/INSTALL 的正确安装方式：
       git clone https://github.com/index-tts/index-tts && cd index-tts && pip install -e .
       —— 注意 PyPI 上没有 'indextts' 包，`pip install indextts` 会失败）
    2. 重启应用（start.bat 或 clean_launch.py）
    3. 顶部引擎切换栏选择 "IndexTTS 2.0"，引擎将加载 model/IndexTTS-2.0/ 权重

注意事项:
    - 模型总大小约 4~5GB，请确保磁盘空间充足
    - ModelScope 国内访问速度较快，支持断点续传
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def download_indextts20_model() -> bool:
    """从 ModelScope 下载 IndexTTS 2.0 模型文件到 model/IndexTTS-2.0/。

    Returns:
        bool: True 表示下载成功且核心文件存在，False 表示失败或缺失。
    """
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        logger.error("modelscope 未安装，请先运行: pip install modelscope")
        return False

    project_root = Path(__file__).resolve().parent.parent
    model_dir = project_root / "model" / "IndexTTS-2.0"
    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"目标目录: {model_dir}")
    logger.info("开始下载 IndexTTS 2.0 模型（仓库 IndexTeam/IndexTTS-2）...")
    logger.info("总大小约 4~5GB，请耐心等待...")

    try:
        cache_dir = model_dir.parent / ".cache" / "indextts20"
        downloaded_path = snapshot_download(
            "IndexTeam/IndexTTS-2",
            cache_dir=str(cache_dir),
            local_dir=str(model_dir),
        )
        logger.info(f"下载完成: {downloaded_path}")

        # 2.0 权重文件命名以实际仓库为准，此处只硬校验必然存在的核心文件，
        # 其余作为信息列出（引擎 _validate_model_files 对 2.0 同样仅要求核心两项）。
        core_files = ["config.yaml", "gpt.pth"]
        optional_hint = [
            "s2mel.pth",
            "bpe.model",
            "feat1.data",
            "feat2.data",
            "wav2vec2bert_stats.pt",
            "configuration.json",
        ]

        missing = []
        for f in core_files:
            fp = model_dir / f
            if not fp.exists():
                missing.append(f)
            else:
                size_mb = fp.stat().st_size / (1024 * 1024)
                logger.info(f"  ✓ {f} ({size_mb:.1f} MB)")
        for f in optional_hint:
            fp = model_dir / f
            mark = "✓" if fp.exists() else "·(未见，视仓库实际命名而定)"
            logger.info(f"  {mark} {f}")

        if missing:
            logger.warning(f"缺少核心文件: {missing} —— 请核对仓库实际文件布局")
            return False

        logger.info("IndexTTS 2.0 核心权重下载完成！")
        return True
    except Exception as e:
        logger.error(f"下载失败: {e}")
        import traceback

        traceback.print_exc()
        return False


def main() -> int:
    """脚本主入口：打印信息、执行下载、给出后续指引。

    Returns:
        int: 0 表示成功，1 表示失败。
    """
    print("=" * 60)
    print("IndexTTS 2.0 模型下载工具")
    print("=" * 60)
    print()

    success = download_indextts20_model()

    print()
    if success:
        print("✅ 下载完成！")
        print()
        print("下一步:")
        print("  1. 安装 index-tts 代码包（2.0/2.5 共用，装一次即可）：")
        print("     git clone https://github.com/index-tts/index-tts.git")
        print("     cd index-tts && pip install -e .")
        print("  2. 重启应用，在顶部引擎切换栏选择 'IndexTTS 2.0'")
    else:
        print("❌ 下载失败，请检查网络连接后重试")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
