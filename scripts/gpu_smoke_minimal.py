#!/usr/bin/env python3
"""scripts/gpu_smoke_minimal.py — TTS_MultiModel 最小可行 GPU 真机冒烟。

在 self-hosted GPU runner 上，对**真实加载**的 TTS 引擎各跑一条最短合成，
校验返回的是真实音频字节（非空、合法 WAV/RIFF），并上报显存峰值。

覆盖三个引擎（+ 一条契约 + 一条负向）：
  - voxcpm2   (OpenAI model "tts-1")         — 服务启动自动加载（TTS_AUTO_LOAD_MODEL=1），真合成
  - indextts2 (IndexTTS 2.5)                 — /api/model/switch 加载后走 /api/generate 真合成
  - indextts20（IndexTTS 2.0）               — 同上（expected_engine=indextts20）；OpenAI 口没有它的位置
  - engine_imports（第 0 步）                — 引擎推理模块导入探针，transformers 一错就地硬失败
  - openai_tts1hd_contract                   — model=tts-1-hd 必须回 400 并指明改走哪条口
    （本端点按 P0-1 不传说话人参考，而 IndexTTS 必需它；2026-09-21 首次真跑时它回的是 500）
  - version_gate_refuses_mismatch（末步）    — 加载 2.0 却声明 indextts2 必须被点名拒绝，不许静默代打

用法：
    python scripts/gpu_smoke_minimal.py \
        --base-url http://127.0.0.1:7869 \
        --output gpu_smoke_report.json

退出码：0 通过 / 非0 失败。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get(url: str, timeout: float, parse: bool = True):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw.decode("utf-8", "replace")) if parse else raw)
    except urllib.error.HTTPError as e:
        if parse:
            try:
                return e.code, json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                return e.code, None
        return e.code, None
    except Exception:
        return 0, (None if parse else b"")


def _speech(base: str, model: str, timeout: float, headers: dict | None = None):
    url = f"{base}/v1/audio/speech"
    body = {
        "model": model,
        "input": "这是一条 GPU 真机冒烟测试音频。",
        "voice": "alloy",
        "response_format": "wav",
        "speed": 1.0,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode("utf-8")


def _is_wav(raw: bytes) -> bool:
    return raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"


def _post_form(url: str, fields: dict, timeout: float, files: dict | None = None, headers: dict | None = None):
    """最小 multipart/form-data 实现，兼容 FastAPI Form / File。

    `files` 形如 ``{"ref_audio": ("name.wav", b"...", "audio/wav")}``；
    `headers` 用于带 CSRF 双提交头（`/api/generate/*` 不在豁免路径里）。
    """
    boundary = "----gpusmokeboundary"
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, blob, mime) in (files or {}).items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode()
            + blob
            + b"\r\n"
        )
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _csrf_headers(base: str) -> tuple[dict, str]:
    """GET 一次首页拿 ``csrf_token`` cookie，再按双提交约定回填 Header。

    CSRF 中间件只豁免 GET/HEAD、/docs 与 /api/sse/，`/api/generate/*` 要带票；
    浏览器里这件事由 htmx 自动完成（GOTCHAS #132 那条就是它没配对时伪装成 403）。
    """
    url = f"{base}/"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=30) as r:
            cookies = r.headers.get_all("Set-Cookie") or []
    except Exception as e:  # noqa: BLE001
        return {}, f"取 CSRF cookie 失败：{e}"
    token = ""
    for c in cookies:
        if "csrf_token=" in c:
            token = c.split("csrf_token=", 1)[1].split(";", 1)[0].strip()
    if not token:
        return {}, "响应里没有 csrf_token cookie（CSRF 中间件没签发）"
    return {"Cookie": f"csrf_token={token}", "X-CSRF-Token": token}, ""


def _status_loaded(sb: dict, engine: str) -> bool:
    """`/api/model/status` 的判据：当前引擎是 engine 且已 loaded。

    WHY 两条 key 都认：这个端点返回的是 `loaded` / `current_engine`，而 `/readyz` 返回的是
    `model_loaded`。原脚本第 3 步拿 `model_loaded` 去判 `/api/model/status`，那个 key 在
    响应里根本不存在 → 谓词恒 False，切换其实成功了也会报"not ready"。这条作业从来没在
    CI 上真跑过（gpu-smoke job 三次全 skipped），所以缺陷一直隐身。
    """
    if not isinstance(sb, dict):
        return False
    if sb.get("current_engine") != engine:
        return False
    return bool(sb.get("loaded") or sb.get("model_loaded"))


def _switch_engine(base: str, engine: str, headers: dict, timeout_s: int = 420) -> tuple[bool, str]:
    """POST /api/model/switch 并轮询 /api/model/status 直到该引擎真加载完。

    切换在服务端串行执行，可能远超客户端超时 —— 那不算失败，以 status 为准。
    """
    st, _ = _post_form(f"{base}/api/model/switch", {"engine": engine}, 30, headers=headers)
    deadline = time.time() + timeout_s
    polls = 0
    while time.time() < deadline:
        polls += 1
        st2, sb = _get(f"{base}/api/model/status", 15)
        if _status_loaded(sb, engine):
            vram = sb.get("vram_used_mb", "?")
            return True, f"loaded（{polls} 次轮询，切换 POST 客户端状态 {st}，vram={vram} MiB）"
        time.sleep(4)
    return False, f"{engine} 在 {timeout_s}s 内没到 loaded（{polls} 次轮询，切换 POST 客户端状态 {st}）"


_REF_WAV = os.path.join(ROOT, "examples", "reference_speaker.wav")


def _gen_and_check(
    base: str,
    headers: dict,
    engine_id: str,
    text: str,
    expect_mismatch: str | None = None,
) -> tuple[bool, str]:
    """POST /api/generate/indextts2 真合成，再从 data-audio-filename 回取音频验 RIFF。

    IndexTTS 2.5 与 2.0 共用这个端点，靠 `expected_engine` 区分；OpenAI 兼容口
    只有 tts-1 / tts-1-hd 两个模型名，2.0 在其中没有位置（而 tts-1-hd 按 P0-1
    压根不给说话人参考，见 openai_api.py 的 400 契约），所以真合成只能走这条形态。

    `expect_mismatch` 非空时表示这是一次**负向**请求：页面声明的引擎与实际加载不一致，
    必须被点名拒绝（无音频 + 文案含"但当前加载的是"），不许静默拿当前引擎的结果代打。
    """
    fields = {
        "text": text,
        "lang": "Auto",
        "seed": "0",
        "has_consent": "true",
        "expected_engine": expect_mismatch or engine_id,
    }
    files: dict = {}
    if os.path.exists(_REF_WAV):
        with open(_REF_WAV, "rb") as f:
            files = {"ref_audio": ("reference_speaker.wav", f.read(), "audio/wav")}
    st, html = _post_form(f"{base}/api/generate/indextts2", fields, 900, files=files, headers=headers)

    if expect_mismatch:
        refused = "data-audio-filename" not in (html or "") and "但当前加载的是" in (html or "")
        detail = f"HTTP {st}，期望被拒绝（声明 {expect_mismatch} vs 加载 {engine_id}）" + (
            "" if refused else f"，响应片段={(html or '')[:200]!r}"
        )
        return refused, detail

    m = re.search(r'data-audio-filename="([^"]+)"', html or "")
    if not m:
        return False, f"HTTP {st}，响应里没有 data-audio-filename；片段={(html or '')[:200]!r}"
    a_st, a_raw = _get(f"{base}/api/audio/{m.group(1)}", 60, parse=False)
    audio = a_raw if isinstance(a_raw, bytes) else b""
    ok = st == 200 and a_st == 200 and len(audio) > 44 and _is_wav(audio)
    return ok, f"HTTP {st} → /api/audio/{m.group(1)} HTTP {a_st} bytes={len(audio)} wav={_is_wav(audio)}"


def _probe_engine_imports() -> tuple[str, dict]:
    """用**服务所在的那个解释器**做一次引擎模块导入探针，返回 (transformers 版本, 结果表)。

    WHY：IndexTTS 的 `indextts.infer_v2` / `infer_v2_5` 在 transformers 4.57 下直接
    ImportError（4.52.1 正常，A/B 见 docs/SECURITY_DEPENDABOT_TRIAGE.md §2），但这件事过去
    只有等到有人真去点"切换引擎"才暴露；每周一次的 GPU 冒烟也只跑 voxcpm2 + indextts2，
    于是"依赖声明被改坏"可以全绿存活一周以上。这一条把它变成开机第一步的硬失败。
    """
    src = (
        "import json,sys\n"
        "out={}\n"
        "try:\n"
        "    import transformers\n"
        "    out['transformers']=transformers.__version__\n"
        "except Exception as e:\n"
        "    out['transformers']='IMPORT_FAIL '+type(e).__name__+' '+str(e)[:120]\n"
        "for m in ('indextts','indextts.infer_v2','indextts.infer_v2_5'):\n"
        "    try:\n"
        "        __import__(m)\n"
        "        out[m]='ok'\n"
        "    except Exception as e:\n"
        "        out[m]=type(e).__name__+': '+str(e)[:160]\n"
        "print(json.dumps(out))\n"
    )
    p = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=240)
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return "", {"error": f"探针没吐出 JSON：rc={p.returncode} err={p.stderr[:200]}"}
    return str(data.get("transformers", "?")), data


def _finish(report: dict, output: str) -> int:
    report["passed"] = report.get("passed", False)
    print(f"[{'PASS' if report['passed'] else 'FAIL'}] gpu smoke {'passed' if report['passed'] else 'failed'}")
    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    return 0 if report["passed"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="TTS_MultiModel GPU 真机冒烟（最小可行）")
    ap.add_argument("--base-url", default="http://127.0.0.1:7869")
    ap.add_argument("--output", default="")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    report: dict = {"base_url": base, "steps": [], "passed": False}

    def step(name: str, ok: bool, detail: str) -> bool:
        report["steps"].append({"name": name, "ok": ok, "detail": detail})
        print(f"[{'OK ' if ok else 'FAIL'}] {name}: {detail}")
        return ok

    # 0) 引擎模块导入探针（不等引擎切换，先把"依赖被改坏"这类断裂挡在最前面）
    tf_ver, probe = _probe_engine_imports()
    report["probe"] = {"transformers": tf_ver, **probe}
    if probe.get("indextts", "").startswith("ModuleNotFoundError"):
        step("engine_imports", True, f"SKIP：本机没装 indextts（transformers={tf_ver}），只报不拦")
    else:
        bad = {k: v for k, v in probe.items() if k.startswith("indextts") and v != "ok"}
        step(
            "engine_imports",
            not bad,
            f"transformers={tf_ver} indextts 模块全通"
            if not bad
            else f"transformers={tf_ver} 下引擎推理模块导入失败 {bad} —— "
            "引擎元数据要求 transformers==4.52.1 + tokenizers==0.21.0；"
            "按 pyproject 的 transformers>=4.52.1,<4.53 重装环境，别抬下界"
            "（证据见 docs/SECURITY_DEPENDABOT_TRIAGE.md §2）",
        )

    # 1) /readyz：model_loaded 闸门（服务启动时 TTS_AUTO_LOAD_MODEL=1 自动加载 voxcpm2）
    ready = False
    for _ in range(120):
        st, body = _get(f"{base}/readyz", 10, parse=True)
        if st == 200 and isinstance(body, dict) and body.get("model_loaded"):
            ready = True
            break
        time.sleep(3)
    if not step("ready", ready, "model loaded" if ready else "model not loaded within 360s"):
        return _finish(report, args.output)

    # 1.5) CSRF 双提交取票：/v1/audio/speech 与 /api/* 的 POST 都要带，浏览器里由 htmx
    #      自动注入，脚本必须自己做（不带时 middleware 一律 403 CSRF_MISSING）。
    headers, csrf_err = _csrf_headers(base)
    if not step("csrf_ticket", not csrf_err, csrf_err or "拿到 csrf_token cookie 并可回填 Header") or csrf_err:
        return _finish(report, args.output)

    # 2) voxcpm2 (tts-1)
    st, raw = _speech(base, "tts-1", 120, headers)
    ok = st == 200 and len(raw) > 44 and _is_wav(raw)
    if not step("synth_tts-1", ok, f"HTTP {st} bytes={len(raw)} wav={_is_wav(raw)}"):
        return _finish(report, args.output)

    # 3) 切换到 indextts2 并等待加载完成
    st, _ = _post_form(f"{base}/api/model/switch", {"engine": "indextts2"}, 30, headers=headers)
    switched = False
    for _ in range(200):
        st2, sb = _get(f"{base}/api/model/status", 10, parse=True)
        if _status_loaded(sb, "indextts2"):
            switched = True
            break
        time.sleep(3)
    if not step("switch_indextts2", switched, "switched + loaded" if switched else f"switch HTTP {st}, not ready"):
        return _finish(report, args.output)

    # 4) indextts2 的真实覆盖走 /api/generate/indextts2（见下），这里只钉 OpenAI 口的契约：
    #    model=tts-1-hd 必须给 **400 + 可操作说明**，不能是 500 "音频生成失败"。
    #    （2026-09-21 首次真跑发现：本端点按 P0-1 不传说话人参考，而 IndexTTS 必需它，
    #     于是这个模型口 500 是必然 —— 以前没人看见是因为这条冒烟从没在 CI 真跑过。）
    st, raw = _speech(base, "tts-1-hd", 120, headers)
    body = (raw or b"").decode("utf-8", "replace")
    contract_ok = st == 400 and "参考音频" in body and "/api/generate/indextts2" in body
    if not step(
        "openai_tts1hd_contract",
        contract_ok,
        f"HTTP {st} bytes={len(raw)} body={body[:150]!r}",
    ):
        return _finish(report, args.output)

    # 4b) indextts2 (IndexTTS 2.5) 真合成 —— 与 2.0 同一形态，验的是"能出真音频"本身
    ok25, why25 = _gen_and_check(base, headers, "indextts2", "这是一条 IndexTTS 2.5 的冒烟音频。")
    if not step("synth_indextts2", ok25, why25):
        return _finish(report, args.output)

    # 5) indextts20（IndexTTS 2.0）
    indextts_present = not probe.get("indextts", "").startswith("ModuleNotFoundError") and "error" not in probe
    if not indextts_present:
        step("synth_indextts20", True, "SKIP：本机没有 indextts，交给装了它的 runner")
        step("version_gate_refuses_mismatch", True, "SKIP：同上")
        report["passed"] = True
        return _finish(report, args.output)

    switched20, why = _switch_engine(base, "indextts20", headers, 420)
    if not step("switch_indextts20", switched20, why):
        return _finish(report, args.output)

    ok20, why20 = _gen_and_check(base, headers, "indextts20", "这是一条 IndexTTS 2.0 的冒烟音频。")
    if not step("synth_indextts20", ok20, why20):
        return _finish(report, args.output)

    # 6) 版本门负向：当前加载 2.0，页面却声明 indextts2 → 必须被点名拒绝，
    #    不许静默拿 2.0 的结果当 2.5 返回（GOTCHAS #130 那一类）。
    refused, why_neg = _gen_and_check(
        base,
        headers,
        "indextts20",
        "这一条不应该被合成。",
        expect_mismatch="indextts2",
    )
    step("version_gate_refuses_mismatch", refused, why_neg)

    report["passed"] = True
    return _finish(report, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
