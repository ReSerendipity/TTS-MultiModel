"""JSON 文件驱动的 i18n 国际化模块。

支持五种语言（zh-CN/zh-TW/en/ja/ko），翻译内容以 JSON 文件形式存储在
`locales/` 目录中。

两层 fallback 链保障翻译永不显示空值：
1. 用户指定语言 → 英文（en）回退 → key 本身兜底（三层保障）
2. 翻译键查找支持两种模式：扁平键直接命中（含 "." 字符的整串）
   和命名空间嵌套（namespace.sub.key 逐段下钻）。
"""

import json
import logging
import os
from typing import Any

_LOCALES_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

_LANG_FILE_MAP: dict[str, str] = {
    "en": "en.json",
    "zh-CN": "zh.json",
    "zh-Hans": "zh.json",
    "zh": "zh.json",
    "zh-TW": "zh-tw.json",
    "zh-Hant": "zh-tw.json",
    "ja": "ja.json",
    "ko": "ko.json",
}

_I18N_TRANSLATIONS: dict[str, dict[str, Any]] = {}

logger = logging.getLogger("tts_multimodel")


def _load_translations(lang: str) -> dict[str, Any] | None:
    """加载指定语言的翻译字典（带缓存）。

    使用模块级 _I18N_TRANSLATIONS 字典作为缓存；缓存命中直接返回，
    否则从 JSON 文件读取并存入缓存。

    Args:
        lang: 语言代码（如 "zh-CN"、"en"）。

    Returns:
        Optional[dict[str, Any]]: 翻译字典；语言不支持、文件不存在、
        JSON 解码或权限错误时返回 None。
    """
    if lang in _I18N_TRANSLATIONS:
        return _I18N_TRANSLATIONS[lang]
    filename = _LANG_FILE_MAP.get(lang)
    if filename is None:
        return None
    filepath = os.path.join(_LOCALES_DIR, filename)
    try:
        if not os.path.exists(filepath):
            return None
    except OSError:
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logger.warning(f"国际化文件 JSON 解码失败: {filepath}")
        return None
    except PermissionError:
        logger.error(f"无法读取国际化文件（权限不足）: {filepath}")
        return None
    except OSError:
        return None
    _I18N_TRANSLATIONS[lang] = data
    return data


def _resolve_key(translations: dict[str, Any], key: str) -> str | None:
    """在翻译字典中解析翻译键。

    先尝试扁平查找：以完整 key 作为字典键直接命中；失败后再使用
    "." 分割并逐段下钻嵌套 dict。只有最终叶子节点是 str 类型才返回，
    dict 子树不返回——防止用户误写 key 前缀，返回 dict 对象的
    __str__ 字符串（如 "{'a': 1}"）破坏 UI。

    扁平优先于嵌套的原因：扁平键允许形如 "recommended_1.0" 这种
    本身含点的字符串做 key，若先走嵌套模式会把 "1.0" 切下钻失败，
    扁平先尝试可覆盖 90% 的常见场景。

    Args:
        translations: 翻译字典。
        key: 翻译键。

    Returns:
        Optional[str]: 翻译文本字符串；未找到或类型不匹配时返回 None。
    """
    try:
        if key in translations:
            result = translations[key]
            return result if isinstance(result, str) else None
    except (TypeError, AttributeError):
        pass

    if "." in key:
        try:
            parts = key.split(".")
            if not parts:
                return None
            result: Any = translations
            for part in parts:
                if isinstance(result, dict) and part in result:
                    result = result[part]
                else:
                    return None
            return result if isinstance(result, str) else None
        except Exception:
            return None
    return None


_DEFAULT_LANG: str = "zh-CN"


def t(key: str, lang: str = _DEFAULT_LANG, default: str | None = None) -> str:
    """翻译函数，三层 fallback 链保障不显示空值。

    fallback 顺序：
    1. 指定 lang 的翻译字典 → _resolve_key
    2. 英文（en）翻译字典 → _resolve_key
    3. default 参数（若不为 None）或 key 本身作为最终兜底

    Args:
        key: 翻译键。
        lang: 目标语言代码，默认 _DEFAULT_LANG（zh-CN）。
        default: 可选的自定义兜底文本；若为 None 则兜底为 key 本身。

    Returns:
        str: 翻译结果或兜底字符串，永不返回 None。
    """
    try:
        lang_dict = _load_translations(lang)
        if lang_dict is not None:
            result = _resolve_key(lang_dict, key)
            if result is not None:
                return result
        en_dict = _load_translations("en")
        if en_dict is not None:
            result = _resolve_key(en_dict, key)
            if result is not None:
                return result
    except Exception:
        pass
    return default if default is not None else key


def get_lang(request: Any) -> str:
    """从 FastAPI Request 中检测目标语言。

    优先级：
    1. URL 查询参数 query_params?lang=
    2. Cookies 中的 lang 字段
    3. 默认 _DEFAULT_LANG（zh-CN）

    同时处理别名映射：jp→ja、kr→ko、zh→zh-CN。

    Args:
        request: FastAPI Request 对象（参数放宽为 Any 以兼容 Mock 测试）。

    Returns:
        str: 解析后的语言代码，保证在 _LANG_FILE_MAP 中存在。
    """
    try:
        lang = request.query_params.get("lang")
        if lang:
            if lang in _LANG_FILE_MAP:
                return lang
            lang_map = {"zh": "zh-CN", "zh-TW": "zh-TW", "zh-Hant": "zh-TW", "zh-tw": "zh-TW", "zhCN": "zh-CN", "zhTW": "zh-TW", "ja": "ja", "jp": "ja", "ko": "ko", "kr": "ko", "en": "en"}
            if lang in lang_map:
                return lang_map[lang]
    except (AttributeError, Exception):
        pass

    try:
        lang = request.cookies.get("lang")
        if lang:
            if lang in _LANG_FILE_MAP:
                return lang
            lang_map = {"zh": "zh-CN", "zh-TW": "zh-TW", "zh-Hant": "zh-TW", "zh-tw": "zh-TW", "zhCN": "zh-CN", "zhTW": "zh-TW", "ja": "ja", "jp": "ja", "ko": "ko", "kr": "ko", "en": "en"}
            if lang in lang_map:
                return lang_map[lang]
    except AttributeError:
        return _DEFAULT_LANG
    except Exception:
        return _DEFAULT_LANG
    return _DEFAULT_LANG


def register_i18n_filters(env: Any) -> None:
    """向 Jinja2 Environment 注册翻译过滤器。

    注册后模板中可使用 {{ "namespace.sub.key" | t }} 语法翻译文本。

    Args:
        env: Jinja2 Environment 实例（参数放宽为 Any）。
    """
    env.filters["t"] = t


class _I18NCallable:
    """I18N 全局实例的可调用包装类。

    通过 __call__ 代理到 t() 函数，使 I18N("key") 语法与 t("key") 等效。
    """

    def __call__(self, key: str, lang: str = _DEFAULT_LANG) -> str:
        """调用翻译函数。

        Args:
            key: 翻译键。
            lang: 目标语言代码。

        Returns:
            str: 翻译结果。
        """
        return t(key, lang)

    def __repr__(self) -> str:
        """返回实例的可读表示，附带英文翻译键数量。

        Returns:
            str: 形如 "I18N(keys=123)" 的字符串。
        """
        en_dict = _load_translations("en")
        return f"I18N(keys={len(en_dict) if en_dict else 0})"


I18N: _I18NCallable = _I18NCallable()


def get_i18n_json(lang: str) -> dict[str, Any]:
    """返回前端 JS 侧使用的合并翻译字典。

    合并策略：以英文（en）字典为基础，再用目标语言字典 update 覆盖。
    这样做的原因：en 是覆盖率最高的基准语言，target 中漏写的键会
    显示英文，而不是空字符串或 key 本身，保障 UI 完整性。

    Args:
        lang: 目标语言代码。

    Returns:
        dict[str, Any]: 合并后的翻译字典。
    """
    translations = _load_translations(lang)
    if translations is None:
        translations = _load_translations("zh-CN") or {}
    en_dict = _load_translations("en") or {}
    merged: dict[str, Any] = dict(en_dict)
    merged.update(translations)
    return merged
