"""为现有音色生成按音色独立的元数据文件，并清理旧的共享 metadata.json。

数据治理修复配套脚本：将历史共享的 ``personas/metadata.json`` 迁移为
``personas/<name>.metadata.json``（与 persona_metadata.py 的新存储策略一致）。

用法：
    python scripts/regenerate_persona_metadata.py [--persona-dir PERSONAS] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from integrated_app.persona_metadata import (  # noqa: E402
    load_persona_metadata,
    save_persona_metadata,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="为现有音色生成 per-persona 元数据并清理共享 metadata.json")
    parser.add_argument("--persona-dir", default=None, help="音色目录（默认项目内 personas/）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将要执行的操作，不写文件")
    args = parser.parse_args()

    persona_dir = args.persona_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "personas"
    )
    if not os.path.isdir(persona_dir):
        print(f"[skip] 音色目录不存在: {persona_dir}")
        return 0

    names = sorted({f[:-4] for f in os.listdir(persona_dir) if f.endswith(".wav")})
    shared_meta = os.path.join(persona_dir, "metadata.json")

    for name in names:
        meta = load_persona_metadata(persona_dir, name)
        target = os.path.join(persona_dir, f"{name}.metadata.json")
        if args.dry_run:
            print(f"[dry-run] 将写入 {target}（name={meta.name}, voice_type={meta.voice_type}）")
        else:
            save_persona_metadata(persona_dir, name, meta)
            print(f"[ok] 已生成 {target}")

    # 清理旧的共享 metadata.json（其数据已被 per-persona 文件取代）
    if os.path.exists(shared_meta):
        if args.dry_run:
            print(f"[dry-run] 将删除旧的共享元数据文件 {shared_meta}")
        else:
            os.remove(shared_meta)
            print(f"[ok] 已删除旧共享元数据文件 {shared_meta}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
