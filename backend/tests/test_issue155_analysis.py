"""Tests for the Issue #155 follow-up on the #143 robustness lattice.

#155 widened the published lattice to `143-trailing-v3` (8 grids) and, most
importantly, made the lattice analysis machine-readable: `run.py` serializes the
`lattice` slice into `summary.json` instead of leaving it only in `report.md`.
These tests are read-only: they inspect the published artifacts and call
`lattice_analysis()` on synthetic rows (no DB, no re-run of #143).
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = BACKEND_ROOT.parent / "analytics/issue-143-trailing-robustness"

SCHEMA_V3 = "143-trailing-v3"
BASE_GRID_ID = "ref139"
CONTROL_GRID_ID = "tight_after_take"
PO_GRID_ID = "ultra_late_tight"
SINGLE_STEP_IDS = {"single_step_2_15", "breakeven_2_0"}
NEW_GRID_IDS = {PO_GRID_ID, *SINGLE_STEP_IDS}
V2_GRID_IDS = {
    BASE_GRID_ID,
    CONTROL_GRID_ID,
    "late_conservative",
    "three_step_steady",
    "two_step_aggressive",
}


def _json(filename: str) -> dict:
    path = ANALYSIS_DIR / filename
    if not path.exists():
        pytest.skip("issue-143 analytics directory is not mounted")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_run():
    path = ANALYSIS_DIR / "run.py"
    if not path.exists():
        pytest.skip("issue-143 analytics directory is not mounted")
    spec = importlib.util.spec_from_file_location("issue143_run", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # runtime deps of the sim harness are optional here
        pytest.skip(f"issue-143 run.py cannot be imported: {exc}")
    return module


def test_grids_declare_schema_v3_with_eight_grids():
    grids = _json("grids.json")
    assert grids["schema"] == SCHEMA_V3
    rows = grids["grids"]
    assert len(rows) == 8, f"expected 8 grids, found {len(rows)}"
    assert {r["id"] for r in rows} == V2_GRID_IDS | NEW_GRID_IDS


def test_grid_shape_covers_single_multi_and_po_probe():
    rows = {r["id"]: r for r in _json("grids.json")["grids"]}
    singles = {gid for gid, r in rows.items() if len(r["steps"]) == 1}
    multis = [gid for gid, r in rows.items() if len(r["steps"]) >= 2]
    assert singles == SINGLE_STEP_IDS
    assert len(multis) == 6
    # a flat stop at breakeven (0R) is a valid sensitivity bound for trailing value
    assert [s["stop"] for s in rows["breakeven_2_0"]["steps"]] == [0.0]
    # PO probe: latest trigger of the lattice and the tightest gap (0.1R)
    po_steps = rows[PO_GRID_ID]["steps"]
    assert max(s["trigger"] for s in po_steps) == 3.0
    assert min(s["trigger"] - s["stop"] for s in po_steps) == pytest.approx(0.1)
    for row in rows.values():
        triggers = [s["trigger"] for s in row["steps"]]
        stops = [s["stop"] for s in row["steps"]]
        assert triggers == sorted(triggers) and stops == sorted(stops), row["id"]
        assert all(0.0 <= st < tr for st, tr in zip(stops, triggers)), row["id"]


def test_grids_json_has_no_stale_v2_leftovers():
    grids = _json("grids.json")
    # the PO probe gap now lives inside a grid, not in a lattice-wide parameter
    assert "tight_gap_r" not in grids
    assert grids["baseline_grid_id"] == BASE_GRID_ID
    assert grids["control"]["grid_id"] == CONTROL_GRID_ID
    assert grids["stakeholder"]["grid_id"] == PO_GRID_ID


def test_summary_publishes_the_lattice_slice():
    summary = _json("summary.json")
    assert summary["schema"] == SCHEMA_V3
    assert summary["n_grids"] == 8 == len(summary["grid_rows"])
    lattice = summary.get("lattice")
    assert isinstance(lattice, dict) and lattice, "summary.json must carry the lattice slice"
    assert lattice["baseline_grid_id"] == BASE_GRID_ID
    assert lattice["control_grid_id"] == CONTROL_GRID_ID
    assert lattice["po_grid_id"] == PO_GRID_ID
    assert lattice["po_gap_r"] == 0.1 and lattice["po_trigger_r"] == 3.0
    # PO-probe materiality threshold (20 RUB) is tighter than the lattice-wide one
    assert lattice["po_material_rub_per_trade"] == 20.0
    assert lattice["material_rub_per_trade"] == 50.0
    step_counts = {g["n_steps"] for g in lattice["groups"]}
    assert 1 in step_counts and any(n >= 2 for n in step_counts)
    singles = next(g for g in lattice["groups"] if g["n_steps"] == 1)
    assert set(singles["grids"]) == SINGLE_STEP_IDS
    assert len(lattice["groups"]) == len(step_counts) == 4


def test_summary_lattice_pairs_and_boundaries():
    lattice = _json("summary.json")["lattice"]
    pairs = {(p["single_grid_id"], p["multi_grid_id"]) for p in lattice["pairs"]}
    # exactly one direct first-step-vs-ladder control: single_step_2_15 against ref139
    assert pairs == {("single_step_2_15", BASE_GRID_ID)}
    assert isinstance(lattice["pairs"][0]["equity_delta_rub"], (int, float))
    tagged = {b["grid_id"]: b["tags"] for b in lattice["boundaries"]}
    assert set(tagged) == NEW_GRID_IDS, "the #155 grids must be reported as lattice bounds"
    assert "безубыток" in tagged["breakeven_2_0"]
    assert "PO-проба" in tagged[PO_GRID_ID]
    assert {"design", "stakeholder", "sensitivity"} <= {v["kind"] for v in lattice["verdicts"]}


def test_summary_lattice_agrees_with_the_grid_rows():
    summary = _json("summary.json")
    by_id = {r["grid_id"]: r for r in summary["grid_rows"]}
    assert set(by_id) == V2_GRID_IDS | NEW_GRID_IDS
    lattice = summary["lattice"]
    for boundary in lattice["boundaries"]:
        row = by_id[boundary["grid_id"]]
        assert boundary["final_equity_rub"] == round(row["final_equity_rub"])
        assert boundary["max_drawdown_pct"] == round(row["max_drawdown_pct"], 2)
    for group in lattice["groups"]:
        # groups are keyed by step count, so best/worst of a group stay inside it
        assert group["best_grid_id"] in set(group["grids"])
        assert group["equity_best_rub"] >= group["equity_median_rub"]


def _row(gid: str, steps: list[tuple[float, float]], equity: float, dd: float,
         avg_pnl: float = 15.0, win_rate: float = 42.0) -> dict:
    exit_counts = {"take": 10, "trailing": 20, "stop": 5, "initial_stop": 3, "game_over": 0}
    return {
        "grid_id": gid,
        "steps": [{"trigger": t, "stop": s} for t, s in steps],
        "final_equity_rub": equity,
        "max_drawdown_pct": dd,
        "profit_factor": 1.3,
        "win_rate_pct": win_rate,
        "avg_trade_pnl_rub": avg_pnl,
        "exit_type_counts": exit_counts,
    }


def test_lattice_analysis_groups_steps_without_hardcoded_ids():
    run = _load_run()
    payload = {
        "baseline_grid_id": "ladder",
        "control_grid_id": "ladder",
        "thresholds": {"material_rub_per_trade": 50.0},
        "stakeholder": {"grid_id": "probe"},
        "robustness": {
            "ladder": {"total": 70.0}, "first_step": {"total": 60.0}, "probe": {"total": 65.0}
        },
        "grid_rows": [
            _row("ladder", [(2.0, 1.5), (2.5, 2.0)], 60_000.0, 12.0),
            _row("first_step", [(2.0, 1.5)], 58_000.0, 11.0),
            _row("probe", [(3.0, 2.9)], 61_000.0, 13.0),
        ],
    }
    lattice = run.lattice_analysis(payload)
    assert lattice, "the lattice slice must never come out empty"
    assert {g["n_steps"] for g in lattice["groups"]} == {1, 2}
    singles = next(g for g in lattice["groups"] if g["n_steps"] == 1)
    assert set(singles["grids"]) == {"first_step", "probe"}
    # pairing is driven by the first step of a ladder, not by grid names
    assert {(p["single_grid_id"], p["multi_grid_id"]) for p in lattice["pairs"]} == {
        ("first_step", "ladder")
    }
    assert lattice["pairs"][0]["equity_delta_rub"] == 2000.0
    assert lattice["pairs"][0]["added_steps"] == "2.5→2"
    assert lattice["po_grid_id"] == "probe" and lattice["po_gap_r"] == pytest.approx(0.1)
    tags = {b["grid_id"]: b["tags"] for b in lattice["boundaries"]}
    assert "PO-проба" in tags["probe"] and "одношаговая" in tags["first_step"]
    # boundaries come out ranked by capital, richest first
    assert [b["grid_id"] for b in lattice["boundaries"]] == ["probe", "first_step"]
    # no 0R stop in this payload, so the breakeven bound (sensitivity) must not appear
    assert {v["kind"] for v in lattice["verdicts"]} == {"design", "stakeholder"}


def test_lattice_analysis_flags_a_breakeven_step_as_a_bound():
    run = _load_run()
    payload = {
        "baseline_grid_id": "ladder",
        "control_grid_id": "tight",
        "thresholds": {"material_rub_per_trade": 50.0},
        "stakeholder": {"grid_id": "flat"},
        "robustness": {},
        "grid_rows": [
            _row("ladder", [(2.0, 1.5), (2.5, 2.0)], 60_000.0, 12.0),
            _row("tight", [(2.0, 1.9), (2.5, 2.4)], 59_500.0, 12.5),
            # stop parked exactly at entry: the flat bound where trailing stops paying
            _row("flat", [(2.0, 0.0)], 59_000.0, 11.5),
        ],
    }
    lattice = run.lattice_analysis(payload)
    tags = {b["grid_id"]: b["tags"] for b in lattice["boundaries"]}
    assert "безубыток" in tags["flat"] and "PO-проба" in tags["flat"]
    # the baseline/control grids stay out of the bound list
    assert set(tags) == {"flat"}
    assert lattice["groups"][0]["n_steps"] == 1
    # a 0R stop is reported as the lower bound of trailing value, not as a defect
    assert "sensitivity" in {v["kind"] for v in lattice["verdicts"]}
    bound = next(v for v in lattice["verdicts"] if v["kind"] == "sensitivity")
    assert "flat" in bound["text"] and "0R" in bound["text"]


def test_report_documents_the_grid_shape_debt_slice():
    path = ANALYSIS_DIR / "report.md"
    if not path.exists():
        pytest.skip("issue-143 report is not mounted")
    text = path.read_text(encoding="utf-8")
    assert "Решётка v3" in text, "the report must document the v3 lattice debt slice"
    assert "Проверено 8 сеток" in text, "the report header must count all 8 grids"
    assert "одношаговые" in text and "безубыток" in text
    for gid in NEW_GRID_IDS:
        assert gid in text, f"grid {gid} is missing from report.md"


def test_debt_wording_marks_the_grid_shape_closed_by_issue_155():
    summary = _json("summary.json")
    limitations = " ".join(summary["limitations"])
    next_issues = " ".join(summary["next_issues"])
    assert "8 сеток" in limitations and "Решётка v3" in limitations
    assert "пять" not in limitations.lower(), "the five-grid wording is stale"
    assert "#155" in next_issues and "закрыта" in next_issues


def test_po_recommendation_cites_the_po_materiality_threshold():
    run = _load_run()
    payload = {
        "robustness": {},
        "lattice": {
            "po_gap_r": 0.1, "po_trigger_r": 3.0,
            "best_other_grid_id": "ladder",
            "material_rub_per_trade": 50.0,      # lattice-wide flip threshold
            "po_material_rub_per_trade": 20.0,   # #155 stakeholder threshold
            "pairs": [],
            "boundaries": [
                {"grid_id": "probe", "tags": "PO-проба", "vs_base_rub": 7218.0,
                 "vs_best_rub": 1865.0, "dpptr_vs_base": 2.0, "material": False},
            ],
        },
    }
    recs, _ = run.build_recommendations(payload)
    po_rec = next(r for r in recs if "Проба PO" in r)
    # the PO probe is judged by the stakeholder threshold of #155, not by the wide one
    assert "20 ₽/сделку" in po_rec, po_rec
    assert "50 ₽/сделку" not in po_rec, po_rec


def test_published_po_recommendation_matches_the_lattice_slice():
    summary = _json("summary.json")
    lattice = summary["lattice"]
    po_rec = next(r for r in summary["recommendations"] if "Проба PO" in r)
    threshold = f"{lattice['po_material_rub_per_trade']:g} ₽/сделку"
    assert threshold in po_rec, po_rec
    assert "материально" in po_rec or "в пределах порога материальности" in po_rec
    report = (ANALYSIS_DIR / "report.md").read_text(encoding="utf-8")
    assert threshold in report, "report.md must cite the same PO threshold as summary.json"


def test_published_lattice_groups_partition_the_grid():
    lattice = _json("summary.json")["lattice"]
    groups = lattice["groups"]
    step_counts = [g["n_steps"] for g in groups]
    assert step_counts == sorted(step_counts), "groups must run from sparse to dense"
    members = [gid for g in groups for gid in g["grids"]]
    assert len(members) == len(set(members)) == 8, "each grid must land in exactly one step group"
    assert set(members) == {r["id"] for r in _json("grids.json")["grids"]}


def test_published_lattice_pair_delta_matches_the_grid_rows():
    summary = _json("summary.json")
    equity = {r["grid_id"]: float(r["final_equity_rub"]) for r in summary["grid_rows"]}
    for pair in summary["lattice"]["pairs"]:
        expected = equity[pair["multi_grid_id"]] - equity[pair["single_grid_id"]]
        assert pair["equity_delta_rub"] == pytest.approx(expected, abs=1.0)


def test_published_lattice_materiality_uses_both_thresholds():
    lattice = _json("summary.json")["lattice"]
    wide = lattice["material_rub_per_trade"]
    po_limit = lattice["po_material_rub_per_trade"]
    assert po_limit < wide, "#155 judges the PO probe against its own, tighter bar"
    for row in lattice["boundaries"]:
        limit = po_limit if "PO-проба" in row["tags"] else wide
        assert row["material"] == (abs(row["dpptr_vs_base"]) >= limit), row["grid_id"]


def test_published_lattice_verdicts_stay_tied_to_the_product_owner():
    verdicts = _json("summary.json")["lattice"]["verdicts"]
    joined = " ".join(v["text"] for v in verdicts)
    assert "одношагов" in joined.lower(), "the ladder-versus-one-step question must be answered"
    po = next(v for v in verdicts if v["kind"] == "stakeholder")
    assert PO_GRID_ID in po["text"] and "#144" in po["text"], "the PO bar stays a PO decision"
    bound = next(v for v in verdicts if v["kind"] == "sensitivity")
    assert "breakeven_2_0" in bound["text"] and "0R" in bound["text"]


def test_report_grid_table_matches_the_published_lattice():
    report = (ANALYSIS_DIR / "report.md").read_text(encoding="utf-8")
    section = next(s for s in report.split("\n## ") if s.startswith("3. Решётка сеток"))
    listed = [m for m in re.findall(r"^\|\s+`([^`]+)`\s*\|", section, re.M)
              if m in {r["id"] for r in _json("grids.json")["grids"]}]
    assert len(listed) == len(set(listed)) == 8, (
        f"report section 3 must present all 8 grids exactly once, found {listed}")


MIRROR_DOCS = (
    "docs/agents/handover.md",
    "docs/agents/handover.ru.md",
    "docs/agents/project-context.md",
    "docs/agents/project-context.ru.md",
    "docs/agents/README.md",
    "analytics/issue-143-trailing-robustness/README.md",
)
EIGHT_GRIDS = re.compile(r"8 (?:grids|сеток)|восьми сеток|eight grids", re.I)
SUPERSEDED = ("143-trailing-v1", "143-trailing-v2")
# the six figures the #155 lattice rework moved, plus the analysis cost of the published run
LATTICE_HEADLINES = ("14 607", "110 434", "95 827", "69.3", "1 797", "1 726", "231.9")


def _doc(rel: str) -> str:
    """A document as one whitespace-normalized string (line wrapping is not a semantic difference)."""
    path = BACKEND_ROOT.parent / rel
    if not path.exists():
        pytest.skip(f"{rel} is not mounted")
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def test_agent_docs_mirror_the_published_lattice_shape():
    """Handover, project-context and the agents index must not lag the published v3 lattice."""
    for rel in MIRROR_DOCS:
        text = _doc(rel)
        assert SCHEMA_V3 in text, f"{rel} must name the {SCHEMA_V3} schema"
        for stale in SUPERSEDED:
            assert stale not in text, f"{rel} still cites the superseded {stale} lattice"
        assert EIGHT_GRIDS.search(text), f"{rel} must state the eight-grid shape"
        assert "#155" in text, f"{rel} must credit #155 for the lattice rework"
        assert "test_issue155_analysis.py" in text, f"{rel} must point at the tracked tests"


def test_ru_and_en_handover_keep_the_same_lattice_numbers():
    en, ru = _doc("docs/agents/handover.md"), _doc("docs/agents/handover.ru.md")
    for probe in LATTICE_HEADLINES:
        assert probe in en, f"EN handover lost the lattice headline {probe!r}"
        assert probe in ru, f"RU handover lost the lattice headline {probe!r}"


def test_report_publishes_the_lattice_slice_and_the_grid_count():
    report = _doc("analytics/issue-143-trailing-robustness/report.md")
    assert f"`{SCHEMA_V3}`" in report, "the report header must cite the lattice schema"
    assert "summary.json.lattice" in report, "the report must point at the machine-readable slice"
    assert "Проверено 8 сеток" in report, "the report must count all published grids"
    summary = _json("summary.json")
    assert summary["n_grids"] == len(_json("grids.json")["grids"]) == 8
    # the report header and summary.json must age together — a stale report is exactly how
    # the docs started to disagree about the lattice in #155
    assert f"{summary['elapsed_sec']} сек" in report, "report.md header elapsed is stale"
