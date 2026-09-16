/**
 * Issue #150: trailingStatus.ts pure module tests.
 *
 * Verifies that all UI helpers are pure (no side effects), locale-aware,
 * and produce correct labels, tones, and summary cards.
 */
import { describe, expect, it } from "vitest";
import {
  trailingLabel,
  trailingEnabledTone,
  activeStopTone,
  stepReachedTone,
  fmtTrailingNum,
  fmtRiskR,
  fmtStopPrice,
  fmtStepReached,
  buildTrailingSummaryCards,
} from "./trailingStatus";

describe("trailingLabel", () => {
  it("returns ru label by default", () => {
    expect(trailingLabel("trailing_enabled", "ru")).toBe("Трейлинг");
  });
  it("returns en label when locale is en", () => {
    expect(trailingLabel("trailing_enabled", "en")).toBe("Trailing");
  });
  it("falls back to key for unknown keys", () => {
    expect(trailingLabel("unknown_key", "ru")).toBe("unknown_key");
  });
});

describe("trailingEnabledTone", () => {
  it("returns sky tone for true", () => {
    expect(trailingEnabledTone(true)).toContain("sky");
  });
  it("returns slate tone for false", () => {
    expect(trailingEnabledTone(false)).toContain("slate");
  });
  it("returns slate tone for null", () => {
    expect(trailingEnabledTone(null)).toContain("slate");
  });
});

describe("activeStopTone", () => {
  it("returns amber tone when stop is present", () => {
    expect(activeStopTone(true)).toContain("amber");
  });
  it("returns slate tone when no stop", () => {
    expect(activeStopTone(false)).toContain("slate");
  });
});

describe("stepReachedTone", () => {
  it("returns emerald for positive step", () => {
    expect(stepReachedTone(2)).toContain("emerald");
  });
  it("returns slate for null", () => {
    expect(stepReachedTone(null)).toContain("slate");
  });
  it("returns slate for zero", () => {
    expect(stepReachedTone(0)).toContain("slate");
  });
});

describe("fmtTrailingNum", () => {
  it("formats finite number", () => {
    expect(fmtTrailingNum(123.456, 2)).toBe("123.46");
  });
  it("returns dash for null", () => {
    expect(fmtTrailingNum(null)).toBe("—");
  });
  it("returns dash for NaN", () => {
    expect(fmtTrailingNum(NaN)).toBe("—");
  });
});

describe("fmtRiskR", () => {
  it("adds + sign for positive", () => {
    expect(fmtRiskR(1.5)).toBe("+1.50R");
  });
  it("no + for negative", () => {
    expect(fmtRiskR(-0.5)).toBe("-0.50R");
  });
  it("dash for null", () => {
    expect(fmtRiskR(null)).toBe("—");
  });
});

describe("fmtStopPrice", () => {
  it("formats to 3 decimals", () => {
    expect(fmtStopPrice(123.4)).toBe("123.400");
  });
  it("dash for undefined", () => {
    expect(fmtStopPrice(undefined)).toBe("—");
  });
});

describe("fmtStepReached", () => {
  it("returns integer string for positive", () => {
    expect(fmtStepReached(2)).toBe("2");
  });
  it("dash for zero", () => {
    expect(fmtStepReached(0)).toBe("—");
  });
  it("dash for null", () => {
    expect(fmtStepReached(null)).toBe("—");
  });
});

describe("buildTrailingSummaryCards", () => {
  it("returns 4 cards when summary is provided", () => {
    const cards = buildTrailingSummaryCards(
      { trailing_closed: 3, trailing_closed_pnl_rub: 1500, trailing_open: 2, active_stop_count: 1 },
      "ru",
    );
    expect(cards).toHaveLength(4);
    expect(cards[0].key).toBe("trailing_closed");
    expect(cards[0].value).toBe("3");
    expect(cards[1].key).toBe("trailing_closed_pnl");
    expect(cards[1].value).toContain("+");
    expect(cards[1].tone).toContain("emerald");
  });

  it("returns empty array for null summary", () => {
    expect(buildTrailingSummaryCards(null, "ru")).toHaveLength(0);
  });

  it("uses en labels when locale is en", () => {
    const cards = buildTrailingSummaryCards(
      { trailing_closed: 1, trailing_closed_pnl_rub: -500, trailing_open: 0, active_stop_count: 0 },
      "en",
    );
    expect(cards[0].label).toBe("Closed by trailing");
    expect(cards[1].label).toBe("Trailing PnL");
    expect(cards[1].tone).toContain("rose");
  });
});