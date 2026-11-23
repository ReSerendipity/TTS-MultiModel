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

    def test_shell_update_workflow_removed(self):
        """单一路线：不保留增量包产线，官网与检查更新共用同一个完整安装包。"""
        wf = ROOT / ".github" / "workflows" / "shell-update.yml"
        assert not wf.exists(), "增量包 workflow 应已移除（单一完整包路线）"

    def test_installer_workflow_still_publishes_installer(self):
        """删增量产线时不能误删唯一的全量安装包产线。"""
        wf = _read(".github/workflows/desktop-installer.yml")
        assert "TTSMultiModel-Setup-v$ver.exe" in wf, "全量安装包产线被误删"
        assert "'v*'" in wf, "全量安装包 workflow 未在 tag push 时触发"


class TestFullInstallerUpdateRoute:
    """桌面检查更新下载完整安装包，与官网同源。"""

    def test_asset_template_is_full_installer(self):
        src = _read("desktop/src-tauri/src/updater.rs")
        assert 'ASSET_TEMPLATE: &str = "TTSMultiModel-Setup-v{version}.exe"' in src, (
            "更新资产不是完整安装包（应为 TTSMultiModel-Setup-v{version}.exe）"
        )
        assert "app-v{version}.zip" not in src, "仍残留旧增量包模板"

    def test_installer_kills_and_overwrites(self):
        """完整包重装可行依赖安装器自带 KillRunning，且不删用户数据目录。"""
        nsi = _read("scripts/installer/setup.nsi")
        assert "!macro KillRunning" in nsi, "安装器缺少关闭运行中实例的宏"
        # 安装段不得删整个安装目录（只有 Uninstall 段才允许）
        install_section = nsi.split('Section "TTSMultiModel 桌面版"', 1)[1].split('Section "Uninstall"', 1)[0]
        assert 'RMDir /r "$INSTDIR"' not in install_section, "安装段会删整个安装目录，老用户重装会丢 runtime/model/data"


class TestAutoRelaunchContract:
    """自更新七步的第 7 步：Rust 传 /RELAUNCH ↔ NSIS 检测 /RELAUNCH 成对存在。"""

    def test_rust_passes_relaunch_flag(self):
        src = _read("desktop/src-tauri/src/updater.rs")
        assert '"/RELAUNCH"' in src, "更新器未向安装器传 /RELAUNCH 标志"
        assert '"/S"' in src, "更新器未传 /S 静默标志"
        assert "std::process::exit(0)" in src, "更新器未有序退出（仍在依赖 KillRunning 强杀）"

    def test_nsis_detects_relaunch_and_execs_app(self):
        nsi = _read("scripts/installer/setup.nsi")
        assert '${StrStr} $0 "$CMDLINE" "/RELAUNCH"' in nsi, "NSIS 未检测 /RELAUNCH 标志"
        assert r"""Exec '"$INSTDIR\TTSMultiModel.exe"'""" in nsi, "NSIS 未在装完后拉起主程序（第 7 步自动重启缺失）"

    def test_frontend_announces_auto_restart(self):
        js = _read("desktop/src/desktop-bridge.js")
        assert 'listen("update-installing"' in js, "前端未监听 update-installing"
        assert "自动" in js and "重新启动" in js, "前端提示未说明会自动重启"
