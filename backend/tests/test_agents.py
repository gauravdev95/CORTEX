"""Tests for the CORTEX 7-agent recovery pipeline.

Covers the full fallback chain — Sentinel -> Governor -> Diagnostician ->
Strategist -> Executor -> Verifier -> Historian — by running the agents in
pipeline order against a synthetic AgentContext (no services, no network:
every agent is a pure function of the context), plus the X-API-Key guard on
mutation routes.

Run from backend/:  pytest tests/test_agents.py
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.agents.base import AgentContext
from app.agents.diagnostician import DiagnosticianAgent
from app.agents.executor import ExecutorAgent
from app.agents.governor import GovernorAgent
from app.agents.historian import HistorianAgent
from app.agents.sentinel import SentinelAgent
from app.agents.strategist import StrategistAgent
from app.agents.verifier import VerifierAgent

PIPELINE = [
    SentinelAgent(),
    GovernorAgent(),
    DiagnosticianAgent(),
    StrategistAgent(),
    ExecutorAgent(),
    VerifierAgent(),
    HistorianAgent(),
]

EXPECTED_ORDER = [
    "sentinel",
    "governor",
    "diagnostician",
    "strategist",
    "executor",
    "verifier",
    "historian",
]


def _context(**facts) -> AgentContext:
    return AgentContext(
        workflow_id=uuid.uuid4(),
        signal="latency_spike",
        severity="high",
        workflow_name="payments-api",
        facts={"latency_ms": 850, **facts},
    )


def _run_all(context: AgentContext) -> AgentContext:
    for agent in PIPELINE:
        context = asyncio.run(agent.run(context))
    return context


def test_pipeline_order_is_seven_agents_in_fallback_sequence() -> None:
    assert [agent.name for agent in PIPELINE] == EXPECTED_ORDER


def test_pipeline_order_matches_recovery_service() -> None:
    """The service that runs the pipeline in production must list the same
    seven agents in the same order — a drift here would silently reorder
    recovery. Skipped if the service module cannot be imported (it pulls in
    the HydraDB/event-bus service layer)."""
    pytest.importorskip("app.services.recovery_service")
    from app.services.recovery_service import RecoveryService

    assert [agent.name for agent in RecoveryService()._agents] == EXPECTED_ORDER


def test_sentinel_detects_latency_spike() -> None:
    ctx = asyncio.run(SentinelAgent().run(_context()))
    assert ctx.facts["detected"] is True
    assert ctx.facts["failure_signature"] == "payments-api:latency_spike:high"
    assert any("Sentinel detected latency_spike" in line for line in ctx.timeline)


def test_sentinel_ignores_healthy_signal() -> None:
    ctx = _context()
    ctx.signal = "ok"
    ctx.facts["latency_ms"] = 120
    ctx = asyncio.run(SentinelAgent().run(ctx))
    assert ctx.facts["detected"] is False


def test_governor_approves_only_when_detected() -> None:
    approved = asyncio.run(GovernorAgent().run(_context(detected=True)))
    assert approved.facts["approved"] is True
    blocked = asyncio.run(GovernorAgent().run(_context(detected=False)))
    assert blocked.facts["approved"] is False
    assert "blocked autonomous recovery" in blocked.timeline[-1]


def test_diagnostician_maps_signal_to_root_cause() -> None:
    ctx = asyncio.run(DiagnosticianAgent().run(_context()))
    assert ctx.facts["root_cause"] == "Downstream dependency saturation with retry amplification"
    assert ctx.facts["impact"] == "single_workflow"


def test_diagnostician_critical_severity_is_cross_workflow() -> None:
    ctx = _context()
    ctx.severity = "critical"
    ctx = asyncio.run(DiagnosticianAgent().run(ctx))
    assert ctx.facts["impact"] == "cross_workflow"


def test_strategist_picks_rollback_for_high_severity() -> None:
    ctx = asyncio.run(StrategistAgent().run(_context()))
    assert ctx.facts["strategy"] == "rollback_checkpoint"
    assert ctx.facts["rollback_required"] is True


def test_strategist_picks_restart_for_low_severity_without_memory() -> None:
    ctx = _context()
    ctx.severity = "low"
    ctx = asyncio.run(StrategistAgent().run(ctx))
    assert ctx.facts["strategy"] == "restart_service"
    assert ctx.facts["rollback_required"] is False


def test_executor_applies_validated_strategy_action() -> None:
    ctx = asyncio.run(ExecutorAgent().run(_context(strategy="rollback_checkpoint")))
    assert ctx.facts["action"] == "Checkpoint rollback staged from latest stable snapshot"
    assert 155 <= ctx.facts["latency_after_ms"] <= 300


def test_verifier_confirms_when_latency_normalised_and_approved() -> None:
    ctx = asyncio.run(
        VerifierAgent().run(_context(approved=True, latency_after_ms=210))
    )
    assert ctx.facts["verified"] is True


def test_verifier_fails_when_latency_still_high() -> None:
    ctx = asyncio.run(
        VerifierAgent().run(_context(approved=True, latency_after_ms=900))
    )
    assert ctx.facts["verified"] is False


def test_verifier_fails_when_forced() -> None:
    ctx = asyncio.run(
        VerifierAgent().run(
            _context(approved=True, latency_after_ms=210, force_verification_failure=True)
        )
    )
    assert ctx.facts["verified"] is False


def test_historian_records_memory() -> None:
    ctx = asyncio.run(HistorianAgent().run(_context()))
    assert ctx.facts["memory_written"] is True


def test_full_pipeline_happy_path_recovers() -> None:
    ctx = _run_all(_context())
    assert ctx.facts["detected"] is True
    assert ctx.facts["approved"] is True
    assert ctx.facts["root_cause"]
    assert ctx.facts["strategy"] == "rollback_checkpoint"
    assert ctx.facts["action"]
    assert ctx.facts["verified"] is True
    assert ctx.facts["memory_written"] is True
    # One timeline entry per agent, in pipeline order.
    assert len(ctx.timeline) == 7
    assert ctx.timeline[0].startswith("Sentinel")
    assert ctx.timeline[-1].startswith("Historian")


def test_full_pipeline_verification_failure_still_reaches_historian() -> None:
    """Fallback path: verification fails (forced), but the pipeline does not
    stop — the Historian still records the incident, which is what the
    rollback path in RecoveryService._finalize_workflow depends on."""
    ctx = _run_all(_context(force_verification_failure=True))
    assert ctx.facts["verified"] is False
    assert ctx.facts["memory_written"] is True
    assert len(ctx.timeline) == 7


def test_api_key_guard_allows_when_unset() -> None:
    from app.core import security
    from app.core.config import settings

    assert (settings.cortex_api_key or "") == ""
    asyncio.run(security.require_api_key(None))
    asyncio.run(security.require_api_key("anything"))


def test_api_key_guard_rejects_without_key_when_set(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.core import security

    monkeypatch.setattr(security.settings, "cortex_api_key", "test-key-123")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(security.require_api_key(None))
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        asyncio.run(security.require_api_key("wrong-key"))
    assert exc.value.status_code == 401
    asyncio.run(security.require_api_key("test-key-123"))  # correct key passes
