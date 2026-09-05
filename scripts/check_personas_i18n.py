"""P2-10：personas 四件套校验 + i18n 缺失 key 检查。

用法:
    python scripts/check_personas_i18n.py
退出码: 0=全部通过, 1=发现问题
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PERSONAS_DIR = PROJECT_ROOT / "personas"
LOCALES_DIR = PROJECT_ROOT / "app" / "integrated_app" / "locales"

# personas 四件套：参考文本(.txt) + 元数据(.metadata.json) + 声纹(.pt) + 参考音频(.wav)
PERSONA_REQUIRED = {".txt", ".metadata.json", ".pt", ".wav"}


def check_personas() -> list[str]:
    """校验 personas 四件套完整性 + 孤儿文件检测。"""
    issues = []
    if not PERSONAS_DIR.exists():
        return [f"personas/ 目录不存在: {PERSONAS_DIR}"]

    # 收集所有音色名（去扩展名）
    persona_names: set[str] = set()
    all_files: dict[str, set[str]] = {}  # name -> set of extensions

    for f in PERSONAS_DIR.iterdir():
        if f.is_dir() or f.name == "README.md":
            continue
        # 解析音色名和扩展名
        if f.name.endswith(".metadata.json"):
            name = f.name[: -len(".metadata.json")]
            ext = ".metadata.json"
        elif f.suffix in {".txt", ".pt", ".wav"}:
            name = f.stem
            ext = f.suffix
        else:
            issues.append(f"未知文件类型: {f.name}")
            continue
        persona_names.add(name)
        all_files.setdefault(name, set()).add(ext)

    print(f"发现 {len(persona_names)} 个音色: {sorted(persona_names)}")

    for name in sorted(persona_names):
        exts = all_files[name]
        missing = PERSONA_REQUIRED - exts
        if missing:
            issues.append(f"音色 [{name}] 缺少文件: {sorted(missing)}")
        # 检查元数据有效性
        meta_path = PERSONAS_DIR / f"{name}.metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                if not isinstance(meta, dict):
                    issues.append(f"音色 [{name}] metadata.json 不是对象")
            except json.JSONDecodeError as e:
                issues.append(f"音色 [{name}] metadata.json 解析失败: {e}")

    # 孤儿检测：有 metadata 但无 pt/wav，或有 pt 但无 metadata
    for name, exts in all_files.items():
        if ".metadata.json" in exts and ".pt" not in exts:
            issues.append(f"音色 [{name}] 有元数据但无声纹(.pt)，可能是孤儿元数据")
        if ".pt" in exts and ".metadata.json" not in exts:
            issues.append(f"音色 [{name}] 有声纹但无元数据(.metadata.json)")

    return issues


def check_i18n_keys() -> list[str]:
    """校验 5 种语言 JSON 的 key 一致性，找出缺失 key。"""
    issues = []
    if not LOCALES_DIR.exists():
        return [f"locales/ 目录不存在: {LOCALES_DIR}"]

    locale_files = sorted(LOCALES_DIR.glob("*.json"))
    if len(locale_files) < 2:
        return [f"locale 文件不足: {len(locale_files)}"]

    print(f"发现 {len(locale_files)} 个语言文件: {[f.stem for f in locale_files]}")

    all_keys: dict[str, set[str]] = {}
    for lf in locale_files:
        try:
            with open(lf, encoding="utf-8") as f:
                data = json.load(f)
            all_keys[lf.stem] = set(_flatten_keys(data))
        except json.JSONDecodeError as e:
            issues.append(f"{lf.name} 解析失败: {e}")

    if not all_keys:
        return issues

    # 以 key 最多的语言为基准
    reference = max(all_keys.values(), key=len)
    ref_lang = [k for k, v in all_keys.items() if v == reference][0]
    print(f"基准语言: {ref_lang} ({len(reference)} 个 key)")

    for lang, keys in all_keys.items():
        if lang == ref_lang:
            continue
        missing = reference - keys
        extra = keys - reference
        if missing:
            issues.append(
                f"语言 [{lang}] 缺失 {len(missing)} 个 key (相对 {ref_lang}): {sorted(missing)[:5]}{'...' if len(missing) > 5 else ''}"
            )
        if extra:
            issues.append(
                f"语言 [{lang}] 多出 {len(extra)} 个 key (相对 {ref_lang}): {sorted(extra)[:5]}{'...' if len(extra) > 5 else ''}"
            )

    # 检查所有语言 key 总数
    key_counts = {lang: len(keys) for lang, keys in all_keys.items()}
    if len(set(key_counts.values())) > 1:
        issues.append(f"各语言 key 数量不一致: {key_counts}")
    else:
        print(f"所有语言 key 数量一致: {list(key_counts.values())[0]}")

    return issues


def _flatten_keys(obj: dict, prefix: str = "") -> list[str]:
    """递归展平嵌套 JSON 的 key 路径。"""
    keys = []
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, full))
        else:
            keys.append(full)
    return keys


def main():
    print("=" * 60)
    print("P2-10: personas 四件套校验 + i18n 缺失 key 检查")
    print("=" * 60)

    all_issues = []

    print("\n[1/2] personas 校验")
    print("-" * 40)
    persona_issues = check_personas()
    all_issues.extend(persona_issues)
    if persona_issues:
        for i in persona_issues:
            print(f"  ✗ {i}")
    else:
        print("  ✓ 全部通过")

    print("\n[2/2] i18n key 校验")
    print("-" * 40)
    i18n_issues = check_i18n_keys()
    all_issues.extend(i18n_issues)
    if i18n_issues:
        for i in i18n_issues:
            print(f"  ✗ {i}")
    else:
        print("  ✓ 全部通过")

    print("\n" + "=" * 60)
    if all_issues:
        print(f"结果: 发现 {len(all_issues)} 个问题")
        sys.exit(1)
    else:
        print("结果: 全部通过 ✓")
        sys.exit(0)


if __name__ == "__main__":
    main()
