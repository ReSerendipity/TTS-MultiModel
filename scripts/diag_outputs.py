import os
from pathlib import Path

outputs = Path("outputs")
files = []
for f in outputs.rglob("*"):
    if f.is_file():
        files.append((str(f.relative_to(outputs)), f.stat().st_size, f.stat().st_mtime))

files.sort(key=lambda x: x[1], reverse=True)
print(f"总文件数: {len(files)}")
print(f"总大小: {sum(f[1] for f in files) / 1024 / 1024:.2f} MB")
print("\n最大的 10 个文件:")
for name, size, _mtime in files[:10]:
    print(f"  {size / 1024 / 1024:8.2f} MB  {name}")

# 按扩展名统计
from collections import Counter

exts = Counter(os.path.splitext(f[0])[1] for f in files)
print("\n按扩展名:")
for ext, count in exts.most_common():
    total = sum(f[1] for f in files if os.path.splitext(f[0])[1] == ext)
    print(f"  {ext or '(无扩展名)'}: {count} 个, {total / 1024 / 1024:.2f} MB")
