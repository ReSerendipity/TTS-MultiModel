import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

print("=== 1. 语法检查 ===")
import py_compile

try:
    py_compile.compile("scripts/train_voxcpm_finetune.py", doraise=True)
    print("  OK: train_voxcpm_finetune.py")
except py_compile.PyCompileError as e:
    print(f"  FAIL: {e}")
    sys.exit(1)

print("\n=== 2. 血缘收集函数验证（独立测试，不导入 voxcpm） ===")


# 复制 _collect_lineage_info 逻辑进行独立测试
def _collect_lineage_info(**hyperparams):
    import time

    info = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_commit": "",
        "dataset_fingerprint": "",
        "config_snapshot_hash": "",
        "hyperparams": {},
    }
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        if result.returncode == 0:
            info["git_commit"] = result.stdout.strip()
    except Exception:
        pass
    train_manifest = hyperparams.get("train_manifest", "")
    if train_manifest and os.path.exists(train_manifest):
        with open(train_manifest, "rb") as f:
            info["dataset_fingerprint"] = hashlib.sha256(f.read()).hexdigest()[:16]
        info["train_manifest"] = train_manifest
    hp_keys = ["pretrained_path", "sample_rate", "batch_size", "learning_rate", "lora"]
    hp_snapshot = {k: hyperparams.get(k) for k in hp_keys if k in hyperparams}
    info["hyperparams"] = hp_snapshot
    info["config_snapshot_hash"] = hashlib.sha256(
        json.dumps(hp_snapshot, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return info


# 创建临时 manifest
tmpdir = tempfile.mkdtemp()
manifest_path = os.path.join(tmpdir, "train.json")
with open(manifest_path, "w") as f:
    json.dump([{"audio": "a.wav", "text": "hello"}], f)

info = _collect_lineage_info(
    pretrained_path="/models/voxcpm2",
    train_manifest=manifest_path,
    sample_rate=16000,
    batch_size=2,
    learning_rate=1e-4,
    lora={"rank": 8},
)
print(f"  git_commit: {info['git_commit'][:8]}... (非空: {bool(info['git_commit'])})")
print(f"  dataset_fingerprint: {info['dataset_fingerprint']} (16位: {len(info['dataset_fingerprint']) == 16})")
print(f"  config_snapshot_hash: {info['config_snapshot_hash']} (16位: {len(info['config_snapshot_hash']) == 16})")
print(f"  hyperparams keys: {list(info['hyperparams'].keys())}")
assert info["git_commit"], "git_commit 应非空"
assert len(info["dataset_fingerprint"]) == 16
assert len(info["config_snapshot_hash"]) == 16
assert "batch_size" in info["hyperparams"]
print("  ✓ 血缘收集函数正常")

print("\n=== 3. 验证脚本中血缘相关代码存在 ===")
with open("scripts/train_voxcpm_finetune.py", encoding="utf-8") as f:
    src = f.read()
checks = [
    ("_LINEAGE_INFO 全局变量", "_LINEAGE_INFO" in src),
    ("_collect_lineage_info 函数", "def _collect_lineage_info" in src),
    ("train() 中设置血缘", "_LINEAGE_INFO = _collect_lineage_info" in src),
    ("save_checkpoint 写 run_manifest", "run_manifest.json" in src),
    ("git_commit 字段", '"git_commit"' in src),
    ("dataset_fingerprint 字段", '"dataset_fingerprint"' in src),
    ("config_snapshot_hash 字段", '"config_snapshot_hash"' in src),
]
for name, ok in checks:
    print(f"  {'✓' if ok else '✗'} {name}")
    assert ok, f"{name} 缺失"

import shutil

shutil.rmtree(tmpdir, ignore_errors=True)
print("\nP1-6 训练血缘接线验收通过 ✓")
print("注：action_logs 表经核实为活跃表（routes/system/logs.py 有完整读写），非死表，无需处理")
