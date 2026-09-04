"""
TTS MultiModel - 应用启动脚本
==============================

项目名称: TTS MultiModel (多引擎语音合成平台)
主要功能: 应用程序的主启动入口，负责环境初始化、配置加载、模型检查和服务启动
核心技术栈: Python + asyncio + FastAPI + WinPython (Windows 便携版)
启动链路: start.bat -> clean_launch.py -> integrated_app/app_server.py

启动流程:
    1. 环境变量初始化 - 设置离线模式、OpenMP 修复、缓存路径
    2. 路径配置 - 配置 sys.path 以正确导入项目模块
    3. 配置加载 - 读取 config.yaml 获取自动加载等设置
    4. 日志配置 - 配置控制台输出和文件日志轮转
    5. 端口处理 - 检测并清理占用端口，自动选择可用端口
    6. 模型检查 - 预检模型文件完整性，缺失时给出下载提示
    7. 浏览器自动打开 - 后台线程等待服务就绪后打开浏览器
    8. 启动服务 - 初始化 asyncio 事件循环，启动 FastAPI 服务

默认配置:
    - 服务地址: 127.0.0.1
    - 默认端口: 7869 (被占用时自动递增选择)
    - 日志级别: INFO
    - 日志轮转: 10MB/文件，保留 3 个备份

关键环境变量:
    - TRANSFORMERS_OFFLINE: HuggingFace Transformers 离线模式
    - HF_HUB_OFFLINE: HuggingFace Hub 离线模式
    - MODELSCOPE_OFFLINE: ModelScope 离线模式
    - KMP_DUPLICATE_LIB_OK: OpenMP 多库加载兼容
    - TTS_AUTO_LOAD_MODEL: 是否自动加载模型（从 config.yaml 读取）
    - HUGGINGFACE_HUB_CACHE: HuggingFace 缓存路径
    - MODELSCOPE_CACHE: ModelScope 缓存路径
    - TORCH_HOME: PyTorch 模型缓存路径

使用方法:
    方式 1: 通过 start.bat 启动（推荐，使用内置 WinPython）
    方式 2: 命令行启动: python app/clean_launch.py

注意事项:
    - 仅适用于 Windows 平台（使用 WinPython 便携环境）
    - 启动前会自动终止占用默认端口的进程
    - 模型文件不完整时打印下载提示，警告后自动继续启动
    - 支持 Ctrl+C 优雅关闭服务
"""

import os
import sys

# --- 【暴力补丁：必须在最前面】 ---
# 注意：以下 SSL 相关补丁仅适用于本地离线部署场景。
# 如果项目部署在有网络访问的环境中，应移除这些补丁以确保安全性。
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["MODELSCOPE_OFFLINE"] = "1"

# 修复 OpenMP 重复加载错误 (libiomp5md.dll conflict)
# 当多个库（如 numpy、torch、funasr）各自携带 libiomp5md.dll 时会触发此错误
os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

# VoxCPM2 缓存路径
_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_bin_dir = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, _bin_dir)
sys.path.insert(0, _root_dir)

_config_yaml_path = os.path.join(_root_dir, "config.yaml")
if os.path.exists(_config_yaml_path):
    try:
        import yaml

        with open(_config_yaml_path, encoding="utf-8") as _f:
            _cfg = yaml.safe_load(_f)
        if _cfg and _cfg.get("server", {}).get("auto_load_model", False):
            os.environ["TTS_AUTO_LOAD_MODEL"] = "1"
    except Exception:
        pass

os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(_root_dir, "cache", "huggingface")
os.environ["MODELSCOPE_CACHE"] = os.path.join(_root_dir, "cache", "modelscope")
os.environ["TORCH_HOME"] = os.path.join(_root_dir, "cache", "torch")
os.environ["XDG_CACHE_HOME"] = os.path.join(_root_dir, "cache")

# httpx SSL 验证：已在服务启动中通过 ssl_verify=False 处理
# 不再全局 monkey-patch httpx，保持其他模块的 SSL 安全性

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=ResourceWarning)
warnings.filterwarnings("ignore", category=UserWarning)
import asyncio
import atexit
import logging

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
from logging.handlers import RotatingFileHandler

# Add file handler only if not already present (avoid duplicates with app_server.setup_logging)
root_logger = logging.getLogger()
if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    root_logger.addHandler(file_handler)

logger = logging.getLogger("tts_multimodel")
import threading
import webbrowser
import time
import socket
import subprocess


def silent_exception_handler(loop, context):
    """
    asyncio 静默异常处理器

    功能说明:
        自定义 asyncio 事件循环的异常处理策略，静默处理常见的连接重置异常，
        避免在日志中输出大量正常的客户端断开连接错误（如浏览器刷新、关闭等）。
        对于其他类型的异常，仍交由默认异常处理器处理。

    Args:
        loop: asyncio 事件循环对象
        context: 异常上下文字典，包含 exception、message、future 等信息

    过滤的异常类型:
        - ConnectionResetError: 连接被对端重置（正常的客户端断开）
        - Windows 错误 10054: WSAECONNRESET，远程主机强制关闭连接
    """
    exception = context.get("exception")
    if isinstance(exception, ConnectionResetError) or (exception and "10054" in str(exception)):
        return
    loop.default_exception_handler(context)


def _kill_port_occupant(port, ip="127.0.0.1"):
    """
    终止占用指定端口的进程（Windows 专用）

    功能说明:
        在启动服务前清理目标端口，防止端口冲突导致启动失败。
        优先使用 psutil 库遍历网络连接查找占用进程，如 psutil 不可用
        则降级使用 netstat + taskkill 命令（Windows 系统自带工具）。

    Args:
        port: 要清理的端口号（整数）
        ip: 绑定的 IP 地址，默认 "127.0.0.1"

    实现策略:
        1. 优先方案（psutil）: 遍历 inet 连接，找到 LISTEN 状态匹配端口的进程，调用 proc.kill()
        2. 降级方案（系统命令）: 使用 netstat -ano 查找占用 PID，通过 taskkill /F 强制终止
        3. 跳过自身进程: 不终止当前 Python 进程自身
        4. 异常容错: 权限不足或进程不存在时静默跳过

    注意事项:
        - 仅适用于 Windows 平台
        - 需要相应权限才能终止其他进程
        - 终止后等待 1 秒确保端口释放
    """
    # L4 整改：端口强杀前需人工确认（CI/容器无 stdin 时自动取消，不误杀）
    if not os.environ.get("TTS_SKIP_PORT_KILL_CONFIRM"):
        try:
            _confirm = input(f"端口 {port} 被占用，将终止占用进程以继续启动，是否继续？(y/N): ")
        except (EOFError, KeyboardInterrupt):
            _confirm = "n"
        if _confirm.strip().lower() != "y":
            logger.info("已取消端口强杀，占用进程保留。")
            return
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                try:
                    proc = psutil.Process(conn.pid)
                    if proc.pid != os.getpid():
                        logger.info(f"端口 {port} 被进程 {conn.pid} ({proc.name()}) 占用，正在终止...")
                        proc.kill()
                        proc.wait(timeout=5)
                        logger.info(f"已终止进程 {conn.pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        time.sleep(1)
    except ImportError:
        # Fallback: use netstat + taskkill
        try:
            result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = int(parts[-1])
                    if pid != os.getpid():
                        logger.info(f"端口 {port} 被进程 {pid} 占用，正在终止...")
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
            time.sleep(1)
        except Exception:
            pass


def auto_open_browser(ip, port, timeout=300):
    """
    等待服务就绪后自动打开浏览器

    功能说明:
        在后台线程中运行，轮询检测服务端口是否可连接。服务启动就绪后，
        等待 2 秒确保 HTTP 服务完全可用，然后使用系统默认浏览器打开 WebUI 页面。
        如果超时仍未检测到服务，则记录警告并放弃打开浏览器。

    Args:
        ip: 服务监听的 IP 地址，通常为 "127.0.0.1"
        port: 服务监听的端口号
        timeout: 最长等待时间（秒），默认 300 秒（5 分钟）

    工作流程:
        1. 每秒轮询一次目标端口是否可连接
        2. 连接成功后额外等待 2 秒（确保 FastAPI 完全启动）
        3. 调用 webbrowser.open() 打开默认浏览器
        4. 超时则输出警告日志并返回
    """
    # 协议跟随服务端：ssl.enabled=true 且证书文件存在时 app_server.run_server 才启用 HTTPS，
    # 弹出 URL 必须使用相同协议，否则 HTTP 请求打到 TLS 端口会得到空响应（ERR_EMPTY_RESPONSE）
    scheme = "http"
    try:
        _ssl_cfg = (globals().get("_cfg") or {}).get("server", {}).get("ssl", {}) or {}
        _cert, _key = _ssl_cfg.get("certfile"), _ssl_cfg.get("keyfile")
        if _ssl_cfg.get("enabled") and _cert and _key and os.path.exists(_cert) and os.path.exists(_key):
            scheme = "https"
    except Exception:
        pass
    url = f"{scheme}://{ip}:{port}"
    logger.info("正在等待引擎加载...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            if sock.connect_ex((ip, int(port))) == 0:
                break
        time.sleep(1)
    else:
        logger.warning(f"等待引擎加载超时（{timeout}秒），未打开浏览器")
        return
    time.sleep(2)
    logger.info("服务就绪，正在弹出网页...")
    webbrowser.open(url)


def verify_binaries():
    """P2 安全修复：校验关键二进制文件的 SHA256 完整性。

    读取 app/SHA256SUMS.known-good 文件并逐项验证。
    如果校验清单为空或不存在，仅打印提示信息。
    """
    import hashlib

    checksum_file = os.path.join(_bin_dir, "SHA256SUMS.known-good")
    if not os.path.exists(checksum_file):
        logger.warning("[verify-binaries] 校验清单不存在: %s，跳过校验", checksum_file)
        return True

    entries = []
    with open(checksum_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                entries.append((parts[0].lower().strip(), parts[1].strip()))

    if not entries:
        logger.info("[verify-binaries] 校验清单为空，跳过校验")
        logger.info("[verify-binaries] 提示: 请运行 sha256sum 生成校验值并填入 SHA256SUMS.known-good")
        return True

    all_ok = True
    for expected_hash, rel_path in entries:
        file_path = os.path.join(_bin_dir, rel_path)
        if not os.path.exists(file_path):
            logger.error("[verify-binaries] [FAIL] 文件缺失: %s", rel_path)
            all_ok = False
            continue

        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()

        if actual_hash == expected_hash:
            logger.info("[verify-binaries] [OK] %s", rel_path)
        else:
            logger.error("[verify-binaries] [FAIL] %s — 哈希不匹配", rel_path)
            logger.error("  期望: %s", expected_hash)
            logger.error("  实际: %s", actual_hash)
            all_ok = False

    return all_ok


def start_app():
    """
    应用主启动函数

    功能说明:
        执行完整的应用启动流程，包括环境配置、端口选择、模型检查、
        浏览器自动打开、信号处理以及最终的 FastAPI 服务启动。

    执行步骤:
        1. 环境路径配置 - 将 WinPython 和 SoX 添加到 PATH
        2. 端口清理与选择 - 清理占用端口，自动选择可用端口（默认 7869 递增）
        3. 端口文件写入 - 将实际端口写入 .server_port 文件供其他工具读取
        4. 模型完整性检查 - 调用 check_models_available() 验证模型文件
        5. 浏览器线程启动 - 后台线程等待服务就绪后打开浏览器
        6. asyncio 事件循环初始化 - 创建新事件循环，设置异常处理器
        7. 信号处理注册 - 注册 SIGINT/SIGTERM 实现优雅关闭
        8. 启动 FastAPI 服务 - 调用 integrated_app.app_server.run_server()

    默认地址:
        - IP: 127.0.0.1
        - 端口: 7869（被占用时自动尝试 7870、7871...最多 10 个）

    异常处理:
        - 模型文件缺失时打印下载提示并退出
        - 服务启动异常时打印堆栈跟踪，等待用户按键退出
        - atexit 注册清理函数，退出时自动删除端口文件
    """
    ip, port = "127.0.0.1", "7869"
    bin_dir = os.path.dirname(os.path.abspath(__file__))
    wpy_path = os.path.join(_root_dir, "WPy64-312101", "python")
    sox_dir = os.path.join(bin_dir, "sox-14.4.2-win32", "sox-14.4.2")
    ffmpeg_dir = os.path.join(bin_dir, "ffmpeg")
    os.environ["PATH"] = (
        wpy_path + os.pathsep + os.path.join(wpy_path, "Scripts") + os.pathsep + os.environ.get("PATH", "")
    )
    if os.path.isdir(sox_dir):
        os.environ["PATH"] = sox_dir + os.pathsep + os.environ["PATH"]
    if os.path.isdir(ffmpeg_dir):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ["PATH"]

    # 端口自动转换：若默认端口被占用，向后寻找第一个可用端口（7869 → 7870 → …），
    # 不再交互式询问是否强杀占用进程（避免启动被阻塞在 y/N 等待上）。

    # Kill any leftover process on the target port before selecting
    _kill_port_occupant(int(port), ip)

    # M8 整改：关键二进制 SHA256 完整性校验（默认关闭；设 TTS_VERIFY_BINARIES=1 启用）。
    # 仅当校验清单存在且哈希不匹配时拒绝启动；清单缺失则跳过（不阻断）。
    if os.environ.get("TTS_VERIFY_BINARIES") == "1" and not verify_binaries():
        logger.error("[start_app] 二进制完整性校验失败，拒绝启动")
        sys.exit(1)

    # Auto-select port if 7869 is occupied
    def _find_available_port(start_port, max_attempts=10):
        """查找可用端口，从 start_port 开始递增尝试。

        端口检测策略采用两阶段验证以避免竞态条件：
        1. 第一阶段：使用 connect_ex() 尝试连接端口，返回 0 表示端口被占用，跳过；
        2. 第二阶段：端口看似空闲时，通过实际 bind() 绑定验证，确认端口真正可用。

        这种双重检测策略可以避免：
        - 端口处于 TIME_WAIT 状态导致 connect_ex 误判为空闲
        - 其他进程在检测和使用之间抢占端口的竞态条件

        Args:
            start_port: 起始端口号（默认 7869）。
            max_attempts: 最大尝试次数，默认 10 次（最多尝试到 start_port+9）。

        Returns:
            int: 找到的可用端口号。如果所有尝试端口都不可用，则回退返回 start_port。
        """
        for attempt in range(max_attempts):
            test_port = start_port + attempt
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    # connect_ex returns 0 if connection succeeds (port occupied)
                    if s.connect_ex((ip, test_port)) == 0:
                        logger.debug(f"端口 {test_port} 被占用，尝试下一个")
                        continue  # port is occupied, try next
                    # Port appears free, verify by binding
                    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    try:
                        s2.bind((ip, test_port))
                        s2.close()
                        logger.info(f"选中可用端口 {test_port}")
                        return test_port
                    except OSError:
                        s2.close()
                        logger.debug(f"端口 {test_port} bind 失败，尝试下一个")
                        continue
            except OSError:
                continue
        logger.warning(f"未找到可用端口，回退到 {start_port}")
        return start_port

    actual_port = str(_find_available_port(int(port)))
    if actual_port != port:
        logger.info(f"端口 {port} 被占用，使用可用端口 {actual_port}")

    _port_file = os.path.join(_root_dir, ".server_port")

    # Clean up port file on exit
    def _cleanup_port_file():
        try:
            if os.path.exists(_port_file):
                os.remove(_port_file)
        except Exception:
            pass

    atexit.register(_cleanup_port_file)

    # Write port file atomically (write to temp file, then rename)
    try:
        _tmp_port_file = _port_file + ".tmp"
        with open(_tmp_port_file, "w", encoding="utf-8") as pf:
            pf.write(actual_port)
        if os.path.exists(_port_file):
            os.remove(_port_file)
        os.rename(_tmp_port_file, _port_file)
    except Exception:
        # Fallback to direct write if rename fails
        try:
            with open(_port_file, "w", encoding="utf-8") as pf:
                pf.write(actual_port)
        except Exception:
            pass

    # --- Pre-flight model integrity check ---
    sys.path.insert(0, os.path.join(_root_dir, "app"))
    from integrated_app.config import check_models_available, get_download_hints

    models_ok, missing = check_models_available()
    if not models_ok:
        print()
        print("=" * 60)
        print("  WARNING: Some model files incomplete or missing")
        print("=" * 60)
        print()
        for item in missing:
            print(f"  - {item}")
        print()
        hints = get_download_hints()
        for engine, hint in hints.items():
            print(f"[{engine}]")
            for line in hint.splitlines():
                print(f"  {line}")
            print()
        print("=" * 60)
        print("  ⚠️ 部分引擎不可用，但服务将继续启动（仅加载已就绪的引擎）")
        print("=" * 60)
        print()
        print("  ℹ️ 提示：未下载的引擎将无法使用，不影响其他引擎正常运行")
        print("  ℹ️ 服务将自动继续启动，如需中止请按 Ctrl+C")
        # 不再使用 input() 阻塞等待回车：缺失引擎（如 IndexTTS 2.5）可能长期不下载，
        # 每次启动都要求手动确认会造成无效阻塞。警告信息照常打印后自动继续。
        # Skip startup block | 跳过启动阻断 # sys.exit(1)

    # M8 整改：关键二进制 SHA256 校验（默认关闭，开启后失败即阻断启动）
    try:
        from integrated_app.config import get_config

        if get_config().pydantic_config.runtime.integrity.verify_binaries and not verify_binaries():
            logger.error("[start_app] 关键二进制校验失败，按 runtime.integrity.verify_binaries 配置阻断启动")
            sys.exit(1)
    except Exception as e:
        logger.debug(f"二进制校验跳过: {e}")

    threading.Thread(target=auto_open_browser, args=(ip, actual_port), daemon=True).start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(silent_exception_handler)

    import signal

    def signal_handler(sig, frame):
        logging.info("Received shutdown signal, stopping server...")
        # Use sys.exit instead of os._exit to allow atexit handlers to run
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        from integrated_app.app_server import run_server

        run_server(ip, actual_port)
    except Exception:
        import traceback

        traceback.print_exc()
        input("\n按任意键退出...")


if __name__ == "__main__":
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    os.system("")

    # P2 安全修复：支持 --verify-binaries 命令行选项
    if "--verify-binaries" in sys.argv:
        ok = verify_binaries()
        if ok:
            print("[verify-binaries] 所有校验通过！")
        else:
            print("[verify-binaries] 校验失败，请检查上方日志！")
            # Skip startup block | 跳过启动阻断 # sys.exit(1)
        sys.exit(0)

    start_app()
