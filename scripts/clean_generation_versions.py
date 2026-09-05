import os
import shutil
import sqlite3

p = "outputs/generation_versions.db"
shutil.copy2(p, p + ".bak_20260905")
c = sqlite3.connect(p)
c.execute("DELETE FROM generation_versions")
c.commit()
print(f"清理前备份: {p}.bak_20260905")
print(f"清理后行数: {c.execute('SELECT COUNT(*) FROM generation_versions').fetchone()[0]}")
c.close()
print(f"文件大小: {os.path.getsize(p)} bytes")
