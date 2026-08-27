# 模型下载与验证示例

本节提供三套引擎的示例下载命令（占位 Hugging Face repo ID），以及本地 / Docker 的一键下载与验证示例。请将示例中的 `HF_REPO_ID` 替换为实际的 Hugging Face 仓库 ID（如 `owner/model-name`）。

> 注意：某些模型权重通过 Git LFS 存储，下载前请先安装并配置 `git-lfs`。大模型文件可能需要较长时间与足够的磁盘空间。

---

## IndexTTS2 示例（本地）

```bash
# 示例：使用 wget 从 HF 下载单个文件（占位 repo-id）
# 替换 HF_REPO_ID 为实际仓库，例如 owner/indextts2-model
wget -O model/indextts2/model.pt "https://huggingface.co/HF_REPO_ID/resolve/main/model.pt"

# 若模型使用多个文件，请确保下载所有必要的文件（config, tokenizer, weights 等）
```

验证文件存在：

```bash
ls -lh model/indextts2/
python -c "import os; print(os.path.exists('model/indextts2/model.pt'))"
```

Docker 一键示例（在容器内执行或通过 volume 挂载下载到宿主机）：

```bash
docker run --rm -v $(pwd)/model:/workspace/model ubuntu:22.04 /app/bash -c "apt update && apt install -y wget && wget -O /workspace/model/indextts2/model.pt \"https://huggingface.co/HF_REPO_ID/resolve/main/model.pt\""
```

---

## VoxCPM2 示例（本地）

```bash
# 若仓库提供单文件权重
wget -O model/voxcpm2/voxcpm2.pt "https://huggingface.co/HF_REPO_ID/resolve/main/voxcpm2.pt"

# 若模型通过 git + git-lfs 管理：
# git lfs install
# git clone https://huggingface.co/HF_REPO_ID model/voxcpm2
```

验证：

```bash
ls -lh model/voxcpm2/
python -c "import os; print(len([f for f in os.listdir('model/voxcpm2')])>0)"
```

---

## dots.tts 示例（本地）

```bash
# 单文件下载示例
wget -O model/dots_tts/dots.pt "https://huggingface.co/HF_REPO_ID/resolve/main/model.pt"
```

验证：

```bash
python -c "import pathlib; print(pathlib.Path('model/dots_tts/dots.pt').exists())"
```

---

## 权重许可证与注意事项

- 请在使用模型权重前确认对应仓库的 License 与使用条款（有些权重不可用于商业用途或对外分发）。
- 对于较大文件，优先使用 `git lfs` 克隆以保持文件完整性。

---

## 在 README 中引用

建议在主 README 的“模型下载”或“部署”一节中引用本文件：

```
See `docs/MODEL_DOWNLOADS.md` for example commands to download IndexTTS2, VoxCPM2, and dots.tts models.
```

---

若你希望我把这些示例直接插入 README，我可以把 README 更新到仓库主分支并发起对应 PR；或者我也可以先把这些作为一个 PR 提交到文档分支并等待 review。