"""P2-11：outputs 目录生命周期治理。

1. 清理 outputs/ 中的非音频文件（迁移备份、数据库、临时文件）
2. 扫描孤儿音频文件（存在于 outputs/ 但 history.db 中无对应记录）
3. 可选：删除孤儿音频文件（--delete 标志）

用法:
    python scripts/clean_outputs.py          # 仅扫描报告
    python scripts/clean_outputs.py --delete # 删除孤儿音频文件
"""

import os
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
HISTORY_DB = PROJECT_ROOT / "data" / "history.db"

# outputs/ 中允许的音频扩展名
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
# 应移动到 data/ 的文件模式
DATA_FILE_PATTERNS = ["history.db.migrated_", "generation_versions.db"]


def get_history_filepaths() -> set[str]:
    """从 history.db 获取所有记录的 filepath（绝对路径和文件名）。"""
    paths = set()
    if not HISTORY_DB.exists():
        return paths
    conn = sqlite3.connect(str(HISTORY_DB))
    try:
        rows = conn.execute(
            "SELECT filepath FROM generation_history WHERE filepath IS NOT NULL AND filepath != ''"
        ).fetchall()
        for (fp,) in rows:
            paths.add(fp)
            paths.add(os.path.basename(fp))
    finally:
        conn.close()
    return paths


def main():
    do_delete = "--delete" in sys.argv

    if not OUTPUTS_DIR.exists():
        print("outputs/ 目录不存在")
        return

    print("=" * 60)
    print("P2-11: outputs 目录生命周期治理")
    print("=" * 60)

    history_paths = get_history_filepaths()
    print(f"history.db 中有 {len(history_paths)} 个文件路径")

    non_audio = []
    orphan_audio = []
    valid_audio = []

    for f in OUTPUTS_DIR.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(OUTPUTS_DIR))
        ext = f.suffix.lower()

        if ext in AUDIO_EXTS:
            # 检查是否在 history 中有记录
            if str(f) in history_paths or f.name in history_paths:
                valid_audio.append((rel, f.stat().st_size))
            else:
                orphan_audio.append((rel, f.stat().st_size))
        else:
            non_audio.append((rel, f.stat().st_size))

    print(f"\n音频文件: {len(valid_audio)} 个 (有 history 记录)")
    for name, size in valid_audio:
        print(f"  ✓ {size / 1024:.1f} KB  {name}")

    print(f"\n孤儿音频文件: {len(orphan_audio)} 个 (无 history 记录)")
    for name, size in orphan_audio:
        print(f"  ? {size / 1024:.1f} KB  {name}")

    print(f"\n非音频文件: {len(non_audio)} 个 (应清理)")
    total_non_audio = 0
    for name, size in non_audio:
        total_non_audio += size
        print(f"  ✗ {size / 1024 / 1024:.2f} MB  {name}")
    print(f"  非音频文件总计: {total_non_audio / 1024 / 1024:.2f} MB")

    # 清理非音频文件
    if non_audio:
        print("\n清理非音频文件...")
        for name, _size in non_audio:
            src = OUTPUTS_DIR / name
            # 数据库迁移备份移动到 data/
            if any(p in name for p in DATA_FILE_PATTERNS):
                dst = PROJECT_ROOT / "data" / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                print(f"  → 移动到 data/: {name}")
            else:
                src.unlink()
                print(f"  → 删除: {name}")

    # 删除孤儿音频文件
    if orphan_audio and do_delete:
        print("\n删除孤儿音频文件...")
        for name, _size in orphan_audio:
            (OUTPUTS_DIR / name).unlink()
            print(f"  → 删除: {name}")
    elif orphan_audio:
        print("\n孤儿音频文件未删除（使用 --delete 标志删除）")

    print("\n" + "=" * 60)
    print("完成")


if __name__ == "__main__":
    main()
