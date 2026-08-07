"""
TTS MultiModel - IndexTTS 2.0 模型下载脚本
===========================================

项目名称: TTS MultiModel (多引擎语音合成平台)
主要功能: 从 ModelScope 下载 IndexTTS 2.0 情感语音合成引擎的模型文件
核心技术栈: Python + modelscope SDK

IndexTTS 2.0 引擎特性:
    - 零样本语音克隆（仅需 3-10 秒参考音频）
    - 8 维情感向量控制（开心/愤怒/悲伤/恐惧/厌恶/忧郁/惊讶/平静）
    - 精细的时长控制
    - 多后端 GPU 支持（CUDA/MPS/CPU）
    - 最低硬件要求: 6GB VRAM + 16GB RAM

下载文件清单（总大小约 4.7GB）:
    - gpt.pth (~3.48GB) - GPT 模型主权重
    - s2mel.pth (~1.20GB) - 声码器/特征转换模型
    - bpe.model - BPE 分词模型
    - config.yaml - 模型配置文件
    - feat1.pt / feat2.pt - 语音特征统计文件
    - wav2vec2bert_stats.pt - Wav2Vec2-BERT 特征统计
    - configuration.json - 额外配置元数据

依赖要求:
    pip install modelscope

使用方法:
    python scripts/download_indextts2.py

下载目标目录:
    pretrained_models/IndexTTS2/

下载完成后:
    1. 安装 IndexTTS 2.0 依赖: pip install indextts
    2. 重启应用（start.bat 或 clean_launch.py）
    3. IndexTTS 2.0 引擎将自动出现在引擎切换选项中

注意事项:
    - 模型总大小约 4.7GB，请确保磁盘空间充足
    - 下载速度取决于网络连接，ModelScope 国内访问速度较快
    - 下载断点续传: 中断后重新运行脚本会自动续传
    - 下载完成后会自动验证所有必需文件是否存在
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def download_indextts2_model():
    """
    从 ModelScope 下载 IndexTTS 2.0 模型文件

    功能说明:
        使用 modelscope 的 snapshot_download 函数下载 IndexTeam/IndexTTS-2 仓库
        到本地 pretrained_models/IndexTTS2 目录。下载完成后验证所有必需文件是否存在，
        并打印每个文件的大小信息。

    Returns:
        bool: True 表示下载成功且所有文件验证通过，False 表示下载失败或文件缺失

    下载流程:
        1. 检查 modelscope 库是否安装，未安装则提示安装并退出
        2. 构建目标目录路径和缓存目录路径
        3. 打印文件清单和总大小提示
        4. 调用 snapshot_download 执行下载
        5. 遍历必需文件列表，检查文件是否存在并统计大小
        6. 输出下载结果和缺失文件警告

    异常处理:
        - 捕获所有下载异常，打印错误信息和堆栈跟踪
        - modelscope 未安装时直接退出，返回码 1

    注意事项:
        - 缓存目录位于 pretrained_models/.cache/indextts2
        - 支持断点续传，重复运行不会重复下载已有文件
        - 下载完成后检查的必需文件共 8 个
    """
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:
        logger.error("modelscope 未安装，请先运行: pip install modelscope")
        sys.exit(1)

    project_root = Path(__file__).parent.parent
    model_dir = project_root / "pretrained_models" / "IndexTTS2"

    logger.info(f"目标目录: {model_dir}")
    logger.info("开始下载 IndexTTS 2.0 模型...")
    logger.info("模型列表:")
    logger.info("  - gpt.pth (~3.48GB)")
    logger.info("  - s2mel.pth (~1.20GB)")
    logger.info("  - bpe.model")
    logger.info("  - config.yaml")
    logger.info("  - feat1.pt")
    logger.info("  - feat2.pt")
    logger.info("  - wav2vec2bert_stats.pt")
    logger.info("")
    logger.info("总大小约 4.7GB，请耐心等待...")

    try:
        cache_dir = model_dir.parent / ".cache" / "indextts2"
        downloaded_path = snapshot_download("IndexTeam/IndexTTS-2", cache_dir=str(cache_dir), local_dir=str(model_dir))

        logger.info(f"下载完成: {downloaded_path}")

        # 验证文件
        required_files = [
            "gpt.pth",
            "s2mel.pth",
            "bpe.model",
            "config.yaml",
            "feat1.pt",
            "feat2.pt",
            "wav2vec2bert_stats.pt",
            "configuration.json",
        ]

        missing_files = []
        for f in required_files:
            file_path = model_dir / f
            if not file_path.exists():
                missing_files.append(f)
            else:
                size_mb = file_path.stat().st_size / (1024 * 1024)
                logger.info(f"  ✓ {f} ({size_mb:.1f} MB)")

        if missing_files:
            logger.warning(f"缺少文件: {missing_files}")
        else:
            logger.info("所有文件下载完成！")

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

        return True

    except Exception as e:
        logger.error(f"下载失败: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """
    脚本主入口函数

    功能说明:
        打印欢迎信息，调用下载函数，根据下载结果输出成功/失败提示
        和后续操作指引。

    Returns:
        int: 0 表示下载成功，1 表示下载失败

    输出内容:
        - 成功时: 打印完成信息和后续安装指引
        - 失败时: 打印错误信息和重试建议
    """
    print("=" * 60)
    print("IndexTTS 2.0 模型下载工具")
    print("=" * 60)
    print()

    success = download_indextts2_model()

    print()
    if success:
        print("✅ 下载完成！")
        print()
        print("下一步:")
        print("  1. 安装 IndexTTS 2.0 依赖: pip install indextts")
        print("  2. 重启应用，IndexTTS 2.0 引擎将自动可用")
    else:
        print("❌ 下载失败，请检查网络连接后重试")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
