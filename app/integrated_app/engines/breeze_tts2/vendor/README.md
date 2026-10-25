# Vendored upstream code: breezeblue-ai/breeze-tts

- Upstream: https://github.com/breezeblue-ai/breeze-tts (Apache-2.0, see LICENSE.upstream-apache-2.0)
- Vendored commit: 6eba901d1845353802652c708a4740fdfc5baa75 (2026-10-25)
- Scope: breeze_infer/{__init__,audio,runtime,templates}.py + models/** + configs/fast.json
  (api.py / batch.py / tests / docker / infer.py CLI are not vendored — the engine wraps the runtime directly)
- Local modifications (keep this list in sync):
  1. breeze_infer/runtime.py: `from models.breeze import ...` -> `from ..models.breeze import ...`
     (avoid top-level `models` namespace collision with the host application)
- Why vendored instead of pip-installed: upstream ships no pyproject/setup.py (not on PyPI),
  and the engine must stay importable offline for users behind restricted networks.
- Upgrade procedure: re-copy the files listed under Scope from a fresh upstream checkout,
  re-apply modification 1, bump the commit hash above, and re-run
  `pytest tests/engines/test_breeze_tts2_*.py -v`.
