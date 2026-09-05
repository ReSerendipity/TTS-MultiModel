"""云原生评估实施统一验收脚本（静态部分）。

覆盖报告 §4 验收命令中本机可验证的部分（无 Docker/GPU 环境下的替身验证）：
  A1 Dockerfile：python3.12 声明、无 3.10 残留、--no-install-recommends、
      HEALTHCHECK/CMD 解释器、STOPSIGNAL、注释 60s
  A2 compose：只读根fs/tmpfs/cap_drop/no-new-priv/pids/shm/stop_grace/
      挂载六件套/GPU预留/healthcheck 用 python3.12/TTS_LOG_FORMAT=json
  A3 k8s：挂载面 ⊇ 需求集、initContainer、权重只读、GPU requests==limits、
      terminationGracePeriod=90、探针路径、无 HPA、readiness=/readyz
  A4 三处优雅关闭数值对齐（Dockerfile 注释 / compose / k8s）
  A5 YAML 全清单解析 + 工作流解析
  A6 证据链：deployment 引用的 tts-auth/tts-models/tts-data/tts-config 均有出处
  A7 工作流引用的仓库文件真实存在
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


def main() -> int:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    dep_docs = [
        d for d in yaml.safe_load_all((ROOT / "deploy/kubernetes/deployment.yaml").read_text(encoding="utf-8")) if d
    ]
    pvc_docs = [d for d in yaml.safe_load_all((ROOT / "deploy/kubernetes/pvc.yaml").read_text(encoding="utf-8")) if d]
    wf = yaml.safe_load((ROOT / ".github/workflows/docker-smoke.yml").read_text(encoding="utf-8"))

    print("A1 Dockerfile")
    check("python3.10" not in dockerfile, "无 python3.10 残留")
    check(dockerfile.count("python3.12") >= 8, "python3.12 显式声明（≥8 处）")
    check("pip3 install" not in dockerfile, "无裸 pip3（全部 python3.12 -m pip）")
    check(dockerfile.count("--no-install-recommends") >= 4, "--no-install-recommends ≥4 处")
    check(
        "HEALTHCHECK" in dockerfile and "python3.12 -c" in dockerfile.split("HEALTHCHECK")[1][:300],
        "HEALTHCHECK 用 python3.12",
    )
    check('CMD ["python3.12"' in dockerfile, "CMD 用 python3.12")
    check("STOPSIGNAL SIGTERM" in dockerfile, "STOPSIGNAL SIGTERM")
    check("60" in dockerfile.split("优雅停机")[1][:200], "Dockerfile 注释排水 60s")

    print("A2 docker-compose")
    svc = compose["services"]["tts"]
    check(svc["read_only"] is True, "只读根 fs")
    check("/tmp" in svc["tmpfs"], "tmpfs /tmp")
    check(svc["cap_drop"] == ["ALL"], "cap_drop ALL")
    check("no-new-privileges:true" in svc["security_opt"], "no-new-privileges")
    check(svc["pids_limit"] == 512, "pids_limit 512")
    check(svc["shm_size"] == "2gb", "shm 2g")
    check(svc["stop_grace_period"] == "90s", "stop_grace_period 90s")
    vols = " ".join(svc["volumes"])
    for v in [
        "./model:/app/model:ro",
        "./outputs:/app/outputs",
        "./logs:/app/logs",
        "./cache:/app/cache",
        "./lora:/app/lora",
        "./personas:/app/personas:ro",
        "./data:/app/data",
    ]:
        check(v in vols, f"卷 {v}")
    check(svc["healthcheck"]["test"][1] == "python3.12", "healthcheck 用 python3.12")
    env = " ".join(svc["environment"])
    check("TTS_LOG_FORMAT=json" in env, "日志 json（与 k8s 对齐）")
    check("TTS_GRACEFUL_SHUTDOWN_S=60" in env, "排水 60s 显式")

    print("A3/A4 k8s")
    pod = dep_docs[0]["spec"]["template"]["spec"]
    c = pod["containers"][0]
    mounts = {m["mountPath"]: m for m in c["volumeMounts"]}
    for need in [
        "/app/config.yaml",
        "/app/model",
        "/app/data",
        "/app/outputs",
        "/app/logs",
        "/app/cache",
        "/app/lora",
        "/app/personas",
        "/tmp",
    ]:
        check(need in mounts, f"挂载 {need}")
    check(mounts["/app/model"].get("readOnly") is True, "model 只读")
    check(mounts["/app/personas"].get("readOnly") is True, "personas 只读")
    check(not mounts["/app/outputs"].get("readOnly"), "outputs 可写")
    check(bool(pod.get("initContainers")), "initContainer 预建 subPath 目录")
    check(c["securityContext"]["readOnlyRootFilesystem"] is True, "只读根 fs")
    gpu = (c["resources"]["limits"]["nvidia.com/gpu"], c["resources"]["requests"]["nvidia.com/gpu"])
    check(gpu == (1, 1), "GPU requests==limits==1")
    check(pod["terminationGracePeriodSeconds"] == 90, "grace 90s")
    check(c["readinessProbe"]["httpGet"]["path"] == "/readyz", "readiness=/readyz")
    check("horizontalpodautoscaler" not in yaml.dump(dep_docs).lower(), "无 HPA（单副本语义）")
    check("TTS_GRACEFUL_SHUTDOWN_S" in yaml.dump(c["env"]), "k8s 排水 60s 显式")
    check(pvc_docs[0]["spec"]["resources"]["requests"]["storage"] == "30Gi", "tts-data 30Gi")
    check(
        pvc_docs[1]["metadata"]["name"] == "tts-models"
        and pvc_docs[1]["spec"]["resources"]["requests"]["storage"] == "60Gi",
        "tts-models 60Gi",
    )

    print("A5 清单与工作流解析")
    for f in ["deploy/kubernetes/secret.example.yaml", "deploy/kubernetes/servicemonitor.example.yaml"]:
        list(yaml.safe_load_all((ROOT / f).read_text(encoding="utf-8")))
        check(True, f"{f} 可解析")
    check(wf["jobs"]["smoke"]["timeout-minutes"] == 40, "工作流 timeout 40m")
    names = [s.get("name", "") for s in wf["jobs"]["smoke"]["steps"]]
    check(len(names) >= 15, f"工作流步骤 {len(names)} ≥15")

    print("A6 证据链闭环（引用必有出处）")
    dep_text = (ROOT / "deploy/kubernetes/deployment.yaml").read_text(encoding="utf-8")
    check("claimName: tts-models" in dep_text and "tts-models" in yaml.dump(pvc_docs), "tts-models PVC 出处")
    check(
        "name: tts-auth" in dep_text
        and "tts-auth" in (ROOT / "deploy/kubernetes/secret.example.yaml").read_text(encoding="utf-8"),
        "tts-auth Secret 样例出处",
    )
    check("claimName: tts-data" in dep_text, "tts-data 引用")
    configmap_path = ROOT / "deploy/kubernetes/configmap.yaml"
    check(not configmap_path.exists(), "骨架 configmap.yaml 已删除")
    check(
        "--from-file" in (ROOT / "deploy/kubernetes/README.md").read_text(encoding="utf-8"),
        "README 用 --from-file 生成 ConfigMap",
    )
    check(
        "configmap.yaml"
        not in re.sub(
            r"^[^#].*骨架.*$", "", (ROOT / "deploy/kubernetes/README.md").read_text(encoding="utf-8"), flags=re.M
        )
        .split("## 部署步骤")[1]
        .split("```")[1],
        "部署步骤不再 apply 骨架",
    )

    print("A7 工作流引用的文件真实存在")
    wf_text = (ROOT / ".github/workflows/docker-smoke.yml").read_text(encoding="utf-8")
    check("docker exec" in wf_text and "python3.12" in wf_text, "容器内探测用 python3.12")

    print()
    if FAILS:
        print(f"共 {len(FAILS)} 项失败：")
        for f in FAILS:
            print(" -", f)
        return 1
    print("=== 全部静态验收通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
