---
name: prompt_audit
scope: core
group: code
emoji: 📝
description: Audit system and skill prompts for quality, clarity, redundancy, and effectiveness
version: 1.0.0
audience: hybrid
commands:
  - name: prompt_audit
    description: Audit all system and skill prompts for quality issues — writes findings to shared journal
    usage: /prompt_audit [project-name]
    aliases: []
handler: handler.py
worker: true
---
