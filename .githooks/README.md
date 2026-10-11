# .githooks

本仓库的 Git 钩子源码。启用一次即可：

```sh
git config core.hooksPath .githooks      # 或执行 ./.githooks/install.sh
```

启用后钩子直接从本目录读取，**改这里立即生效**，不需要再往 `.git/hooks` 复制文件。

> **自动 Bootstrap**：`install.sh` / `install.bat` 会自动执行上述配置；`post-checkout` 钩子在首次克隆时也会自动检测并设置 `core.hooksPath`（若尚未配置）。

| 钩子 | 作用 |
| --- | --- |
| `pre-commit` | 分发器按「.venv → PATH python → 控制台 pre-commit → pre-commit-lite」四级回退执行框架检查（见下方说明） |
| `pre-push` | 有 `precheck.ps1` 时执行它；没有则跑根目录守卫 + **打印显式降级声明**（详见下方「pre-push 门禁口径」） |
| `prepare-commit-msg` | 自动追加 `Signed-off-by`（幂等，插在注释块之前） |
| `commit-msg` | DCO 硬校验（缺签名阻断）+ conventional 规范软提示 |
| `post-merge` / `post-checkout` | 依赖清单变更提醒（pip / pnpm / npm / cargo / gradle） |
| `pre-commit-lite` | 轻量检查：大文件、私钥与令牌、冲突标记 + 根目录守卫 |

## 说明

- `pre-commit-lite` 找不到 Python 时会打印醒目 `[WARN]` 并放行；设 `GUARD_STRICT=1` 可让它改为阻断。
- 大文件阈值默认 5MB，可用 `GUARD_MAX_FILE_MB` 调整。
- `core.hooksPath` 是**本地**配置，不会随克隆传播——换机器请重新执行上面那行命令。

- `pre-commit` 分发器框架解析优先级（2026-09-17 升级）：① 仓库 `.venv` 内的 python → ② PATH 上的 `python` → ③ 控制台命令 `pre-commit`（系统/用户级安装）→ ④ 均不可用时回退 `pre-commit-lite` 兜底。无 venv 的机器不再静默跳过框架钩子。
- 存在 `precheck.ps1` 时（入库或本地），其内部同样做 python 回退（`.venv` → PATH）；依赖缺失打印 `[WARN]` 后跳过对应步骤，完整门禁由 CI 承担。

## pre-push 门禁口径（2026-10-06 明确）

- `precheck.ps1` **被 `.gitignore` 排除**（`.gitignore` 里有 `precheck.ps1` 条目），属本机文件：新克隆、新工作树天然没有它。
- 因此 `pre-push` 有两级行为边界：
  | 场景 | 实际执行 | 退出码语义 |
  | --- | --- | --- |
  | 有 `precheck.ps1` | ruff lint / ruff format / mypy 棘轮 / REVISION_LOG 台账（硬门禁）；改动命中 `app\|scripts\|tests` 时自动升级 `-Full`（pytest + 覆盖率 >=45%）；另带编码 BOM 守卫 | 任一硬门禁失败 = 阻断 |
  | 无 `precheck.ps1` | 仅 structure-guard 根目录守卫；其余层**不会执行**，钩子向 stderr 打印逐条降级声明（未跑哪些层、由谁兜底） | 默认放行但声明可见；`GUARD_STRICT=1` 改为阻断 |
- 无 `precheck.ps1` 时**不改为**调用 `scripts/check_local.py`：它的快检层对全仓执行 `ruff format --check .`，而本仓 lint/格式门禁的权威范围是 `^(app/integrated_app\|scripts\|tests)/`（见 `.pre-commit-config.yaml` 的 `files` 过滤与 `precheck.ps1` 的 Step 参数）。接入会把仓外既有偏差变成误报阻断，且它在 `ruff format` 返回 1 时回退调未安装的 `black`，报错会伪装成环境问题。取舍口径：**行为可见 + 人工授权**，而不是换一道范围不一致的闸。
- 两条分支共用的降级判据只有 `structure-guard` 与变更感知升级规则（后者决定声明里要不要写明「本该跑 `-Full` 却没跑」）。
- 钩子里不出现需要网络或本机专属路径的步骤；`--no-verify` 始终是唯一的显式绕过点。
