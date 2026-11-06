#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""O3 多路径切换演练（进程内 · 离线 · 不触 GPU · F6 隔离）。

背景（2026-11-01 o3-failover-drill §2b 后的遗留缺口）：自动降级切换此前只有
``/api/generate/streaming_audio`` 单路径有实测覆盖（且真机演练中止损于降级目标
不可用），其余生成路径的「主引擎不可用时行为」无演练留痕。属主已裁决单点本体
「接受为既定形态」，本演练**不消除单点**，只把切换演练从单路径扩到多路径并留痕。

演练面（六条路径，见 PATH_MATRIX）：
    P1 streaming_audio  自动降级成功支路   → 200 JSON + degraded 标记
    P2 streaming_audio  自动降级失败支路   → 503 fallback_failed（与 engine_not_ready 可区分）
    P3 voxcpm_design    非流式同引擎路径   → 主引擎不可用时无自动降级接管（硬失败）
    P4 edge-tts/tts     降级目标直连路径   → 200 JSON + engine=edge-tts
    P5 /api/model/switch 人工切换路径      → 响应含 status 字段（实际值打印留痕）
    其余 13 条生成 POST 路径：以代码证据登记（全仓 `_maybe_edge_tts_fallback`
    调用点仅 streaming.py 一处），不做进程内演练（clone 类需授权门禁 + 参考音频
    才能到达引擎分支，属真实参考音频_fixture 范畴）。

硬边界（与 tests/conftest.py 同源口径）：
    - **F6 隔离**：导入 integrated_app **之前**强制 ``TTS_HISTORY_DB_PATH`` 指向
      本进程独占的系统临时目录库；生产 ``data/history.db`` 零接触。
    - **离线**：降级目标合成一律走 seam 桩（``_synthesize_edge_tts_wav``），
      不导入、不安装、不调用 edge_tts，不出网。
    - **不触 GPU**：不加载任何引擎权重（``auto_load_model=false`` + 守卫桩）。

用法：
    python scripts/run_python.py scripts/o3_multipath_drill.py
退出码：0 = 全路径行为与预期矩阵一致；1 = 存在路径行为漂移（演练失败）；
        2 = 环境前置不满足（无法起 TestClient 等）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "app"))  # 同 scripts/backup_history_db.py 口径

# ---- F6 隔离：必须先于一切 integrated_app 导入 ----
_ISOLATED_DIR = tempfile.mkdtemp(prefix="o3_drill_f6_")
os.environ["TTS_HISTORY_DB_PATH"] = str(Path(_ISOLATED_DIR) / "o3_drill_history.db")

from fastapi.testclient import TestClient  # noqa: E402

from integrated_app.app_server import create_app  # noqa: E402
from integrated_app.model_registry import registry  # noqa: E402
from integrated_app.routes.generate import utils as gen_utils  # noqa: E402

_FAKE_AUDIO = b"\xff\xf3" + b"\x00" * 60


class _Stubs:
    """成组打桩 + finally 还原（不依赖 pytest monkeypatch）。"""

    def __init__(self) -> None:
        self._saved: list[tuple[Any, str, Any]] = []

    def set(self, obj: Any, name: str, value: Any) -> None:
        self._saved.append((obj, name, getattr(obj, name, None)))
        setattr(obj, name, value)

    def restore(self) -> None:
        for obj, name, value in reversed(self._saved):
            setattr(obj, name, value)
        self._saved.clear()


def _csrf_post(client: TestClient, url: str, data: dict[str, Any]) -> Any:
    """带 CSRF 双提交的 POST（先 GET 取 cookie 再回传 header），同 tests 口径。"""
    client.get("/api/model/status")
    token = client.cookies.get("csrf_token")
    headers = {"X-CSRF-Token": token} if token else {}
    return client.post(url, data=data, headers=headers)


def _stub_main_unavailable(stubs: _Stubs) -> None:
    """主引擎不可用态（未加载 + 权重缺失），同 tests/test_edge_tts_fallback.py ②。"""
    stubs.set(registry, "voxcpm_model", None)
    stubs.set(gen_utils, "_engine_model_files_present", lambda _name: False)


def _stub_edge_ok(stubs: _Stubs, filename: str) -> None:
    async def _fake_edge(text: str, lang: str = "zh") -> tuple[str, int]:
        return (filename, len(_FAKE_AUDIO))

    stubs.set(gen_utils, "_synthesize_edge_tts_wav", _fake_edge)


def _stub_edge_broken(stubs: _Stubs) -> None:
    async def _broken_edge(text: str, lang: str = "zh") -> tuple[str, int]:
        raise gen_utils.EdgeTTSFallbackUnavailableError("演练注入：降级目标不可用")

    stubs.set(gen_utils, "_synthesize_edge_tts_wav", _broken_edge)


def _is_json(resp: Any) -> bool:
    return resp.headers.get("content-type", "").startswith("application/json")


def _check_p1(resp: Any) -> tuple[bool, str]:
    body = resp.json()
    ok = (
        resp.status_code == 200
        and _is_json(resp)
        and body.get("status") == "ok"
        and body.get("engine") == "edge-tts"
        and body.get("degraded", {}).get("from") == "voxcpm2"
        and body.get("degraded", {}).get("to") == "edge-tts"
        and body.get("size_bytes", 0) > 0
    )
    return ok, f"status={resp.status_code} engine={body.get('engine')} degraded={body.get('degraded')}"


def _check_p2(resp: Any) -> tuple[bool, str]:
    body = resp.json()
    ok = (
        resp.status_code == 503
        and body.get("error_type") == "fallback_failed"
        and body.get("error_type") != "engine_not_ready"
        and "filename" not in body
        and body.get("degraded", {}).get("attempted") is True
    )
    return ok, f"status={resp.status_code} error_type={body.get('error_type')}"


def _check_p3(resp: Any) -> tuple[bool, str]:
    # 非流式路径：不得伪成功（200 产物），且不得出现降级字段（无自动降级接管的覆盖缺口）。
    body = resp.text
    ok = resp.status_code >= 400 and "degraded" not in body
    return (
        ok,
        f"status={resp.status_code} content-type={resp.headers.get('content-type', '')[:24]} degraded_absent={'degraded' not in body}",
    )


def _check_p4(resp: Any) -> tuple[bool, str]:
    body = resp.json()
    ok = resp.status_code == 200 and body.get("engine") == "edge-tts" and body.get("size_bytes", 0) > 0
    return ok, f"status={resp.status_code} engine={body.get('engine')} size_bytes={body.get('size_bytes')}"


def _check_p5(resp: Any) -> tuple[bool, str]:
    # 人工切换路径，两种可接受形态（均良构、可区分、不伪成功）：
    #   a) 降级目标缺依赖（当前实态，edge-tts 未安装）→ TTSError(ENGINE_LOAD_ERROR) fail-fast，
    #      全局错误面 503 + status=error + 含 edge-tts 的补救信息；不产生半切换态。
    #   b) 依赖在场（属主将来安装 edge-tts）→ 200 + status=ok + engine=edge-tts。
    # 其余任何形态（如 200 伪成功带 error、503 无 status 字段）判 FAIL。
    body = resp.json()
    if resp.status_code == 503:
        ok = body.get("status") == "error" and "edge-tts" in str(body)
        return ok, f"status={resp.status_code} body.status={body.get('status')} (fail-fast 分支，依赖缺席实态)"
    if resp.status_code == 200:
        ok = body.get("status") == "ok" and body.get("engine") == "edge-tts"
        return ok, f"status={resp.status_code} body.status={body.get('status')} engine={body.get('engine')} (完成分支)"
    return False, f"status={resp.status_code} body={body!r:.200}"


# (路径 id, URL, form, 预备桩列表, 判定函数)
PATH_MATRIX: list[
    tuple[str, str, dict[str, Any], list[Callable[[_Stubs], None]], Callable[[Any], tuple[bool, str]]]
] = [
    (
        "P1 streaming_audio 自动降级成功支路",
        "/api/generate/streaming_audio",
        {"text": "O3 多路径演练 P1。"},
        [_stub_main_unavailable, lambda s: _stub_edge_ok(s, "o3_drill_p1.mp3")],
        _check_p1,
    ),
    (
        "P2 streaming_audio 自动降级失败支路",
        "/api/generate/streaming_audio",
        {"text": "O3 多路径演练 P2。"},
        [_stub_main_unavailable, _stub_edge_broken],
        _check_p2,
    ),
    (
        "P3 voxcpm_design 非流式同引擎路径（预期无自动降级）",
        "/api/generate/voxcpm_design",
        {"text": "O3 多路径演练 P3。", "instruction": "平稳"},
        [_stub_main_unavailable],
        _check_p3,
    ),
    (
        "P4 edge-tts/tts 降级目标直连路径",
        "/api/generate/edge-tts/tts",
        {"text": "O3 多路径演练 P4。"},
        [lambda s: _stub_edge_ok(s, "o3_drill_p4.wav")],
        _check_p4,
    ),
    (
        "P5 /api/model/switch 人工切换路径（当前实态：降级目标缺依赖 fail-fast）",
        "/api/model/switch",
        {"engine": "edge-tts"},
        [],
        _check_p5,
    ),
]


def main() -> int:
    print(f"[o3-drill] F6 隔离库: {os.environ['TTS_HISTORY_DB_PATH']}")
    try:
        app = create_app()
    except Exception as exc:  # noqa: BLE001
        print(f"[o3-drill] 环境前置不满足，无法创建应用: {exc}", file=sys.stderr)
        return 2
    client = TestClient(app)
    failures = 0
    for pid, url, form, prepares, check in PATH_MATRIX:
        stubs = _Stubs()
        try:
            for prepare in prepares:
                prepare(stubs)
            resp = _csrf_post(client, url, form)
            ok, detail = check(resp)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"异常: {type(exc).__name__}: {exc}"
        finally:
            stubs.restore()
        print(f"[o3-drill] {pid}")
        print(f"[o3-drill]   URL    : {url}")
        print(f"[o3-drill]   实测   : {detail}")
        print(f"[o3-drill]   判定   : {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures += 1
    print(f"[o3-drill] 汇总: {len(PATH_MATRIX) - failures}/{len(PATH_MATRIX)} PASS, {failures} FAIL")
    print("[o3-drill] 零生产写入: 历史库指向进程临时目录，降级合成走 seam 桩（无真实音频/网络/权重加载）。")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
