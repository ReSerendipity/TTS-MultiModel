#!/usr/bin/env python
"""模型权重路径漂移检测脚本。

检测 config.yaml 中配置的模型路径是否与实际文件系统一致，
防止因目录移动/重命名导致的路径漂移问题。

用法：
    python scripts/check_model_paths.py
"""

import os
import sys

import yaml


def check_model_paths(config_path: str = "config.yaml") -> bool:
    """检查 config.yaml 中的模型路径。

    Args:
        config_path: config.yaml 文件路径。

    Returns:
        True 表示所有路径有效。
    """
    if not os.path.exists(config_path):
        print(f"错误：配置文件不存在: {config_path}", file=sys.stderr)
        return False

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    models_config = config.get("models", {})
    all_ok = True

    # 检查各引擎路径
    for engine_name, engine_config in models_config.items():
        if not isinstance(engine_config, dict):
            continue

        for path_key, path_value in engine_config.items():
            if not isinstance(path_value, str) or "path" not in path_key.lower():
                continue

            if not path_value:
                continue

            # 检查路径是否存在
            if not os.path.exists(path_value):
                print(f"[{engine_name}] 路径不存在: {path_key}={path_value}")
                all_ok = False
            else:
                print(f"[{engine_name}] OK: {path_key}={path_value}")

    # 检查 pretrained_models 目录
    pretrained_dir = os.path.join(os.getcwd(), "pretrained_models")
    if os.path.isdir(pretrained_dir):
        for entry in os.listdir(pretrained_dir):
            entry_path = os.path.join(pretrained_dir, entry)
            if os.path.isdir(entry_path):
                # 检查目录中是否有权重文件
                weight_exts = (".bin", ".safetensors", ".pt", ".pth", ".ckpt", ".onnx")
                has_weights = any(f.endswith(weight_exts) for root, _, files in os.walk(entry_path) for f in files)
                if has_weights:
                    print(f"[pretrained_models/{entry}] 权重文件存在")

    return all_ok


if __name__ == "__main__":
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"

    if check_model_paths(config_path):
        print("\n所有模型路径检查通过")
        sys.exit(0)
    else:
        print("\n部分模型路径检查失败", file=sys.stderr)
        sys.exit(1)
