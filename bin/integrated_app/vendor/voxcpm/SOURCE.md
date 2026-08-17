# VoxCPM Vendor Source Baseline

## Upstream Information

- **Upstream Project**: VoxCPM by OpenBMB (面壁智能)
- **Repository**: https://github.com/OpenBMB/VoxCPM
- **License**: Apache-2.0 (Copyright 2025 OpenBMB)
- **Package name**: `voxcpm` (as seen in `__init__.py` and module structure)

## What's Vendored Here

This directory (`vendor/voxcpm/`) contains a subset of the official VoxCPM repository, vendored for direct import within TTS_MultiModel:

```
vendor/voxcpm/
├── __init__.py
├── cli.py           # CLI tooling
├── core.py          # Main VoxCPM class (VoxCPM wrapper with download support)
├── model/
│   ├── __init__.py
│   ├── utils.py
│   ├── voxcpm.py    # Original VoxCPM model implementation
│   └── voxcpm2.py   # VoxCPM2 model variant (if present in upstream)
├── modules/         # Core neural network modules
├── timestamps/      # Timestamp utilities
└── training/        # Training utilities (if included)
```

## Known Modifications

The following files have been modified from upstream (preserving copyright header):

| File | Modification | Date |
|------|--------------|------|
| `core.py` | Added local filesystem path support in `__init__`, adjusted device handling for TTS_MultiModel integration | Aug 2026 |

**Note**: For a complete diff, compare against the upstream commit used as baseline. Since we don't have git history here, run this check periodically:

```bash
# Compare current vendor against upstream HEAD
curl -s https://raw.githubusercontent.com/OpenBMB/VoxCPM/main/voxcpm/core.py > /tmp/upstream_core.py
diff bin/integrated_app/vendor/voxcpm/core.py /tmp/upstream_core.py
```

## Upgrade Strategy

When upstream releases security fixes or new features:

1. Clone upstream repo to temp location: `git clone https://github.com/OpenBMB/VoxCPM /tmp/voxcpm-upstream`
2. Review changes relevant to vendored files (`core.py`, `cli.py`, `model/`)
3. Apply necessary updates while preserving TTS_MultiModel-specific modifications
4. Test with existing TTS workflows
5. Document changes in this file's "Known Modifications" table

## License Compliance

All files in this directory retain their original `Copyright 2025 OpenBMB` notice and are licensed under Apache-2.0 per the upstream license. See upstream repository for full license text.

---

*Last verified against upstream*: 2026-08-17 (no upstream commit hash tracked locally)
*Baseline source*: OpenBMB/VoxCPM main branch (pre-August 2026)
