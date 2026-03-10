# Security Policy

## Supported Versions

The following versions receive security updates:

| Version | Supported |
|---------|-----------|
| 0.x     | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

**Do not open a public GitHub issue.**

Instead, use [GitHub's private vulnerability reporting](https://github.com/Aureliolo/ai-company/security/advisories/new) to submit your report.

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

- **Secret scanning** and **push protection** enabled on the repository
- **Dependabot** monitors dependencies for known vulnerabilities
- **Dependency review** runs on every pull request
- **CodeQL** performs static analysis to find potential vulnerabilities
- **Gitleaks** pre-commit hook prevents committing secrets locally
- **Ruff bandit rules** (S category) check for common security issues in Python code
