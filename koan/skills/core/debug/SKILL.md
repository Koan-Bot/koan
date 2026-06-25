---
name: debug
scope: core
group: code
emoji: "\U0001f41b"
description: "Structured 4-step debugging: reproduce, hypothesize, fix, verify"
version: 1.0.0
audience: hybrid
caveman: true
model_key: mission
github_enabled: true
github_context_aware: true
commands:
  - name: debug
    description: "Run a structured debug loop on a failed issue"
    usage: "/debug <issue-url> [additional context]"
    aliases: [dbg]
handler: handler.py
---
