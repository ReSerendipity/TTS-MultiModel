"""Benchmark 基线入库验证测试。

验证：
- benchmarks/baseline.json 存在且为合法 JSON
- 基线文件结构符合 pytest-benchmark storage 格式
- export_benchmark_baseline.py 脚本可正常导入且函数签名正确
- 基线文件 benchmarks 列表中的每个条目含必要字段（name/stats）
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "benchmarks" / "baseline.json"
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "export_benchmark_baseline.py"


class TestBenchmarkBaselineFile:
    """基线文件结构验证。"""

    def test_baseline_file_exists(self):
        assert BASELINE_PATH.is_file(), f"基线文件不存在: {BASELINE_PATH}"

    def test_baseline_is_valid_json(self):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_baseline_has_benchmark_key(self):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert "benchmark" in data, "基线文件缺少顶层 'benchmark' 键"

    def test_baseline_benchmark_has_required_metadata(self):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        bm = data["benchmark"]
        assert "name" in bm
        assert "datetime" in bm
        assert "benchmarks" in bm
        assert isinstance(bm["benchmarks"], list)

    def test_baseline_benchmark_entries_have_required_fields(self):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for entry in data["benchmark"]["benchmarks"]:
            assert "name" in entry, f"benchmark 条目缺少 name: {entry}"
            assert "stats" in entry, f"benchmark 条目缺少 stats: {entry}"
            stats = entry["stats"]
            assert "mean" in stats, f"stats 缺少 mean: {stats}"
            assert "median" in stats, f"stats 缺少 median: {stats}"

    def test_baseline_machine_info_present(self):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        bm = data["benchmark"]
        if bm["benchmarks"]:  # 非空基线才要求 machine_info
            assert "machine_info" in bm


class TestExportScript:
    """导出脚本可导入性验证。"""

    def test_export_script_exists(self):
        assert EXPORT_SCRIPT.is_file(), f"导出脚本不存在: {EXPORT_SCRIPT}"

    def test_export_script_importable(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("export_benchmark_baseline", EXPORT_SCRIPT)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "export_baseline")
        assert hasattr(module, "find_latest_run")
        assert hasattr(module, "main")

    def test_find_latest_run_returns_none_for_empty_dir(self, tmp_path):
        import importlib.util

        spec = importlib.util.spec_from_file_location("export_benchmark_baseline", EXPORT_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # 空目录应返回 None
        assert module.find_latest_run(tmp_path) is None

    def test_find_latest_run_picks_highest_number(self, tmp_path):
        import importlib.util

        spec = importlib.util.spec_from_file_location("export_benchmark_baseline", EXPORT_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # 创建两个 run 文件
        (tmp_path / "0001_first.json").write_text("{}", encoding="utf-8")
        (tmp_path / "0002_second.json").write_text("{}", encoding="utf-8")
        latest = module.find_latest_run(tmp_path)
        assert latest is not None
        assert latest.name == "0002_second.json"


class TestBenchmarkReadme:
    """benchmarks/README.md 存在性验证。"""

    def test_readme_exists(self):
        readme = REPO_ROOT / "benchmarks" / "README.md"
        assert readme.is_file(), "benchmarks/README.md 不存在"

    def test_readme_mentions_storage_strategy(self):
        readme = REPO_ROOT / "benchmarks" / "README.md"
        content = readme.read_text(encoding="utf-8")
        assert "release" in content.lower() or "Release" in content
        assert "cache" in content.lower() or "缓存" in content
