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

## DCO (Developer Certificate of Origin)
## DCO (Developer Certificate of Origin)

This project requires all contributors to sign their commits with the
Developer Certificate of Origin (DCO). This attests that you have the right
to submit the work under the project's license.

### How to Sign

Add the `-s` (or `--signoff`) flag to your git commit:

```bash
git commit -s -m "feat(voxcpm2): add streaming generation support"
```

This adds a `Signed-off-by: Your Name <your.email@example.com>` line to the
commit message, which serves as your DCO attestation.

### DCO Full Text

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.

Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

### Verification

PRs will be checked for DCO compliance. Commits without a `Signed-off-by` line
will be rejected. If you forgot to sign a commit, you can amend it:

```bash
git commit --amend -s --no-edit
```

## Getting Help

- **Issues**: For bugs and feature requests
- **Discussions**: For questions and general discussion
- **Code Review**: Maintainers will review PRs within a reasonable timeframe
---

Thank you for contributing — the community makes this project better!
