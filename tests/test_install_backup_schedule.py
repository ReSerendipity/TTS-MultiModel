"""scripts/install_backup_schedule.py 的 dry-run 输出契约与幂等判据测试（阶段 9）。

**不触碰系统**：所有 schtasks 调用都通过注入 ``runner`` 的 fake 函数完成断言，
本测试从不调用真实 ``subprocess``、不读写任务计划程序、不落任何临时系统文件。
"""

import datetime as dt
import os
import sys

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import install_backup_schedule as ibs  # noqa: E402


class FakeRunner:
    """记录 argv 调用的假 subprocess.run。rc_for 决定返回码（默认 1=任务不存在）。"""

    def __init__(self, rc_for_query: int = 1, raise_oserror: bool = False):
        self.calls: list[list[str]] = []
        self.rc_for_query = rc_for_query
        self.raise_oserror = raise_oserror

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))

        class _R:
            def __init__(self, rc):
                self.returncode = rc
                self.stdout = "fake stdout"
                self.stderr = ""

        if self.raise_oserror:
            raise OSError("schtasks unavailable")
        return _R(self.rc_for_query if "/query" in argv else 0)


def _fixed_now() -> dt.datetime:
    return dt.datetime(2026, 11, 2, 11, 30, 0)


# ---------------------------------------------------------------- 下一次应执行时刻
def test_next_run_before_slot_is_today():
    nxt = ibs.compute_next_run("03:00", _fixed_now())
    assert nxt == dt.datetime(2026, 11, 3, 3, 0, 0)  # 今天 03:00 已过 → 明天


def test_next_run_after_midnight_is_today():
    now = dt.datetime(2026, 11, 2, 1, 0, 0)
    assert ibs.compute_next_run("03:00", now) == dt.datetime(2026, 11, 2, 3, 0, 0)


def test_next_run_exactly_at_slot_rolls_to_tomorrow():
    now = dt.datetime(2026, 11, 2, 3, 0, 0)
    assert ibs.compute_next_run("03:00", now) == dt.datetime(2026, 11, 3, 3, 0, 0)


# ---------------------------------------------------------------- 注册计划契约
def test_build_plan_contains_required_contract_fields():
    plan = ibs.build_plan(now=_fixed_now())
    for key in ("task_name", "frequency", "trigger", "command", "working_dir",
                "identity", "next_run", "query_argv", "create_argv"):
        assert key in plan and plan[key], key
    # 命令行 = §3.3 文档推荐入口 + venv 解释器绝对路径 + keep 透传
    assert plan["command"] == (
        f'"{ibs.VENV_PY}" "{ibs.BACKUP_SCRIPT}" --keep {ibs.DEFAULT_KEEP}'
    )
    # 频率与 §3.3「每日全量备份」节奏句自洽
    assert "每日" in plan["frequency"] and "§3.3" in plan["frequency"]
    assert plan["next_run"] == "2026-11-03 03:00:00"


def test_create_argv_is_idempotent_overwrite():
    argv = ibs.build_plan(now=_fixed_now())["create_argv"]
    assert argv[0] == "schtasks" and "/create" in argv
    assert "/f" in argv, "幂等判据：注册必须带 /f（同名任务就地更新而非重复新建）"
    assert "/sc" in argv and "daily" in argv
    assert argv[argv.index("/tn") + 1] == ibs.DEFAULT_TASK_NAME


# ---------------------------------------------------------------- dry-run 输出契约
def test_dry_run_prints_full_contract_and_does_not_write(capsys):
    fake = FakeRunner(rc_for_query=1)
    rc = ibs.main(["--dry-run"], runner=fake)
    out = capsys.readouterr().out
    assert rc == 0
    for token in ("任务名", "频率", "触发时刻", "完整命令行", "工作目录",
                  "运行身份", "下一次应执行", "TTS-MultiModel-Backup-Daily"):
        assert token in out, token
    assert "未做任何系统写入" in out and "未调用 schtasks /create" in out
    # 探测只允许只读 /query；任何 /create /change /delete 都不许出现在 dry-run
    assert all("/create" not in c and "/change" not in c and "/delete" not in c
               for c in fake.calls), fake.calls


def test_dry_run_output_is_deterministic_for_same_inputs(capsys):
    # 幂等判据的纯函数层：固定 now 构造计划，两次渲染必须逐字节一致
    # （main() 层的 next_run 依赖真实时钟，跨分钟边界时输出允许不同，不作断言）
    p = ibs.build_plan(now=_fixed_now())
    r1 = ibs.render_dry_run(p, False)
    r2 = ibs.render_dry_run(p, False)
    assert r1 == r2
    r3 = ibs.render_dry_run(p, True)
    assert r3 != r1  # 探测态差异体现在「就地更新」话术，而非新增任务话术


def test_dry_run_idempotency_statement_when_task_exists(capsys):
    fake = FakeRunner(rc_for_query=0)  # /query rc=0 → 同名任务已存在
    assert ibs.main(["--dry-run"], runner=fake) == 0
    out = capsys.readouterr().out
    assert "已存在" in out and "就地更新" in out and "不会产生第二个" in out


def test_dry_run_survives_schtasks_absent(capsys):
    fake = FakeRunner(raise_oserror=True)
    assert ibs.main(["--dry-run"], runner=fake) == 0
    out = capsys.readouterr().out
    assert "未探测" in out


def test_rejects_bad_trigger(capsys):
    assert ibs.main(["--dry-run", "--trigger", "99:99"], runner=FakeRunner()) == 2


# ---------------------------------------------------------------- --apply 只走 /f 注册
def test_apply_single_call_with_force_never_query_then_create_twice(capsys):
    fake = FakeRunner()
    rc = ibs.main(["--apply"], runner=fake)
    assert rc == 0
    assert len(fake.calls) == 1, "apply 应只发起一次 schtasks /create /f（同名幂等覆盖）"
    argv = fake.calls[0]
    assert argv[0] == "schtasks" and "/create" in argv and "/f" in argv
    assert "/query" not in argv
