"""OpenAI 兼容 API 模块（第 13 章）

提供 /v1/audio/speech 端点，兼容 OpenAI SDK 格式，
允许用户使用 openai Python 库直接调用 TTS_MultiModel 服务。

核心组件:
    - OpenAICompatibleRouter: FastAPI APIRouter，实现 /v1/audio/speech 端点
    - TaskCancelManager: 生成任务取消管理器（线程安全 + asyncio 支持）
    - BatchGenerationManager: 批量生成管理器（并发控制 + 结果收集）
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .config import SAVE_DIR

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class BatchSpeechRequest(BaseModel):
    """批量语音合成请求模型。

    Attributes:
        texts: 要合成的文本列表
        model: 模型名称
        voice: 音色名称或描述
        response_format: 输出音频格式
        speed: 语速倍率
    """

    texts: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="要合成的文本列表（最多 100 条）",
    )
    model: str = Field(
        default="tts-1",
        description="模型名称: tts-1 (VoxCPM2) 或 tts-1-hd (IndexTTS2)",
    )
    voice: str = Field(
        default="alloy",
        description="音色名称或声音描述",
    )
    response_format: str = Field(
        default="wav",
        description="输出音频格式: wav, mp3, opus, aac",
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="语速倍率 (0.25-4.0)",
    )

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        """验证并规范化模型名称。"""
        v_lower = v.strip().lower()
        if v_lower in ("tts-1", "tts-1-hd"):
            return v_lower
        raise ValueError(f"未知模型 '{v}'，可选: tts-1, tts-1-hd")


class SpeechRequest(BaseModel):
    """OpenAI /v1/audio/speech 请求模型。

    兼容 OpenAI SDK 的 audio.speech.create() 调用格式。

    Attributes:
        model: 模型名称，映射到内部引擎
        input: 要合成的文本内容
        voice: 音色名称或描述
        response_format: 输出音频格式
        speed: 语速倍率
    """

    model: str = Field(
        default="tts-1",
        description="模型名称: tts-1 (VoxCPM2) 或 tts-1-hd (IndexTTS2)",
    )
    input: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="要合成的文本内容",
    )
    voice: str = Field(
        default="alloy",
        description="音色名称或声音描述",
    )
    response_format: str = Field(
        default="wav",
        description="输出音频格式: wav, mp3, opus, aac",
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="语速倍率 (0.25-4.0)",
    )

    @field_validator("response_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        """验证音频格式是否支持。"""
        supported = {"wav", "mp3", "opus", "aac"}
        v_lower = v.strip().lower()
        if v_lower not in supported:
            raise ValueError(f"不支持的音频格式 '{v}'，可选: {', '.join(sorted(supported))}")
        return v_lower

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        """验证并规范化模型名称。"""
        v_lower = v.strip().lower()
        if v_lower in ("tts-1", "tts-1-hd"):
            return v_lower
        raise ValueError(f"未知模型 '{v}'，可选: tts-1, tts-1-hd")


# ---------------------------------------------------------------------------
# 引擎映射
# ---------------------------------------------------------------------------

# OpenAI 模型名 -> 内部引擎名
_MODEL_ENGINE_MAP: dict[str, str] = {
    "tts-1": "voxcpm2",
    "tts-1-hd": "indextts2",
}

# OpenAI 预设音色名 -> 内部 persona 名（或空字符串表示使用默认）
_VOICE_PERSONA_MAP: dict[str, str] = {
    "alloy": "",
    "echo": "",
    "fable": "",
    "onyx": "",
    "nova": "",
    "shimmer": "",
}


# ---------------------------------------------------------------------------
# 音频格式转换辅助
# ---------------------------------------------------------------------------


def _convert_audio_format(
    wav_path: str,
    target_format: str,
) -> str:
    """将 WAV 文件转换为目标格式。

    Args:
        wav_path: 源 WAV 文件路径
        target_format: 目标格式 (wav/mp3/opus/aac)

    Returns:
        str: 转换后的文件路径（WAV 直接返回原路径）
    """
    if target_format == "wav":
        return wav_path

    try:
        from pydub import AudioSegment
    except ImportError:
        logger.warning("[OpenAI API] pydub 未安装，无法转换格式，返回原始 WAV")
        return wav_path

    try:
        audio = AudioSegment.from_wav(wav_path)
        output_dir = os.path.dirname(wav_path)
        base_name = os.path.splitext(os.path.basename(wav_path))[0]
        output_path = os.path.join(output_dir, f"{base_name}.{target_format}")

        format_kwargs: dict[str, Any] = {"format": target_format}
        if target_format == "mp3":
            format_kwargs["bitrate"] = "192k"

        audio.export(output_path, **format_kwargs)
        return output_path
    except Exception as e:
        logger.error(f"[OpenAI API] 音频格式转换失败: {e}")
        return wav_path


def _stream_file(filepath: str, chunk_size: int = 8192):
    """生成器：逐块读取文件用于流式响应。

    Args:
        filepath: 文件路径
        chunk_size: 每块字节数

    Yields:
        bytes: 文件数据块
    """
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk


# ---------------------------------------------------------------------------
# TaskCancelManager: 生成任务取消管理器
# ---------------------------------------------------------------------------


class TaskCancelManager:
    """生成任务取消管理器。

    跟踪活跃的生成任务，支持通过 task_id 取消任务。
    线程安全，同时支持 asyncio 事件循环中的取消操作。

    使用方式:
        manager = TaskCancelManager()

        # 注册任务
        task_id = manager.register()

        # 在生成循环中检查取消状态
        if manager.is_cancelled(task_id):
            break

        # 取消任务
        manager.cancel_task(task_id)

        # 任务完成后注销
        manager.unregister(task_id)
    """

    def __init__(self) -> None:
        self._active_tasks: dict[str, asyncio.Event] = {}
        self._cancelled_tasks: set[str] = set()
        self._lock = threading.RLock()

    def register(self, task_id: str | None = None) -> str:
        """注册一个新的生成任务。

        Args:
            task_id: 可选的任务 ID，不提供时自动生成 UUID

        Returns:
            str: 任务 ID
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        cancel_event = asyncio.Event()

        with self._lock:
            self._active_tasks[task_id] = cancel_event

        logger.debug(f"[TaskCancelManager] 注册任务: {task_id}")
        return task_id

    def unregister(self, task_id: str) -> bool:
        """注销一个生成任务。

        Args:
            task_id: 任务 ID

        Returns:
            bool: 是否成功注销
        """
        with self._lock:
            removed = self._active_tasks.pop(task_id, None) is not None
            self._cancelled_tasks.discard(task_id)

        if removed:
            logger.debug(f"[TaskCancelManager] 注销任务: {task_id}")
        return removed

    def cancel_task(self, task_id: str) -> bool:
        """取消一个活跃的生成任务。

        Args:
            task_id: 任务 ID

        Returns:
            bool: 是否成功取消（任务存在且未被取消时返回 True）
        """
        with self._lock:
            if task_id not in self._active_tasks:
                return False
            self._cancelled_tasks.add(task_id)
            cancel_event = self._active_tasks[task_id]

        # 设置取消事件（线程安全：Event.set() 本身是原子的）
        cancel_event.set()
        logger.info(f"[TaskCancelManager] 取消任务: {task_id}")
        return True

    def is_cancelled(self, task_id: str) -> bool:
        """检查任务是否已被取消。

        Args:
            task_id: 任务 ID

        Returns:
            bool: 任务是否已被取消
        """
        with self._lock:
            return task_id in self._cancelled_tasks

    def get_active_count(self) -> int:
        """获取当前活跃任务数量。

        Returns:
            int: 活跃任务数量
        """
        with self._lock:
            return len(self._active_tasks)

    def get_active_task_ids(self) -> list[str]:
        """获取所有活跃任务的 ID 列表。

        Returns:
            list[str]: 任务 ID 列表
        """
        with self._lock:
            return list(self._active_tasks.keys())

    def cancel_all(self) -> int:
        """取消所有活跃任务。

        Returns:
            int: 被取消的任务数量
        """
        with self._lock:
            task_ids = list(self._active_tasks.keys())
            for tid in task_ids:
                self._cancelled_tasks.add(tid)
                self._active_tasks[tid].set()

        if task_ids:
            logger.info(f"[TaskCancelManager] 批量取消 {len(task_ids)} 个任务")
        return len(task_ids)

    async def wait_for_cancel(self, task_id: str, timeout: float = 0.1) -> bool:
        """异步等待任务被取消。

        Args:
            task_id: 任务 ID
            timeout: 等待超时时间（秒）

        Returns:
            bool: 是否在超时前收到取消信号
        """
        with self._lock:
            event = self._active_tasks.get(task_id)
            if event is None:
                return True  # 任务不存在视为已取消

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


# ---------------------------------------------------------------------------
# BatchGenerationManager: 批量生成管理器
# ---------------------------------------------------------------------------


class BatchStatus(str, Enum):
    """批量生成任务状态。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # 部分成功部分失败
    CANCELLED = "cancelled"


class BatchGenerationManager:
    """批量生成管理器。

    管理多文本的批量语音合成，控制并发度，收集完成结果。

    使用方式:
        manager = BatchGenerationManager(max_concurrent=2)

        # 提交批量任务
        batch_id = await manager.submit_batch(
            texts=["你好", "世界"],
            params={"model": "tts-1", "voice": "alloy"}
        )

        # 查询状态
        status = manager.get_batch_status(batch_id)

        # 获取结果
        results = manager.get_batch_results(batch_id)
    """

    def __init__(self, max_concurrent: int = 1) -> None:
        """初始化批量生成管理器。

        Args:
            max_concurrent: 最大并发任务数，默认 1（串行，与项目单 Worker 约束一致）
        """
        self._max_concurrent = max(1, min(max_concurrent, 4))
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._batches: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._cancel_manager = TaskCancelManager()

    async def submit_batch(
        self,
        texts: list[str],
        params: dict[str, Any],
    ) -> str:
        """提交批量生成任务。

        Args:
            texts: 要合成的文本列表
            params: 生成参数（model, voice, response_format, speed 等）

        Returns:
            str: 批量任务 ID
        """
        batch_id = str(uuid.uuid4())

        # 初始化批量任务状态
        with self._lock:
            self._batches[batch_id] = {
                "status": BatchStatus.PENDING,
                "total": len(texts),
                "completed": 0,
                "failed": 0,
                "results": [None] * len(texts),
                "params": params,
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
            }

        # 启动后台任务
        asyncio.create_task(self._execute_batch(batch_id, texts, params))

        logger.info(
            f"[BatchGenerationManager] 提交批量任务: batch_id={batch_id}, "
            f"count={len(texts)}, max_concurrent={self._max_concurrent}"
        )
        return batch_id

    async def _execute_batch(
        self,
        batch_id: str,
        texts: list[str],
        params: dict[str, Any],
    ) -> None:
        """执行批量生成任务。

        Args:
            batch_id: 批量任务 ID
            texts: 文本列表
            params: 生成参数
        """
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return
            batch["status"] = BatchStatus.IN_PROGRESS
            batch["started_at"] = time.time()

        async def _process_item(index: int, text: str) -> None:
            """处理单个生成项。"""
            async with self._semaphore:
                # 检查取消状态
                if self._cancel_manager.is_cancelled(batch_id):
                    return

                task_id = self._cancel_manager.register()
                try:
                    result = await self._generate_single(text, params)

                    with self._lock:
                        batch = self._batches.get(batch_id)
                        if batch is None:
                            return
                        if result is not None:
                            batch["results"][index] = result
                            batch["completed"] += 1
                        else:
                            batch["failed"] += 1
                except Exception as e:
                    logger.error(f"[BatchGenerationManager] 项 {index} 生成失败: {e}")
                    with self._lock:
                        batch = self._batches.get(batch_id)
                        if batch is not None:
                            batch["failed"] += 1
                finally:
                    self._cancel_manager.unregister(task_id)

        # 并发执行所有项
        tasks = [_process_item(i, text) for i, text in enumerate(texts)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # 更新最终状态
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return

            if self._cancel_manager.is_cancelled(batch_id):
                batch["status"] = BatchStatus.CANCELLED
            elif batch["failed"] == 0:
                batch["status"] = BatchStatus.COMPLETED
            elif batch["completed"] == 0:
                batch["status"] = BatchStatus.FAILED
            else:
                batch["status"] = BatchStatus.PARTIAL

            batch["finished_at"] = time.time()

        logger.info(
            f"[BatchGenerationManager] 批量任务完成: batch_id={batch_id}, "
            f"status={self._batches[batch_id]['status'].value}"
        )

    async def _generate_single(
        self,
        text: str,
        params: dict[str, Any],
    ) -> str | None:
        """执行单个文本的语音合成。

        Args:
            text: 要合成的文本
            params: 生成参数

        Returns:
            str | None: 生成的音频文件路径，失败返回 None
        """
        from .model_registry import registry

        engine_name = _MODEL_ENGINE_MAP.get(params.get("model", "tts-1"), "voxcpm2")
        engine = registry.get_current_engine()

        if engine is None:
            logger.error("[BatchGenerationManager] 引擎未加载")
            return None

        try:
            loop = asyncio.get_running_loop()

            if engine_name == "indextts2" and registry.current_engine == "indextts2":
                # IndexTTS2 引擎
                infer_kwargs: dict[str, Any] = {
                    "text": text,
                    "spk_audio_prompt": params.get("ref_audio_path", ""),
                }
                voice = params.get("voice", "")
                if voice and voice not in _VOICE_PERSONA_MAP:
                    infer_kwargs["emo_text"] = voice
                    infer_kwargs["use_emo_text"] = True

                speed = params.get("speed", 1.0)
                if speed != 1.0:
                    infer_kwargs["target_duration"] = None

                result_path = await loop.run_in_executor(
                    None,
                    lambda: engine.infer(**infer_kwargs),
                )
            else:
                # VoxCPM2 引擎
                voice = params.get("voice", "")
                instruction = ""
                if voice and voice not in _VOICE_PERSONA_MAP:
                    instruction = voice

                result_path, _ = await loop.run_in_executor(
                    None,
                    lambda: engine.generate_voice_clone(
                        text=text,
                        instruction=instruction,
                        normalize=True,
                    ),
                )

            return result_path
        except Exception as e:
            logger.error(f"[BatchGenerationManager] 单项生成异常: {e}")
            return None

    def get_batch_status(self, batch_id: str) -> dict[str, Any] | None:
        """获取批量任务状态。

        Args:
            batch_id: 批量任务 ID

        Returns:
            dict[str, Any] | None: 状态信息，任务不存在返回 None
        """
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return None
            return {
                "batch_id": batch_id,
                "status": batch["status"].value if isinstance(batch["status"], BatchStatus) else batch["status"],
                "total": batch["total"],
                "completed": batch["completed"],
                "failed": batch["failed"],
                "created_at": batch["created_at"],
                "started_at": batch["started_at"],
                "finished_at": batch["finished_at"],
            }

    def get_batch_results(self, batch_id: str) -> list[dict[str, Any]] | None:
        """获取批量任务结果。

        Args:
            batch_id: 批量任务 ID

        Returns:
            list[dict[str, Any]] | None: 结果列表，每项包含 index, path, status
        """
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return None

            results = []
            for i, r in enumerate(batch["results"]):
                results.append(
                    {
                        "index": i,
                        "path": r,
                        "status": "success" if r is not None else "failed",
                    }
                )
            return results

    def cancel_batch(self, batch_id: str) -> bool:
        """取消批量任务。

        Args:
            batch_id: 批量任务 ID

        Returns:
            bool: 是否成功发起取消
        """
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return False
            if batch["status"] in (BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED):
                return False

        return self._cancel_manager.cancel_task(batch_id)

    def cleanup_batch(self, batch_id: str) -> bool:
        """清理批量任务数据。

        Args:
            batch_id: 批量任务 ID

        Returns:
            bool: 是否成功清理
        """
        with self._lock:
            removed = self._batches.pop(batch_id, None) is not None

        if removed:
            self._cancel_manager.unregister(batch_id)
            logger.debug(f"[BatchGenerationManager] 清理批量任务: {batch_id}")
        return removed

    def list_batches(self) -> list[dict[str, Any]]:
        """列出所有批量任务的状态。

        Returns:
            list[dict[str, Any]]: 批量任务状态列表
        """
        with self._lock:
            return [self.get_batch_status(bid) for bid in self._batches]


# ---------------------------------------------------------------------------
# OpenAICompatibleRouter: FastAPI APIRouter
# ---------------------------------------------------------------------------


class OpenAICompatibleRouter:
    """OpenAI 兼容 API 路由器。

    创建 FastAPI APIRouter，实现 /v1/audio/speech 端点，
    接受 OpenAI SDK 格式请求，内部映射到 TTS_MultiModel 引擎。

    模型映射:
        - tts-1 -> voxcpm2
        - tts-1-hd -> indextts2

    音色映射:
        - alloy/echo/fable/onyx/nova/shimmer -> 使用默认音色
        - 其他字符串 -> 作为声音描述或 persona 名称
    """

    def __init__(self) -> None:
        self._router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])
        self._cancel_manager = TaskCancelManager()
        self._batch_manager = BatchGenerationManager(max_concurrent=1)
        self._setup_routes()

    @property
    def router(self) -> APIRouter:
        """获取 FastAPI APIRouter 实例。"""
        return self._router

    @property
    def cancel_manager(self) -> TaskCancelManager:
        """获取任务取消管理器。"""
        return self._cancel_manager

    @property
    def batch_manager(self) -> BatchGenerationManager:
        """获取批量生成管理器。"""
        return self._batch_manager

    def _setup_routes(self) -> None:
        """注册所有路由。"""

        @self._router.post(
            "/audio/speech",
            summary="语音合成（OpenAI 兼容）",
            description="兼容 OpenAI /v1/audio/speech 端点格式",
        )
        async def create_speech(request: Request, body: SpeechRequest):
            """处理 /v1/audio/speech 请求。

            Args:
                request: FastAPI 请求对象
                body: 语音合成请求体

            Returns:
                StreamingResponse: 音频流式响应
            """
            from .model_registry import registry

            # 检查引擎是否就绪
            engine_name = _MODEL_ENGINE_MAP.get(body.model, "voxcpm2")
            if not registry.model_loaded:
                raise HTTPException(
                    status_code=503,
                    detail="模型未加载，请先加载模型",
                )

            # 如果请求的引擎与当前引擎不同，提示切换
            if registry.current_engine != engine_name:
                raise HTTPException(
                    status_code=409,
                    detail=f"当前引擎为 {registry.current_engine}，请求需要 {engine_name}。请先切换引擎。",
                )

            engine = registry.get_current_engine()
            if engine is None:
                raise HTTPException(
                    status_code=503,
                    detail="引擎实例不可用",
                )

            # 注册任务以支持取消
            task_id = self._cancel_manager.register()

            try:
                loop = asyncio.get_running_loop()

                # 执行生成
                if engine_name == "indextts2":
                    result_path = await loop.run_in_executor(
                        None,
                        lambda: self._generate_indextts2(engine, body),
                    )
                else:
                    result_path = await loop.run_in_executor(
                        None,
                        lambda: self._generate_voxcpm2(engine, body),
                    )

                if result_path is None or not os.path.exists(result_path):
                    raise HTTPException(
                        status_code=500,
                        detail="音频生成失败",
                    )

                # 格式转换
                final_path = _convert_audio_format(result_path, body.response_format)

                # 确定内容类型
                content_types = {
                    "wav": "audio/wav",
                    "mp3": "audio/mpeg",
                    "opus": "audio/opus",
                    "aac": "audio/aac",
                }
                content_type = content_types.get(body.response_format, "audio/wav")

                # 流式返回音频
                return StreamingResponse(
                    _stream_file(final_path),
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f"attachment; filename=speech.{body.response_format}",
                        "X-Task-ID": task_id,
                    },
                )

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[OpenAI API] 生成失败: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"生成失败: {str(e)}",
                ) from e
            finally:
                self._cancel_manager.unregister(task_id)

        @self._router.post(
            "/audio/speech/batch",
            summary="批量语音合成",
            description="提交批量语音合成任务",
        )
        async def create_speech_batch(
            request: Request,
            body: BatchSpeechRequest,
        ):
            """提交批量生成任务。"""
            batch_id = await self._batch_manager.submit_batch(
                texts=body.texts,
                params={
                    "model": body.model,
                    "voice": body.voice,
                    "response_format": body.response_format,
                    "speed": body.speed,
                },
            )
            return {"batch_id": batch_id, "status": "pending", "total": len(body.texts)}

        @self._router.get(
            "/audio/speech/batch/{batch_id}",
            summary="批量任务状态",
            description="查询批量语音合成任务状态",
        )
        async def get_batch_status(batch_id: str):
            """查询批量任务状态。"""
            status = self._batch_manager.get_batch_status(batch_id)
            if status is None:
                raise HTTPException(status_code=404, detail="批量任务不存在")
            return status

        @self._router.delete(
            "/audio/speech/batch/{batch_id}",
            summary="取消批量任务",
            description="取消批量语音合成任务",
        )
        async def cancel_batch(batch_id: str):
            """取消批量任务。"""
            if not self._batch_manager.cancel_batch(batch_id):
                raise HTTPException(status_code=404, detail="批量任务不存在或已完成")
            return {"batch_id": batch_id, "status": "cancelled"}

        @self._router.get(
            "/models",
            summary="可用模型列表",
            description="列出可用的 TTS 模型（OpenAI 兼容格式）",
        )
        async def list_models():
            """列出可用模型。"""
            return {
                "object": "list",
                "data": [
                    {
                        "id": "tts-1",
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "tts-multimodel",
                        "engine": "voxcpm2",
                    },
                    {
                        "id": "tts-1-hd",
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "tts-multimodel",
                        "engine": "indextts2",
                    },
                ],
            }

    @staticmethod
    def _generate_voxcpm2(engine: Any, body: SpeechRequest) -> str | None:
        """使用 VoxCPM2 引擎生成音频。

        Args:
            engine: VoxCPM2 引擎实例
            body: 请求体

        Returns:
            str | None: 音频文件路径
        """
        voice = body.voice
        instruction = ""
        if voice and voice not in _VOICE_PERSONA_MAP:
            instruction = voice

        try:
            result = engine.generate_voice_clone(
                text=body.input,
                instruction=instruction,
                normalize=True,
            )
            # VoxCPM2 generate_voice_clone 实际返回结构为
            #   ((sample_rate, wav_ndarray, filename), message)
            # 其中 filename 只是 basename，需与 SAVE_DIR 拼接成完整路径返回，
            # 否则上游 os.path.exists(result_path) 校验失败抛 500"音频生成失败"。
            if isinstance(result, tuple) and len(result) >= 1:
                inner = result[0]
                if isinstance(inner, tuple) and len(inner) >= 3:
                    path = inner[2]
                elif isinstance(inner, str):
                    path = inner
                else:
                    path = None
                if isinstance(path, str) and path:
                    full = path if os.path.isabs(path) else os.path.join(SAVE_DIR, path)
                    return full if os.path.exists(full) else None
                return None
            return result
        except Exception as e:
            logger.error(f"[OpenAI API] VoxCPM2 生成失败: {e}")
            return None

    @staticmethod
    def _generate_indextts2(engine: Any, body: SpeechRequest) -> str | None:
        """使用 IndexTTS2 引擎生成音频。

        Args:
            engine: IndexTTS2 引擎实例
            body: 请求体

        Returns:
            str | None: 音频文件路径
        """
        voice = body.voice
        infer_kwargs: dict[str, Any] = {
            "text": body.input,
            "spk_audio_prompt": "",
        }

        # 非预设音色 -> 作为情感描述
        if voice and voice not in _VOICE_PERSONA_MAP:
            infer_kwargs["emo_text"] = voice
            infer_kwargs["use_emo_text"] = True

        try:
            return engine.infer(**infer_kwargs)
        except Exception as e:
            logger.error(f"[OpenAI API] IndexTTS2 生成失败: {e}")
            return None


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

openai_router = OpenAICompatibleRouter()
"""OpenAI 兼容 API 路由器单例，供 app_server 自动发现和注册。"""
