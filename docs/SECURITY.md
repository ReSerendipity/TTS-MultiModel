# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| latest  | Yes       |

## Reporting a Vulnerability

Please report security vulnerabilities by opening a GitHub Issue with the "security" label, or by contacting the maintainers directly.

We will respond within 72 hours and provide a patch or workaround within 7 days.

## Security Measures

- Ed25519 signed integrity manifest
- gitleaks secret scanning (pre-commit + CI)
- Dependabot dependency updates
- CodeQL SAST analysis
- Path traversal protection
- CSRF middleware

See SECURITY_MATRIX.md for details.
