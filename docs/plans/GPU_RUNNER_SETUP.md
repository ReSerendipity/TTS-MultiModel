# Self-Hosted GPU Runner 配置指南

> 本文档说明如何配置 GitHub Actions self-hosted GPU runner，用于运行需要 GPU 的集成测试。
>
> **最后更新**：2026-08-01

---

## 1. 硬件要求

| 组件 | 最低要求 | 推荐 |
|------|----------|------|
| GPU | NVIDIA 12GB VRAM | NVIDIA 24GB+ VRAM |
| CPU | 4 核 | 8 核+ |
| RAM | 16GB | 32GB+ |
| 存储 | 100GB SSD | 500GB NVMe SSD |
| OS | Ubuntu 22.04 | Ubuntu 24.04 |
| CUDA | 12.1+ | 12.4+ |
| NVIDIA Driver | 535+ | 550+ |

---

## 2. 环境准备

### 2.1 安装 NVIDIA Driver

```bash
# Ubuntu
sudo apt-get update
sudo apt-get install -y nvidia-driver-550
sudo reboot
# 验证
nvidia-smi
```

### 2.2 安装 CUDA Toolkit

```bash
# 下载并安装 CUDA 12.4
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-ubuntu2204.pin
sudo mv cuda-ubuntu2204.pin /etc/apt/preferences.d/cuda-repository-pin-600
wget https://developer.download.nvidia.com/compute/cuda/12.4.0/local_installers/cuda-repo-ubuntu2204-12-4-local_12.4.0-550.54.14-1_amd64.deb
sudo dpkg -i cuda-repo-ubuntu2204-12-4-local_12.4.0-550.54.14-1_amd64.deb
sudo cp /var/cuda-repo-ubuntu2204-12-4-local/cuda-*-keyring.gpg /usr/share/keyrings/
sudo apt-get update
sudo apt-get -y install cuda-toolkit-12-4
# 添加到 PATH
echo 'export PATH=/usr/local/cuda-12.4/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda-12.4/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc
```

### 2.3 安装 Python 和依赖

```bash
# 安装 pyenv 或使用系统 Python
sudo apt-get install -y python3.12 python3.12-venv python3-pip

# 克隆项目
git clone https://github.com/ReSerendipity/TTS_MultiModel.git
cd TTS_MultiModel

# 创建虚拟环境
python3.12 -m venv venv
source venv/bin/activate

# 安装依赖
pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov httpx playwright
playwright install
```

### 2.4 下载模型

```bash
# VoxCPM2 模型
# 见 docs/MODEL_DOWNLOAD_GUIDE.md

# IndexTTS2 模型
python scripts/download_indextts2.py

# dots.tts 模型
python scripts/download_dotstts.py
```

---

## 3. 注册 GitHub Actions Runner

### 3.1 创建 Runner

1. 进入 GitHub 仓库 → Settings → Actions → Runners → New self-hosted runner
2. 选择 Linux x64
3. 按照页面指令操作：

```bash
# 创建 runner 用户
sudo useradd -m -s /bin/bash github-runner
sudo usermod -aG sudo github-runner

# 切换到 runner 用户
sudo su - github-runner

# 下载并配置
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64-2.317.0.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.317.0/actions-runner-linux-x64-2.317.0.tar.gz
tar xzf actions-runner-linux-x64-2.317.0.tar.gz

# 配置（使用页面显示的 token）
./config.sh --url https://github.com/ReSerendipity/TTS_MultiModel --token YOUR_TOKEN

# 安装为系统服务
exit
sudo ./actions-runner/bin/installactions-runner.sh
sudo systemctl start actions.runner.ReSerendipity-TTS-MultiModel
sudo systemctl enable actions.runner.ReSerendipity-TTS-MultiModel
```

### 3.2 添加 Runner Labels

为 GPU runner 添加 `gpu` 标签：
- GitHub 仓库 → Settings → Actions → Runners → 选择 runner → Edit → Add labels: `gpu`, `cuda`, `linux`

---

## 4. 使用 GPU Runner 的 CI Workflow

创建 `.github/workflows/gpu-tests.yml`：

```yaml
name: GPU Tests

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  gpu-integration:
    name: GPU Integration Tests
    runs-on: [self-hosted, gpu]
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest pytest-asyncio httpx

      - name: Run 3-engine compatibility check
        run: python scripts/check_3engine_compat.py

      - name: Run GPU integration tests
        env:
          TRANSFORMERS_OFFLINE: "1"
          HF_HUB_OFFLINE: "1"
          MODELSCOPE_OFFLINE: "1"
        run: |
          pytest tests/integration/ -v --tb=short -m "integration"

      - name: Run VRAM switch tests
        env:
          TRANSFORMERS_OFFLINE: "1"
          HF_HUB_OFFLINE: "1"
          MODELSCOPE_OFFLINE: "1"
          TTS_RUN_GPU_TESTS: "1"
        run: |
          pytest tests/integration/test_vram_switch.py -v --tb=short || true
```

---

## 5. 维护和监控

### 定期更新

```bash
# 更新 runner
cd actions-runner
./config.sh --version 2.317.0
sudo systemctl restart actions.runner.ReSerendipity-TTS-MultiModel

# 更新模型
cd /opt/TTS_MultiModel
git pull
pip install -r requirements.txt --upgrade
```

### 健康检查

```bash
# 检查 runner 状态
sudo systemctl status actions.runner.ReSerendipity-TTS-MultiModel

# 检查 GPU
nvidia-smi

# 检查磁盘空间
df -h

# 清理旧 workflow artifacts
sudo rm -rf /opt/actions-runner/_work/_temp/*
```

### 安全注意事项

1. Runner 账户应有最小权限原则
2. 不要在 runner 上存储敏感信息
3. 定期更新系统和 NVIDIA Driver
4. 使用 `systemd` 限制资源使用：

```ini
# /etc/systemd/system/actions.runner.ReSerendipity-TTS-MultiModel.service.d/limits.conf
[Service]
MemoryMax=32G
CPUQuota=800%
```
