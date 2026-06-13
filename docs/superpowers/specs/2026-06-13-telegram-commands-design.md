# Доработка Telegram-общения: ясные сообщения + меню команд

**Дата:** 2026-06-13
**Статус:** дизайн согласован, ожидает ревью спека

## Цель

Две независимые части:

- **Часть A — ясные сообщения.** Сделать исходящие уведомления (`format.py`) понятнее: заголовки-эмодзи, разделители тысяч, нотионал, направление словами. Объём правок — умеренный (структура сообщений прежняя, без MarkdownV2).
- **Часть B — меню команд.** Дать возможность в любой момент спросить у бота активные позиции и P&L за период через команды Telegram.

## Контекст (как сейчас)

- Система — **разовый ежедневный прогон**: `trading.run` → `run_daily` отрабатывает один цикл и завершается. Постоянно слушающего процесса нет.
- `TelegramNotifier` (`reporting/telegram.py`) умеет только слать сообщения и один раз блокирующе ждать подтверждения сделки во время прогона (`request_confirmation` опрашивает `getUpdates`).
- Данные для запросов уже в БД (`persistence/schema.py`):
  - `equity_snapshots(agent_id, date, equity)` — дневной equity → P&L за период.
  - `positions(agent_id, symbol, quantity, avg_price)` — активные позиции (qty signed: + лонг, − шорт).
  - `fills(...)` — история сделок.
  - `freezes(scope, reason, ts)` — заморозки.
- Форматирование изолировано в `reporting/format.py`, покрыто `tests/test_report_format.py`.
- Несколько агентов (суб-аккаунтов), профили из `config/profiles.toml`.

## Решения

| Вопрос | Решение |
|---|---|
| Как слушать команды | Отдельный демон с polling: `python -m trading.bot` |
| Детализация ответов | Портфель (агрегат) + разбивка по агентам |
| Набор команд | `/positions`, `/pnl` (день/неделя/месяц/всё), `/status`, `/trades` |
| Меню | Нативное `setMyCommands` + inline-кнопки периода для `/pnl`; `/help` не делаем (дублирует меню) |
| Конфликт `getUpdates` | Кооперативная пауза через лок в БД |
| Стиль части A | Умеренный (эмодзи-заголовки, числа с разделителями, без MarkdownV2) |

## Архитектура и границы модулей

Принцип: **разделить вычисление, форматирование и транспорт** — каждый кусок тестируется изолированно.

```
trading/reporting/queries.py   (НОВЫЙ)  — чистые функции: сводки из репозиториев + цен
trading/reporting/format.py    (правка) — данные → строки (часть A + новые отчёты)
trading/bot.py                 (НОВЫЙ)  — демон: polling, диспетчер команд, меню, проверка лока
trading/persistence/runlock.py (НОВЫЙ)  — лок «идёт цикл» (таблица run_state)
trading/orchestrator/daily.py  (правка) — обернуть прогон в лок
trading/persistence/schema.py  (правка) — таблица run_state
```

### `reporting/queries.py`

Не знает про Telegram. Зависимости: `AccountRepository`, `JournalRepository`, `price_fn(symbol) -> float`, список агентов (имена из профилей). Возвращает dataclass'ы — никаких строк, никакого I/O помимо чтения БД и цен.

Dataclass'ы (имена ориентировочные):

- `PositionLine(agent_id, symbol, quantity, avg_price, current_price, unrealized_pnl)`
- `PositionsReport(per_agent: dict[str, list[PositionLine]], portfolio_unrealized: float, portfolio_market_value: float)`
- `PnlReport(period: str, per_agent: list[(agent_id, start_equity, end_equity, pnl, pct)], portfolio_start, portfolio_end, portfolio_pnl, portfolio_pct)`
- `StatusReport(portfolio_equity, today_pnl, today_pct, open_positions_count, freezes: list[(scope, reason)])`
- `TradesReport(rows: list[(ts, agent_id, action_or_intent, symbol, quantity, price)])`

Функции: `positions_report(...)`, `pnl_report(period)`, `status_report(...)`, `trades_report(limit=10)`.

### `reporting/format.py` (правка)

Чистые функции `dataclass -> str`. Новые: `format_positions`, `format_pnl_report`, `format_status`, `format_trades`. Плюс правки части A для существующих `format_fill`, `format_digest`, `format_confirmation`.

### `trading/bot.py` (НОВЫЙ)

Тонкий диспетчер. На старте: `setMyCommands` (positions, pnl, status, trades). Цикл:

1. Проверить лок (`runlock`). Если активен — спать ~3с, не опрашивать.
2. `getUpdates` (long-poll). Для каждого апдейта:
   - Игнорировать отправителей не из `admin_ids` (переиспользуем логику из `TelegramNotifier`).
   - `message` с командой → разобрать → query → format → `sendMessage`.
   - `/pnl` без явного периода → отправить сообщение с inline-кнопками `[День][Неделя][Месяц][Всё]`.
   - `callback_query` от кнопок периода → посчитать `pnl_report` → `editMessageText` с результатом.
3. Транспорт: переиспользовать `httpx`-клиент; цены — `YFinanceSource`.

Параметризация через env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_IDS`, `DB_PATH` (как в `run.py`).

### `persistence/runlock.py` (НОВЫЙ)

Таблица `run_state(scope TEXT PRIMARY KEY, active INTEGER NOT NULL, since TEXT)`.

- `acquire(scope='GLOBAL')` → `active=1, since=now`.
- `release(scope='GLOBAL')` → `active=0`.
- `is_active(scope='GLOBAL', stale_after_s=900)` → `True`, только если `active=1` **и** `since` не старше 15 минут (защита от зависшего лока, если прогон упал, не сняв флаг).

`run_daily` оборачивается в `try/finally`: `acquire()` в начале, `release()` в `finally`.

## Поток данных по командам

- **`/positions`** — `get_state(agent)` → позиции; `price_fn` (yfinance) → текущая цена; нереализованный P&L = `(price − avg_price) * quantity` (signed qty учитывает шорт). Портфель = сумма по агентам.
- **`/pnl <период>`** — `equity_curve(agent)`. Базовая точка = снапшот на дату `(последняя_дата − N дней)` или ближайший более ранний: день N=1, неделя N=7, месяц N=30, всё = первый снапшот. P&L = `end − start`, % = `pnl/start`. Портфель = сумма по агентам.
- **`/status`** — суммарный equity сейчас; P&L за сегодня = последний снапшот − предыдущий; число открытых позиций; активные freeze из `FreezeStore`.
- **`/trades`** — последние N (по умолч. 10) `fills` по всем агентам, отсортированы по `ts` убыв., с меткой агента.

## Часть A — правки формулировок (умеренно)

Пример `format_fill`:

```
# было
[momentum] Покупка: 10 AAPL @ 213.5
# станет
✅ Сделка исполнена · momentum
Покупка 10 × AAPL @ $213.50  (≈ $2 135)
```

- `format_digest`: добавить в шапку итоговый equity и P&L за день — дневная сводка самодостаточна.
- `format_confirmation`: выровнять стиль (эмодзи-заголовок, единый формат чисел/нотионала), логику подтверждения не трогать.
- Общий принцип: заголовок-эмодзи + понятная роль агента, числа с разделителями тысяч и `$`, направление словами. Без `parse_mode` (plain text) — чтобы не возиться с экранированием.

## Известное ограничение

Команды, отправленные **во время** ежедневного прогона (пара минут в день), могут быть проглочены циклом подтверждения и потеряны: Telegram-апдейты разбираются единожды, а `request_confirmation` дренирует backlog. Лечение — повторить команду после прогона. IPC-очередь ради этого не делаем (YAGNI). Демон при этом не теряет апдейты, пришедшие *после* снятия лока.

## Тестирование

- `queries.py` — юнит на in-memory SQLite + `FakeSource`: P&L по периодам, валюация позиций (лонг/шорт), граничные случаи (нет снапшотов, пустой портфель, один снапшот).
- `format.py` — снапшот-тесты строк (расширить `tests/test_report_format.py`).
- `runlock.py` — acquire/release/is_active, в т.ч. staleness (старый `since` → не активен).
- `bot.py` — диспетчер с фейковым Telegram-клиентом: команда → ожидаемый ответ; игнор не-админов; пауза при активном локе; обработка `callback_query` периода.

## Вне объёма (YAGNI)

- Webhook, публичный HTTPS.
- Единый процесс-сервис (слияние прогона и демона) — отвергнуто в пользу лёгкого лока.
- IPC-очередь подтверждений через БД.
- Выбор конкретного агента аргументом команды (`/pnl momentum week`) — пока только портфель+разбивка.
- MarkdownV2-разметка.
