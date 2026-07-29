# Security Policy

## Supported versions

Security fixes are issued for the **latest released version** of `backtest360-mcp`
on PyPI, across the supported Python versions (3.10–3.12). Please upgrade to the
latest version before reporting an issue:

```bash
pip install --upgrade backtest360-mcp
```

## Reporting a vulnerability

Please report security vulnerabilities **privately** by email to
**hello@backtest360.com**.

Do **not** open a public GitHub issue for security reports — public disclosure
before a fix is available puts other users at risk.

### What to include

To help us triage and fix the issue quickly, please include:

- The affected version (`python -c "import backtest360_mcp; print(backtest360_mcp.__version__)"`)
- Steps to reproduce, or a minimal proof of concept
- The impact you observed or expect

### What to expect

We will acknowledge your report within **3 business days** and keep you updated
as we investigate and resolve the issue.

There is no bug bounty program, but with your permission we are happy to credit
you in the release notes for the fix.

## Scope

This repository contains the **MCP server** only. Reports about the Backtest360
service itself are equally welcome at the same address: **hello@backtest360.com**.
