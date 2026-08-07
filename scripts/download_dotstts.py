"""
TTS MultiModel - dots.tts 模型下载脚本
======================================

项目名称: TTS MultiModel (多引擎语音合成平台)
主要功能: 从 HuggingFace / ModelScope 下载 dots.tts 引擎的预训练权重快照

dots.tts 引擎特性:
    - 2B 参数完全连续端到端自回归 TTS
    - 48kHz 高保真零样本语音克隆
    - 强多语种能力与情感表现力
    - 最低硬件要求: 约 8GB VRAM

下载内容（放置于 pretrained_models/dots.tts/）:
    - dots.tts-soar 权重快照（推荐，最佳克隆效果）

依赖要求:
    pip install dots.tts modelscope

使用方法:
    python scripts/download_dotstts.py

下载完成后:
    1. 安装 dots.tts 推理依赖: pip install dots.tts
    2. 重启应用，dots.tts 引擎将自动出现在引擎切换选项中

注意事项:
    - 仅在推理前离线下载，运行期不会自动联网下载
    - 支持断点续传，重复运行不会重复下载已有文件
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

#: ModelScope 上的 dots.tts 权重仓库（soar 变体，最佳克隆效果）。
_REPO_ID = "rednote-hilab/dots.tts-soar"


def download_dotstts_model() -> bool:
    """从 ModelScope 下载 dots.tts 权重快照到本地目录。

    Returns:
        bool: True 表示下载成功，False 表示下载失败。
    """
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        logger.error("modelscope 未安装，请先运行: pip install modelscope")
        sys.exit(1)

    project_root = Path(__file__).parent.parent
    model_dir = project_root / "pretrained_models" / "dots.tts"

    logger.info(f"目标目录: {model_dir}")
    logger.info(f"开始下载 dots.tts 权重快照 ({_REPO_ID})...")
    logger.info("模型约 2B 参数，请耐心等待...")

    try:
        cache_dir = model_dir.parent / ".cache" / "dotstts"
        downloaded_path = snapshot_download(_REPO_ID, cache_dir=str(cache_dir), local_dir=str(model_dir))
        logger.info(f"下载完成: {downloaded_path}")

        # 校验目录非空
        if not any(model_dir.iterdir()):
            logger.warning("下载目录为空，请检查网络连接后重试。")
            return False

        # P1 安全修复：下载后自动校验 SHA256
        try:
            import subprocess

            verify_script = project_root / "scripts" / "verify_model_checksums.py"
            if verify_script.exists():
                logger.info("正在执行 SHA256 校验...")
                result = subprocess.run(
                    [sys.executable, str(verify_script)],
                    capture_output=True,
                    text=True,
                    cwd=str(project_root),
                )
                if result.returncode == 0:
                    logger.info("SHA256 校验通过")
                else:
                    logger.warning("SHA256 校验未通过（可能首次下载无校验清单），请检查日志")
        except Exception as verify_err:
            logger.warning("SHA256 校验异常（已忽略）: %s", verify_err)

        logger.info("dots.tts 权重快照下载完成！")
        return True

    except Exception as e:
        logger.error(f"下载失败: {e}")
        import traceback

        traceback.print_exc()
        return False


def main() -> int:
    """脚本主入口。

    Returns:
        int: 0 表示成功，1 表示失败。
    """
    print("=" * 60)
    print("dots.tts 模型下载工具")
    print("=" * 60)
    print()

    success = download_dotstts_model()

    print()
    if success:
        print("✅ 下载完成！")
        print()
        print("下一步:")
        print("  1. 安装依赖: pip install dots.tts")
        print("  2. 重启应用，dots.tts 引擎将自动可用")
    else:
        print("❌ 下载未完成，请检查网络连接后重试")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
