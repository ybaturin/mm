# Deploy & Ops

## Server

- **Host:** `root@95.179.169.77` (Ubuntu 24.04, **amd64**, Frankfurt). x86 → IB Gateway can run here later.
- **SSH key:** `~/.ssh/id_ed25519` (deploy key; authorized on the host).
- **Remote repo dir:** `/root/mm`
- **uv on host:** `/root/.local/bin/uv`

## Deploy / update (run from your workstation, in the repo root)

```bash
./scripts/deploy.sh root@95.179.169.77
```

This runs the test suite **locally first** (refuses to ship if red), then rsyncs the
working tree to `/root/mm`, then re-provisions (`uv sync` + tests on the host). Use it for
both the first deploy and every update. Overrides: `SSH_KEY=...`, `REMOTE_DIR=...`.

`scripts/provision.sh` is the host-side half (installs uv, syncs, tests); `deploy.sh`
calls it over SSH — you normally don't run it directly.

## Run on the host

```bash
# one daily cycle, manually:
ssh -i ~/.ssh/id_ed25519 root@95.179.169.77 'cd /root/mm && bash scripts/run-daily.sh'

# scheme simulation (free, deterministic):
ssh -i ~/.ssh/id_ed25519 root@95.179.169.77 'cd /root/mm && ~/.local/bin/uv run python -m trading.orchestrator.simulate --days 30'
```

## Cron (already installed on the host)

```
0 13 * * 1-5  /root/mm/scripts/run-daily.sh >> /root/mm/run.log 2>&1
```

13:00 UTC (16:00 MSK), Mon–Fri — pre-market. `run-daily.sh` sources `.env`, guards against
overlap (`flock`) and a stuck run (`timeout 3600`). Edit with `ssh ... 'crontab -e'`.

## Config & secrets (on the host, NOT in git)

- **`/root/mm/.env`** (chmod 600) holds the secrets and mode. Current mode:
  `BROKER=fake STRATEGY=claude PANEL=off NOTIFIER=telegram` — real Claude decisions + real
  Telegram, simulated fills on real yfinance prices (no IBKR, no money).
- Telegram test bot: **@Mm_approved_test_bot**, chat_id `139514019`. (Token lives only in `.env`.)
- Risk profiles & budgets: `config/profiles.toml` — two agents (moderate + aggressive), $2400 each.

## State

- **Track record DB, separated by mode so fake/paper/live never commingle:**
  `data/trading-fake.db` (BROKER=fake), `data/trading-paper.db` (IBKR paper, port 4002),
  `data/trading-live.db` (IBKR live, port 4001). Switching to real money starts a clean
  ledger. Override the path with `DB_PATH`. **Do not delete** the active one — it feeds the
  go-live gate. Back up with `make backup`.
- **`/root/mm/run.log`** = appended run output. Logs, not data; safe to truncate.

## Going to real money (later)

`run.py` refuses live IBKR (port 4001) until each agent's track record beats SPY
(`evaluate_go_live`). Bypass knowingly with `GO_LIVE_OVERRIDE=1`. Paper (port 4002) is exempt.
