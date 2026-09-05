"""OpenAPI Schema 快照测试 — 防止端点被意外删除/重命名/改方法。

契约快照只记录「不可变契约面」：路径 + HTTP 方法 + operationId + 响应状态码。
不记录 schema 细节（请求体/响应体模型），避免每次字段调整都触发快照更新。

用法：
  正常运行：pytest tests/test_openapi_snapshot.py
  更新基线：UPDATE_OPENAPI_SNAPSHOT=1 pytest tests/test_openapi_snapshot.py

基线文件：tests/snapshots/openapi_contract.json
首次运行时自动生成基线；后续运行对比，不一致则失败并输出 diff。
"""

import json
import os
from pathlib import Path

import pytest

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "openapi_contract.json"
UPDATE_SNAPSHOT = os.environ.get("UPDATE_OPENAPI_SNAPSHOT", "").strip() in ("1", "true", "True")


def _extract_contract(schema: dict) -> dict:
    """从 OpenAPI schema 提取契约指纹（路径+方法+operationId+响应码）。

    忽略 info.version 等易变字段，只保留对外契约面。
    """
    paths = schema.get("paths", {})
    contract: dict[str, dict[str, dict]] = {}
    for path, methods in sorted(paths.items()):
        contract[path] = {}
        for method, spec in methods.items():
            if method not in ("get", "post", "put", "delete", "patch", "options", "head"):
                continue
            responses = spec.get("responses", {})
            contract[path][method.upper()] = {
                "operationId": spec.get("operationId"),
                "status_codes": sorted(responses.keys()),
            }
    return contract


def _diff_contract(old: dict, new: dict) -> list[str]:
    """生成新旧契约的人类可读 diff（只列变更，不列完全相同的端点）。"""
    diffs: list[str] = []
    old_paths = set(old.keys())
    new_paths = set(new.keys())

    for path in sorted(old_paths - new_paths):
        diffs.append(f"[REMOVED] {path} (methods: {', '.join(old[path].keys())})")

    for path in sorted(new_paths - old_paths):
        diffs.append(f"[ADDED]   {path} (methods: {', '.join(new[path].keys())})")

    for path in sorted(old_paths & new_paths):
        old_methods = set(old[path].keys())
        new_methods = set(new[path].keys())
        for m in sorted(old_methods - new_methods):
            diffs.append(f"[REMOVED] {path} {m}")
        for m in sorted(new_methods - old_methods):
            diffs.append(f"[ADDED]   {path} {m}")
        for m in sorted(old_methods & new_methods):
            old_op = old[path][m].get("operationId")
            new_op = new[path][m].get("operationId")
            if old_op != new_op:
                diffs.append(f"[CHANGED] {path} {m} operationId: {old_op!r} -> {new_op!r}")
            old_codes = old[path][m].get("status_codes", [])
            new_codes = new[path][m].get("status_codes", [])
            if old_codes != new_codes:
                diffs.append(f"[CHANGED] {path} {m} status_codes: {old_codes} -> {new_codes}")
    return diffs


class TestOpenAPISnapshot:
    """OpenAPI 契约快照测试。"""

    def test_contract_matches_snapshot(self, app):
        """当前 OpenAPI schema 的契约面必须与基线一致。"""
        schema = app.openapi()
        current = _extract_contract(schema)

        if UPDATE_SNAPSHOT or not SNAPSHOT_PATH.exists():
            SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_PATH.write_text(
                json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if not UPDATE_SNAPSHOT:
                pytest.skip(f"基线不存在，已自动生成: {SNAPSHOT_PATH}")
            return

        baseline = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        diffs = _diff_contract(baseline, current)

        if diffs:
            diff_text = "\n".join(diffs)
            pytest.fail(
                f"OpenAPI 契约快照不匹配（{len(diffs)} 处变更）：\n{diff_text}\n\n"
                f"如变更为预期，请运行 UPDATE_OPENAPI_SNAPSHOT=1 pytest tests/test_openapi_snapshot.py 更新基线。"
            )

    def test_no_duplicate_operation_ids(self, app):
        """所有 operationId 必须唯一（OpenAPI 规范要求）。"""
        schema = app.openapi()
        contract = _extract_contract(schema)
        ids: list[str] = []
        for _path, methods in contract.items():
            for _method, spec in methods.items():
                oid = spec.get("operationId")
                if oid:
                    ids.append(oid)
        from collections import Counter

        dupes = {k: v for k, v in Counter(ids).items() if v > 1}
        assert not dupes, f"存在重复 operationId: {dupes}"

    def test_all_paths_have_operation_id(self, app):
        """每个端点都必须有显式或自动生成的 operationId（不应为空）。"""
        schema = app.openapi()
        contract = _extract_contract(schema)
        missing: list[str] = []
        for _path, methods in contract.items():
            for _method, spec in methods.items():
                if not spec.get("operationId"):
                    missing.append(f"{_method} {_path}")
        assert not missing, f"以下端点缺少 operationId: {missing}"
