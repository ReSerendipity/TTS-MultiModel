# Contributing to TTS MultiModel

Thank you for your interest in contributing to TTS MultiModel — a multi-model TTS platform (VoxCPM2, IndexTTS 2.0, dots.tts).

This document gives a short "10-minute quick start" to get contributors productive, and a concise reference for common contribution tasks.

---

## Quick Start (10 minutes)

1. Clone the repository

```bash
git clone https://github.com/ReSerendipity/TTS_MultiModel.git
cd TTS_MultiModel
```

2. Run with Docker (one-line start)

```bash
# Build + run (example)
docker compose up --build -d
# Or use provided script (Windows)
start.bat
```

3. Create a branch for your change

```bash
git checkout -b fix/short-description
# make changes, run tests, then push
git add .
git commit -m "fix(docs): update short description"
git push origin fix/short-description
```

4. Open a Pull Request using the provided template.

---

## Development (local)

Prerequisites
- Python 3.10+ (3.12 recommended)
- Optional GPU (NVIDIA CUDA or Apple MPS) for model testing
- Git

Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install -e ".[dev]"
```

Run tests (skip GPU tests locally if you don't have GPU)

```bash
pytest tests/ -v -k "not gpu and not cuda and not vram" -m "not integration"
```

Lint/format

```bash
ruff check bin/integrated_app/ scripts/
ruff format bin/integrated_app/ scripts/
```

---

## How to File Good Issues

- Bug reports: include environment (OS, Python, GPU), steps to reproduce, expected vs actual behavior, and logs (logs/app.log).
- Feature requests: describe the use case, proposed solution, and any alternatives.

Use the provided issue templates (bug_report / feature_request).

---

## Pull Request Checklist

- Use a descriptive title and include a short summary in the PR body.
- Link related issues using `Closes #<issue>` when appropriate.
- Add tests for new behavior where feasible.
- Run tests & linters locally before opening the PR.
- Follow Conventional Commits for commit messages (`feat:`, `fix:`, `docs:`, etc.).

---

## Labels and Their Meaning

- good-first-issue — Suitable for newcomers (small docs/bug fixes)
- help-wanted — Tasks where maintainers welcome community help
- enhancement — New features / improvements
- bug — Bug reports
- documentation — Docs-only changes
- discussion-needed — Complex proposals that need discussion first

(These labels are recommended and will be created in the repository settings.)

---

## Contributing translations

Add JSON translation files under `bin/integrated_app/locales/` and follow the existing format. Submit a PR with the new locale file.

---

## Architecture & Large Changes

For major changes (new engines, large refactors):
1. Open an Issue to discuss the approach and design.
2. Wait for maintainer feedback.
3. Break large work into smaller, reviewable PRs.

---

## License

By contributing, you agree your contributions are licensed under the Apache License 2.0 (see LICENSE).

---

## Need help?

- Issues: bug reports and feature requests
- Discussions: general questions and community help

Thank you for contributing — the community makes this project better!
