# Bridge Self-Heal Watchdog

The Telegram bridge (`awake.py`) is a long-running process **with no
wrapper**: once started, nothing restarts it automatically. If its
`sys.modules` cache goes stale (after `/update` pulls new code), the
bridge keeps serving the *old* code until an operator with shell access
kills it. The original `cb6e927` ("per-process restart markers") fix
patched the race that drops restart signals, but only *after* both
processes are running the new code — the transitional `/update` from
old to new can still leave the bridge wedged.

The watchdog runs **inside the agent loop** (`run.py`) and recovers a
stale or hung bridge automatically.

## Why this lives in the runner, not the bridge

`run.py` is wrapped by `run.sh` and restarted on every exit. Whatever
SHA is on disk is what the runner is executing — it can't be stale.
That makes it the natural place to watch the bridge.

## Two failure modes detected

| Failure mode | How it shows up | How it's detected |
|---|---|---|
| **Stale `sys.modules`** | Bridge is alive, heartbeat fresh, but `/list` and other skills raise `ImportError` on names added by the update. | Stamp the bridge's git HEAD at startup; runner compares with `git rev-parse HEAD` on each tick. |
| **Hung / dead bridge** | Heartbeat mtime stops advancing; possibly the process is gone entirely. | Read `.koan-heartbeat` mtime; read `.koan-pid-awake` and probe with `kill(pid, 0)`. |

## Four-tier escalation

```
                                       ┌─ healthy → reset state, return None
            ┌─── unhealthy? ─── yes ───┤
unhealthy = │                          └─ in cooldown? ── yes ─── return None
  sha drift │                                      no
  OR        │                                       ↓
  hb stale  │        ┌── circuit-broken? ─ yes ── alert, no action
  OR        │        │                no
  no PID    │        │                 ↓
            └────────┴──────── execute tier:
                                  Tier 1: request_restart()           [cooperative]
                                  Tier 2: SIGTERM bridge pid
                                  Tier 3: SIGKILL + start_awake()     [last resort]
```

| Tier | Action | When |
|------|--------|------|
| **1** | `request_restart(koan_root)` — runner is on fresh code so it writes the *new* triple-marker correctly | First sign of trouble, bridge still alive |
| **2** | `os.kill(bridge_pid, SIGTERM)` | Tier 1 didn't take after `HEAL_TIER_COOLDOWN_S` |
| **3** | `SIGKILL` (after `SIGTERM_GRACE_S`) + `pid_manager.start_awake()` | Tier 2 didn't take, or PID is missing/dead — cold start |
| **4** | No action; emit "circuit-broken" alert | `HEAL_CIRCUIT_BREAKER_LIMIT` consecutive tier-3 failures |

**Important:** if the bridge has no PID file at all (crashed long ago,
never came back), we **skip tiers 1–2** and jump straight to tier 3. There
is no process to receive a restart signal.

## State files

Both files live under `$KOAN_ROOT/`:

| File | Written by | Read by | Purpose |
|---|---|---|---|
| `.koan-bridge-version` | `awake.py` startup | `bridge_watchdog` | Git HEAD SHA the bridge process was launched against |
| `.koan-bridge-heal-state` | `bridge_watchdog` | `bridge_watchdog` | JSON with `last_action_ts`, `last_tier`, `consecutive_failures` |

Both writes go through `atomic_write` (temp file + rename + flock) so a
crashed mid-write never leaves a partial file.

## Tunables

Defined as module-level constants in `koan/app/bridge_watchdog.py`:

| Constant | Default | Meaning |
|---|---|---|
| `BRIDGE_HEARTBEAT_STALE_S` | `90.0` | Heartbeat older than this ⇒ bridge hung |
| `HEAL_TIER_COOLDOWN_S` | `45.0` | After firing a tier, wait this long before the next escalation |
| `SIGTERM_GRACE_S` | `5.0` | Window between SIGTERM and SIGKILL in tier 3 |
| `POST_HEAL_QUIET_S` | `60.0` | After the circuit breaker trips, re-emit the alert at most this often |
| `HEAL_CIRCUIT_BREAKER_LIMIT` | `3` | Consecutive tier-3 failures before giving up |

The runner also throttles **how often** the watchdog is even consulted —
once per `_BRIDGE_WATCHDOG_INTERVAL = 5` main-loop iterations
(`run.py`). With a typical 60–300 s iteration cycle, that puts the
maximum detection latency at ~5–25 minutes — fine for a watchdog whose
job is "don't let a stuck bridge sit forever."

## Notification

When the watchdog acts, it returns a one-line summary like:

```
Bridge self-heal tier 1: cooperative restart requested via request_restart().
status: pid=12345 alive=True heartbeat_age=4.2s bridge_sha=cb6e927 disk_sha=ab12cd3
```

The runner forwards this to:

1. **Telegram** via `_notify_raw` (terse, no Claude-CLI reformat). It
   goes through the outbox — if the bridge is dead, the message sits
   there until a freshly-relaunched bridge flushes it on its first
   poll, so the operator still finds out.
2. **Today's journal** (`instance/journal/YYYY-MM-DD/koan.md`) for
   after-the-fact audit.

## Detection latency, in practice

| Scenario | Time to first heal action |
|---|---|
| Stale `sys.modules` after `/update` | Up to one watchdog interval × runner loop interval (≈ 5 min worst case) |
| Bridge hangs mid-iteration | `BRIDGE_HEARTBEAT_STALE_S` + one watchdog interval (≈ 90 s + a few minutes) |
| Bridge crashes (no PID) | One watchdog interval (≈ minutes) |

These are detection latencies; actual recovery (tier 1) is typically a
few seconds beyond that since `request_restart` is fast.

## Failure modes the watchdog does **not** cover

- **Both processes stale simultaneously.** The runner has a wrapper, so
  it's always fresh — this case is structurally impossible.
- **Runner is itself wedged.** Out of scope; the runner's wrapper
  restarts it on every exit, and the existing stagnation monitor
  (`stagnation_monitor.py`) handles long-running stuck CLI calls.
- **Bridge starts fresh but immediately crashes.** Tier 3 will call
  `start_awake` and report whatever its verification timeout returns;
  if startup fails repeatedly the circuit breaker trips after
  `HEAL_CIRCUIT_BREAKER_LIMIT` cycles and the operator is alerted.
- **Git unreachable.** `_read_git_head` returns `None`; the SHA-mismatch
  check is skipped that iteration. Heartbeat-based detection is
  unaffected.

## Operator notes

- A bridge restart triggered by the watchdog is **observable**: look
  for `bridge_watchdog: …` lines in the runner log and `🩹 Bridge
  self-heal …` messages in Telegram.
- The `.koan-bridge-heal-state` JSON is the source of truth for tier
  state. Inspect it (`cat $KOAN_ROOT/instance/.koan-bridge-heal-state`)
  to see what's pending.
- To **manually reset** the state (e.g., after fixing root cause out of
  band), simply delete the file: `rm $KOAN_ROOT/instance/.koan-bridge-heal-state`.
- The watchdog uses `pid_manager.start_awake`, the same helper invoked
  by `make start`. Logs end up in the same place (`logs/awake.log`)
  with the same rotation policy.

## Disabling

There is no on/off switch — the watchdog is always on. If you need to
temporarily silence it (e.g., during planned maintenance):

1. **Easiest:** set `KOAN_ROOT/.koan-shutdown` — the runner exits and
   nothing supervises the bridge until you start it again.
2. **Targeted:** patch `_BRIDGE_WATCHDOG_INTERVAL` in `run.py` to a
   very large value, restart the runner. (No configuration plumbing
   yet — by design; if you find yourself needing this, file an issue.)

## Implementation map

| File | Role |
|---|---|
| `koan/app/bridge_watchdog.py` | Watchdog module: detection, state, tier escalation, version-stamp writer |
| `koan/app/awake.py` | Calls `write_bridge_version_stamp` once at startup |
| `koan/app/run.py` | Calls `check_and_heal_bridge` from the main loop; forwards heal messages to Telegram + journal |
| `koan/app/signals.py` | File-name constants `BRIDGE_VERSION_FILE`, `BRIDGE_HEAL_STATE_FILE` |
| `koan/tests/test_bridge_watchdog.py` | Behavioral tests for each tier and the circuit breaker |

## Rollback

The watchdog is additive — no existing behavior changes. To disable
without reverting:

```python
# In run.py, _maybe_run_bridge_watchdog: short-circuit at the top.
def _maybe_run_bridge_watchdog(koan_root, instance):
    return
```

A full revert is a single-commit revert; the bridge-side version stamp
is harmless (writes one small file at startup, ignored if nothing reads
it).
