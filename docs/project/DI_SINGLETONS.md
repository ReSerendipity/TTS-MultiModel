# 7. 依赖注入 & 单例获取方式清单

> 本文由 2026-08-27 家族治理 E3 从 AGENTS.md §7 移出，内容逐字保留。

> **⚠️ 2026-08-27 重大更正**：本节此前规定"所有跨层访问必须通过 FastAPI `Depends` 或 `get_xxx()`
> 工厂，禁止直接从模块 import 全局变量实例"，并列出了 `get_settings` / `get_engine_registry` /
> `get_scheduler` / `get_db_pool` / `get_synthesis_service` / `get_history_service` 六个工厂。
> **实测：`Depends(` 在全仓 `app/integrated_app/` 下出现 0 次，上述六个函数有五个不存在**
> （仅 `get_history_db()` 真实存在）。也就是说，旧规则与本仓实际架构方向相反——
> 项目实际采用的正是"模块级单例 + 直接 import"。照旧规则编码会找不到工厂函数，
> 并自行发明一套 DI，与既有 55 个顶层模块的风格冲突。
> 以下按实际代码重写。**若未来真的要引入 DI，请先写一条 ADR 再改本节。**

| 共享状态 | 真实获取方式 | 定义位置 | 作用域 |
|------|--------------------------------|--------|--------|
| 引擎注册表（**能力声明**） | `from .engine_interface import engine_registry` | `app/integrated_app/engine_interface.py:669`<br>`engine_registry: InMemoryEngineRegistry = InMemoryEngineRegistry()` | 模块级单例，`_register_builtin_engines()` 于导入时填充 |
| 模型注册表（**运行时加载态**） | `from .model_registry import registry` | `app/integrated_app/model_registry.py`（`class ModelRegistry` L164） | 模块级单例 + `RLock`；持 `voxcpm_model` / `indextts2_engine` / `current_engine`，批量原子更新走 `set_voxcpm_loaded()` / `set_indextts2_loaded()` |
| 引擎声明式规格 | `app/integrated_app/config_models.py`（与上述注册表协作） | `config_models.py` | 只读声明源 |
| 配置项 | 函数式访问器：`get_project_root()` / `get_pretrained_dir()` / `get_voxcpm2_model_path()` / `get_voxcpm2_asr_path()` / `get_voxcpm2_denoiser_path()` / `get_indextts2_model_path()` | `app/integrated_app/config.py` L94-L169 | 每次调用读取；底层为 YAML + Pydantic 双重加载 |
| 历史库 | `get_history_db() -> HistoryDatabase`；建库 `create_history_db(output_dir)`；释放 `close_all_connections()` | `app/integrated_app/history_db.py` L1984 / L2009 / L2024 | 连接按路径缓存（标准库 sqlite3，`check_same_thread=False`） |
| 推理串行 | `_generation_semaphores`（per-engine `asyncio.Semaphore`，默认容量 1），经 `_execute_generation()` 取用 | `app/integrated_app/routes/generate/utils.py` | 进程内字典，按引擎 key 分桶 |
| 权重完整性 | `integrity_check.py` / `integrity_selfcheck.py` + 清单 `security/integrity_manifest.json` | `app/integrated_app/security/` | 只读 |

**测试中替换共享状态的正确姿势**（因无 DI，不能依赖 `app.dependency_overrides`）：
```python
# 引擎注册表：monkeypatch 模块属性，而不是覆盖 FastAPI 依赖
monkeypatch.setattr("integrated_app.engine_interface.engine_registry", FakeRegistry())

# 历史库：monkeypatch 工厂函数本身
monkeypatch.setattr("integrated_app.history_db.get_history_db", lambda: fake_db)

# 配置路径：patch 访问器
monkeypatch.setattr("integrated_app.config.get_voxcpm2_model_path", lambda: str(tmp_path))
```

> 新增共享状态时，请沿用「模块级单例 + `get_xxx()` 访问器」的既有约定，不要混用 `Depends`，
> 也不要在函数内部重复 `import` 后即时构造实例。

---

