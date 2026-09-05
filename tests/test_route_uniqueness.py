"""路由唯一性测试（P2-1 后端设计评估落地）。

验证 app_server 不会因双路由发现机制导致同一 (path, method) 被重复注册。
历史问题：_discover_routes + _auto_discover_routers 兜底并存，且共享 router
被多个模块 re-export 后逐个 include，导致每条路由重复注册 N 次 → 281 条
Duplicate Operation ID 警告。本测试在移除兜底 + router 对象去重后锁定回归。

注意：FastAPI/Starlette 新版本将 include_router 的路由包装为 _IncludedRouter，
直接遍历 app.routes 看不到内部路由；必须通过 app.openapi()["paths"] 获取
解析后的完整 (path, method) 集合。
"""

from __future__ import annotations

import os
import warnings

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


def _collect_path_methods(app) -> list[tuple[str, str]]:
    """从 app.openapi() 收集 (path, method) 对（已解析所有嵌套 _IncludedRouter）。"""
    schema = app.openapi()
    pairs: list[tuple[str, str]] = []
    for path, methods in schema.get("paths", {}).items():
        for method in methods:
            if method in ("head", "options"):
                continue
            pairs.append((path, method.upper()))
    return pairs


def test_no_duplicate_routes():
    """同一 (path, method) 不应在 OpenAPI schema 中出现超过一次。"""
    from integrated_app.app_server import create_app

    app = create_app()
    pairs = _collect_path_methods(app)
    from collections import Counter

    counter = Counter(pairs)
    duplicates = {k: v for k, v in counter.items() if v > 1}
    assert not duplicates, (
        f"发现 {len(duplicates)} 个重复路由 (path, method): "
        f"{list(duplicates.items())[:5]}{'...' if len(duplicates) > 5 else ''}"
    )


def test_route_count_reasonable():
    """注册路由数应在合理范围（>20，排除空 app / 路由未挂载）。"""
    from integrated_app.app_server import create_app

    app = create_app()
    pairs = _collect_path_methods(app)
    assert len(pairs) > 20, f"路由数过少: {len(pairs)}"


def test_openapi_no_duplicate_operation_ids():
    """生成 OpenAPI schema 时不应产生 Duplicate Operation ID 警告。"""
    from integrated_app.app_server import create_app

    app = create_app()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _ = app.openapi()
    dup_warnings = [str(x.message) for x in w if "Duplicate Operation ID" in str(x.message)]
    assert not dup_warnings, (
        f"OpenAPI 生成产生 {len(dup_warnings)} 条 Duplicate Operation ID 警告，"
        f"首条: {dup_warnings[0] if dup_warnings else ''}"
    )
