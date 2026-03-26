---
name: ci_check
scope: core
group: pr
description: "Check CI status for a PR and attempt fixes if failing (ex: /ci_check https://github.com/owner/repo/pull/42)"
version: 1.0.0
audience: hybrid
github_enabled: true
github_context_aware: true
commands:
  - name: ci_check
    description: "Check CI and fix failures for a PR"
    aliases: [cicheck]
handler: handler.py
---
