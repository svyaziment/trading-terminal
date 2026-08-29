import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PatternSettingsModal from "./PatternSettingsModal";
import {
  LEVEL_BREAKOUT_RETEST_DEF,
  LEVEL_BREAKOUT_RETEST_DEFAULTS,
  LEVELS_SR_BREAKOUT_DEF,
  LEVELS_SR_BREAKOUT_DEFAULTS,
} from "../test/fixtures";

function renderModal(
  values: Record<string, unknown> = LEVEL_BREAKOUT_RETEST_DEFAULTS,
  onSave = vi.fn(),
) {
  return render(
    <PatternSettingsModal
      def={LEVEL_BREAKOUT_RETEST_DEF}
      values={values}
      locale="ru"
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
}

describe("PatternSettingsModal · level_breakout_retest", () => {
  it("renders six schema fields from the API def (no hardcoded inputs)", () => {
    renderModal();
    expect(screen.getByRole("group", { name: "Таймфрейм уровней" })).toBeInTheDocument();
    expect(screen.getByLabelText("Окно ретеста (баров ТФ)")).toBeInTheDocument();
    expect(screen.getByLabelText("Зона ретеста (×ATR)")).toBeInTheDocument();
    expect(screen.getByLabelText("Триггер: бычья свеча / пробой high")).toBeInTheDocument();
    expect(screen.getByLabelText("Стоп (×ATR)")).toBeInTheDocument();
    expect(screen.getByLabelText("Take / risk")).toBeInTheDocument();
    expect(screen.getAllByRole("spinbutton")).toHaveLength(4);
  });

  it("shows a validation error for retest_window_bars = 0", () => {
    renderModal();
    const input = screen.getByLabelText("Окно ретеста (баров ТФ)");
    fireEvent.change(input, { target: { value: "0" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/допустимо от 1 до 100/);
    expect(screen.getByRole("button", { name: "Применить" })).toBeDisabled();
  });

  it("shows a validation error for risk_reward = 0.5", () => {
    renderModal();
    const input = screen.getByLabelText("Take / risk");
    fireEvent.change(input, { target: { value: "0.5" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/допустимо от 1 до 5/);
    expect(screen.getByRole("button", { name: "Применить" })).toBeDisabled();
  });

  it("reset restores API defaults", () => {
    renderModal();
    const input = screen.getByLabelText("Окно ретеста (баров ТФ)");
    fireEvent.change(input, { target: { value: "7" } });
    expect(input).toHaveValue(7);
    fireEvent.click(screen.getByRole("button", { name: "Сбросить дефолты" }));
    expect(input).toHaveValue(20);
    expect(screen.getByLabelText("Take / risk")).toHaveValue(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("PatternSettingsModal · levels_sr_breakout", () => {
  function renderComposite(
    values: Record<string, unknown> = LEVELS_SR_BREAKOUT_DEFAULTS,
  ) {
    return render(
      <PatternSettingsModal
        def={LEVELS_SR_BREAKOUT_DEF}
        values={values}
        locale="ru"
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );
  }

  it("renders every schema field from the API def (no hardcoded param keys)", () => {
    renderComposite();
    expect(screen.getByText("Support Reversal + Resistance Breakout")).toBeInTheDocument();
    for (const param of LEVELS_SR_BREAKOUT_DEF.params) {
      expect(screen.getByText(param.label)).toBeInTheDocument();
    }
    expect(screen.getAllByRole("spinbutton")).toHaveLength(
      LEVELS_SR_BREAKOUT_DEF.params.filter((p) => p.type === "number").length,
    );
  });

  it("shows a validation error and blocks Apply for retest_window_bars = 0", () => {
    renderComposite();
    fireEvent.change(screen.getByLabelText("Окно ретеста (баров ТФ)"), { target: { value: "0" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/допустимо от 1 до 100/);
    expect(screen.getByRole("button", { name: "Применить" })).toBeDisabled();
  });
});
