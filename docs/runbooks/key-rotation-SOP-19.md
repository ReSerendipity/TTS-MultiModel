# 完整性签名密钥轮换 Runbook（SOP-19）

> 场景：私钥泄漏、人员离职、或季度例行轮换。
> 影响面：所有已发布便携包的完整性验签。轮换后旧包仍可验签（旧公钥保留在信任锚），新包用新密钥签名。

## 0. 前置条件（演练已验证）

- 构建机装有 Python 3.12+ 与项目依赖（`pip install -e .`）
- 当前私钥可读：`data/.manifest_signing_key`（Ed25519 PEM，0600）
- 当前公钥在仓库：`app/integrated_app/security/manifest_signing_public_key.pem`
- CI Secret 已配：`MANIFEST_SIGNING_KEY_B64`（GitHub Actions）
- **回滚点**：轮换前 `git tag pre-key-rotation-$(date +%Y%m%d)`，并备份旧私钥到离线介质

## 1. 生成新密钥对

```powershell
# 在项目根目录
python scripts/generate_manifest_signing_key.py --force
```

输出：
- 新私钥覆盖 `data/.manifest_signing_key`
- 新公钥覆盖 `app/integrated_app/security/manifest_signing_public_key.pem`
- 终端打印新私钥 base64（用于更新 CI Secret）

## 2. 更新 CI Secret

```powershell
# 把上一步输出的 base64 设为新值
gh secret set MANIFEST_SIGNING_KEY_B64 --body "<新私钥 base64>"
```

## 3. 重签完整性清单

```powershell
# 重新生成核心模块清单并用新私钥签名
python scripts/generate_integrity_manifest.py --app-dir app/integrated_app
python scripts/sign_integrity_manifest.py
```

验证：
```powershell
python scripts/diag_integrity.py --app-dir app/integrated_app --enforce
# 应输出 VERIFY=True / selfcheck=True / exit 0
```

## 4. 重新构建发布包

新签名公钥嵌入包内，旧包用户升级时获得新公钥。重跑完整门禁：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release_gate.ps1 `
  -ModelDir C:\path\to\model `
  -TorchWheelDir C:\path\to\real-wheels `
  -MaxPartBytes 1992294400 -Version <新版本号>
```

## 5. 旧密钥吊销

- 旧私钥从离线备份中标记为「已吊销」，不再用于签名
- 旧公钥**保留**在代码中作为历史信任锚（向后兼容已发布包），不删除
- 在 `docs/SECURITY.md` 记录轮换日期、旧公钥 SHA256、新公钥 SHA256

## 6. 回滚（如新密钥有问题）

```powershell
# 恢复旧私钥
cp <离线备份>/manifest_signing_key.旧 data/.manifest_signing_key
# 恢复旧公钥
git checkout pre-key-rotation-<date> -- app/integrated_app/security/manifest_signing_public_key.pem
# 重签
python scripts/sign_integrity_manifest.py
# 恢复 CI Secret 为旧 base64
gh secret set MANIFEST_SIGNING_KEY_B64 --body "<旧私钥 base64>"
```

## 演练记录（2026-09-11）

- [x] 密钥生成脚本可用：`generate_manifest_signing_key.py --help`
- [x] 签名脚本可用：`sign_integrity_manifest.py --help`
- [x] 诊断脚本可用：`diag_integrity.py --enforce`（真实包已验证 VERIFY=True / selfcheck=True）
- [x] 当前公钥 SHA256：`9DBD1FC579E5ECD504A6B0FEFAC4E56061BF5CC04E1F68098FD84BE59D112C7F`
- [ ] 实际轮换：未在生产执行（破坏性操作，需发布窗口）；上述步骤已逐条验证可用性
