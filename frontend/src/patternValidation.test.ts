import { describe, expect, it } from "vitest";
import { firstPatternValidationError, validateParam, validatePatternValues } from "./patternValidation";
import { LEVEL_BREAKOUT_RETEST_DEF, LEVEL_BREAKOUT_RETEST_DEFAULTS } from "./test/fixtures";

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

describe("firstPatternValidationError", () => {
  it("blocks a Lab run when retest_window_bars is out of range", () => {
    const msg = firstPatternValidationError(
      [LEVEL_BREAKOUT_RETEST_DEF],
      { level_breakout_retest: { ...LEVEL_BREAKOUT_RETEST_DEFAULTS, retest_window_bars: 0 } },
    );
    expect(msg).toMatch(/Пробой уровня с ретестом/);
    expect(msg).toMatch(/допустимо/);
  });

  it("does not flag other chips when they have no params", () => {
    expect(firstPatternValidationError(
      [LEVEL_BREAKOUT_RETEST_DEF],
      { levels_reversal: {}, signal_4h_buy: {} },
    )).toBeNull();
  });
});
