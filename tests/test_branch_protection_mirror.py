"""docs/ci/apply_branch_protection.py 的判据测试（全部离线，不碰网络、不写远端）。

锁三件事，都是 2026-09-27 实测踩过的：

1. `required_pull_request_reviews` 必须来自**专用端点** —— 组合端点
   `GET .../branches/main/protection` 的响应里根本没有这个键，从组合响应读它会恒得 `{}`，
   于是「审批数」这一项永远被误报成漂移（真值比对反而做不到）。
2. 404 要与「存在但值不同」分开 —— 404 只能记「判不了」，不能记「无漂移」。
3. 摘掉必需检查必须逐字批准 —— JSON 少一条 `DCO Check` 时 `--apply` 会静默摘掉这条门禁。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "docs" / "ci" / "apply_branch_protection.py"
MIRROR = REPO_ROOT / "docs" / "ci" / "branch-protection.json"

_spec = importlib.util.spec_from_file_location("apply_branch_protection", SCRIPT)
assert _spec and _spec.loader
bpp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bpp
_spec.loader.exec_module(bpp)

LIVE_CTX = [
    "Lint (ruff)",
    "Test (pytest) (3.12, ubuntu-latest)",
    "Typecheck (mypy ratchet) / Typecheck (mypy ratchet)",
    "DCO Check",
    # 2026-10-07 起 live 第 5 条：release-gate 以 check-suite（workflow name）匹配，
    # PR 上实测 pass（#183），见 docs/ci/branch-protection.json 的 _measured_at 注。
    "release-gate",
]
REVIEWS_1 = {
    "dismiss_stale_reviews": False,
    "require_code_owner_reviews": False,
    "require_last_push_approval": False,
    "required_approving_review_count": 1,
}


def _combined(contexts=LIVE_CTX, enforce_admins=False):
    """复刻组合端点的真实形状：没有 required_pull_request_reviews 这个键。

    2026-09-28 治理变更：enforce_admins 默认 False（admin=ReSerendipity 直推豁免，
    个人账号仓库以 enforce_admins=false 实现 admin 绕过 PR，其余协作者走 PR + 1 审批）。
    """
    return {
        "required_status_checks": {"strict": False, "contexts": list(contexts)},
        "enforce_admins": {"enabled": enforce_admins},
        "required_conversation_resolution": {"enabled": False},
    }


def _want(cfg=None):
    cfg = cfg or json.loads(MIRROR.read_text(encoding="utf-8"))
    w = bpp.desired_body(cfg)
    w["_allow_auto_merge"] = bool(cfg["policy"]["allow_auto_merge"])
    return w


def _am_true(_slug):
    return 0, "true", ""


def test_json_is_a_mirror_of_live_not_an_older_wish():
    """声明镜像必须与实测 live 对齐：四条必需检查齐、审批数 1、不 dismiss 陈旧审批、enforce_admins=false（2026-09-28 治理变更：admin 直推豁免）。"""
    cfg = json.loads(MIRROR.read_text(encoding="utf-8"))
    assert cfg["contexts"] == LIVE_CTX, cfg["contexts"]
    assert "DCO Check" in cfg["contexts"], "摘掉 DCO Check 就是关掉 DCO 门禁"
    assert cfg["policy"]["required_approving_review_count"] == 1
    assert cfg["policy"]["dismiss_stale_reviews"] is False
    assert cfg["policy"]["enforce_admins"] is False


def test_annotation_keys_never_reach_the_request_body():
    """JSON 里的 _comment / _measured_at 只是口径说明，绝不能混进 PUT 请求体。"""
    body = {k: v for k, v in _want().items() if not k.startswith("_")}
    assert not [k for k in body if k.startswith("_")]
    assert set(body) >= {"required_status_checks", "required_pull_request_reviews", "enforce_admins"}


@pytest.mark.parametrize(
    "live_ctx, want_ctx, expect_add, expect_drop",
    [
        (LIVE_CTX, LIVE_CTX, [], []),
        (LIVE_CTX, LIVE_CTX[:-1], [], ["release-gate"]),
        (["Lint (ruff)"], LIVE_CTX, sorted(set(LIVE_CTX) - {"Lint (ruff)"}), []),
    ],
)
def test_context_changes_lists_adds_and_drops(live_ctx, want_ctx, expect_add, expect_drop):
    add, drop = bpp.context_changes(live_ctx, want_ctx)
    assert add == expect_add
    assert drop == expect_drop


def test_context_changes_detects_rename_as_add_plus_drop():
    """改 context 名字（例如 mypy job 改名）必须同时暴露新增与摘掉，不能读成「无变化」。"""
    renamed = [c.replace("Typecheck (mypy ratchet)", "Typecheck (mypy)") for c in LIVE_CTX]
    add, drop = bpp.context_changes(LIVE_CTX, renamed)
    assert add == ["Typecheck (mypy) / Typecheck (mypy)"]
    assert drop == ["Typecheck (mypy ratchet) / Typecheck (mypy ratchet)"]


def test_drops_confirmed_needs_verbatim_names():
    assert bpp.drops_confirmed([], None, isatty=False)[0] is True
    expected = "DCO Check"
    assert bpp.drops_confirmed([expected], expected, isatty=False)[0] is True
    assert bpp.drops_confirmed([expected], "DCO", isatty=False)[0] is False
    assert bpp.drops_confirmed([expected], None, isatty=False)[0] is False
    assert bpp.drops_confirmed([expected], None, isatty=True, typed=expected)[0] is True
    assert bpp.drops_confirmed([expected], None, isatty=True, typed="yes")[0] is False


def test_multi_drop_rejects_partial_approval():
    """只批准其中一条不算批准整批 —— 防止「确认了一个、顺手摘了另一个」。"""
    drop = ["DCO Check", "Lint (ruff)"]
    assert bpp.drops_confirmed(drop, "DCO Check", isatty=False)[0] is False
    ok, why = bpp.drops_confirmed(drop, "DCO Check|Lint (ruff)", isatty=False)
    assert ok is True
    assert "DCO Check|Lint (ruff)" in why


def test_no_review_drift_when_dedicated_endpoint_matches_declaration():
    drift, unknown = bpp.compute_drift(_combined(), (bpp.REVIEWS_OK, dict(REVIEWS_1), ""), _want(), "o/r", _am_true)
    assert drift == []
    assert unknown == []


def test_old_bug_combined_response_without_key_used_to_fake_review_drift():
    """回归锁：组合响应缺键时，只要专用端点值相符，就不许再报 review 漂移。"""
    assert "required_pull_request_reviews" not in _combined()
    drift, _ = bpp.compute_drift(_combined(), (bpp.REVIEWS_OK, dict(REVIEWS_1), ""), _want(), "o/r", _am_true)
    assert not [d for d in drift if d.startswith("review")]


@pytest.mark.parametrize(
    "patched, expected_field",
    [
        ({"required_approving_review_count": 0}, "review:required_approving_review_count"),
        ({"dismiss_stale_reviews": True}, "review:dismiss_stale_reviews"),
        ({"require_code_owner_reviews": True}, "review:require_code_owner_reviews"),
        ({"require_last_push_approval": True}, "review:require_last_push_approval"),
    ],
)
def test_each_review_field_mismatch_is_named(patched, expected_field):
    live = dict(REVIEWS_1, **patched)
    drift, unknown = bpp.compute_drift(_combined(), (bpp.REVIEWS_OK, live, ""), _want(), "o/r", _am_true)
    assert drift == [expected_field]
    assert unknown == []


def test_reviews_404_is_unreadable_not_clean():
    """404 只能进「判不了」，不能被读成「无漂移」—— [OK] 不许在这种状态下打印。"""
    drift, unknown = bpp.compute_drift(
        _combined(), (bpp.REVIEWS_MISSING, None, "gh: Required review not found (HTTP 404)"), _want(), "o/r", _am_true
    )
    assert not [d for d in drift if d.startswith("review")]
    assert len(unknown) == 1 and unknown[0].startswith("review(404")
    assert "404" in unknown[0]


def test_reviews_other_http_error_is_also_unreadable():
    drift, unknown = bpp.compute_drift(
        _combined(), (bpp.REVIEWS_ERROR, None, "gh: Server Error (HTTP 502)"), _want(), "o/r", _am_true
    )
    assert drift == []
    assert unknown[0].startswith("review(读取失败")


def test_context_drift_flagged_without_mutating_the_declaration():
    """live 少一条 → 报 contexts 漂移，并给出「将新增」而不是「将摘掉」的方向。"""
    want = _want()
    want["required_status_checks"]["contexts"] = LIVE_CTX + ["E2E (Playwright)"]
    drift, unknown = bpp.compute_drift(_combined(), (bpp.REVIEWS_OK, dict(REVIEWS_1), ""), want, "o/r", _am_true)
    assert "contexts" in drift
    assert bpp.context_changes(LIVE_CTX, want["required_status_checks"]["contexts"])[1] == []
    assert unknown == []


def test_allow_auto_merge_unreadable_is_unknown_not_drift():
    def _boom(_slug):
        return 1, "", "gh: Not Found (HTTP 404)"

    drift, unknown = bpp.compute_drift(_combined(), (bpp.REVIEWS_OK, dict(REVIEWS_1), ""), _want(), "o/r", _boom)
    assert drift == []
    assert unknown[0].startswith("allow_auto_merge(读取失败")


# --------------------------------------------------------------------------- main() 接线


class _FakeGh:
    """把 gh api 全部收进内存：既能断言「一次都没 PUT」，也能断言「该 PUT 时确实 PUT」。"""

    def __init__(self, contexts=LIVE_CTX, reviews=REVIEWS_1):
        self.calls = []
        self.contexts = list(contexts)
        self.reviews = dict(reviews)

    def __call__(self, args):
        self.calls.append(list(args))
        flat = " ".join(args)
        if args[:2] == ["-X", "PUT"] or args[:2] == ["-X", "PATCH"]:
            return 0, "{}", ""
        if "/required_pull_request_reviews" in flat:
            return 0, json.dumps(self.reviews), ""
        if flat.endswith("branches/main/protection"):
            return 0, json.dumps(_combined(self.contexts)), ""
        if "--jq" in args and ".allow_auto_merge" in flat:
            return 0, "true", ""
        raise AssertionError(f"未预期的 gh 调用: {flat}")

    def put_calls(self):
        return [c for c in self.calls if c[:2] == ["-X", "PUT"]]


def _write_stale_mirror(tmp_path, contexts):
    """造一份「比 live 少一条必需检查」的旧镜像（就是 2026-09-27 实测到的那种漂移）。"""
    cfg = json.loads(MIRROR.read_text(encoding="utf-8"))
    cfg["contexts"] = contexts
    p = tmp_path / "branch-protection.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return p


def test_main_apply_refuses_to_drop_required_check_without_approval(tmp_path, monkeypatch):
    """非交互 + 没有逐字批准 = 一次都不许 PUT。这里 PUT 真发生就会改掉仓库门禁设置。"""
    stale = _write_stale_mirror(tmp_path, LIVE_CTX[:-1])
    fake = _FakeGh()
    monkeypatch.setattr(bpp, "CFG", str(stale))
    monkeypatch.setattr(bpp, "gh", fake)
    monkeypatch.setattr(sys, "argv", ["apply_branch_protection.py", "--apply"])
    monkeypatch.setattr(bpp.sys.stdin, "isatty", lambda: False)

    rc = bpp.main()

    assert rc == 1
    assert fake.put_calls() == []


def test_main_apply_proceeds_when_drop_is_approved_verbatim(tmp_path, monkeypatch, capsys):
    """同一个场景，给了逐字批准就该放行 —— 证明上面那条拦的是「没批准」而不是永远拦死。"""
    stale = _write_stale_mirror(tmp_path, LIVE_CTX[:-1])
    fake = _FakeGh()
    monkeypatch.setattr(bpp, "CFG", str(stale))
    monkeypatch.setattr(bpp, "gh", fake)
    monkeypatch.setattr(
        sys, "argv",
        ["apply_branch_protection.py", "--apply", "--allow-drop-contexts", "release-gate"],
    )

    rc = bpp.main()

    assert rc == 1
    assert len(fake.put_calls()) == 1
    assert "摘检查确认" in capsys.readouterr().out


def test_main_readonly_reports_no_drift_when_mirror_matches_live(tmp_path, monkeypatch, capsys):
    """镜像与 live 一致时，只读模式必须打印 [OK] 无漂移 且 rc=0（DRIFT=0 的正向证据）。"""
    fake = _FakeGh()
    monkeypatch.setattr(bpp, "CFG", str(MIRROR))
    monkeypatch.setattr(bpp, "gh", fake)
    monkeypatch.setattr(sys, "argv", ["apply_branch_protection.py"])

    rc = bpp.main()

    out = capsys.readouterr().out
    assert rc == 0
    assert "[OK] 无漂移" in out
    assert fake.put_calls() == []


def test_main_readonly_prints_direction_of_context_change(tmp_path, monkeypatch, capsys):
    stale = _write_stale_mirror(tmp_path, LIVE_CTX + ["E2E (Playwright)"])
    fake = _FakeGh()
    monkeypatch.setattr(bpp, "CFG", str(stale))
    monkeypatch.setattr(bpp, "gh", fake)
    monkeypatch.setattr(sys, "argv", ["apply_branch_protection.py"])

    rc = bpp.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "将摘掉 无" in out
    assert "E2E (Playwright)" in out
