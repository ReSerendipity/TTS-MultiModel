# FILEMAP.md — TTS_MultiModel 文件结构清单

> 本文件由 `scripts/update_docs.py` 维护末尾 AUTO-SYNC 标记；
> 目录结构描述以实际仓库树为准（自动生成，噪声/构建/资产大文件已过滤）。

## 顶层

| 类型 | 条目 |
|---|---|
| 目录 | `app/` |
| 目录 | `cache/` |
| 目录 | `data/` |
| 目录 | `demo/` |
| 目录 | `dist/` |
| 目录 | `docs/` |
| 目录 | `examples/` |
| 目录 | `logs/` |
| 目录 | `lora/` |
| 目录 | `model/` |
| 目录 | `outputs/` |
| 目录 | `perf/` |
| 目录 | `personas/` |
| 目录 | `prompt_cache/` |
| 目录 | `scripts/` |
| 目录 | `tests/` |
| 目录 | `torch_compile_cache/` |
| 文件 | `AGENTS.md` |
| 文件 | `CHANGELOG.md` |
| 文件 | `CONTRIBUTING.md` |
| 文件 | `Dockerfile` |
| 文件 | `LICENSE` |
| 文件 | `LOCAL_RULES.md` |
| 文件 | `NOTICE` |
| 文件 | `README.md` |
| 文件 | `SECURITY.md` |
| 文件 | `USER_AGREEMENT.md` |
| 文件 | `config.yaml` |
| 文件 | `docker-compose.yml` |
| 文件 | `install.bat` |
| 文件 | `install.sh` |
| 文件 | `pyproject.toml` |
| 文件 | `requirements-lock.txt` |
| 文件 | `requirements.txt` |
| 文件 | `start.bat` |
| 文件 | `start.sh` |

## `app/`

子目录：`ffmpeg`、`integrated_app`、`tts_multimodel.egg-info`

- `SHA256SUMS.known-good`
- `cert.pem`
- `clean_launch.py`
- `general_settings.json`
- `key.pem`
- `start_app.bat`
- `start_ui_test.py`

## `app\ffmpeg/`

## `app\integrated_app/`

子目录：`engines`、`locales`、`middleware`、`model_manager_core`、`routes`、`security`、`static`、`templates`、`tests`、`training`、`vendor`

- `__init__.py`
- `app_server.py`
- `audio_processing.py`
- `audio_watermark.py`
- `auth.py`
- `bad_case_retry.py`
- `batch_inference.py`
- `cache.py`
- `checkpoint.py`
- `cli.py`
- `config.py`
- `config_models.py`
- `cross_platform.py`
- `emotion_control.py`
- `emotion_tags.py`
- `engine_interface.py`
- `engine_ui_data.py`
- `estimator.py`
- `exceptions.py`
- `fts_tokenizer.py`
- `g2p_manager.py`
- `generation.py`
- `generation_versioning.py`
- `gpu_backend.py`
- `gpu_utils.py`
- `history_db.py`
- `i18n.py`
- `mcp_server.py`
- `mixed_precision.py`
- `model_manager.py`
- `model_optimizer.py`
- `model_registry.py`
- `monitor.py`
- `openai_api.py`
- `persona_manager.py`
- `persona_metadata.py`
- `progress.py`
- `prompt_cache.py`
- `prompt_expander.py`
- `ras_sampling.py`
- `resampling.py`
- `service_layer.py`
- `signal_handlers.py`
- `spec.py`
- `streaming_monitor.py`
- `task_queue.py`
- `text_frontend.py`
- `text_segmenter.py`
- `tracker.py`
- `training_manager.py`
- `training_utils.py`
- `utils.py`
- `vllm_backend.py`
- `voice_clone_utils.py`
- `watermark.py`

## `app\integrated_app\engines/`

子目录：`voxcpm2`

- `__init__.py`
- `indextts2_engine.py`
- `voxcpm2_engine.py`

## `app\integrated_app\locales/`

- `en.json`
- `ja.json`
- `ko.json`
- `zh-tw.json`
- `zh.json`

## `app\integrated_app\middleware/`

- `__init__.py`
- `csrf.py`
- `error_handler.py`
- `rate_limit.py`
- `request_id.py`

## `app\integrated_app\model_manager_core/`

- `__init__.py`
- `load.py`
- `state.py`
- `switch.py`
- `unload.py`

## `app\integrated_app\routes/`

子目录：`api`、`generate`、`system`、`web`

- `__init__.py`
- `audio.py`
- `model.py`
- `pages.py`
- `persona.py`
- `sse.py`
- `tabs.py`
- `training.py`

## `app\integrated_app\security/`

- `__init__.py`
- `content_safety.py`
- `integrity_check.py`
- `integrity_manifest.json`
- `integrity_selfcheck.py`

## `app\integrated_app\static/`

子目录：`css`、`fonts`、`js`

## `app\integrated_app\templates/`

子目录：`partials`、`tabs`

- `base.html`
- `download_guide.html`

## `app\integrated_app\tests/`

## `app\integrated_app\training/`

- `__init__.py`
- `accelerator.py`
- `config.py`
- `data.py`
- `packers.py`
- `state.py`
- `tracker.py`

## `app\integrated_app\vendor/`

子目录：`tn`、`voxcpm`

- `__init__.py`

## `app\tts_multimodel.egg-info/`

- `PKG-INFO`
- `SOURCES.txt`
- `dependency_links.txt`
- `entry_points.txt`
- `requires.txt`
- `top_level.txt`

## `cache/`

子目录：`huggingface`、`modelscope`、`torch`

## `cache\huggingface/`

## `cache\modelscope/`

子目录：`models`

## `cache\modelscope\models/`

子目录：`iic`

## `cache\torch/`

## `data/`

子目录：`checkpoints`

- `generation_times.json`
- `history.db-shm`
- `history.db-wal`
- `push_subscriptions.db-shm`
- `push_subscriptions.db-wal`

## `data\checkpoints/`

## `demo/`

- `README.md`
- `index.html`

## `dist/`

- `tts_multimodel-2.2.0-py3-none-any.whl`
- `tts_multimodel-2.2.0.tar.gz`

## `docs/`

子目录：`adr`、`archived-prototypes`、`installers`、`plans`、`project`、`repo-analysis`、`reports`、`research`、`ui_optimization_screenshots`

- `COMPLIANCE_CHECKLIST.md`
- `LICENSE_COMPLIANCE.md`
- `README.md`
- `SECURITY.md`
- `SHA256SUMS.models`
- `api_test_report.json`
- `api_test_report_v2.json`
- `api_test_round3.json`
- `整理记录_20260823.md`

## `docs\adr/`

- `0001-remove-gptsovits.md`
- `0002-consolidate-into-app-integrated-app.md`
- `0003-single-engine-voxcpm2-json-i18n.md`
- `README.md`

## `docs\archived-prototypes/`

- `preview_theme_selector.html`
- `prototype-compact.html`
- `prototype-dashboard.html`
- `prototype-minimal.html`
- `prototype-v2-cards.html`
- `prototype-v2-fusion.html`
- `prototype-v2-ide.html`
- `prototype-v2-taskflow.html`
- `prototype-v2-workbench.html`
- `prototype-v4-complete.html`
- `tts_multimodel_replica.html`
- `yuanbao_html_20260718_mZeher.html`

## `docs\installers/`

子目录：`VC运行库`

## `docs\installers\VC运行库/`

## `docs\plans/`

- `DEPLOYMENT.md`
- `DIALECT_SUPPORT_PLAN.md`
- `EDGE_DEPLOYMENT_PLAN.md`
- `GPTSOVITS_DOTSTTS_INTEGRATION_GUIDE.md`
- `GPU_RUNNER_SETUP.md`
- `IMPROVEMENT_GUIDEBOOK.md`
- `INDEXTTS2_INTEGRATION_GUIDE.md`
- `INSTALLATION_FALLBACKS.md`
- `MODEL_DOWNLOADS.md`
- `MODEL_DOWNLOAD_GUIDE.md`
- `MODEL_EXTENSION_GUIDE.md`
- `MODEL_TIERING_PLAN.md`
- `MULTI_SPEAKER_PLAN.md`
- `OPTIMIZATION_IMPLEMENTATION_GUIDE.md`
- `RL_ALIGNMENT_PLAN.md`
- `ROADMAP.md`
- `STAGE_E_EXECUTION_PLAN.md`
- `TASKS.md`
- `TENSORRT_INTEGRATION_PLAN.md`
- `TRAINING_GUIDE.md`
- `TRAINING_TOOLCHAIN_PLAN.md`
- `UI开发指南_README.md`
- `全功能实施指南.md`

## `docs\project/`

- `ADJUSTABLE_PARAMETERS.md`
- `ARCHITECTURE.md`
- `DEPENDENCIES_DECISIONS.md`
- `INTEGRATION_DECISIONS.md`
- `MULTI_ENGINE_DESIGN.md`
- `OPENAI_COMPATIBLE_API.md`
- `PROJECT_ARCHITECTURE.md`
- `TOGGLE_SWITCH_DESIGN.md`
- `WATERMARK_LIMITATIONS.md`

## `docs\repo-analysis/`

- `Bark_技术学习报告.md`
- `Bert-VITS2_技术学习报告.md`
- `ChatTTS_技术学习报告.md`
- `Coqui-TTS_技术学习报告.md`
- `CosyVoice_技术学习报告.md`
- `Edge-TTS_技术学习报告.md`
- `EmotiVoice_技术学习报告.md`
- `GPT-SoVITS_技术学习报告.md`
- `OpenVoice_技术学习报告.md`
- `Piper_技术学习报告.md`
- `Real-Time-Voice-Cloning_技术学习报告.md`
- `StyleTTS2_技术学习报告.md`
- `Tortoise-TTS_技术学习报告.md`
- `VALL-E_技术学习报告.md`
- `VoiceBox_技术学习报告.md`
- `VoxCPM_技术学习报告.md`
- `chatterbox_技术学习报告.md`
- `fish-speech_技术学习报告.md`
- `综合技术学习报告.md`

## `docs\reports/`

- `CI-LESSONS.md`
- `CODEC_EVALUATION.md`
- `COMMON_PARTS_ANALYSIS_REPORT_AI.md`
- `COMPARISON_REPORT_OFFICIAL_VS_CUSTOM.md`
- `DEEP_DIFF_ANALYSIS_REPORT.md`
- `ENGINE_EVALUATION.md`
- `FINAL_CONSISTENCY_REPORT.md`
- `GITHUB_REFERENCE_REPOS_ANALYSIS.md`
- `GITHUB_SECURITY_ASSESSMENT_REPORT.md`
- `ISSUE_ANALYSIS.md`
- `LOGGING_AUDIT_REPORT.md`
- `PENDING_ISSUES.md`
- `SECURITY_HARDENING_TASKS_v2.0.md`
- `STAGE_E_QUALITY_REPORT.md`
- `TECHNICAL_ANALYSIS_REPORT.md`
- `TEST_SYSTEM_AUDIT_REPORT.md`
- `UI_UX_OPTIMIZATION_IMPLEMENTATION_REPORT.md`
- `USER_BEHAVIOR_TEST_REPORT.md`
- `USER_BEHAVIOR_TEST_REPORT_v2.1.0.md`
- `UX_ISSUES.md`
- `UX_UI_COMPREHENSIVE_EVALUATION_REPORT.md`
- `UX_UI_EVALUATION_REPORT.md`
- `comprehensive_report.md`
- `interactive_report.md`
- `multi_page_style_report.md`
- `report.md`
- `responsive_report.md`
- `style_compare_report.md`
- `test_report_v2.0.6.md`
- `功能实现状态分析报告.md`
- `项目健康度评估报告.md`

## `docs\research/`

- `voxcpm_comparison_analysis.md`
- `voxcpm_github_report.md`
- `voxcpm_huggingface_report.md`
- `voxcpm_official_website_report.md`

## `docs\ui_optimization_screenshots/`

## `examples/`

- `README.md`
- `api_example.py`
- `batch_clone_all_personas.py`
- `batch_example.py`
- `clone_example.py`
- `train_data_example.jsonl`

## `logs/`

## `lora/`

## `model/`

子目录：`GPT-SoVITS`、`IndexTTS2`、`SenseVoiceSmall`、`VoxCPM2`、`speech_zipenhancer`

## `model\GPT-SoVITS/`

子目录：`chinese-hubert-base`、`chinese-roberta-wwm-ext-large`、`gsv-v2final-pretrained`、`gsv-v4-pretrained`、`models--nvidia--bigvgan_v2_24khz_100band_256x`、`sv`、`v2Pro`

- `README.md`
- `configuration.json`
- `hifigan_config.json`
- `hifigan_do_03357000`

## `model\GPT-SoVITS\chinese-hubert-base/`

- `config.json`
- `preprocessor_config.json`

## `model\GPT-SoVITS\chinese-roberta-wwm-ext-large/`

- `config.json`
- `tokenizer.json`

## `model\GPT-SoVITS\gsv-v2final-pretrained/`

## `model\GPT-SoVITS\gsv-v4-pretrained/`

## `model\GPT-SoVITS\models--nvidia--bigvgan_v2_24khz_100band_256x/`

- `config.json`

## `model\GPT-SoVITS\sv/`

## `model\GPT-SoVITS\v2Pro/`

## `model\IndexTTS2/`

## `model\SenseVoiceSmall/`

子目录：`example`、`fig`

- `README.md`
- `am.mvn`
- `chn_jpn_yue_eng_ko_spectok.bpe.model`
- `config.yaml`
- `configuration.json`
- `tokens.json`

## `model\SenseVoiceSmall\example/`

## `model\SenseVoiceSmall\fig/`

## `model\VoxCPM2/`

- `README.md`
- `config.json`
- `special_tokens_map.json`
- `tokenization_voxcpm2.py`
- `tokenizer.json`
- `tokenizer_config.json`

## `model\speech_zipenhancer/`

子目录：`description`、`examples`

- `README.md`
- `configuration.json`

## `model\speech_zipenhancer\description/`

## `model\speech_zipenhancer\examples/`

## `outputs/`

- `desktop.ini`
- `history.db.migrated_1785477740`

## `perf/`

子目录：`results`

- `README.md`
- `__init__.py`
- `cold-start.py`
- `generation-benchmark.py`
- `monitoring_plan.md`
- `report_generator.py`
- `stress-test.py`
- `vram-usage.py`

## `perf\results/`

- `report.html`

## `personas/`

- `README.md`
- `gf1.txt`
- `南宫婉.txt`
- `小林.txt`
- `御姐.txt`
- `旁白.txt`
- `李老师.txt`
- `韩立.txt`

## `prompt_cache/`

## `scripts/`

子目录：`git-hooks`、`pynini_src`

- `benchmark_history_db.py`
- `capture-screenshots.bat`
- `check_3engine_compat.py`
- `check_local.py`
- `check_model_paths.py`
- `check_spec_refs.py`
- `cleanup_cache.bat`
- `cleanup_persona_metadata.py`
- `cleanup_reference_repos.bat`
- `download_indextts2.py`
- `generate_integrity_manifest.py`
- `init_watermark_key.py`
- `install-hooks.ps1`
- `perf_monitor.py`
- `render_pages.py`
- `stage_e_quality_gate.bat`
- `sync_requirements.py`
- `test_sse.py`
- `train_voxcpm_finetune.py`
- `verify_model_checksums.py`
- `verify_model_weights.py`
- `verify_persona_pt_origin.py`
- `verify_ui_optimizations.py`
- `verify_watermark.py`

## `scripts\git-hooks/`

## `scripts\pynini_src/`

子目录：`pynini-2.1.7`

- `pynini-2.1.7.tar.gz`

## `scripts\pynini_src\pynini-2.1.7/`

子目录：`bazel`、`extensions`、`pynini`、`pynini.egg-info`、`pywrapfst`、`scripts`、`tests`、`third_party`

- `AUTHORS`
- `BUILD.bazel`
- `CONTRIBUTING`
- `LICENSE`
- `MANIFEST.in`
- `NEWS`
- `PKG-INFO`
- `README.md`
- `WORKSPACE.bazel`
- `pyproject.toml`
- `requirements.txt`
- `setup.cfg`
- `setup.py`

## `tests/`

子目录：`benchmarks`、`e2e`、`engines`、`frontend`、`integration`、`training`

- `__init__.py`
- `capture-screenshots.js`
- `conftest.py`
- `package-lock.json`
- `package.json`
- `test_api_contract.py`
- `test_app.py`
- `test_app_server.py`
- `test_audio_processing.py`
- `test_audio_processing_ext.py`
- `test_audio_routes_helpers.py`
- `test_audio_watermark.py`
- `test_auth.py`
- `test_auth_integration.py`
- `test_bad_case_retry.py`
- `test_batch_inference.py`
- `test_bin_integration.py`
- `test_bin_system_enhancements.py`
- `test_cache.py`
- `test_cache_ext.py`
- `test_checkpoint.py`
- `test_checkpoint_resume.py`
- `test_cli.py`
- `test_config.py`
- `test_config_models.py`
- `test_content_safety.py`
- `test_coverage_boost.py`
- `test_coverage_boost_ext.py`
- `test_cross_platform.py`
- `test_csrf_integration.py`
- `test_emotion_control.py`
- `test_emotion_tags.py`
- `test_engine_interface.py`
- `test_engine_registry.py`
- `test_engine_registry_ext.py`
- `test_engine_switch.py`
- `test_engine_ui_data.py`
- `test_error_handler_ext.py`
- `test_estimator.py`
- `test_exceptions.py`
- `test_fts_tokenizer.py`
- `test_g2p_manager.py`
- `test_g2p_processor.py`
- `test_generate_utils.py`
- `test_generation.py`
- `test_generation_ext.py`
- `test_generation_versioning.py`
- `test_generic_clone_routes.py`
- `test_gpu_utilization_routes.py`
- `test_gpu_utils.py`
- `test_gpu_utils_ext.py`
- `test_hard_constraints.py`
- `test_history_db.py`
- `test_i18n.py`
- `test_i18n_ext.py`
- `test_indextts2_interface.py`
- `test_logs_ext.py`
- `test_mcp_server.py`
- `test_middleware_ext.py`
- `test_mixed_precision.py`
- `test_model_registry_ext.py`
- `test_monitor_ext.py`
- `test_openai_api.py`
- `test_page_switch.py`
- `test_path_traversal.py`
- `test_persona_manager_ext.py`
- `test_persona_metadata_ext.py`
- `test_progress.py`
- `test_progress_ext.py`
- `test_prompt_cache.py`
- `test_prompt_cache_ext.py`
- `test_prompt_expander.py`
- `test_ras_sampling.py`
- `test_resampling.py`
- `test_resume_handler.py`
- `test_routes_htmx.py`
- `test_routes_light.py`
- `test_security.py`
- `test_security_expanded.py`
- `test_service_layer.py`
- `test_service_layer_signal_taskqueue.py`
- `test_settings_helpers.py`
- `test_signal_handlers.py`
- `test_smoke.py`
- `test_spec.py`
- `test_sse.py`
- `test_sse_bus.py`
- `test_streaming_monitor.py`
- `test_system_routes.py`
- `test_task_queue.py`
- `test_text_frontend.py`
- `test_text_segmenter.py`
- `test_tracker.py`
- `test_training_routes.py`
- `test_utils.py`
- `test_utils_helpers.py`
- `test_verification_ci.py`
- `test_vllm_backend.py`
- `test_voice_clone_utils.py`
- `test_voxcpm2_base.py`
- `test_voxcpm2_decorators.py`
- `test_voxcpm2_routes.py`
- `test_watermark.py`

## `tests\benchmarks/`

- `__init__.py`
- `test_generation_bench.py`
- `test_load_stress.py`

## `tests\e2e/`

- `__init__.py`
- `test_mock_engine_flow.py`
- `test_screenshot_capture.py`
- `test_screenshot_capture_extended.py`
- `test_visual_regression.py`

## `tests\engines/`

- `__init__.py`
- `test_protocol_compliance.py`

## `tests\frontend/`

子目录：`_rendered`

- `smoke.js`

## `tests\frontend\_rendered/`

- `download_guide.html`

## `tests\integration/`

- `__init__.py`
- `test_engine_switch_vram.py`
- `test_offline_integration_ext.py`
- `test_pipeline_offline.py`
- `test_real_inference_smoke.py`
- `test_service_layer_core.py`
- `test_text_pipeline_integration.py`
- `test_vram_switch.py`

## `tests\training/`

- `__init__.py`
- `test_data.py`
- `test_packers.py`

## `torch_compile_cache/`

<!-- AUTO-SYNC 2026-08-27 15:16 : +2 ~4 -0 -->
