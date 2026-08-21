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
