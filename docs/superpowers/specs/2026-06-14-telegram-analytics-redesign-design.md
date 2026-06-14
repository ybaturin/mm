# Telegram Analytics Redesign — Design

Date: 2026-06-14

## Problem

The Telegram bot's reporting is hard to read and the trade-confirmation prompt is
opaque. Concretely:

1. **Poor readability / no visual hierarchy.** Messages are sent as plain text with
   no `parse_mode`, no bold, no grouping. `/trades` is one undifferentiated wall of
   lines (see the owner's screenshot), not grouped by strategy and not column-aligned.
2. **The confirm/decline prompt is technical noise.** It shows `agent_id`, the intent
   code, symbol, price, notional, a bare `стоп: $X`, and the raw model `rationale`.
   There is no expected profit, no horizon, no plain-language "why".
3. **The bot's expectations at purchase are invisible.** The agent only emits
   `reference_price`, `stop_loss_price`, and a free-text `rationale`. No target price,
   expected move, or horizon is captured anywhere.
4. **Whether a forecast panned out is uncomputable.** With no stored forecast, there is
   nothing to compare actual outcomes against.

## Goals

- All Telegram messages are readable: HTML formatting, bold headers, grouping by bot
  strategy (`aggressive` / `moderate` / `conservative`), and monospace tables for lists.
- The confirmation prompt is in plain language and states expected profit (in `$` and
  `%`), the horizon ("when"), and a jargon-free "why".
- The agent commits to a **target price + horizon** on every opening trade.
- `/positions` shows progress toward target; a retrospective is pushed when a position
  closes; `/pnl` shows each strategy's performance for the period plus an SPY benchmark.

## Non-goals

- No new standalone reporting subsystem. We enrich existing modules (`reporting/*`,
  `agent/*`, `persistence/*`).
- No change to confirmation transport logic (nonce, timeout, draining) — only the text.
- No change to guardrails. Forecast validity is presentational, not a risk gate.

## Telegram formatting constraint (drives the list design)

Telegram cannot do *both* aligned columns and per-row color:

- **Aligned columns** require a monospace block (`<pre>…</pre>`). Telegram does not
  color individual cells inside it, and emoji break the alignment.
- **Per-row color / bold** (🟢/🔴) works only in normal text, where columns do not align.

Decision: **lists use monospace tables (aligned), grouped by bot.** Color (🟢/🔴) is
reserved for places where it is about money and lives in normal text: the per-group P&L
summary header (in `/pnl`, `/positions`) and the confirmation message. The `/trades`
list itself is a plain aligned table; direction is shown by the sign of the quantity
(`+N` bought, `−N` sold).

## Approach

Incremental, across three coordinated layers:

1. **Formatting layer** — `reporting/format.py` + `parse_mode` plumbing.
2. **Forecast capture** — `agent/schema.py`, `agent/prompts.py`, `domain.py`.
3. **Storage + fact computation** — `persistence/*`, `reporting/queries.py`.

Rejected alternative: a separate reporting module with its own renderer — overkill for a
single Telegram channel (YAGNI).

---

## Section 1 — Message format (HTML + tables)

- All send points pass `parse_mode="HTML"`:
  `TelegramNotifier.notify`, `TelegramNotifier.request_confirmation`, `Bot._send`,
  `Bot._edit`.
- New helper `mono_table(rows, aligns)` in `format.py`: builds a width-aligned monospace
  table wrapped in `<pre>…</pre>`. No emoji inside the table (they break alignment).
- New HTML-escape helper for user-supplied text (tickers, rationale) so `<` and `&`
  cannot break the markup or get the message rejected by Telegram.
- Grouping by bot: for each `agent_id`, a bold normal-text header (with 🟢/🔴 by P&L
  where relevant), then a `<pre>` table beneath it.

Example `/trades`:

```
🧾 Последние сделки

━ AGGRESSIVE ━━━━━━━━
 14.06   +3  IWM     292.95
 13.06   −1  TSLA    406.43

━ MODERATE ━━━━━━━━━━
 14.06   +1  DIA     513.06
 14.06   −2  AAPL    291.13
```

(`+N` — bought, `−N` — sold.)

## Section 2 — Confirm / decline message

Replaces `format_confirmation`. Normal text (color and bold work here), block layout
"Что / Зачем / Цель / Риск":

```
❓ Купить AAPL? — агрессивный портфель

Что:    купить 12 × AAPL по ~$185  (вложим ≈ $2 224)
Зачем:  перепродана, жду отскок к средней цене
Цель:   🟢 $200 за ~2 недели
        ожидаемая прибыль +8%  (≈ +$178)
Риск:   🔴 стоп $176  (−5%, ≈ −$112)

   [ ✅ Купить ]   [ ❌ Пропустить ]
```

- **Expected profit** in both `%` and `$`, next to the horizon. For a long:
  `profit$ = (target − reference) × qty`, `profit% = target/reference − 1`.
- **Why** comes from the agent rationale, written in plain language (see Section 3). If
  it still arrives with jargon, show it as-is (the model is not perfect) — but the
  prompt forbids it.
- **Risk** translates the stop into human terms, in `%` and `$`.
- Shorts: signs invert (profit as price falls to target). Closing trades
  (`close_long` / `close_short`) have no forecast — show only "Что / Зачем".
- Buttons and the nonce/timeout logic in `request_confirmation` are unchanged; only the
  text passed in changes.

## Section 3 — Forecast capture in the agent schema

Two new fields, **opening trades only**:

- `target_price: float | None`
- `horizon_days: int | None`

Changes:
- `agent/schema.py::ProposedTrade` — two optional fields.
- `domain.py::TradeProposal` — same two fields (frozen dataclass).
- `agent/schema.py::to_domain_proposals` — pass them through.
- `agent/prompts.py::build_system_prompt` — instruct the model to:
  - set `target_price` and `horizon_days` on every opening trade;
  - put `target_price` on the correct side (above entry for long, below for short);
  - write `rationale` in plain language, **without indicator names** (not "RSI14=28"
    but "акция сильно просела, жду отскок");
  - leave forecast fields `null` for closing trades.

Deterministic safeguards (we do not trust the model blindly):
- Missing `target_price`/`horizon_days` on an opening trade → treat as "no forecast":
  the "Цель" line is omitted in the confirmation and reports; log it as an anomaly. Do
  not crash.
- `target_price` on the wrong side (e.g. below entry for a long) → forecast invalid,
  drop the field, log it. Do not block the trade — that is guardrails' job, not this.

Horizon display: human-friendly. `<10 d → "N дней"`, `~7 → "~1 неделя"`,
`10–24 → "~N недель"`, `≥25 → "~N месяц(ев)"`.

## Section 4 — Storage and fact computation

The forecast must survive proposal → fill → open position → close. The `positions`
table has no room, so add a live-forecast table:

```sql
CREATE TABLE IF NOT EXISTS theses (
    agent_id      TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    entry_price   REAL NOT NULL,   -- entry price at open time
    target_price  REAL NOT NULL,
    horizon_days  INTEGER NOT NULL,
    opened_on     TEXT NOT NULL,   -- YYYY-MM-DD, for "days left"
    rationale     TEXT NOT NULL,
    PRIMARY KEY (agent_id, symbol)
);
```

Lifecycle, tied to fill execution (where positions are updated):
- **Open from flat** → insert a thesis row.
- **Add to existing position** → update the thesis with the latest forecast (the fresh
  view wins) and sync `entry_price` to the position's average price.
- **Full close (qty → 0)** → compute the retrospective, send the message, **delete** the
  row.
- **Partial close** → thesis stays, target unchanged.

Audit: add nullable columns `target_price`, `horizon_days` to `decisions` — an immutable
log of forecasts for future accuracy analysis. `theses` is live state; `decisions` is
history.

Migration (the SQLite DB is the entire track record — never recreate): `init_db` uses
`CREATE TABLE IF NOT EXISTS`, which will not add columns to an existing `decisions`
table. Add a lightweight migration: read `PRAGMA table_info(decisions)` and
`ALTER TABLE … ADD COLUMN` when the column is absent. Idempotent; data preserved.

Pure calculations (in `queries.py`):
- `path_to_target = (current − entry) / (target − entry)` (signs work out for shorts),
  clamped to `[0%, 100%+]`.
- `days_left = horizon_days − (today − opened_on).days`.
- Retro on close: `expected% = target/entry − 1`, `actual% = exit/entry − 1`,
  `path_reached = (exit − entry)/(target − entry)`, and "in time / overdue" by days.

## Section 5 — Display: /positions, /pnl, retro on close

All grouped by bot, monospace tables, with a colored P&L summary header.

`/pnl` — each strategy's performance for the period in its header, plus an SPY benchmark:

```
💰 P&L за неделю

🟢 Портфель  +$1 240  (+2.1%)   ·   SPY +1.4% — обыгрываем

━ AGGRESSIVE ━━━━━━━━━━━━━━
🔴  −$420   (−3.1%)
 нач. 13 500   →   тек. 13 080

━ MODERATE ━━━━━━━━━━━━━━━
🟢  +$820   (+2.0%)
 нач. 40 200   →   тек. 41 020
```

SPY benchmark: fetch historical SPY close between the period's baseline date and the
latest, compute return, expose as `benchmark_pct` on `PnlReport`. Header notes whether
the portfolio beats or trails it.

`/positions` — per open position: entry → current → target, % of path to target, days
left:

```
📦 Позиции · нереализ. 🟢 +$310

━ AGGRESSIVE ━━━━━━━━━━━━━━
 IWM  ×3
  вход 292.95 → 298.10 → цель 315
  путь к цели 43%   ·   ост. ~9 дней   🟢 +$15
```

Retro on close (pushed as its own message when a position closes):

```
🏁 Закрыта позиция · aggressive
TSLA ×1 — итог 🔴 −$38 (−1.2%)

Прогноз был:  +6% за ~1 неделю
По факту:     −1.2%, дошли на 0% пути
Срок:         закрыто на 4-й день из ~7
```

Positions without a forecast omit the forecast lines and show plain P&L.

## Section 6 — Testing

Unit tests on pure functions + integration, matching the existing style.

- **Formatting** (`format.py`): `mono_table` aligns columns and escapes HTML; grouping by
  bot; empty cases ("позиций нет", "сделок нет"); `+/−` signs for buy/sell;
  horizon-to-human conversion (7 → "~1 неделя", etc.). Snapshot the rendered strings.
- **Confirmation**: expected profit `$`/`%` correct for long and short; closing trade
  shows no "Цель" block; missing forecast omits the "Цель" line.
- **Agent schema**: `to_domain_proposals` passes `target_price`/`horizon_days` through;
  wrong-side target dropped with a log; missing fields do not break parsing.
- **Storage**: open inserts a thesis, add-to updates it, full close deletes it and
  returns retro data, partial close keeps it; the `decisions` migration is idempotent and
  loses no data (test against an old schema without the new columns).
- **Queries**: `path_to_target`, `days_left`, retro calculations; `benchmark_pct` from
  stubbed SPY prices.
- **Parse mode**: messages go out with `parse_mode="HTML"` and contain no unescaped
  `<`/`&` in user-supplied text (else Telegram rejects them).

## Files touched

- `src/trading/reporting/format.py` — `mono_table`, HTML escape, grouped renderers,
  rewritten `format_confirmation`, enriched `format_pnl_report` / `format_positions` /
  `format_trades`, new retro formatter.
- `src/trading/reporting/telegram.py` — `parse_mode="HTML"` in `notify` and
  `request_confirmation`.
- `src/trading/bot.py` — `parse_mode="HTML"` in `_send` / `_edit`.
- `src/trading/reporting/queries.py` — forecast progress, retro calc, `benchmark_pct`.
- `src/trading/agent/schema.py`, `src/trading/agent/prompts.py`, `src/trading/domain.py`
  — forecast fields + prompt changes.
- `src/trading/persistence/schema.py` (+ migration), and the fill/position update path
  that writes/updates/deletes `theses` and emits the retro.
- `tests/**` — coverage per Section 6.
