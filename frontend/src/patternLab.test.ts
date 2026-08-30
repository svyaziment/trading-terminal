import { describe, expect, it } from "vitest";
import {
  applyTopLevelConfirmWindows,
  groupPatternsByCategory,
  patternLabel,
  patternTooltip,
  resolveConfirmWindows,
} from "./patternLab";
import {
  BO_BB_SQUEEZE_DEF,
  LEVEL_BREAKOUT_RETEST_DEF,
  LEVELS_REVERSAL_DEF,
  LEVELS_SR_BREAKOUT_DEF,
  LEVELS_SR_BREAKOUT_DEFAULTS,
  LEVELS_SR_SUPPORT_DEF,
  LEVELS_SR_SUPPORT_DEFAULTS,
  SIGNAL_4H_BUY_DEF,
} from "./test/fixtures";

const LAB_DEFS = [
  LEVELS_REVERSAL_DEF,
  LEVELS_SR_BREAKOUT_DEF,
  LEVELS_SR_SUPPORT_DEF,
  SIGNAL_4H_BUY_DEF,
  BO_BB_SQUEEZE_DEF,
  LEVEL_BREAKOUT_RETEST_DEF,
];

describe("groupPatternsByCategory", () => {
  it("places level_breakout_retest in the breakout group next to other breakout chips", () => {
    const groups = groupPatternsByCategory(LAB_DEFS, "ru");
    const breakout = groups.find((g) => g.category === "breakout");
    expect(breakout?.label).toBe("Пробой");
    expect(breakout?.patterns.map((p) => p.id)).toEqual(["BO_BB_Squeeze", "level_breakout_retest"]);
    expect(breakout?.patterns.map((p) => p.id)).not.toContain("levels_sr_breakout");
    expect(breakout?.patterns.map((p) => p.id)).not.toContain("levels_sr_support");
  });

  it("places levels_sr_breakout in the levels group next to levels_reversal", () => {
    const groups = groupPatternsByCategory(LAB_DEFS, "ru");
    const levels = groups.find((g) => g.category === "levels");
    expect(levels?.label).toBe("Уровни");
    expect(levels?.patterns.map((p) => p.id)).toEqual([
      "levels_reversal",
      "levels_sr_breakout",
      "levels_sr_support",
    ]);
  });

  it("places levels_sr_support in the levels group, not breakout", () => {
    const groups = groupPatternsByCategory(LAB_DEFS, "ru");
    const levels = groups.find((g) => g.category === "levels");
    expect(levels?.label).toBe("Уровни");
    expect(levels?.patterns.map((p) => p.id)).toContain("levels_sr_support");
    expect(levels?.patterns.find((p) => p.id === "levels_sr_support")?.icon).toBe("support_tracker");
  });
});

describe("pattern labels", () => {
  it("uses API RU/EN names without a hardcoded map", () => {
    expect(patternLabel(LEVEL_BREAKOUT_RETEST_DEF, "ru")).toBe("Пробой уровня с ретестом");
    expect(patternLabel(LEVEL_BREAKOUT_RETEST_DEF, "en")).toBe("Level Breakout Retest");
    expect(patternLabel(LEVELS_SR_BREAKOUT_DEF, "ru")).toBe("Поддержка + пробой сопротивления");
    expect(patternLabel(LEVELS_SR_BREAKOUT_DEF, "en")).toBe("Support Reversal + Resistance Breakout");
    expect(patternLabel(LEVELS_SR_SUPPORT_DEF, "ru")).toBe("Поддержка с трекером");
    expect(patternLabel(LEVELS_SR_SUPPORT_DEF, "en")).toBe("Support Reversal (tracker veto)");
  });

  it("puts the EN name and role-reversal hint into the RU tooltip", () => {
    const tip = patternTooltip(LEVEL_BREAKOUT_RETEST_DEF, "ru");
    expect(tip).toMatch(/Level Breakout Retest/);
    expect(tip).toMatch(/смен[аы] роли/i);
  });

  it("puts the EN name and isolated-engine hint into the RU tooltip", () => {
    const tip = patternTooltip(LEVELS_SR_BREAKOUT_DEF, "ru");
    expect(tip).toMatch(/Support Reversal \+ Resistance Breakout/);
    expect(tip).toMatch(/Не AND/);
  });

  it("puts the EN name and tracker-veto hint into the RU tooltip", () => {
    const tip = patternTooltip(LEVELS_SR_SUPPORT_DEF, "ru");
    expect(tip).toMatch(/Support Reversal \(tracker veto\)/);
    expect(tip).toMatch(/LevelsTracker/);
  });
});

describe("resolveConfirmWindows", () => {
  it("reads confirm_windows from the composite when levels_reversal is off", () => {
    expect(
      resolveConfirmWindows(LAB_DEFS, {
        levels_sr_breakout: { ...LEVELS_SR_BREAKOUT_DEFAULTS, confirm_windows: [15] },
      }),
    ).toEqual([15]);
  });

  it("prefers the composite over levels_reversal when both chips are on", () => {
    const levelsWithConfirm = {
      ...LEVELS_REVERSAL_DEF,
      params: LEVELS_SR_BREAKOUT_DEF.params.filter((p) => p.key === "confirm_windows"),
    };
    expect(
      resolveConfirmWindows([levelsWithConfirm, LEVELS_SR_BREAKOUT_DEF], {
        levels_reversal: { confirm_windows: [5] },
        levels_sr_breakout: { confirm_windows: [20] },
      }),
    ).toEqual([20]);
  });

  it("reads confirm_windows from levels_sr_support when it is the only owner", () => {
    expect(
      resolveConfirmWindows(LAB_DEFS, {
        levels_sr_support: { ...LEVELS_SR_SUPPORT_DEFAULTS, confirm_windows: [15] },
      }),
    ).toEqual([15]);
  });

  it("prefers levels_sr_support over levels_reversal when both are on", () => {
    const levelsWithConfirm = {
      ...LEVELS_REVERSAL_DEF,
      params: LEVELS_SR_SUPPORT_DEF.params.filter((p) => p.key === "confirm_windows"),
    };
    expect(
      resolveConfirmWindows([levelsWithConfirm, LEVELS_SR_SUPPORT_DEF], {
        levels_reversal: { confirm_windows: [5] },
        levels_sr_support: { confirm_windows: [25] },
      }),
    ).toEqual([25]);
  });

  it("prefers the composite over levels_sr_support over levels_reversal", () => {
    const levelsWithConfirm = {
      ...LEVELS_REVERSAL_DEF,
      params: LEVELS_SR_SUPPORT_DEF.params.filter((p) => p.key === "confirm_windows"),
    };
    expect(
      resolveConfirmWindows([levelsWithConfirm, LEVELS_SR_SUPPORT_DEF, LEVELS_SR_BREAKOUT_DEF], {
        levels_reversal: { confirm_windows: [5] },
        levels_sr_support: { confirm_windows: [25] },
        levels_sr_breakout: { confirm_windows: [20] },
      }),
    ).toEqual([20]);
  });
});

describe("applyTopLevelConfirmWindows", () => {
  it("maps a legacy list onto the composite owner", () => {
    const applied = applyTopLevelConfirmWindows(
      [LEVELS_SR_BREAKOUT_DEF],
      { levels_sr_breakout: {} },
      [15],
    );
    expect(applied.levels_sr_breakout.confirm_windows).toEqual([15]);
  });

  it("maps a legacy list onto the support-with-tracker owner", () => {
    const applied = applyTopLevelConfirmWindows(
      [LEVELS_SR_SUPPORT_DEF],
      { levels_sr_support: {} },
      [15],
    );
    expect(applied.levels_sr_support.confirm_windows).toEqual([15]);
  });
});
