# 在线模拟演示（GitHub Pages）

本目录是项目的**纯前端模拟演示站**，部署在 GitHub Pages 上，无需 GPU / Python / 模型权重即可体验。

## 演示内容

- 完整的产品界面交互（与真实 Web UI 同款设计语言）
- 模拟任务流程：上传 → 进度 → 日志 → 结果
- 所有结果均为**本地模拟**（内置示例图片 / 浏览器语音引擎），不执行真实模型推理

## 本地预览

直接用浏览器打开 `demo/index.html` 即可（无需服务器）。

## 部署

推送到 `main`（或 `master`）分支后，`.github/workflows/pages-deploy.yml` 会自动把 `demo/` 部署到 GitHub Pages。

部署前需要在仓库 Settings → Pages 中把 Source 设置为 **GitHub Actions**（只需一次）。

线上地址：`https://<owner>.github.io/<repo>/`

> 部署触发记录：2026-08-10 23:52
