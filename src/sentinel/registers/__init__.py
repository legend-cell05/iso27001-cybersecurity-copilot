"""The registers: assets, risks, control decisions and evidence.

Everything an organisation records about its own ISMS lives here. The
assessment engine reads these and writes findings; it never writes back into
them, because a tool that silently fixes its own findings is a tool whose
findings mean nothing.
"""

from __future__ import annotations

from sentinel.registers.assets import Asset, asset_count, list_assets, upsert_assets
from sentinel.registers.controls import (
    ControlState,
    RequirementState,
    latest_activity,
    load_control_states,
    load_requirement_states,
    record_activity,
    upsert_control_states,
    upsert_requirement_states,
)
from sentinel.registers.evidence import (
    Evidence,
    freshness,
    hash_bytes,
    hash_file,
    load_evidence,
    record_evidence,
)
from sentinel.registers.risks import (
    Acceptance,
    Risk,
    accept_risk,
    list_risks,
    risk_posture,
    upsert_risks,
)

__all__ = [
    "Acceptance",
    "Asset",
    "ControlState",
    "Evidence",
    "RequirementState",
    "Risk",
    "accept_risk",
    "asset_count",
    "freshness",
    "hash_bytes",
    "hash_file",
    "latest_activity",
    "list_assets",
    "list_risks",
    "load_control_states",
    "load_evidence",
    "load_requirement_states",
    "record_activity",
    "record_evidence",
    "risk_posture",
    "upsert_assets",
    "upsert_control_states",
    "upsert_requirement_states",
    "upsert_risks",
]
