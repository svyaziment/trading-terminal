# Trading Terminal Frontend

React + TypeScript + Vite + Tailwind frontend for Trading Terminal.

Backend API should be available on:

  http://localhost:8000

Start backend:

  docker compose up -d backend

Install frontend dependencies:

  npm install

Start dev server:

  npm run dev

Open:

  http://localhost:5173

Build:

  npm run build

Tests (Vitest + React Testing Library):

  npm test

There is no `frontend` service in `docker-compose.yml`. Use the Vite dev server or `npm run build`.

## Strategy Lab: Level Breakout Retest

Pattern `level_breakout_retest` appears in the **Пробой / Breakout** group after `GET /api/patterns` returns it (backend Issue #107). The UI does not hardcode its six parameters.

**What it does.** After a confirmed resistance break, wait for price to retest the level as new support (role reversal) and enter on a bullish trigger. Stop/take come from the pattern (`stop_atr` × ATR and `risk_reward`), not from the native support-zone geometry.

**When to enable.** Use it to add a breakout-retest path next to `levels_reversal`. Keep combining with `levels_reversal` (still required for the support-zone path) and optional `signal_4h_buy` / SignalEngine filters. Leave it off on locked paper strategy `test_20260731` (read-only in the Lab).

**How to configure.** Click the chip to open settings. Defaults: 4h levels, 20-bar retest window, 0.5×ATR zone, bullish trigger on, stop 1.0×ATR, RR 2.0. Out-of-range values block Apply and Save+Run. Reset restores API defaults.

## Strategy Lab: Support + Resistance Breakout

Pattern `levels_sr_breakout` appears in the **Уровни / Levels** group after `GET /api/patterns` returns it (backend Issue #117). The UI does not hardcode its params. Icon `support_breakout` is distinct from the breakout-group `breakout_up`.

**What it does.** Isolated entry engine (OR of two paths): path A is the native support zone of `levels_reversal` (with the active-resistance veto); path B is a confirmed resistance break + retest without a native support zone. This chip **replaces** `levels_reversal` for the new strategy — do not AND it with `levels_reversal` or `level_breakout_retest`.

**When to enable.** Use it alone (plus optional `signal_4h_buy` / SignalEngine filters) when you want both support reversals and resistance-retest entries. Leave it off on locked paper strategy `test_20260731` (read-only in the Lab).

**How to configure.** Click the chip to open settings. Schema = all `levels_reversal` fields + retest fields. Out-of-range values block Apply and Save+Run. Reset restores API defaults. Save still goes through `POST /api/strategies` then `POST /api/strategies/{id}/run`.
