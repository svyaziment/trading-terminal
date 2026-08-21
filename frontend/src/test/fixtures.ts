import type { PatternDef } from "../types";

/** Shape returned by GET /api/patterns for level_breakout_retest (Issue #107/#109). */
export const LEVEL_BREAKOUT_RETEST_DEF: PatternDef = {
  id: "level_breakout_retest",
  label: "Пробой уровня с ретестом",
  label_en: "Level Breakout Retest",
  hint: "После подтверждённого пробоя сопротивления цена возвращается к уровню как к новой поддержке (смена роли); вход — по бычьему триггеру.",
  hint_en: "After a confirmed resistance break, price retests the level as new support (role reversal); entry waits for a bullish trigger.",
  icon: "breakout_up",
  category: "breakout",
  params: [
    {
      key: "level_timeframe",
      label: "Таймфрейм уровней",
      label_en: "Level timeframe",
      type: "select",
      options: ["1h", "4h", "1d"],
      default: "4h",
    },
    {
      key: "retest_window_bars",
      label: "Окно ретеста (баров ТФ)",
      label_en: "Retest window (TF bars)",
      type: "number",
      min: 1,
      max: 100,
      step: 1,
      default: 20,
    },
    {
      key: "retest_zone_atr",
      label: "Зона ретеста (×ATR)",
      label_en: "Retest zone (×ATR)",
      type: "number",
      min: 0.1,
      max: 2.0,
      step: 0.05,
      default: 0.5,
    },
    {
      key: "entry_trigger_bullish",
      label: "Триггер: бычья свеча / пробой high",
      label_en: "Bullish trigger (body / break of high)",
      type: "boolean",
      default: true,
    },
    {
      key: "stop_atr",
      label: "Стоп (×ATR)",
      label_en: "Stop (×ATR)",
      type: "number",
      min: 0.5,
      max: 3.0,
      step: 0.1,
      default: 1.0,
    },
    {
      key: "risk_reward",
      label: "Take / risk",
      label_en: "Take / risk",
      type: "number",
      min: 1.0,
      max: 5.0,
      step: 0.1,
      default: 2.0,
    },
  ],
};

export const LEVELS_REVERSAL_DEF: PatternDef = {
  id: "levels_reversal",
  label: "Levels Reversal",
  hint: "цена в зоне 4h + подтверждение",
  category: "levels",
  params: [],
};

export const SIGNAL_4H_BUY_DEF: PatternDef = {
  id: "signal_4h_buy",
  label: "4H Buy",
  hint: "активный BUY из trading.signals",
  category: "signal",
  params: [],
};

export const BO_BB_SQUEEZE_DEF: PatternDef = {
  id: "BO_BB_Squeeze",
  label: "BB Squeeze",
  category: "breakout",
  params: [],
};

export const LEVEL_BREAKOUT_RETEST_DEFAULTS = {
  level_timeframe: "4h",
  retest_window_bars: 20,
  retest_zone_atr: 0.5,
  entry_trigger_bullish: true,
  stop_atr: 1.0,
  risk_reward: 2.0,
};
