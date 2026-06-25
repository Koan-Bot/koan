---
name: audit_all
scope: core
group: code
emoji: 🔬
description: "Run security_audit, dead_code, and profile in parallel"
version: 1.0.0
audience: hybrid
parallel: true
sub_commands: [security_audit, dead_code, profile]
commands:
  - name: audit_all
    description: "Queue security_audit, dead_code, and profile as parallel missions"
    usage: "/audit_all"
    aliases: [aa]
---
