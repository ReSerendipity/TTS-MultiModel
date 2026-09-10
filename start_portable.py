"""
TTS MultiModel - 桌面壳便携版启动入口（Desktop Shell）
======================================================

项目名称: TTS MultiModel (多引擎语音合成平台)
作用: 由 Tauri 桌面壳（desktop/src-tauri）启动的 Python 运行时入口，
      与 start.bat -> clean_launch.py 的唯一差异：**不自动打开浏览器**
      （WebView 即浏览器），其余环境初始化与 clean_launch.py 对齐。

启动链路: desktop shell -> start_portable.py --port N -> integrated_app.app_server.run_server()

关键约定（与壳的契约，勿破坏）:
    1. 命令行参数 --host / --port 必填（壳经 find_free_port 选定端口后传入）。
    2. 不调用 webbrowser.open（壳的 WebView 负责展示）。
    3. 日志写往 stdout（壳将 stdout/stderr 重定向到 logs/python_*.log），
       端口冲突时 run_server 内部自动递增并打印「监听端口: N」。
    4. 禁止 reload 模式（uvicorn.run 不传 reload），避免壳与子进程双启动。

环境变量: 与 clean_launch.py 一致（离线模式、OpenMP 兼容、缓存路径、自动加载模型）。
"""

import os
import sys

# --- 环境初始化（与 clean_launch.py 对齐，仅本地离线部署） ---
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["MODELSCOPE_OFFLINE"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

_ROOT = os.path.dirname(os.path.abspath(__file__))
_BIN_DIR = os.path.join(_ROOT, "app")
sys.path.insert(0, _BIN_DIR)
sys.path.insert(0, _ROOT)

# 自动加载模型开关（与 config.yaml server.auto_load_model 对齐）
_config_yaml_path = os.path.join(_ROOT, "config.yaml")
if os.path.exists(_config_yaml_path):
    try:
        import yaml

        with open(_config_yaml_path, encoding="utf-8") as _f:
            _cfg = yaml.safe_load(_f)
        if _cfg and _cfg.get("server", {}).get("auto_load_model", False):
            os.environ["TTS_AUTO_LOAD_MODEL"] = "1"
    except Exception:
        pass

os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(_ROOT, "cache", "huggingface")
os.environ["MODELSCOPE_CACHE"] = os.path.join(_ROOT, "cache", "modelscope")
os.environ["TORCH_HOME"] = os.path.join(_ROOT, "cache", "torch")
os.environ["XDG_CACHE_HOME"] = os.path.join(_ROOT, "cache")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="TTS MultiModel 桌面壳启动入口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=7869, help="监听端口（默认 7869，占用时自动递增）")
    args = parser.parse_args()

    # 预检模型完整性（仅告警，不阻断，与 clean_launch 行为一致）
    try:
        from integrated_app.config import check_models_available

        models_ok, missing = check_models_available()
        if not models_ok:
            print("[start_portable] WARNING: 部分模型文件缺失，服务仍将启动（仅加载已就绪引擎）")
            for item in missing:
                print(f"  - {item}")
    except Exception:
        pass

    # 关键二进制校验（默认关闭；TTS_VERIFY_BINARIES=1 且清单存在时校验）
    if os.environ.get("TTS_VERIFY_BINARIES") == "1":
        try:
            from clean_launch import verify_binaries  # noqa: PLC0415

            if not verify_binaries():
                print("[start_portable] 关键二进制完整性校验失败，拒绝启动", file=sys.stderr)
                sys.exit(1)
        except Exception as exc:
            print(f"[start_portable] 二进制校验跳过: {exc}")

    from integrated_app.app_server import run_server  # noqa: PLC0415

    run_server(ip=args.host, port=args.port)


if __name__ == "__main__":
    main()
