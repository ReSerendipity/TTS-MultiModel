# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""发布产物的仓库名一致性（改名 TTS_MultiModel → TTS-MultiModel 后）。

2026-09-30 仓库改名。GitHub API 对旧名会 301 重定向，但**拼进下载 URL 的旧名
不会自动修正**：
- `make_shell_update.ps1` 生成的 `shell-update.json` 里 url 含仓库名，
  旧名 → 老用户「检查更新」下载 404；
- 自研 updater.rs 的默认 `UpdateSource::Github.repo` 已是新名（有测试更好）。

 archival 文档/CHANGELOG 历史条目/本机旧检出路径名**不是**本测试对象——
那些是历史记录，改了反而失真。这里只钉「会拼进 URL、影响下载」的活跃值。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = "TTS_MultiModel"
NEW = "TTS-MultiModel"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


class TestActiveRepoName:
    """活跃 URL 构造点必须指向改名后的仓库。"""

    def test_shell_update_script_default_repo(self):
        text = _read("scripts/make_shell_update.ps1")
        match = re.search(r"\[string\]\$RepoName\s*=\s*'([^']+)'", text)
        assert match, "make_shell_update.ps1 找不到 RepoName 默认值"
        assert match.group(1) == NEW, f"增量包更新 URL 将指向旧仓库 {match.group(1)}，老用户检查更新会 404"

    def test_shell_update_url_template_uses_owner_and_name(self):
        text = _read("scripts/make_shell_update.ps1")
        assert "https://github.com/$RepoOwner/$RepoName/releases/download/" in text, (
            "增量包下载 URL 模板变了，请人工核对拼出的完整地址"
        )

    def test_rust_default_update_source_repo(self):
        text = _read("desktop/src-tauri/src/config.rs")
        assert f'repo: "{NEW}".into()' in text, "桌面壳更新源默认仓库名不是新名，检查更新会走 301 或 404"

    def test_installer_workflow_uses_context_repo(self):
        """安装包 workflow 用 github.repository 上下文，不硬编码旧名。"""
        text = _read(".github/workflows/desktop-installer.yml")
        assert OLD not in text, "桌面安装包 workflow 内出现旧仓库名"


class TestSingleUpdateRoute:
    """桌面壳只保留自研原子换载一条更新路线，且增量包有产线。"""

    def test_tauri_plugin_updater_removed(self):
        """Tauri 官方 updater 插件已移除——它与自研更新器并存只会造成双路线困惑。"""
        cargo = _read("desktop/src-tauri/Cargo.toml")
        assert "tauri-plugin-updater" not in cargo, "Cargo.toml 仍有 tauri-plugin-updater 依赖"
        conf = _read("desktop/src-tauri/tauri.conf.json")
        assert '"updater"' not in conf, "tauri.conf.json 仍配着无人调用的官方 updater endpoint"
        caps = _read("desktop/src-tauri/capabilities/main.json")
        assert '"updater:default"' not in caps, "capabilities 仍给已删插件授权"
        main = _read("desktop/src-tauri/src/main.rs")
        assert "tauri_plugin_updater" not in main, "main.rs 仍注册官方 updater 插件"

    def test_self_built_updater_still_wired(self):
        """删插件时不能误删真正在跑的自研命令。"""
        main = _read("desktop/src-tauri/src/main.rs")
        for cmd in ("check_update", "start_update", "get_pending_update", "trigger_check_update"):
            assert cmd in main, f"自研更新命令 {cmd} 被误删"
        caps = _read("desktop/src-tauri/capabilities/main.json")
        for perm in ("allow-check-update", "allow-start-update"):
            assert perm in caps, f"自研命令权限 {perm} 被误删"

    def test_shell_update_workflow_exists(self):
        """tag push 必须产出 app-v{ver}.zip，否则老用户检查更新无包可下。"""
        wf = _read(".github/workflows/shell-update.yml")
        assert "make_shell_update.ps1" in wf, "增量包 workflow 未调用生成脚本"
        assert "'v*'" in wf, "增量包 workflow 未在 tag push 时触发"
        assert "app-v$ver.zip" in wf, "增量包 workflow 未上传 app-v zip"
