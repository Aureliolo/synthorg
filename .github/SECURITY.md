# Security Policy

## Supported Versions

The following versions receive security updates:

| Version | Supported |
|---------|-----------|
| 0.x     | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

**Do not open a public GitHub issue.**

Instead, use [GitHub's private vulnerability reporting](https://github.com/Aureliolo/synthorg/security/advisories/new) to submit your report.

You should receive a response within 72 hours. If the vulnerability is confirmed, we aim to provide a fix within 90 days for non-critical issues, and as quickly as possible for actively exploited vulnerabilities.

We will not pursue legal action against researchers who discover and report vulnerabilities in good faith. We are happy to credit reporters in security advisories unless they prefer to remain anonymous.

## Scope

This project is designed to handle LLM API keys, sandboxed code execution, and agent-to-tool interactions. As these features are implemented, security-relevant areas include:

- API key and secret management
- Sandboxed code execution boundaries
- Agent permission and approval gates
- Input validation on all external boundaries
- Dependency supply chain (monitored via Dependabot and dependency review)

## Security Features

### Application-Level Security

- **Ruff bandit rules** (S category) check for common security issues in Python code
- **CodeQL** performs static analysis to find potential vulnerabilities
- **Secret scanning** and **push protection** enabled on the repository
- **Gitleaks** pre-commit hook prevents committing secrets locally + weekly CI workflow

### Dependency & Supply Chain Scanning

- **Dependabot** monitors dependencies for known vulnerabilities (daily, uv + github-actions + docker)
- **Dependency review** runs on every pull request (license allow-list, PR comment summaries)
- **pip-audit** scans Python dependencies for known vulnerabilities (every PR + weekly scheduled workflow)
- **npm audit** scans Node.js dependencies for known vulnerabilities (every PR, critical + high severity)
- **Socket.dev** GitHub App detects supply chain attacks on PRs (typosquatting, malware, suspicious ownership changes, obfuscated code)

### Container & Infrastructure Security

- **Trivy** scans container images (CRITICAL = hard fail, HIGH = warn-only)
- **Grype** scans container images (critical severity cutoff)
- **Cosign** keyless image signing for supply-chain integrity (GHCR)
- **Hadolint** lints all Dockerfiles (backend, web, sandbox) in CI + pre-commit hook
- **Chainguard Python** distroless runtime (non-root, CIS Docker Benchmark v1.6.0 hardened)

### Workflow & Supply Chain Security

- **Zizmor** static analysis of GitHub Actions workflows (SARIF uploaded on push events only — fork PRs lack `security-events: write`)
- **OSSF Scorecard** supply chain maturity scoring (SARIF to Security tab, weekly + push to main)
- All workflow actions pinned by full SHA with version comments
- `permissions: {}` at workflow level with least-privilege per job
- `persist-credentials: false` on all `actions/checkout` steps
- **Signed commits** required on `main` via branch protection

### Dynamic Application Security Testing (DAST)

- **ZAP API Scan** runs against the backend OpenAPI spec (push to main + weekly)
- SARIF results uploaded to GitHub Security tab
