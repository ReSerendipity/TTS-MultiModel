"""跨平台兼容性模块。

提供 Windows / macOS / Linux 三平台统一的工具函数，封装平台差异：
- 路径处理（长路径支持、大小写敏感性）
- 进程管理（进程创建、优先级设置）
- 系统信息获取（GPU 检测、内存信息）
- 文件系统操作（原子重命名、文件锁）
- 音频设备相关
- 终端/控制台特性

设计要点：
- 所有公共函数在三平台均可调用，平台不支持的功能优雅降级
- 使用 sys.platform 检测平台，不依赖第三方库（除可选依赖）
- 函数返回值类型统一，不抛出平台相关异常
- 提供 is_windows / is_macos / is_linux 便捷判断
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# 平台检测常量
# ---------------------------------------------------------------------------

IS_WINDOWS: bool = sys.platform.startswith("win")
IS_MACOS: bool = sys.platform == "darwin"
IS_LINUX: bool = sys.platform.startswith("linux")
IS_WSL: bool = "microsoft" in platform.release().lower() if IS_LINUX else False


# ---------------------------------------------------------------------------
# 平台信息函数
# ---------------------------------------------------------------------------


def get_platform_name() -> str:
    """获取当前平台名称（人类可读）。

    Returns:
        "Windows", "macOS", "Linux", 或 "WSL"。
    """
    if IS_WSL:
        return "WSL"
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    if IS_LINUX:
        return "Linux"
    return sys.platform


def get_system_info() -> dict[str, Any]:
    """获取系统信息摘要。

    Returns:
        包含 platform, python_version, architecture, processor 等字段的字典。
    """
    return {
        "platform": get_platform_name(),
        "platform_version": platform.version(),
        "python_version": platform.python_version(),
        "architecture": platform.architecture()[0],
        "processor": platform.processor() or "unknown",
        "hostname": platform.node(),
    }


# ---------------------------------------------------------------------------
# 路径处理
# ---------------------------------------------------------------------------


def normalize_path(path: str | Path) -> str:
    """规范化路径，处理跨平台差异。

    Windows 下：
    - 自动添加长路径前缀（\\\\?\\）以支持超过 260 字符的路径
    - 正斜杠转为反斜杠

    其他平台：
    - 展开 ~ 用户目录
    - 解析符号链接

    Args:
        path: 输入路径。

    Returns:
        规范化后的绝对路径字符串。
    """
    p = Path(path).expanduser().resolve()
    path_str = str(p)

    if IS_WINDOWS:
        if not path_str.startswith("\\\\?\\") and len(path_str) > 200:
            path_str = "\\\\?\\" + path_str
        path_str = path_str.replace("/", "\\")

    return path_str


def get_app_data_dir(app_name: str = "TTS_MultiModel") -> str:
    """获取应用数据目录（跨平台）。

    Windows: %APPDATA%\\TTS_MultiModel
    macOS:   ~/Library/Application Support/TTS_MultiModel
    Linux:   ~/.local/share/TTS_MultiModel

    Args:
        app_name: 应用名称。

    Returns:
        应用数据目录的绝对路径（目录会被自动创建）。
    """
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif IS_MACOS:
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")

    app_dir = os.path.join(base, app_name)
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


def get_temp_dir() -> str:
    """获取系统临时目录。

    Returns:
        临时目录路径。
    """
    import tempfile

    return tempfile.gettempdir()


# ---------------------------------------------------------------------------
# 文件系统原子操作
# ---------------------------------------------------------------------------


def atomic_write(file_path: str | Path, data: bytes | str) -> None:
    """原子写入文件（先写临时文件，再重命名）。

    避免写入过程中程序崩溃导致文件损坏。

    Args:
        file_path: 目标文件路径。
        data: 要写入的数据（bytes 或 str）。
    """
    import tempfile

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    mode = "wb" if isinstance(data, bytes) else "w"
    encoding = None if isinstance(data, bytes) else "utf-8"

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, mode, encoding=encoding) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def file_lock(file_path: str | Path):
    """获取文件锁（跨平台）。

    Windows 使用 msvcrt.locking，Unix 使用 fcntl.flock。

    Usage::

        with file_lock("myfile.lock"):
            # 临界区代码
            pass

    Args:
        file_path: 锁文件路径。

    Returns:
        上下文管理器。
    """

    class _FileLock:
        """跨平台文件锁上下文管理器内部实现。"""

        def __init__(self, path: str | Path) -> None:
            """初始化文件锁。

            Args:
                path: 锁文件路径。
            """
            self.path = Path(path)
            self.fd: int | None = None

        def __enter__(self) -> _FileLock:
            """进入上下文，获取文件锁。

            在 Windows 上使用 msvcrt.locking，Unix 上使用 fcntl.flock。
            非阻塞模式，获取失败立即抛出异常。

            Returns:
                self

            Raises:
                RuntimeError: 无法获取文件锁时抛出。
            """
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR)
            try:
                if IS_WINDOWS:
                    import msvcrt

                    msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(self.fd)
                self.fd = None
                raise RuntimeError(f"无法获取文件锁: {self.path}") from None
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
            """退出上下文，释放文件锁。

            Args:
                exc_type: 异常类型（如果有）。
                exc_val: 异常值（如果有）。
                exc_tb: 异常回溯（如果有）。

            Returns:
                False 表示不抑制异常。
            """
            if self.fd is not None:
                try:
                    if IS_WINDOWS:
                        import msvcrt

                        msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(self.fd, fcntl.LOCK_UN)
                finally:
                    os.close(self.fd)
                    self.fd = None
            return False

    return _FileLock(file_path)


# ---------------------------------------------------------------------------
# 进程管理
# ---------------------------------------------------------------------------


def set_process_high_priority() -> bool:
    """尝试将当前进程设置为高优先级。

    Windows: HIGH_PRIORITY_CLASS
    macOS/Linux: nice -10

    Returns:
        True 表示设置成功。
    """
    try:
        if IS_WINDOWS:
            import psutil

            p = psutil.Process()
            p.nice(psutil.HIGH_PRIORITY_CLASS)
        else:
            os.nice(-10)
        logger.debug("[cross_platform] 已设置进程高优先级")
        return True
    except Exception as e:
        logger.debug(f"[cross_platform] 设置高优先级失败（忽略）: {e}")
        return False


def open_file_explorer(path: str | Path) -> bool:
    """用系统文件管理器打开指定路径。

    Args:
        path: 要打开的文件或目录路径。

    Returns:
        True 表示成功启动文件管理器。
    """
    path_str = str(path)
    try:
        if IS_WINDOWS:
            os.startfile(path_str)
        elif IS_MACOS:
            subprocess.Popen(["open", path_str])
        else:
            subprocess.Popen(["xdg-open", path_str])
        return True
    except Exception as e:
        logger.warning(f"[cross_platform] 打开文件管理器失败: {e}")
        return False


# ---------------------------------------------------------------------------
# GPU/硬件信息
# ---------------------------------------------------------------------------


def get_cuda_visible_devices() -> list[int]:
    """获取 CUDA_VISIBLE_DEVICES 环境变量指定的 GPU 列表。

    Returns:
        GPU 索引列表，未设置时返回 [0]。
    """
    cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    if not cuda_visible:
        return [0]
    try:
        return [int(x.strip()) for x in cuda_visible.split(",") if x.strip()]
    except ValueError:
        return [0]


def is_admin() -> bool:
    """检查当前进程是否有管理员/root 权限。

    Returns:
        True 表示是管理员。
    """
    try:
        if IS_WINDOWS:
            import ctypes

            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 控制台/终端
# ---------------------------------------------------------------------------


def supports_color() -> bool:
    """检测终端是否支持彩色输出。

    Returns:
        True 表示支持。
    """
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if IS_WINDOWS:
        if "ANSICON" in os.environ or "WT_SESSION" in os.environ:
            return True
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            mode = ctypes.c_ulong()
            h = kernel32.GetStdHandle(-11)
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                mode.value |= 0x0004
                kernel32.SetConsoleMode(h, mode)
                return True
        except Exception:
            pass
        return False
    return True


def get_terminal_size() -> tuple[int, int]:
    """获取终端尺寸（列数，行数）。

    Returns:
        (columns, lines) 元组，默认 (80, 24)。
    """
    try:
        os_size = os.get_terminal_size()
        return os_size.columns, os_size.lines
    except (OSError, ValueError):
        return 80, 24


# ---------------------------------------------------------------------------
# 音频设备
# ---------------------------------------------------------------------------


def get_default_audio_output_device() -> str | None:
    """获取系统默认音频输出设备名称。

    Returns:
        设备名称字符串，无法获取时返回 None。
    """
    try:
        if IS_WINDOWS:
            return "Default Windows Audio Device"
        elif IS_MACOS:
            result = subprocess.run(
                ["system_profiler", "SPAudioDataType"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if "Default Output Device" in line:
                    return line.split(":")[-1].strip()
        else:
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
    except Exception as e:
        logger.debug(f"[cross_platform] 获取默认音频设备失败: {e}")
    return None


# ---------------------------------------------------------------------------
# 环境变量便捷访问
# ---------------------------------------------------------------------------


def get_env_bool(name: str, default: bool = False) -> bool:
    """获取布尔型环境变量。

    接受值: "1", "true", "yes", "on" (不区分大小写) 为 True。

    Args:
        name: 环境变量名。
        default: 默认值。

    Returns:
        布尔值。
    """
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def get_env_int(name: str, default: int) -> int:
    """获取整数型环境变量。

    Args:
        name: 环境变量名。
        default: 默认值。

    Returns:
        整数值。
    """
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def get_env_float(name: str, default: float) -> float:
    """获取浮点型环境变量。

    Args:
        name: 环境变量名。
        default: 默认值。

    Returns:
        浮点值。
    """
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default
