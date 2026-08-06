# CLEANUP_REPORT.md

Repository: ReSerendipity/TTS_MultiModel
Scan date: 2026-08-06
Large-file threshold: 5 MB

Summary of findings

1) Current HEAD items of note
- personas/gf1.pt — 159,621 bytes
- personas/旁白.pt — 51,493 bytes
- personas/李老师.pt — 38,373 bytes
- several other personas/*.pt in the 15 KB - 31 KB range

Notes: The repository .gitignore intentionally allows personas/*.pt (there is an explicit deny-exception). Per project convention these persona files are tracked. Based on your choice A, this branch will NOT remove personas from the index. Instead this PR documents the presence of these files and provides recommendations.

2) Noted directories
- models/, pretrained_models/, outputs/, lora/ — potential locations for large weights; ensure these are ignored in .gitignore or moved to LFS/external storage.

Recommendations (safe/non-destructive)

A) Non-destructive review (recommended first step)
- Keep personas tracked only if they are small, licensed for tracking, and required by consumers of the repo. Otherwise migrate large persona files to Git LFS or external storage and keep only download/install scripts in the repo.

B) Historical purge (destructive — coordinate with contributors)
- If you must remove files from history, use git filter-repo or BFG. This re-writes history and requires force push and coordination.

C) Long-term strategy
- Use Git LFS or external object storage (S3 / HF Hub) for model binaries.
- Keep runtime artifacts (logs, outputs, uploads) out of Git; ensure .gitignore covers them.

Files added in this branch
- CLEANUP_REPORT.md (this file)
- history_scan.sh (history scanning helper)
- remove_tracked_sample_ttsmultimodel.sh (non-destructive removal example)
- GITIGNORE_UPDATE.md (recommended .gitignore snippets and instructions)

