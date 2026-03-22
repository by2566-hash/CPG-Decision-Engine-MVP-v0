"""
telemetry_audit_logger.py

Writes one JSONL record per LLM evaluation event to the audit log.

AUDIT_LOG_FILE defaults to "logs/telemetry_audit.jsonl" but is overridable:

  import telemetry_audit_logger
  telemetry_audit_logger.AUDIT_LOG_FILE = "/path/to/run/logs/step6_audit.jsonl"

run_pipeline.py patches this at startup so each pipeline run writes its
audit events to the run-specific logs/ folder rather than the global default.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

AUDIT_LOG_FILE = "logs/telemetry_audit.jsonl"


def log_audit_event(
    action_trade_id: str,
    validation_result: str,
    gate_errors: list[str],
    fallback_used: bool,
    final_copy_source: str,
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """
    Append one audit record to AUDIT_LOG_FILE.

    Complies with the telemetry_audit_log_schema defined in CONTRACTS/.
    Creates parent directories if they do not exist.
    """
    os.makedirs(os.path.dirname(os.path.abspath(AUDIT_LOG_FILE)), exist_ok=True)

    record = {
        "action_trade_id":  str(action_trade_id),
        "timestamp_iso":    datetime.now(timezone.utc).isoformat(),
        "validation_result": validation_result,
        "gate_errors":      gate_errors,
        "fallback_used":    fallback_used,
        "final_copy_source": final_copy_source,
        "latency_ms":       latency_ms,
        "input_tokens":     input_tokens,
        "output_tokens":    output_tokens,
    }

    with open(AUDIT_LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
