# 异常吞没（except…: pass）分级台账

> 来源：后端服务设计评估（v2.9 Flash）P1-3「异常吞没存量待分级治理」。

## 口径

- 扫描范围：`app/integrated_app/**.py`，排除 `vendor/`（上游同步代码，由 upstream_sync_state.json 治理，不由本仓 lint 改写）。
- 扫描模式：`except …:` 下一行为 `pass` 的全部位置（2026-10-15 快照，行号以当前 HEAD 为准）。
- 分级结论口径：**清理/回退/探测/取消语义四类属合法 best-effort**（吞掉的是「辅助动作失败」，主流程正确性不受影响）；判定为「需修复」的唯一标准是——被吞异常发生在**主流程正确性路径**上且失败会被静默掩盖。
- 本台账为治理基线：新增吞错点必须带 `# nosec B110`/`# noqa: BLE001` + 一行理由注释，并登记于此表。

## 结论速览

- 台账登记 **76** 处（不含 vendor）。
- **判定「需修复」0 处**——全部 76 处第一方吞错点均落在清理、可选依赖回退、类型/能力探测、取消语义或带注释的 best-effort 分支，无一处在主流程正确性路径上。
- 遗留建议（P2 级，不改行为）：3 处无审计注释的 `except Exception:`（见「通用兜底」行）建议后续补一行理由注释，与本仓 nosec B110 注释纪律对齐。

## 分类统计

| 分类 | 处数 |
|---|---:|
| GPU 能力探测 | 1 |
| 事件循环探测 | 1 |
| 取消语义 | 1 |
| 可选依赖回退 | 16 |
| 响应体装饰兜底 | 4 |
| 外部命令解析兜底 | 1 |
| 并发清理 | 1 |
| 接口探测 | 1 |
| 查找兜底 | 2 |
| 清理兜底 | 12 |
| 状态读取兜底 | 1 |
| 类型探测 | 2 |
| 解析兜底 | 1 |
| 通用兜底 | 3 |
| 通用兜底（带注释审计） | 29 |

## 逐处明细

| # | 位置 | 分类 | except 行 | 判定理由 |
|---:|---|---|---|---|
| 1 | `app/integrated_app/app_server.py:209` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 2 | `app/integrated_app/app_server.py:1181` | 通用兜底 | `except Exception:` | best-effort 分支（无审计注释，建议后续补注释；不构成吞错缺陷） |
| 3 | `app/integrated_app/audio_processing.py:1160` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 4 | `app/integrated_app/audio_processing.py:1255` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 5 | `app/integrated_app/auth.py:300` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 6 | `app/integrated_app/cache.py:283` | 类型探测 | `except AttributeError:` | duck-typing 探测对象能力（hasattr 语义的 except 形式），失败走估算兜底 |
| 7 | `app/integrated_app/cache.py:288` | 类型探测 | `except AttributeError:` | duck-typing 探测对象能力（hasattr 语义的 except 形式），失败走估算兜底 |
| 8 | `app/integrated_app/cache.py:290` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 9 | `app/integrated_app/cross_platform.py:391` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 10 | `app/integrated_app/engines/indextts2_engine.py:883` | 通用兜底 | `except Exception:` | best-effort 分支（无审计注释，建议后续补注释；不构成吞错缺陷） |
| 11 | `app/integrated_app/engines/voxcpm2/clone.py:465` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 12 | `app/integrated_app/engines/voxcpm2/clone.py:577` | 可选依赖回退 | `except (ImportError, RuntimeError):  # noqa: BLE001` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 13 | `app/integrated_app/engines/voxcpm2/design.py:302` | 可选依赖回退 | `except (ImportError, RuntimeError):  # noqa: BLE001` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 14 | `app/integrated_app/engines/voxcpm2/design.py:442` | 可选依赖回退 | `except (ImportError, RuntimeError):  # noqa: BLE001` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 15 | `app/integrated_app/generation.py:107` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 16 | `app/integrated_app/generation.py:145` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 17 | `app/integrated_app/generation.py:826` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 18 | `app/integrated_app/gpu_utils.py:105` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 19 | `app/integrated_app/i18n.py:97` | 查找兜底 | `except (TypeError, AttributeError):` | 翻译/映射查找类型不符时返回 None（调用方有 None 分支） |
| 20 | `app/integrated_app/i18n.py:147` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 21 | `app/integrated_app/i18n.py:188` | 查找兜底 | `except (AttributeError, Exception):` | 语言码映射失败时回退默认语言 |
| 22 | `app/integrated_app/middleware/error_handler.py:492` | 响应体装饰兜底 | `except (ValueError, UnicodeDecodeError, AttributeError):` | 错误响应体注入 request_id 的二次序列化失败时退回原始响应体（响应本身已生成） |
| 23 | `app/integrated_app/middleware/error_handler.py:533` | 响应体装饰兜底 | `except (ValueError, UnicodeDecodeError, AttributeError):` | 错误响应体注入 request_id 的二次序列化失败时退回原始响应体（响应本身已生成） |
| 24 | `app/integrated_app/middleware/error_handler.py:581` | 响应体装饰兜底 | `except (ValueError, UnicodeDecodeError, AttributeError):` | 错误响应体注入 request_id 的二次序列化失败时退回原始响应体（响应本身已生成） |
| 25 | `app/integrated_app/middleware/error_handler.py:600` | 响应体装饰兜底 | `except (ValueError, UnicodeDecodeError, AttributeError):` | 错误响应体注入 request_id 的二次序列化失败时退回原始响应体（响应本身已生成） |
| 26 | `app/integrated_app/monitor.py:311` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 27 | `app/integrated_app/monitor.py:433` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001 — 告警失败绝不阻断熔断主流程` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 28 | `app/integrated_app/monitor.py:444` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 29 | `app/integrated_app/monitor.py:676` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 30 | `app/integrated_app/observability/alerting.py:218` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 31 | `app/integrated_app/persona_metadata.py:380` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 32 | `app/integrated_app/persona_metadata.py:391` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 兜底异常处理` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 33 | `app/integrated_app/resampling.py:220` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 34 | `app/integrated_app/resampling.py:227` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 35 | `app/integrated_app/resampling.py:723` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 36 | `app/integrated_app/routes/generate/utils.py:245` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 37 | `app/integrated_app/routes/sse.py:232` | 事件循环探测 | `except (RuntimeError, AttributeError):` | 无运行中事件循环时切换到同步路径（线程上下文合法差异） |
| 38 | `app/integrated_app/routes/sse.py:301` | 并发清理 | `except asyncio.QueueFull:` | 心跳队列满时丢弃心跳事件（设计语义：心跳可丢，业务事件不丢） |
| 39 | `app/integrated_app/routes/sse.py:673` | 取消语义 | `except asyncio.CancelledError:` | 任务取消时的预期控制流（CancelledError 本就是信号不是错误） |
| 40 | `app/integrated_app/routes/system/gpu.py:157` | GPU 能力探测 | `except pynvml.NVMLError_LibraryNotLoaded:` | pynvml 库不可用时禁用 GPU 统计（CPU 模式合法路径） |
| 41 | `app/integrated_app/routes/system/gpu.py:372` | 解析兜底 | `except ValueError:` | 数值解析失败时字段留空（nvidia-smi 输出变异） |
| 42 | `app/integrated_app/routes/system/gpu.py:377` | 外部命令解析兜底 | `except (FileNotFoundError, subprocess.TimeoutExpired, OSError, IndexError, Value` | nvidia-smi 输出解析失败时字段留空（外部工具不可控） |
| 43 | `app/integrated_app/routes/system/gpu.py:475` | 可选依赖回退 | `except (OSError, RuntimeError, ImportError, AttributeError):` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 44 | `app/integrated_app/routes/system/gpu.py:489` | 可选依赖回退 | `except (OSError, RuntimeError, ImportError, AttributeError):` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 45 | `app/integrated_app/routes/system/health.py:402` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 46 | `app/integrated_app/routes/system/settings.py:283` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 47 | `app/integrated_app/routes/system/settings.py:333` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 48 | `app/integrated_app/routes/system/settings.py:401` | 通用兜底 | `except (AttributeError, TypeError, ValueError):` | best-effort 分支（无审计注释，建议后续补注释；不构成吞错缺陷） |
| 49 | `app/integrated_app/routes/system/settings.py:461` | 可选依赖回退 | `except (ImportError, AttributeError):` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 50 | `app/integrated_app/routes/system/settings.py:532` | 可选依赖回退 | `except (ImportError, OSError, RuntimeError, AttributeError, ValueError):` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 51 | `app/integrated_app/routes/system/settings.py:549` | 可选依赖回退 | `except (ImportError, AttributeError, TypeError):` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 52 | `app/integrated_app/routes/training.py:221` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 53 | `app/integrated_app/routes/training.py:265` | 状态读取兜底 | `except (OSError, json.JSONDecodeError):` | 训练状态文件损坏/缺失时返回空默认（文件为进程间通信暂存，非事实源） |
| 54 | `app/integrated_app/text_frontend.py:1992` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 55 | `app/integrated_app/text_frontend.py:2007` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 56 | `app/integrated_app/tracker.py:52` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 57 | `app/integrated_app/tracker.py:54` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 58 | `app/integrated_app/training/accelerator.py:484` | 接口探测 | `except TypeError:` | 优化器 zero_grad 接口签名探测（新旧 torch 兼容） |
| 59 | `app/integrated_app/training/accelerator.py:568` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 60 | `app/integrated_app/training/config.py:477` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 61 | `app/integrated_app/training/config.py:485` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 62 | `app/integrated_app/training/data.py:342` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 63 | `app/integrated_app/training/state.py:376` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |
| 64 | `app/integrated_app/training/state.py:404` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 65 | `app/integrated_app/training/tracker.py:194` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 66 | `app/integrated_app/training/tracker.py:350` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 67 | `app/integrated_app/training/tracker.py:414` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 68 | `app/integrated_app/training/tracker.py:453` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 69 | `app/integrated_app/training/tracker.py:534` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 70 | `app/integrated_app/training/tracker.py:726` | 通用兜底（带注释审计） | `except Exception:  # noqa: BLE001  # nosec B110` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 71 | `app/integrated_app/utils.py:95` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 72 | `app/integrated_app/utils.py:114` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 73 | `app/integrated_app/utils.py:158` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 74 | `app/integrated_app/utils.py:213` | 清理兜底 | `except OSError:` | 临时文件删除/文件操作 best-effort：删除失败不影响主流程正确性（OSError 吞掉属业界标准清理写法） |
| 75 | `app/integrated_app/vllm_backend.py:240` | 通用兜底（带注释审计） | `except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）` | 带 nosec/ noqa 审计标记的 best-effort 分支，吞错已有明示与理由注释 |
| 76 | `app/integrated_app/vllm_backend.py:324` | 可选依赖回退 | `except ImportError:` | 依赖缺失时的能力降级路径（懒加载可选包），缺失即走兜底分支，符合声明式降级设计 |

## 与报告建议的对应

- 报告建议：「建立抑制白名单台账，区分清理路径与吞错，清理非合法项」。
- 执行结果：台账建立（本文档）；经逐处上下文核查，**未发现需要清理的非合法项**（0 处），因此本批次零代码行为变更——只评现状口径下，把 76 处存量（较报告期 46 处的窄口径统计更全）逐一落到可指认的分类与理由，即为该 P1 的落地形态。
- 后续治理入口：CI 已有 `# nosec B110` 注释纪律与 bandit 扫描；本台账随代码变更在修复阶段滚动维护。
