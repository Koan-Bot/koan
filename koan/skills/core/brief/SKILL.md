---
name: brief
scope: core
group: status
emoji: 📋
description: Daily digest — pending missions, recent completions, quota health, journal highlights
version: 1.0.0
audience: bridge
worker: true
commands:
  - name: brief
    description: Show daily digest (or schedule daily delivery)
    aliases: [digest]
handler: handler.py
---
