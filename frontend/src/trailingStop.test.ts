/**
 * Issue #146 — the Lab's trailing-stop editor logic: ladder parsing, the #144 client-side
 * pre-flight, and the save payload. Pure functions only; the backend stays the authority
 * (these tests assert the two agree, not that the browser gets the last word).
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  buildTrailingStopPayload,
  defaultLadder,
  describeLadder,
  emptyLadder,
  matchesDefaultGrid,
  numToText,
  parseR,
  reasonCodeShort,
  reasonText,
  rowsFromLadder,
  shareOfTake,
  stepsFromRows,
  validateLadder,
  TRAILING_REASON,
} from "./trailingStop";
import { ladder, TRAILING_STOP_SCHEMA as SCHEMA } from "./test/fixtures";

// Vitest runs these in jsdom, where import.meta.url is an http URL that readFileSync
// rejects, so resolve against the frontend root the runner is launched from.
const GRID_SOURCE = resolve(process.cwd(), "../analytics/issue-143-trailing-robustness/grids.json");

/** Reasons only — the array form keeps the assertions readable. */
const reasons = (rows: ReturnType<typeof ladder>, enabled = true) =>
  validateLadder(rows, SCHEMA, enabled).reasons;

/** A ladder #144 would accept: no code, and no row flagged either. */
const expectClean = (rows: ReturnType<typeof ladder>) => {
  const out = validateLadder(rows, SCHEMA, true);
  expect(out.reasons).toEqual([]);
  out.rowCodes.forEach((codes) => expect(codes).toBeUndefined());
};

describe("the schema the editor renders from", () => {
  it("is the approved ultra_late_tight grid, decimals intact", () => {
    // The #144 precision rule: 1.9 / 2.4 / 2.9 must not become 2.0 / 2.5 / 3.0 anywhere.
    expect(SCHEMA.steps.map((s) => s.stop)).toEqual([1.9, 2.4, 2.9]);
    expect(SCHEMA.steps.map((s) => s.trigger)).toEqual([2, 2.5, 3]);
    expect(SCHEMA.default_grid).toBe("ultra_late_tight");
    expect(SCHEMA.enabled).toBe(false);
  });

  it("agrees with the published lattice the default is drawn from", () => {
    const raw = JSON.parse(readFileSync(GRID_SOURCE, "utf-8"));
    const grid = raw.grids.find((g: { id: string }) => g.id === SCHEMA.default_grid);
    expect(grid).toBeDefined();
    expect(grid.steps).toEqual(SCHEMA.steps);
  });

  it("offers a fine enough keystroke to type the default gap", () => {
    const gaps = SCHEMA.steps.slice(1).map((s, i) => Math.abs(s.stop - SCHEMA.steps[i].stop));
    expect(SCHEMA.input_step).toBeLessThanOrEqual(Math.min(...gaps));
  });
});

describe("parse / render round trip", () => {
  it("keeps 0 and empty string apart", () => {
    expect(parseR("")).toBeNull();
    expect(parseR("  ")).toBeNull();
    expect(parseR("0")).toBe(0);
    expect(parseR("1.9")).toBe(1.9);
    expect(parseR("abc")).toBeNull();
    expect(parseR("Infinity")).toBeNull();
    expect(parseR("NaN")).toBeNull();
  });

  it("renders contract numbers without inventing a false zero", () => {
    expect(numToText(null)).toBe("");
    expect(numToText(undefined)).toBe("");
    expect(numToText(0)).toBe("0");
    expect(numToText(1.9)).toBe("1.9");
    expect(numToText("x")).toBe("");
  });

  it("round-trips the production ladder through the editor's string form", () => {
    const stored = { enabled: true, steps: SCHEMA.steps };
    const rows = rowsFromLadder(stored);
    expect(rows).toEqual(ladder(["2", "1.9"], ["2.5", "2.4"], ["3", "2.9"]));
    expect(stepsFromRows(rows)).toEqual(SCHEMA.steps);
    expect(matchesDefaultGrid(rows, SCHEMA)).toBe(true);
  });

  it("drops half-typed rows instead of sending them", () => {
    expect(stepsFromRows(ladder(["2", "1.9"], ["2.5", ""]))).toEqual([{ trigger: 2, stop: 1.9 }]);
  });

  it("describes a ladder for the section badge", () => {
    expect(describeLadder(defaultLadder(SCHEMA))).toBe("+2R→+1.9R, +2.5R→+2.4R, +3R→+2.9R");
  });
});


describe("client-side validation mirrors #144", () => {
  it("accepts the approved production ladder", () => {
    expectClean(defaultLadder(SCHEMA));
  });

  it("accepts a break-even rung parked at 0R", () => {
    // #155 made stop = 0R a legal bound; min_stop is inclusive, so this must pass.
    expectClean(ladder(["2", "0"]));
  });

  it("rejects a stop at or above its own trigger", () => {
    expect(reasons(ladder(["2", "2"]))).toEqual([TRAILING_REASON.stepInvalid]);
    expect(reasons(ladder(["2", "2.1"]))).toEqual([TRAILING_REASON.stepInvalid]);
  });

  it("rejects a trigger at 0R because min_trigger is exclusive", () => {
    expect(reasons(ladder(["0", "0"]))).toEqual([TRAILING_REASON.stepInvalid]);
  });

  it("rejects values outside the schema bounds", () => {
    expect(reasons(ladder(["9", "8"]))).toEqual([TRAILING_REASON.stepInvalid]);
    expect(reasons(ladder(["1", "4"]))).toEqual([TRAILING_REASON.stepInvalid]);
  });

  it("rejects a ladder that is too long and names only the overflow rows", () => {
    // Seven rungs, every one of them individually legal — only the length is wrong.
    const rows = ladder(
      ...(Array.from({ length: SCHEMA.max_steps + 1 }, (_, i) => {
        const trigger = (i + 1) * 0.5;
        return [String(trigger), String(Math.min(trigger - 0.1, SCHEMA.max_stop))];
      }) as unknown as [string, string][]),
    );
    for (const row of rows) {
      expect(validateLadder([row], SCHEMA, true).reasons).toEqual([]);
    }
    expect(reasons(rows)).toEqual([TRAILING_REASON.tooManySteps]);
    const out = validateLadder(rows, SCHEMA, true);
    expect(out.rowCodes[SCHEMA.max_steps]).toEqual([TRAILING_REASON.tooManySteps]);
    expect(out.rowCodes[0]).toBeUndefined();
  });

  it("requires a rung when the switch is on but nothing was typed", () => {
    expect(reasons(emptyLadder())).toEqual([TRAILING_REASON.disabled]);
    // switched off with nothing to arm is the shipped default, not an error
    expect(reasons(emptyLadder(), false)).toEqual([]);
  });

  it("rejects a stop that falls as the trigger rises", () => {
    expect(reasons(ladder(["2", "1.9"], ["3", "1.5"]))).toEqual([TRAILING_REASON.notMonotonic]);
  });

  it("rejects two different stops on one trigger", () => {
    expect(reasons(ladder(["2", "1"], ["2", "1.5"]))).toEqual([TRAILING_REASON.notMonotonic]);
  });

  it("collapses an exact duplicate rather than rejecting it", () => {
    // #144: normalization dedupes exact repeats, so the editor must not block the save.
    expectClean(ladder(["2", "1.9"], ["2", "1.9"]));
  });

  it("still checks the bounds of a ladder that is switched off", () => {
    // The engine arms this the instant the flag flips, so it must never reach the database.
    expect(reasons(ladder(["9", "8"]), false)).toEqual([TRAILING_REASON.stepInvalid]);
  });

  it("sorts before judging order, so typing sequence does not matter", () => {
    expectClean(ladder(["3", "2.9"], ["2", "1.9"]));
  });

  it("emits only codes the schema declared", () => {
    const emitted = [
      ...reasons(defaultLadder(SCHEMA)),
      ...reasons(ladder(["2", "2"])),
      ...reasons(ladder(["2", "1.9"], ["3", "1.5"])),
      ...reasons(emptyLadder()),
    ];
    expect(new Set(emitted).size).toBe(3);
    for (const code of emitted) expect(SCHEMA.reason_codes).toContain(code);
  });

  it("labels reasons in both languages and never invents one", () => {
    expect(reasonText(TRAILING_REASON.disabled, "ru")).toContain("лестница пуста");
    expect(reasonText(TRAILING_REASON.disabled, "en")).toContain("ladder is empty");
    expect(reasonText("trailing_made_up", "ru")).toBe("trailing_made_up");
    expect(reasonCodeShort("trailing_not_monotonic")).toBe("not_monotonic");
  });

describe("save payload", () => {
  it("sends nothing for a strategy that never carried the key", () => {
    expect(
      buildTrailingStopPayload({
        touched: false,
        stored: undefined,
        enabled: false,
        rows: defaultLadder(SCHEMA),
        schema: SCHEMA,
      }),
    ).toBeNull();
  });

  it("echoes a stored ladder while untouched", () => {
    const stored = { enabled: false, steps: [{ trigger: 2, stop: 1.9 }] };
    expect(
      buildTrailingStopPayload({
        touched: false,
        stored,
        enabled: false,
        rows: rowsFromLadder(stored),
        schema: SCHEMA,
      }),
    ).toEqual(stored);
  });

  it("sends an explicit opt-out once the operator has touched the block", () => {
    expect(
      buildTrailingStopPayload({
        touched: true,
        stored: undefined,
        enabled: false,
        rows: defaultLadder(SCHEMA),
        schema: SCHEMA,
      }),
    ).toEqual({ enabled: false, steps: SCHEMA.steps });
  });

  it("persists the default grid with its tenths when applied from the schema", () => {
    const payload = buildTrailingStopPayload({
      touched: true,
      stored: undefined,
      enabled: true,
      rows: defaultLadder(SCHEMA),
      schema: SCHEMA,
    });
    expect(payload).toEqual({ enabled: true, steps: SCHEMA.steps });
    // survives JSON the way the API will receive it
    expect(JSON.parse(JSON.stringify(payload)).steps[0].stop).toBe(1.9);
  });

  it("refuses to build a payload with no schema to read bounds from", () => {
    expect(
      buildTrailingStopPayload({
        touched: true,
        stored: undefined,
        enabled: true,
        rows: defaultLadder(SCHEMA),
        schema: null,
      }),
    ).toBeNull();
  });
});

describe("derived take-relative hint", () => {
  it("places a rung against the strategy's take", () => {
    // risk 1 : reward 3 → the take sits at +3R, so +2R is two thirds of the way there.
    expect(shareOfTake(2, { risk: 1, reward: 3 })).toBeCloseTo(2 / 3, 10);
    expect(shareOfTake(3, { risk: 1, reward: 3 })).toBeCloseTo(1, 10);
  });

  it("stays silent without a usable risk_reward", () => {
    expect(shareOfTake(2, null)).toBeNull();
    expect(shareOfTake(2, { risk: 0, reward: 2 })).toBeNull();
    expect(shareOfTake(2, { risk: Number.NaN, reward: 2 })).toBeNull();
  });
});

});
