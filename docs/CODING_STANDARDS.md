# 编码与 Git 规范（Coding & Git Standards）

> 本文件是仓库内所有入库内容的**可移植性、依赖与 Git 卫生**约束。任何修改请在提交前通读并对照检查。
> 最后更新：2026-09-18（路径可移植性专项修复）。

## 1. 路径可移植性（强制）

### 1.1 禁止

- 在入库文件中硬编码本机绝对路径：`C:\Users\<用户名>\...`、`/home/<用户名>/...`、`/Users/<用户名>/...`
- 在 pip 依赖清单里写 `file:///C:/...` 本机 wheel 路径
- 构建 / 打包 / 安装脚本里写死本机工具路径（如 `C:\Users\...\7za.exe`、`C:\Program Files (x86)\Microsoft Visual Studio\<版本>\...\vcvars64.bat`）

### 1.2 正确姿势

| 场景 | 做法 |
|---|---|
| Python 生产代码 | `Path(__file__).resolve().parents[N]` 推导项目根；外部路径用环境变量 + 项目内默认值 |
| PowerShell 脚本 | `$PSScriptRoot` 推导脚本所在目录 |
| 批处理 | `%~dp0` 推导脚本所在目录 |
| NSIS 安装器 | 相对脚本目录的路径 + `!ifndef/!define` 允许 `-D` 覆盖 |
| pip 依赖 | 官方索引 URL（如 `https://download.pytorch.org/whl/cu130/...`）或普通版本声明，禁止本机文件路径 |
| 数据 / 配置文件 | 不写本机路径；含路径的历史数据需在文档标注，不新增 |

### 1.3 本机专用（DEV-ONLY）运维脚本

仅本机使用的运维 / 开发辅助脚本（检查其他仓库 venv、清理本机数据等）：

- 文件头必须标注：`DEV-ONLY` + 用途 + 是否含本机路径
- 尽量参数化（环境变量 / 命令行参数）；不能参数化的必须明示"克隆后不可直接用"

### 1.4 提交前检查

PowerShell：

```powershell
# 全库搜索本机路径痕迹（排除文档与锁定文件后人工复核）
rg -n -i "C:\\Users\\|/home/|/Users/" --glob '!*.md' --glob '!*.lock' .
```

Python 项目：

```bash
grep -rn --include='*.py' -iE 'C:\\Users|/home/|/Users/' .
```

自动化门禁（已接入 `.githooks/pre-commit`，提交时自动执行）：

```bash
# 提交时拦截新引入的本机绝对路径；--all 全库审计（人工复核用）
python scripts/check_no_hardcoded_paths.py          # 默认：git diff --cached
python scripts/check_no_hardcoded_paths.py --all    # 全库
```

> 规则：占位符（`/home/user`、`C:\Users\me` 等）与豁免清单（`Dockerfile` 容器路径、`locales/` 占位文案、`.env.example`、测试断言文件、防泄漏断言脚本自身等）见脚本头部注释；新增豁免必须人工复核后加入 `ALLOWLIST` 并注明理由。

## 2. Git 工作流（强制）

- main 受保护：禁止直推。流程：`git fetch` → 从 `origin/main` 建分支 → 修改 → `git commit -s`（DCO）→ push（过 pre-push 门禁）→ GitHub PR。
- 提交信息使用 Conventional Commits 风格：`feat: / fix: / chore: / docs: / refactor: / test:`；同一仓库内语言保持一致。
- 钩子存放在 `.githooks/` 并随仓库分发（仓库自包含），克隆后启用：

  ```bash
  git config core.hooksPath .githooks
  ```

- `.gitattributes` 统一 `text eol=lf`；`.mailmap` 用于历史身份归并（新增身份先加映射）。
- 合并 PR 后删除源分支；恢复期保护分支（backup / pre-recovery-* 等）由所有者确认后删除，不擅自清理。

## 3. 仓库自包含（强制）

- 不依赖"家族 / 公用"仓库的内容：工作流、钩子、脚本必须随本仓库分发，保证**克隆后即可工作**。
- 已知例外（需逐步消除）：CI 引用 `ReSerendipity/.github` 的 self-purify.yml（`.github/workflows/self-purify.yml` 的 `self-purify` job）/ python-quality-baseline.yml（`.github/workflows/ci.yml` 的 `typecheck` job）。两处均已从 `@main` 钉定到 SHA `2c0c5abbe2acdb8efb793482b5c79411ed34bde6`（消除上游漂移风险）；解钉口径见各 workflow 引用上方注释——仅本仓需要上游新功能/安全修复时，由维护者手动核对并替换 SHA，不自动跟随 `main`。新增工作流禁止再引用外部仓库文件。
- 不使用 git submodule / symlink 传递必要内容。

## 4. 卫生与安全

- 密钥、`.env*`、本地数据库 / 产物不入库（见 `.gitignore`）。
- 大文件（>10MB）不入库，已有大资产走 LFS 或移出仓库。
- 不提交生成物（build/、dist/、target/、__pycache__/、*.log）。
- 修改 `.gitattributes` / `.gitignore` / `.mailmap` 前先确认与上游 `origin/main` 一致，避免重复 / 冲突提交。

## 5. 本仓修复记录（2026-09-18）

第二轮（检查脚本落地 + 首轮遗漏补齐）：

- `scripts/check_no_hardcoded_paths.py`：新增路径可移植性检查脚本，已接入 `.githooks/pre-commit`（提交时 `git diff --cached` 拦截）。
- `scripts/check_spec_refs.py`：docstring 中的本机路径说明改为通用表述（首轮遗漏，本轮补齐）。

首轮（PR #75）：

- 本次扫描**未发现**生产代码 / 构建脚本硬编码执行路径。
- 保留设计（符合规范）：`scripts/release_gate.ps1` 中的本机路径断言（如发现 `C:\Users\Doro` 残留即失败）是**防泄漏检查**，属健康设计，勿删除。
- 全库仅存在可接受的 /tmp、Dockerfile 容器路径、CI runner 路径与示例文本，均不构成硬编码执行路径。

## 6. 关联文档

- `docs/release-governance.md`、`docs/DOD.md`

---

## 8. Markdown 写作规范

所有 .md 文件遵循以下约定（2026-09-19 家族统一新增）：

- **编码**：UTF-8 **无 BOM**。禁止保存为带 BOM 的 UTF-8、UTF-16 或 GBK。
- **行尾**：统一 LF（.gitattributes 已锁 *.md text eol=lf），禁止 CRLF 入库。
- **行宽**：正文建议 ≤ 120 字符；URL、表格、代码块、长 JSON 不强制折行。
- **标题**：每个文件仅一个 #（一级标题），标题层级不跳级（# → ## → ###）。
- **中英文混排**：中文字符与英文/数字之间加一个半角空格（例：使用 ruff check .、Python 3.12）。
- **文件名**：公开文档优先英文 kebab-case（如 developer-guide.md）；存量中文文件名保留，新文档同一目录内风格保持一致。日期后缀统一用 -YYYYMMDD（如 exec-checklist-20260910.md）。
- **换行**：markdown 行尾两个空格表示硬换行；不要用 \ 转义。
- **链接**：相对路径引用仓库内文件，禁止引用本地绝对路径（C:\...、/home/...）。
