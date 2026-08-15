#!/usr/bin/env python
"""批量克隆所有 Persona 示例 — 遍历 personas/ 目录批量生成。

读取 personas/ 目录下的所有音色，对每个音色使用指定引擎克隆生成语音。

用法：
    python examples/batch_clone_all_personas.py --text "你好世界"
    python examples/batch_clone_all_personas.py --text "测试" --engine voxcpm2
"""

import argparse
import os
import sys

import requests

SERVER = "http://127.0.0.1:7869"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONAS_DIR = os.path.join(PROJECT_ROOT, "personas")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "outputs", "batch")


def list_personas(personas_dir: str = PERSONAS_DIR) -> list[str]:
    """列出 personas/ 目录下的所有有效音色名。

    Args:
        personas_dir: 音色目录路径。

    Returns:
        音色名称列表（有 .wav 文件的）。
    """
    if not os.path.isdir(personas_dir):
        return []
    names = []
    for entry in os.listdir(personas_dir):
        if entry.endswith(".wav"):
            name = entry[:-4]
            names.append(name)
    return sorted(names)


def batch_clone(
    text: str,
    engine: str = "voxcpm2",
    personas_dir: str = PERSONAS_DIR,
    output_dir: str = OUTPUT_DIR,
):
    """批量克隆所有 persona。

    Args:
        text: 要合成的文本。
        engine: 引擎名称（voxcpm2 / indextts2 / generic_tts_engine）。
        personas_dir: 音色目录。
        output_dir: 输出目录。
    """
    personas = list_personas(personas_dir)
    if not personas:
        print(f"未找到 persona（目录: {personas_dir}）")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    print(f"找到 {len(personas)} 个 persona: {personas}")
    print(f"合成文本: {text}")
    print(f"引擎: {engine}")
    print(f"输出目录: {output_dir}")
    print("-" * 60)

    success = 0
    failed = 0

    for name in personas:
        wav_path = os.path.join(personas_dir, f"{name}.wav")
        output_path = os.path.join(output_dir, f"{name}_{engine}.wav")

        try:
            with open(wav_path, "rb") as f:
                resp = requests.post(
                    f"{SERVER}/api/generate/generic/clone",
                    files={"ref_audio": f},
                    data={
                        "engine": engine,
                        "text": text,
                        "language": "auto",
                    },
                    timeout=180,
                )
            resp.raise_for_status()

            if "audio" in resp.headers.get("content-type", ""):
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                print(f"  [OK] {name} -> {output_path}")
                success += 1
            else:
                result = resp.json()
                print(f"  [FAIL] {name}: {result}")
                failed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1

    print("-" * 60)
    print(f"完成: {success} 成功, {failed} 失败, 共 {len(personas)} 个")


def main():
    parser = argparse.ArgumentParser(description="批量克隆所有 Persona")
    parser.add_argument("--text", default="你好，这是语音克隆测试。", help="合成文本")
    parser.add_argument(
        "--engine",
        default="voxcpm2",
        choices=["voxcpm2", "indextts2", "generic_tts_engine"],
        help="TTS 引擎",
    )
    parser.add_argument("--server", default=SERVER, help="服务器地址")
    args = parser.parse_args()

    global SERVER
    SERVER = args.server

    batch_clone(text=args.text, engine=args.engine)


if __name__ == "__main__":
    main()
