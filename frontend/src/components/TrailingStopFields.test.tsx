import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TrailingStopFields from "./TrailingStopFields";
import { defaultLadder, emptyLadder, validateLadder } from "../trailingStop";
import { ladder, TRAILING_STOP_SCHEMA as SCHEMA } from "../test/fixtures";
import type { TrailingStopSchema } from "../types";

/* ==================================================================
   TrailingStopFields (Issue #146) — the red line this file defends is
   "the Lab renders the ladder from the API schema, never from a frontend
   constant". Several tests therefore assert against a *mutated* schema:
   if any value were baked into the component, the default-schema
   assertions would keep passing and these would not.
   ================================================================== */

function renderFields(overrides: Partial<Parameters<typeof TrailingStopFields>[0]> = {}) {
  const props = {
    schema: SCHEMA,
    enabled: true,
    rows: defaultLadder(SCHEMA),
    validation: validateLadder(defaultLadder(SCHEMA), SCHEMA, true),
    locked: false,
    riskReward: { risk: 1, reward: 3 },
    locale: "ru" as const,
    onToggle: vi.fn(),
    onEditRow: vi.fn(),
    onAddRow: vi.fn(),
    onRemoveRow: vi.fn(),
    onApplyDefault: vi.fn(),
    ...overrides,
  };
  render(<TrailingStopFields {...props} />);
  return props;
}

const spins = () => screen.getAllByRole("spinbutton") as HTMLInputElement[];
const triggers = () => screen.getAllByLabelText(/триггер, R/) as HTMLInputElement[];
// The caption is "⤺ Применить дефолтную сетку · <grid>"; match the grid token inside it.
const buttonFor = (grid: string) =>
  screen.getByRole("button", { name: new RegExp(grid, "i") });
const defaultButton = () => buttonFor(SCHEMA.default_grid);

describe("TrailingStopFields · renders from the schema", () => {
  it("draws one row per step of the ladder it is handed", () => {
    renderFields();
    expect(spins()).toHaveLength(SCHEMA.steps.length * 2);
  });

  it("shows the approved grid's own tenths, not rounded halves", () => {
    renderFields();
    const stops = screen.getAllByLabelText(/стоп, R/).map((el) => (el as HTMLInputElement).value);
    expect(stops).toEqual(["1.9", "2.4", "2.9"]);
  });

  it("takes the input resolution from the schema, so the ladder stays typeable", () => {
    renderFields();
    expect(spins()[0]).toHaveAttribute("step", String(SCHEMA.input_step));
  });

  it("bounds the inputs with the schema's bounds", () => {
    renderFields();
    expect(spins()[0].min).toBe(String(SCHEMA.min_trigger));
    expect(spins()[0].max).toBe(String(SCHEMA.max_trigger));
  });

  it("carries the grid name in the button caption", () => {
    renderFields();
    expect(defaultButton()).toBeInTheDocument();
  });

  it("follows a different schema without being rewritten (no baked-in numbers)", () => {
    // A ladder and bounds the shipped default never had: if the component read its own
    // constants, 1.75 / 0.05 / max 4 could not appear on screen.
    const other: TrailingStopSchema = {
      ...SCHEMA,
      steps: [{ trigger: 1.75, stop: 1.7 }],
      max_steps: 4,
      max_trigger: 4,
      input_step: 0.05,
      default_grid: "some_other_grid",
    };
    renderFields({ schema: other, rows: defaultLadder(other) });
    const cells = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    expect(cells).toHaveLength(2);
    expect(cells[0].value).toBe("1.75");
    expect(cells[0]).toHaveAttribute("step", "0.05");
    expect(cells[0]).toHaveAttribute("max", "4");
    expect(screen.getByRole("button", { name: /some_other_grid/ })).toBeInTheDocument();
  });

  it("switches the copy to English on the same markup", () => {
    renderFields({ locale: "en" });
    expect(screen.getByText("trigger, R")).toBeInTheDocument();
  });

describe("TrailingStopFields · editing", () => {
  it("reports which cell changed and to what", () => {
    const onEditRow = vi.fn();
    renderFields({ onEditRow });
    fireEvent.change(triggers()[0], { target: { value: "2.2" } });
    expect(onEditRow).toHaveBeenCalledWith(0, "trigger", "2.2");
  });

  it("refuses to add a rung past the schema maximum", () => {
    const full = ladder(
      ...(Array.from({ length: SCHEMA.max_steps }, (_, i) => [String(i + 1), "0.5"]) as [string, string][]),
    );
    renderFields({ rows: full, validation: validateLadder(full, SCHEMA, true) });
    expect(screen.getByRole("button", { name: /^\+ / })).toBeDisabled();
  });

  it("allows another rung while the ladder is shorter than the maximum", () => {
    renderFields();
    expect(screen.getByRole("button", { name: /^\+ / })).toBeEnabled();
  });

  it("disables 'apply default grid' when the ladder already is the default", () => {
    renderFields();
    expect(defaultButton()).toBeDisabled();
  });

  it("enables it again once the ladder drifts from the default", () => {
    const rows = defaultLadder(SCHEMA);
    rows[0] = { trigger: "2", stop: "1.5" };
    renderFields({ rows, validation: validateLadder(rows, SCHEMA, true) });
    expect(defaultButton()).toBeEnabled();
  });

  it("hands the chosen grid back to the caller when pressed", () => {
    const onApplyDefault = vi.fn();
    const rows = defaultLadder(SCHEMA);
    rows[0] = { trigger: "2", stop: "1.5" };
    renderFields({ rows, onApplyDefault });
    fireEvent.click(defaultButton());
    expect(onApplyDefault).toHaveBeenCalledOnce();
  });

  it("keeps every control dead for a locked strategy", () => {
    renderFields({ locked: true });
    expect(triggers()[0]).toHaveAttribute("readonly");
    expect(defaultButton()).toBeDisabled();
  });
});

describe("TrailingStopFields · validation surfacing", () => {
  it("names the #144 reason code and explains it", () => {
    const rows = ladder(["2", "1.9"], ["3", "1.5"]);
    renderFields({ rows, validation: validateLadder(rows, SCHEMA, true) });
    expect(screen.getByText("not_monotonic")).toBeInTheDocument();
    expect(screen.getByText(/монотонн/i)).toBeInTheDocument();
  });

  it("marks the offending row rather than the whole ladder", () => {
    const rows = ladder(["2", "1.9"], ["3", "1.5"]);
    renderFields({ rows, validation: validateLadder(rows, SCHEMA, true) });
    expect(triggers()[1].className).toContain("border-rose-500");
    expect(triggers()[0].className).not.toContain("border-rose-500");
  });

  it("says nothing when the ladder is clean", () => {
    renderFields();
    expect(screen.queryByText(/not_monotonic|step_invalid|too_many_steps|disabled/)).toBeNull();
  });

  it("treats an unarmed empty ladder as the shipped default, not an error", () => {
    const rows = emptyLadder();
    renderFields({ rows, validation: validateLadder(rows, SCHEMA, false), enabled: false });
    expect(screen.queryByText(/not_monotonic|step_invalid|too_many_steps|disabled/)).toBeNull();
  });
});

describe("TrailingStopFields · derived take hint", () => {
  it("places each rung against the strategy's take", () => {
    // risk 1 : reward 3 puts the take at +3R, so +2R is 67 % of the way and +3R is all of it.
    renderFields();
    expect(screen.getByText("67%")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("falls back to a dash when risk_reward is off", () => {
    renderFields({ riskReward: null });
    expect(screen.queryByText("67%")).toBeNull();
    expect(screen.getAllByText("—")).toHaveLength(SCHEMA.steps.length);
  });
});

});
