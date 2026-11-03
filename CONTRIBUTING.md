# 贡献指南 — TTS-MultiModel

## 提交前

1. 读根 `README.md`；有 `ARCHITECTURE.md` 时一并读，先弄清组件边界再改代码。
2. 提交必须带 DCO 签名：`git commit -s`，身份用 noreply 邮箱。
3. 跑本仓 `README.md` 里列出的测试 / 检查命令；失败不隐藏、不静默跳过。
4. 不提交密钥、token、连接串、本机绝对路径与构建产物。

## Pull Request

描述写清四段：

- **改了什么**
- **为什么改**
- **怎么验证**（命令、截图、日志）
- **哪些没做 / 已知限制**

改动涉及发布链、CI 门禁或依赖清单时，在描述里单独说明影响面。

## 代码风格

以仓库根的 `.editorconfig` 为准；提交前确认没有引入 CRLF 或制表符混用。

## 分支与合并

功能开发走 `feat/*`、`fix/*`、`ci/*`、`chore/*` 分支，完成后合入 `main`。

## 行为准则

参与本项目即表示同意遵守 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。
