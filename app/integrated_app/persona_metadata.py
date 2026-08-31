"""音色元数据管理模块。

本模块负责音色（Persona）的扩展元数据管理，主要功能包括：

**1. 结构化元数据模型（PersonaMetadata）**
    - 扩展基础 .txt 信息文件，支持标签（tags）、分类（category）、使用统计、
      收藏标记、评分、源音频信息、语言等结构化字段。
    - 提供 to_dict / from_dict 序列化方法，支持 JSON 持久化。
    - 提供 to_legacy_text / from_legacy_text 兼容旧版三行 .txt 格式。

**2. 预定义标签与分类**
    - VOICE_TAGS：按性别、年龄、风格、使用场景、情绪分类的预设标签集合。
    - VOICE_CATEGORIES：预设音色/自定义克隆/声音设计/剧本角色四大分类。

**3. 导入/导出功能（PersonaExporter）**
    - 导出：将单个音色目录打包为 .zip 文件，包含 .wav/.txt/.pt/metadata.json。
    - 导入：从 .zip 包解压音色，包含 Zip Slip 路径遍历防护。
    - 自动兼容旧版格式：导出时若无 metadata.json 则从 .txt 生成。

**4. 元数据持久化**
    - load_persona_metadata：加载元数据，优先读取 metadata.json，失败则回退旧版 .txt。
    - save_persona_metadata：同时写入 metadata.json（新版）和同名 .txt（向后兼容）。

依赖关系：
    - 被 persona_manager.py 调用：保存/删除音色时维护元数据。
    - 被 routes/api/persona.py 调用：标签查询、分类查询、导入导出接口。
"""

import contextlib
import json
import logging
import os
import zipfile
from datetime import datetime
from typing import Any

logger = logging.getLogger("tts_multimodel")


PERSONA_METADATA_VERSION = 1
"""音色元数据格式版本号，用于未来格式升级时的兼容性判断。"""


class PersonaMetadata:
    """音色/语音克隆的扩展元数据模型。

    在基础 .txt 描述文件之上，提供结构化的元数据存储，包括标签、分类、
    使用统计、创建时间等信息，支持更丰富的音色管理与检索功能。

    Attributes:
        name (str): 音色名称（唯一标识，对应 .wav/.txt/.pt 文件名前缀）。
        description (str): 音色描述文本。
        tags (list[str]): 音色标签列表，用于分类检索（如 ["女声", "少女", "甜美"]）。
        category (str): 音色分类，取值见 VOICE_CATEGORIES。
        voice_type (str): 语音类型（如 "萝莉音"、"御姐音"、"磁性男声"）。
        traits (str): 音色特征描述。
        created_at (str): 创建时间，ISO 8601 格式字符串。
        usage_count (int): 使用次数统计，用于排序与推荐。
        favorite (bool): 是否为收藏音色。
        rating (float): 用户评分，范围 0.0~5.0，自动 clamp 到合法区间。
        source_audio (str): 源音频文件路径或来源说明。
        language (str): 主要语言，默认 "zh"（中文）。
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        tags: list[str] = None,
        category: str = "",
        voice_type: str = "",
        traits: str = "",
        created_at: str = None,
        usage_count: int = 0,
        favorite: bool = False,
        rating: float = 0.0,
        source_audio: str = "",
        language: str = "zh",
    ):
        """初始化音色元数据实例。

        Args:
            name: 音色名称（必填）。
            description: 音色描述文本，默认为空字符串。
            tags: 音色标签列表，默认为空列表。
            category: 音色分类，默认为空字符串。
            voice_type: 语音类型描述，默认为空字符串。
            traits: 音色特征描述，默认为空字符串。
            created_at: 创建时间（ISO 格式），为 None 时自动使用当前时间。
            usage_count: 初始使用次数，默认为 0。
            favorite: 是否收藏，默认为 False。
            rating: 初始评分（0.0~5.0），自动限制到合法区间，默认为 0.0。
            source_audio: 源音频信息，默认为空字符串。
            language: 主要语言代码，默认为 "zh"。
        """
        self.name = name
        self.description = description
        self.tags = tags or []
        self.category = category
        self.voice_type = voice_type
        self.traits = traits
        self.created_at = created_at or datetime.now().isoformat()
        self.usage_count = usage_count
        self.favorite = favorite
        self.rating = min(5.0, max(0.0, rating))
        self.source_audio = source_audio
        self.language = language

    def to_dict(self) -> dict[str, Any]:
        """将元数据序列化为字典，用于 JSON 持久化。

        Returns:
            dict[str, Any]: 包含所有元数据字段的字典，含版本号字段。
        """
        return {
            "version": PERSONA_METADATA_VERSION,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "category": self.category,
            "voice_type": self.voice_type,
            "traits": self.traits,
            "created_at": self.created_at,
            "usage_count": self.usage_count,
            "favorite": self.favorite,
            "rating": self.rating,
            "source_audio": self.source_audio,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonaMetadata":
        """从字典反序列化元数据实例（兼容缺失字段）。

        使用 dict.get() 读取字段，缺失时使用默认值，保证向前/向后兼容性。

        Args:
            data: 元数据字典，通常从 JSON 文件加载。

        Returns:
            PersonaMetadata: 反序列化后的元数据实例。
        """
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            category=data.get("category", ""),
            voice_type=data.get("voice_type", ""),
            traits=data.get("traits", ""),
            created_at=data.get("created_at"),
            usage_count=data.get("usage_count", 0),
            favorite=data.get("favorite", False),
            rating=data.get("rating", 0.0),
            source_audio=data.get("source_audio", ""),
            language=data.get("language", "zh"),
        )

    @classmethod
    def from_legacy_text(cls, name: str, text: str) -> "PersonaMetadata":
        """从旧版三行 .txt 格式解析元数据。

        旧版格式约定：
            - 第 1 行：语音类型（如 "萝莉音"）
            - 第 2 行：描述文本
            - 第 3 行：音色特征

        Args:
            name: 音色名称。
            text: 旧版 .txt 文件内容文本。

        Returns:
            PersonaMetadata: 解析后的元数据实例，缺失行使用空字符串填充。
        """
        lines = text.strip().split("\n")
        voice_type = lines[0].strip() if len(lines) > 0 else ""
        description = lines[1].strip() if len(lines) > 1 else ""
        traits = lines[2].strip() if len(lines) > 2 else ""

        return cls(
            name=name,
            description=description,
            voice_type=voice_type,
            traits=traits,
        )

    def to_legacy_text(self) -> str:
        """将元数据转换回旧版三行 .txt 格式，用于向后兼容。

        Returns:
            str: 三行文本，依次为 voice_type、description、traits，以换行符分隔。
        """
        return f"{self.voice_type}\n{self.description}\n{self.traits}"


# 预定义音色标签：按维度分类的标签集合，用于前端标签选择器和分类检索
VOICE_TAGS = {
    "gender": ["女声", "男声", "中性"],
    "age": ["萝莉", "少女", "青年", "中年", "老年", "正太", "少年"],
    "style": ["甜美", "御姐", "温柔", "活泼", "沉稳", "磁性", "清新", "知性"],
    "use_case": ["日常对话", "广告配音", "有声书", "游戏角色", "动漫角色", "新闻播报", "旁白"],
    "mood": ["欢快", "悲伤", "愤怒", "温柔", "严肃", "神秘", "可爱"],
}

# 音色分类定义
VOICE_CATEGORIES = ["预设音色", "自定义克隆", "声音设计", "剧本角色"]


def get_all_tags() -> dict[str, list[str]]:
    """获取所有可用标签分类及其标签值。

    Returns:
        dict[str, list[str]]: 标签分类字典的副本，键为分类名，值为标签列表。
    """
    return dict(VOICE_TAGS)


def get_categories() -> list[str]:
    """获取所有可用音色分类列表。

    Returns:
        list[str]: 音色分类名称列表的副本。
    """
    return list(VOICE_CATEGORIES)


class PersonaExporter:
    """音色导入/导出器，支持将音色打包为 zip 文件或从 zip 解包。

    提供静态方法 export_persona 和 import_persona，用于音色备份、分享与迁移。
    导入时包含 Zip Slip 路径遍历防护，确保解压路径安全。
    """

    @staticmethod
    def export_persona(persona_dir: str, output_path: str) -> str:
        """将单个音色目录导出为 zip 压缩包。

        打包目录内所有文件（.wav/.txt/.pt 等），若目录中无 metadata.json，
        则自动从现有 .txt 生成元数据文件一并打包。

        Args:
            persona_dir: 待导出音色的目录路径（包含 .wav/.txt/.pt 等文件）。
            output_path: 输出 zip 文件的完整路径。

        Returns:
            str: 已创建的 zip 文件路径（与 output_path 一致）。
        """
        persona_name = os.path.basename(persona_dir)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename in os.listdir(persona_dir):
                filepath = os.path.join(persona_dir, filename)
                if os.path.isfile(filepath):
                    arcname = os.path.join(persona_name, filename)
                    zf.write(filepath, arcname)

            meta_path = os.path.join(persona_dir, f"{persona_name}.metadata.json")
            if not os.path.exists(meta_path):
                # 优先由 .txt 生成结构化元数据，再写入独立的 <name>.metadata.json
                meta = PersonaMetadata(name=persona_name)
                txt_path = os.path.join(persona_dir, f"{persona_name}.txt")
                if os.path.exists(txt_path):
                    with open(txt_path, encoding="utf-8") as f:
                        meta = PersonaMetadata.from_legacy_text(persona_name, f.read())
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)

            zf.write(meta_path, os.path.join(persona_name, f"{persona_name}.metadata.json"))

        return output_path

    @staticmethod
    def import_persona(zip_path: str, persona_dir: str) -> str:
        """从 zip 压缩包导入音色到指定目录。

        包含 Zip Slip 防护：检查每个解压条目的最终真实路径是否仍在目标目录内，
        防止恶意构造的 zip 文件通过 "../" 路径遍历覆盖系统文件。

        Args:
            zip_path: 音色 zip 文件路径。
            persona_dir: 解压目标目录（通常为 PERSONA_DIR）。

        Returns:
            str: 导入的音色名称（从 zip 内第一个条目的目录名推导）。

        Raises:
            ValueError: 检测到 Zip Slip 路径遍历攻击时抛出。
        """
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if not member or member.startswith("/") or member.startswith("\\"):
                    continue
                target_path = os.path.realpath(os.path.join(persona_dir, member))
                if not target_path.startswith(os.path.realpath(persona_dir) + os.sep):
                    raise ValueError(f"检测到 Zip Slip 路径遍历攻击: {member}")
                zf.extract(member, persona_dir)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if names:
                persona_name = names[0].split("/")[0]
                # 兼容旧版 zip 中的共享 metadata.json：重命名为 per-persona 文件
                # zip 内部布局为 <persona_name>/...，故共享文件位于子目录内
                legacy = os.path.join(persona_dir, persona_name, "metadata.json")
                if os.path.exists(legacy):
                    with contextlib.suppress(OSError):
                        os.replace(legacy, os.path.join(persona_dir, persona_name, f"{persona_name}.metadata.json"))
                return persona_name

        return os.path.splitext(os.path.basename(zip_path))[0]


def load_persona_metadata(persona_dir: str, persona_name: str) -> PersonaMetadata:
    """加载音色元数据，优先读取 <persona_name>.metadata.json，回退旧版 .txt 格式。

    存储策略（数据治理修复：此前所有音色共享同一个 ``metadata.json``，
    导致最后写入者覆盖、且根目录残留的测试数据污染所有音色）：
        - 每个音色独立存储为 ``{persona_dir}/{persona_name}.metadata.json``；
        - 兼容旧版 ``{persona_name}.txt`` 三行格式作为兜底；
        - 仍兼容极少数历史 ``{persona_dir}/metadata.json`` 单文件（迁移期）。

    加载策略：
        1. 尝试读取 ``<persona_name>.metadata.json``（新版结构化元数据）；
        2. 若其不存在或解析失败，尝试读取同名 .txt（旧版三行格式）；
        3. 若两者均失败，尝试读取旧的共享 ``metadata.json``；
        4. 若仍失败，返回仅含 name 的默认元数据对象。

    Args:
        persona_dir: 音色文件所在目录（通常为 PERSONA_DIR）。
        persona_name: 音色名称。

    Returns:
        PersonaMetadata: 加载到的元数据实例，永远不会返回 None。
    """
    legacy_shared = os.path.join(persona_dir, "metadata.json")
    meta_path = os.path.join(persona_dir, f"{persona_name}.metadata.json")

    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
                return PersonaMetadata.from_dict(data)
        except Exception as e:
            logger.warning(f"加载 {persona_name} 的 {persona_name}.metadata.json 失败: {e}")

    txt_path = os.path.join(persona_dir, f"{persona_name}.txt")
    if os.path.exists(txt_path):
        try:
            with open(txt_path, encoding="utf-8") as f:
                return PersonaMetadata.from_legacy_text(persona_name, f.read())
        except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）
            pass

    # 迁移期兜底：兼容极少数历史共享 metadata.json
    if os.path.exists(legacy_shared):
        try:
            with open(legacy_shared, encoding="utf-8") as f:
                data = json.load(f)
                # 仅当共享文件恰好描述当前音色时才采用，避免再次污染
                if data.get("name") == persona_name:
                    return PersonaMetadata.from_dict(data)
        except Exception:  # nosec B110 - 兜底异常处理
            pass

    return PersonaMetadata(name=persona_name)


def save_persona_metadata(persona_dir: str, persona_name: str, meta: PersonaMetadata):
    """保存音色元数据到 <persona_name>.metadata.json，并同步更新旧版 .txt 文件。

    双写策略保证新旧版本兼容性：新代码读取 ``<persona_name>.metadata.json``
    获取完整信息，旧代码仍可从 .txt 获取基础语音类型/描述/特征。

    Args:
        persona_dir: 音色文件所在目录（通常为 PERSONA_DIR）。
        persona_name: 音色名称，用于生成 .metadata.json / .txt 文件名。
        meta: 待保存的元数据实例。
    """
    meta_path = os.path.join(persona_dir, f"{persona_name}.metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)

    txt_path = os.path.join(persona_dir, f"{persona_name}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(meta.to_legacy_text())
