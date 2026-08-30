import { describe, expect, it } from "vitest";
import { firstPatternValidationError, validateParam, validatePatternValues } from "./patternValidation";
import {
  LEVEL_BREAKOUT_RETEST_DEF,
  LEVEL_BREAKOUT_RETEST_DEFAULTS,
  LEVELS_SR_BREAKOUT_DEF,
  LEVELS_SR_BREAKOUT_DEFAULTS,
  LEVELS_SR_SUPPORT_DEF,
  LEVELS_SR_SUPPORT_DEFAULTS,
} from "./test/fixtures";

describe("validateParam / level_breakout_retest schema", () => {
  const byKey = Object.fromEntries(LEVEL_BREAKOUT_RETEST_DEF.params.map((p) => [p.key, p]));

  it("accepts registry defaults", () => {
    expect(validatePatternValues(LEVEL_BREAKOUT_RETEST_DEF, LEVEL_BREAKOUT_RETEST_DEFAULTS)).toEqual({});
  });

  it("rejects retest_window_bars = 0", () => {
    expect(validateParam(byKey.retest_window_bars, 0)).toMatch(/допустимо/);
  });

  it("rejects risk_reward = 0.5", () => {
    expect(validateParam(byKey.risk_reward, 0.5)).toMatch(/допустимо/);
  });

  it("requires level_timeframe from options", () => {
    expect(validateParam(byKey.level_timeframe, "30min")).toMatch(/списка/);
  });
});

describe("validateParam / levels_sr_breakout schema", () => {
  const byKey = Object.fromEntries(LEVELS_SR_BREAKOUT_DEF.params.map((p) => [p.key, p]));

  it("accepts registry defaults", () => {
    expect(validatePatternValues(LEVELS_SR_BREAKOUT_DEF, LEVELS_SR_BREAKOUT_DEFAULTS)).toEqual({});
  });

  it("rejects retest_window_bars below min", () => {
    expect(validateParam(byKey.retest_window_bars, 0)).toMatch(/допустимо/);
  });

  it("rejects swing_window above max", () => {
    expect(validateParam(byKey.swing_window, 51)).toMatch(/допустимо/);
  });
});

describe("validateParam / levels_sr_support schema", () => {
  const byKey = Object.fromEntries(LEVELS_SR_SUPPORT_DEF.params.map((p) => [p.key, p]));

  it("accepts registry defaults", () => {
    expect(validatePatternValues(LEVELS_SR_SUPPORT_DEF, LEVELS_SR_SUPPORT_DEFAULTS)).toEqual({});
  });

  it("has no retest fields", () => {
    expect(LEVELS_SR_SUPPORT_DEF.params.map((p) => p.key)).toEqual([
      "level_timeframe",
      "level_method",
      "swing_window",
      "impulse_body_ratio",
      "impulse_atr_mult",
      "zone_atr_mult",
      "confirm_windows",
    ]);
  });

  it("rejects swing_window above max", () => {
    expect(validateParam(byKey.swing_window, 51)).toMatch(/допустимо/);
  });

  it("rejects zone_atr_mult below min", () => {
    expect(validateParam(byKey.zone_atr_mult, 0.05)).toMatch(/допустимо/);
  });
});

describe("firstPatternValidationError", () => {
  it("blocks a Lab run when retest_window_bars is out of range", () => {
    const msg = firstPatternValidationError(
      [LEVEL_BREAKOUT_RETEST_DEF],
      { level_breakout_retest: { ...LEVEL_BREAKOUT_RETEST_DEFAULTS, retest_window_bars: 0 } },
    );
    expect(msg).toMatch(/Пробой уровня с ретестом/);
    expect(msg).toMatch(/допустимо/);
  });

  it("blocks a Lab run when composite retest_window_bars is out of range", () => {
    const msg = firstPatternValidationError(
      [LEVELS_SR_BREAKOUT_DEF],
      { levels_sr_breakout: { ...LEVELS_SR_BREAKOUT_DEFAULTS, retest_window_bars: 0 } },
    );
    expect(msg).toMatch(/Поддержка \+ пробой сопротивления/);
    expect(msg).toMatch(/допустимо/);
  });

  it("blocks a Lab run when support-with-tracker swing_window is out of range", () => {
    const msg = firstPatternValidationError(
      [LEVELS_SR_SUPPORT_DEF],
      { levels_sr_support: { ...LEVELS_SR_SUPPORT_DEFAULTS, swing_window: 51 } },
    );
    expect(msg).toMatch(/Поддержка с трекером/);
    expect(msg).toMatch(/допустимо/);
  });

  it("does not flag other chips when they have no params", () => {
    expect(firstPatternValidationError(
      [LEVEL_BREAKOUT_RETEST_DEF],
      { levels_reversal: {}, signal_4h_buy: {} },
    )).toBeNull();
  });
});
