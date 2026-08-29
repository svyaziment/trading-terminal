import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PatternIcon } from "./PatternIcon";
import { LEVEL_BREAKOUT_RETEST_DEF, LEVELS_SR_BREAKOUT_DEF } from "../test/fixtures";

describe("PatternIcon", () => {
  it("renders support_breakout from the API icon, not the pattern id", () => {
    const { container } = render(<PatternIcon icon={LEVELS_SR_BREAKOUT_DEF.icon} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("keeps breakout_up distinct from support_breakout", () => {
    const retest = render(<PatternIcon icon={LEVEL_BREAKOUT_RETEST_DEF.icon} />);
    const composite = render(<PatternIcon icon={LEVELS_SR_BREAKOUT_DEF.icon} />);
    expect(LEVELS_SR_BREAKOUT_DEF.icon).toBe("support_breakout");
    expect(LEVEL_BREAKOUT_RETEST_DEF.icon).toBe("breakout_up");
    expect(retest.container.innerHTML).not.toBe(composite.container.innerHTML);
  });

  it("renders nothing for an unknown icon", () => {
    const { container } = render(<PatternIcon icon="unknown_glyph" />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
