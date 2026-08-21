import { describe, expect, it } from "vitest";
import { groupPatternsByCategory, patternLabel, patternTooltip } from "./patternLab";
import {
  BO_BB_SQUEEZE_DEF,
  LEVEL_BREAKOUT_RETEST_DEF,
  LEVELS_REVERSAL_DEF,
  SIGNAL_4H_BUY_DEF,
} from "./test/fixtures";

describe("groupPatternsByCategory", () => {
  it("places level_breakout_retest in the breakout group next to other breakout chips", () => {
    const groups = groupPatternsByCategory(
      [LEVELS_REVERSAL_DEF, SIGNAL_4H_BUY_DEF, BO_BB_SQUEEZE_DEF, LEVEL_BREAKOUT_RETEST_DEF],
      "ru",
    );
    const breakout = groups.find((g) => g.category === "breakout");
    expect(breakout?.label).toBe("Пробой");
    expect(breakout?.patterns.map((p) => p.id)).toEqual(["BO_BB_Squeeze", "level_breakout_retest"]);
  });
});

describe("pattern labels", () => {
  it("uses API RU/EN names without a hardcoded map", () => {
    expect(patternLabel(LEVEL_BREAKOUT_RETEST_DEF, "ru")).toBe("Пробой уровня с ретестом");
    expect(patternLabel(LEVEL_BREAKOUT_RETEST_DEF, "en")).toBe("Level Breakout Retest");
  });

  it("puts the EN name and role-reversal hint into the RU tooltip", () => {
    const tip = patternTooltip(LEVEL_BREAKOUT_RETEST_DEF, "ru");
    expect(tip).toMatch(/Level Breakout Retest/);
    expect(tip).toMatch(/смен[аы] роли/i);
  });
});
