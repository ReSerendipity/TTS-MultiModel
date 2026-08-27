# 12. 典型 AI 开发场景 SOP（照着做，少踩坑）

> 本文由 2026-08-27 家族治理 E3 从 AGENTS.md §12 移出，内容逐字保留。

<!-- 📥 新SOP追加模板（AI 完成新类型任务后复制填好追加到这里）：
#### SOP-X: [场景名称]
**适用条件**：什么情况下走这个流程
**步骤**：
1. 第一步...
2. 第二步...
3. 第三步...
**验证**：怎么确认操作成功
**关联文件**：
- path/to/file1.py
- path/to/file2.py
-->

#### SOP-1: 添加新的 TTS 引擎（比如新增 XTTS v2）

> **⚠️ 2026-08-27 重大更正**：本节此前 7 步里有 5 步指向不存在的实现——
> `engines/auto_register.py`（**不存在，本仓没有任何目录扫描式自动注册**）、
> `BaseTTSProtocol`（**不存在这个名字**，真实 Protocol 是 `TTSEngine` / `ControllableTTSEngine`）、
> `configs/config.example.yaml`（**`configs/` 目录不存在**，唯一配置文件是根的 `config.yaml`）、
> `core/prompt_templates/`（**不存在**，prompt 逻辑内联在引擎包内）、
> `perf/engine-benchmark.py`（真实文件名是 `perf/generation-benchmark.py`）、
> pytest 参数 `--run-engine`（**不是本仓 marker**，真实 marker 见步骤 6）。
> 照旧步骤执行会在第 3 步就停下来找 `auto_register.py`，然后自行发明一套扫描机制。
>
> **本仓终态是多引擎，注册机制是「显式注册」而非「自动扫描」**——这是有意设计：
> 显式注册让每个引擎的导入策略（立即导入 / 懒导入）在代码里可读、可单独 try/except，
> 自动扫描会把某个引擎的 ImportError 直接升级为全站启动失败。新增引擎请沿用显式注册。

**适用条件**：需要新增一种 TTS 引擎实现，通过统一注册表与 `/api/model/*` 契约暴露

**步骤**：
1. 在 `app/integrated_app/engines/` 下新建引擎实现。两种既有形态任选：
   - 依赖多、需要分文件 → 建**包** `xttsv2/`（参照 `engines/voxcpm2/`：`engine.py` + `prompt.py` + `clone.py` + `design.py` + …）
   - 单文件可容纳 → 建模块 `xttsv2_engine.py`（参照 `engines/indextts2_engine.py` / `engines/voxcpm2_engine.py`）
2. 实现引擎类，满足 `app/integrated_app/engine_interface.py:35` 的 `TTSEngine` Protocol（**结构化子类型，不用显式继承**）；
   若需要情感/时长等可控能力，参照 `ControllableTTSEngine`（L208）。
   类标识用 `name`（Registry key，全局唯一）+ `display_name`。
3. **在 `_register_builtin_engines()`（`engine_interface.py:672`）里显式调用 `engine_registry.register(...)`**。
   按引擎重要性选导入策略（这是既有三引擎的真实分工）：

   | 策略 | 适用 | 写法 |
   |------|------|------|
   | 立即导入 + 懒导入回退 | **核心引擎**（现状：VoxCPM2） | `try: from .engines.xttsv2.engine import XTTSv2Engine; engine_registry.register("xttsv2", engine_class=XTTSv2Engine, ...) except ImportError: engine_registry.register("xttsv2", lazy_module="engines.xttsv2.engine:XTTSv2Engine", ...)` |
   | 纯懒导入 | **可选引擎**（现状：IndexTTS2） | `engine_registry.register("indextts2", lazy_module="...:IndexTTS2Engine", ...)` —— 启动期绝不 import，依赖缺失时不影响核心引擎 |
   | 注释掉注册 | **停用**（现状：dots.tts，见 Gotcha #12） | 保留注册代码但注释 + 写明原因，可逆 |

   `register()` 完整签名（L483）：
   `name, engine_class=None, display_name="", vram_requirement=6.0, lazy_module="", languages=None, supported_features=None, sample_rate=24000, requires_gpu=True, quality="high"`；
   `lazy_module` 格式必须是 `"package.module:ClassName"`。
4. 在 `model_registry.py` 的 `EngineName` 枚举（L70）加值（当前仅 `VOXCPM2="voxcpm2"` / `INDEXTTS2="indextts2"`），
   并补对应的 `_<engine>_loaded` 加载位与 `set_<engine>_loaded()` 原子更新方法；
   同步在 `config.yaml → models.engines.<key>` 加声明式配置（**不要新建 `configs/` 目录**）。
5. Prompt / 模板逻辑内联在引擎包内（参照 `engines/voxcpm2/prompt.py`），**不引入 `core/prompt_templates/` 这类新目录层级**。
6. **测试**：
   - 合规性：`pytest tests/engines/test_protocol_compliance.py -v`（L2 层，校验 Protocol 契约）
   - 新引擎用例放 `tests/engines/test_<engine>_engine.py`
   - 需要真 GPU 的用 marker 标注，**真实可用 marker**：`integration` / `benchmark` / `gpu` / `cuda` / `vram` / `smoke`
     （`pyproject.toml` 的 `markers`；**`--run-engine` 不存在**，不要臆造参数，pytest 会报 unrecognized）
   - 免 GPU 的依赖层兼容性：`python scripts/check_3engine_compat.py`（9 项检测，含 torch/transformers/numpy/pydantic 版本与各引擎可 import 性；该钩子已挂 pre-commit + pre-push，见 §10）
7. （可选）性能基准：`python perf/generation-benchmark.py`（对比既有引擎 RTF），基线产物落 `perf/results/`。

**验证**：启动服务 → `GET /api/model/status` 的 `voxcpm2_loaded` / `indextts2_loaded` 不受新引擎影响且 `loaded` 为 `true`
→ `POST /api/model/switch` 切到新引擎返回 `{"status":"ok","engine":"xttsv2"}`
→ `GET /api/persona/table` 能返回该引擎可见音色（旧文档的 `GET /api/v1/tts/voices` 端点不存在）。

**关联文件**（下例以占位符 `<new_engine>` 表示待新增引擎的注册名，实际请替换）：
- `app/integrated_app/engines/<new_engine>/engine.py`（或 `engines/<new_engine>_engine.py`）
- `app/integrated_app/engine_interface.py`（`_register_builtin_engines()` L672）
- `app/integrated_app/model_registry.py`（`EngineName` L70 / `ModelRegistry` L164）
- `config.yaml`（`models.engines.<key>`）
- `app/integrated_app/config_models.py`（声明式规格）
- `tests/engines/test_<new_engine>_engine.py`
- `scripts/check_3engine_compat.py`

#### SOP-2: 修改现有引擎的生成逻辑（比如调整 IndexTTS2 的情感向量默认值）
**适用条件**：不新增引擎，只调参数 / prompt 逻辑 / 后处理

**步骤**：
1. 改对应引擎的实现与 prompt 模块：`engines/voxcpm2/prompt.py`、`engines/voxcpm2/engine.py`、
   `engines/indextts2_engine.py`，或路由层参数默认值 `routes/generate/{voxcpm2,indextts2,generic}/`
   （**不存在 `core/prompt_templates/<engine>/*.txt`**，模板不是独立 txt 资产）
2. 跑回归：`pytest tests/engines/ -v` + `pytest tests/ -m "not gpu and not cuda and not benchmark" -v`，
   确认接口兼容（`TTSEngine` Protocol 的返回结构字段一个没少）
3. 跑性能对比：改前改后各跑一次 `python perf/generation-benchmark.py`，确认 RTF 劣化不超过 10%
4. **改了契约就要同步全部实现方**：若动了 `engine_interface.py` 的 `TTSEngine` / `ControllableTTSEngine` Protocol
   或 `SynthesisResult` 字段 → **必须同步另一现役引擎**（voxcpm2 ↔ indextts2）与通用引擎 vendor stub，
   并跑 `python scripts/check_3engine_compat.py` 确认三个实现都仍可 import
   （旧文档写"更新 `engines/base.py` 的 Protocol"——**不存在 `engines/base.py`**，Protocol 就在 `engine_interface.py`）

#### SOP-3: 添加新的 API 端点
**适用条件**：在既有 `/api/*` 前缀体系下加新路由

**步骤**：
1. 在 `app/integrated_app/routes/`（或其子包 `routes/generate/`、`routes/system/`）下新建模块。
   **文件名无任何约束**——真实发现契约是**模块级 `router` 变量**：
   `app_server.py` 的 `_discover_routes()`（L179）+ `_auto_discover_routers()`（L220）
   用 `pkgutil.iter_modules` 递归遍历 `routes` 包，凡 `hasattr(mod, "router")` 即收集并挂载。
   （旧文档要求文件名必须以 `*_router.py` 结尾且由 `auto_register` 扫描——**两条都不成立**；
   现有真实文件如 `routes/persona.py`、`routes/model.py`、`routes/system/health.py` 均无 `_router` 后缀。）
2. 文件内定义（**prefix 与 tag 都写在 `APIRouter(...)` 上，本仓未使用 `openapi_tags`，全仓零命中**）：
   ```python
   from fastapi import APIRouter, Request
   from .generate.utils import _generation_semaphores   # 模块级单例直接 import，本仓不用 Depends

   router = APIRouter(prefix="/api/xxx", tags=["xxx"])   # 变量名必须是 router！

   @router.get("/table")
   async def list_xxx(request: Request) -> dict:
       ...
   ```
   既有 prefix 只有：`/api/generate`、`/api/system`、`/api/model`、`/api/persona`、`/api/training`、`/api`（audio）；
   `pages` / `sse` / `tabs` 三个 router 无 prefix。**`/api/v1/tts/*` 前缀全仓零命中，不要新开 v1 前缀**。
3. **不允许**在路由模块里写具体业务逻辑，逻辑下沉到同层能力模块（`history_db.py`、`persona_manager.py`、
   `model_manager.py`、`gpu_backend.py` 等）。
   注意：旧文档写的 `core.services.*` 分层**不存在**，本仓是 `app/integrated_app/` 下的扁平能力模块。
4. 测试放**扁平** `tests/test_xxx_api.py`（用 `TestClient` 或 `httpx.AsyncClient` 发真实请求，
   覆盖状态码、响应字段、错误场景）。
   **`tests/api/` 目录不存在**（见 §4.1）；且本仓无 DI，不能靠 `app.dependency_overrides`，
   共享状态用 `monkeypatch.setattr` 打模块属性（见 §7）。
5. 若需 Swagger 分组说明：tag 描述只能靠 `APIRouter(tags=[...])` + 各端点的 `summary` / `description`
   自行写清（既有做法），**不要去 `create_app()` 里找 `openapi_tags` 列表加描述——那里没有**。

---

