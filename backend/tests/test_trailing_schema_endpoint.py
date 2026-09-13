"""Contract tests for GET /api/strategies/trailing-schema - Issue #146 (Epic #142, Block W).

#146 delivered the schema-driven Lab editor for `config.trailing_stop`. The editor is
forbidden from restating any trailing number (handover red line: the constructor renders
from the API schema), so every default, bound, grid name and reason code it shows must come
from this one endpoint - and therefore from `trading_config`, the single source of truth.

These tests pin three things the editor depends on:
  1. the endpoint serves the contract object, not a router-local copy;
  2. the approved default ladder survives the JSON round trip without losing its decimals
     (the #144 precision rule - a snapped 1.9 -> 2.0 would arm a ladder nobody measured);
  3. the default-grid LABEL shipped in the payload really names the grid whose steps are
     in the payload (cross-checked against the published 143-trailing-v3 lattice, the same
     way test_trailing_contract.py pins it on the Python side).

The write-side validation of the same block is Issue #149's, not ours.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics.trading_config import (
    TRAILING_REASON_CODES,
    TRAILING_STOP,
    TRAILING_STOP_DEFAULT_GRID,
    validate_trailing_steps,
)
from app.main import app

client = TestClient(app)

ENDPOINT = "/api/strategies/trailing-schema"

# Same published lattice the #144 contract test reads; the label must agree with it.
ANALYSIS_DIR = Path(__file__).resolve().parents[2] / "analytics" / "issue-143-trailing-robustness"


def _payload() -> dict:
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    return response.json()


def _schema() -> dict:
    body = _payload()
    assert "trailing_stop" in body, "the editor keys its whole section off trailing_stop"
    return body["trailing_stop"]


def _grids() -> dict:
    path = ANALYSIS_DIR / "grids.json"
    if not path.exists():
        pytest.skip("issue-143 analytics directory is not mounted")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {row["id"]: row for row in raw["grids"]}


# ---------------------------------------------------------------------------
# 1. it is the trading_config contract, served verbatim
# ---------------------------------------------------------------------------

def test_endpoint_serves_the_contract_not_a_router_copy():
    schema = _schema()
    for key in ("enabled", "steps", "max_steps", "min_trigger",
                "max_trigger", "min_stop", "max_stop"):
        assert schema[key] == TRAILING_STOP[key], f"{key} drifted from trading_config"


def test_shipped_default_is_still_switched_off():
    # #144 ships the policy OFF; the editor must not be able to imply otherwise.
    assert _schema()["enabled"] is False


def test_reason_codes_are_the_stable_vocabulary():
    assert _schema()["reason_codes"] == TRAILING_REASON_CODES


# ---------------------------------------------------------------------------
# 2. the approved ladder survives HTTP JSON without losing its decimals
# ---------------------------------------------------------------------------

def test_default_ladder_survives_the_json_round_trip():
    steps = _schema()["steps"]
    assert steps == TRAILING_STOP["steps"]
    # 1.9 / 2.4 / 2.9 are the whole point of ultra_late_tight: a float that snapped to
    # 0.5R on the way out would arm a different ladder than #143 measured.
    assert [step["stop"] for step in steps] == [1.9, 2.4, 2.9]
    assert all(isinstance(step["trigger"], float) for step in steps)
    assert all(isinstance(step["stop"], float) for step in steps)


def test_default_ladder_validates_clean():
    assert validate_trailing_steps(_schema()["steps"]) == []


def test_input_step_is_fine_enough_for_the_default_gap():
    # A coarser keystroke than the default's own gap would make the grid untypeable.
    schema = _schema()
    gaps = [
        abs(a["stop"] - b["stop"])
        for a, b in zip(schema["steps"], schema["steps"][1:])
    ]
    assert gaps, "the default ladder is expected to have more than one step"
    assert schema["input_step"] <= min(gaps)


# ---------------------------------------------------------------------------
# 3. the label names the grid whose steps are in the payload
# ---------------------------------------------------------------------------

def test_default_grid_label_names_the_steps_in_the_payload():
    schema = _schema()
    assert schema["default_grid"] == TRAILING_STOP_DEFAULT_GRID
    grid = _grids().get(schema["default_grid"])
    assert grid is not None, "the payload names a grid the published lattice does not have"
    assert grid["steps"] == schema["steps"], (
        "the button caption and the button's values disagree - the operator would be "
        "cross-checking the wrong grid against #144"
    )


def test_endpoint_is_read_only():
    before = json.loads(json.dumps(TRAILING_STOP))
    _payload()
    _payload()
    assert TRAILING_STOP == before, "serving the schema mutated the contract"
