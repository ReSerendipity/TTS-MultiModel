# 多引擎并发加载设计文档

> 本文档探讨 TTS_MultiModel 在未来支持多引擎同时加载（并发推理）的架构设计。
>
> 当前状态：单引擎串行模式（AGENTS.md §6 硬约束 4）
>
> **最后更新**：2026-08-01

---

## 1. 当前架构限制

### 单 Worker 串行

- 生成任务通过 `model_manager.py` 串行处理
- 同一时间仅一个引擎可以加载到 GPU 显存
- 引擎切换需要先卸载旧引擎再加载新引擎

### 限制原因

1. **显存竞争**：多引擎同时加载会导致 GPU OOM
2. **状态一致性**：`model_registry` 使用全局单例管理引擎状态
3. **SSE 事件流**：当前 SSE 端点假设单一生成任务

---

## 2. 并发加载设计目标

- 支持同时加载 VoxCPM2 + IndexTTS2（或 dots.tts）
- 用户无需等待引擎切换即可使用不同引擎
- 每个引擎独立维护生成队列和进度

---

## 3. 架构设计

### 3.1 多引擎注册表

```python
# model_registry.py 扩展
class MultiEngineRegistry:
    """多引擎并发注册表。"""

    _engines: dict[str, EngineInstance]  # engine_name -> instance
    _lock: threading.RLock

    def load_engine(self, name: str) -> EngineInstance:
        """加载指定引擎，不影响已加载的其他引擎。"""
        # 检查显存是否足够
        # 加载引擎
        # 注册到 _engines 字典

    def unload_engine(self, name: str) -> None:
        """卸载指定引擎。"""
        # 从 _engines 移除
        # 释放显存

    def get_engine(self, name: str) -> EngineInstance | None:
        """获取已加载的引擎实例。"""
        return self._engines.get(name)
```

### 3.2 多队列任务调度

```python
# task_queue.py 扩展
class MultiEngineTaskQueue:
    """每个引擎一个独立队列。"""

    _queues: dict[str, asyncio.Queue]  # engine_name -> queue
    _workers: dict[str, asyncio.Task]  # engine_name -> worker

    async def enqueue(self, engine: str, job: GenerationJob):
        """将任务放入对应引擎的队列。"""

    async def init_engine_queue(self, engine: str):
        """为引擎初始化独立队列和 worker。"""
```

### 3.3 显存管理

- **预检**：加载引擎前检查可用显存是否 >= 模型大小 × 1.5
- **LRU 驱逐**：显存不足时驱逐最久未使用的引擎
- **内存熔断**：总显存占用超过 90% 时拒绝新加载请求

### 3.4 SSE 事件流

- 每个引擎的生成事件在 `data` 字段中携带 `engine` 标识
- 前端通过 `engine` 字段区分不同引擎的进度

---

## 4. 实施路径

### 阶段 1：多引擎注册表
- 重构 `model_registry.py` 支持多引擎
- 保持向后兼容（`current_engine` 返回最近使用的引擎）

### 阶段 2：多队列调度
- 扩展 `task_queue.py` 支持每引擎独立队列
- SSE 事件增加 `engine` 字段

### 阶段 3：UI 适配
- 前端支持多引擎状态显示
- 生成请求指定目标引擎

---

## 5. 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 显存 OOM | 严格预检 + LRU 驱逐 |
| 状态不一致 | 每引擎独立 RLock |
| SSE 事件混淆 | 事件 data 携带 engine 字段 |
| 测试复杂度 | 引擎间隔离，独立测试 |

---

## 相关文件

| 文件 | 角色 |
|------|------|
| `model_registry.py` | 引擎状态管理（需重构） |
| `model_manager.py` | 加载/卸载逻辑（需扩展） |
| `task_queue.py` | 任务队列（需扩展为多队列） |
| `routes/sse.py` | SSE 事件流（需增加 engine 字段） |
| `gpu_utils.py` | 显存监控（无需修改） |
