"""routes/generate/generic 路由测试。

覆盖目标模块: app/integrated_app/routes/generate/generic/clone.py

历史坑（GOTCHAS 追加）：本文件旧版三个用例都写成
``assert response.status_code in (200, 400, 403, 422)``，等于"任何结果都算过"。
端点里 ``effective_seed`` 被写在 ``return`` 之后（永不执行）、``num_steps=``
被写在同行注释之后（从未下传），成功路径必然 NameError —— 而这三个用例
全绿。"接受一切"的断言不提供任何保护。
"""

import pytest

from integrated_app.routes.generate.generic import clone as clone_mod


class TestCloneKwargsTranslation:
    """通用路由必须按当前引擎翻译参数名：底层签名是封闭的。"""

    def test_voxcpm2_gets_engine_native_names(self):
        kw = clone_mod._clone_kwargs_for_engine("voxcpm2", seed=-1, num_steps=12, guidance_scale=1.7, language="auto")
        assert kw == {"cfg_value": 1.7, "inference_timesteps": 12}
        # fn_voxcpm_clone 没有这些形参，原名透传会直接 TypeError
        assert not {"num_steps", "guidance_scale", "seed", "language"} & set(kw)

    def test_voxcpm2_ignores_unsupported_seed_with_warning(self, caplog):
        with caplog.at_level("WARNING"):
            kw = clone_mod._clone_kwargs_for_engine("voxcpm2", seed=42, num_steps=10, guidance_scale=2.0, language="")
        assert "seed" not in kw
        assert any("seed" in r.message for r in caplog.records), "静默丢弃用户显式设置的参数"

    def test_indextts_keeps_lang_and_positive_seed(self):
        kw = clone_mod._clone_kwargs_for_engine("indextts2", seed=42, num_steps=10, guidance_scale=1.2, language="中文")
        assert kw == {"lang": "中文", "seed": 42}

    def test_indextts_random_seed_not_forwarded(self):
        """seed=-1（随机）不下传：_infer_impl 只在 seed>0 时才 manual_seed。"""
        kw = clone_mod._clone_kwargs_for_engine("indextts20", seed=-1, num_steps=10, guidance_scale=1.2, language="")
        assert kw == {}

    def test_unknown_engine_sends_nothing_exotic(self, caplog):
        with caplog.at_level("WARNING"):
            kw = clone_mod._clone_kwargs_for_engine(
                "mystery-tts", seed=7, num_steps=10, guidance_scale=1.2, language="en"
            )
        assert kw == {}
        assert any("未知" in r.message for r in caplog.records)


class TestGenericCloneSuccessPath:
    """真正执行到生成闭包，证明 effective_seed / num_steps 两处缺陷已修。"""

    @staticmethod
    async def _persona_ref(_request, _name):
        return "C:/fake/ref.wav", None

    @staticmethod
    async def _fake_execute(_request, **kwargs):
        # 旧代码在这里第一次触发 _run() 就 NameError
        return kwargs["run_fn"]()

    @pytest.fixture
    def captured(self, monkeypatch, client):
        calls: dict = {}

        class _FakeEngine:
            def generate_voice_clone(self, text, **kwargs):
                calls["text"] = text
                calls.update(kwargs)
                return ("out.wav", "ok")

        monkeypatch.setattr(clone_mod, "pre_validate", lambda *a, **k: None)
        monkeypatch.setattr(clone_mod, "resolve_persona_ref", self._persona_ref)
        monkeypatch.setattr(clone_mod, "_execute_generation", self._fake_execute)
        monkeypatch.setattr(clone_mod, "get_persona_consent_state", lambda _name: "verified")
        monkeypatch.setattr(clone_mod.registry, "get_current_engine", lambda: _FakeEngine())
        monkeypatch.setattr(clone_mod.registry, "current_engine", "voxcpm2", raising=False)
        return calls

    def test_voxcpm2_path_receives_translated_kwargs(self, client, captured):
        import asyncio
        from types import SimpleNamespace

        request = SimpleNamespace(state=SimpleNamespace(request_id="r-test"))
        asyncio.run(
            clone_mod.generic_clone_endpoint(
                request=request,
                text="你好世界",
                engine="",
                prompt_text="参考音频的转写文本",
                persona_name="someone",
                has_consent=False,
                ref_audio=None,
                tempo_factor=1.0,
                voice_enhancement="false",
                target_lufs=-16.0,
                num_steps=12,
                guidance_scale=1.7,
                seed=42,
                random_seed="false",
                language="auto",
            )
        )
        assert captured["text"] == "你好世界"
        assert captured["reference_audio_path"] == "C:/fake/ref.wav"
        assert captured["instruction"] == "参考音频的转写文本"
        assert captured["cfg_value"] == 1.7
        assert captured["inference_timesteps"] == 12
        # random_seed=false + seed=42 -> effective_seed=42，但 voxcpm2 不接受 seed，必须被丢弃
        assert "seed" not in captured


class TestGenericCloneEndpoint:
    def test_missing_ref_audio(self, client):
        # 无参考音频：要么被 CSRF 门禁 403，要么进路由体返回"需要参考音频"的 HTMX 片段。
        # 关键是不得再出现 500（旧版 effective_seed NameError 的表现）。
        response = client.post(
            "/api/generate/generic/clone",
            data={"text": "你好世界"},
        )
        assert response.status_code != 500, response.text[-800:]
        if response.status_code == 403:
            pytest.skip("CSRF 门禁先拦截，未进入路由体")
        assert response.status_code in (200, 400, 422)
        if response.status_code == 200:
            assert "参考音频" in response.text

    def test_empty_text(self, client):
        response = client.post(
            "/api/generate/generic/clone",
            data={"text": ""},
        )
        assert response.status_code in (200, 400, 403, 422)

    def test_get_method_not_allowed(self, client):
        response = client.get("/api/generate/generic/clone")
        assert response.status_code in (405, 404)
