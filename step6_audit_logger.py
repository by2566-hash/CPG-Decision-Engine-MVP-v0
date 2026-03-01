import json
import os
from datetime import datetime, timezone

AUDIT_LOG_FILE = "logs/step6_audit.jsonl"

def log_audit_event(
    action_trade_id: str,
    validation_result: str,
    gate_errors: list[str],
    fallback_used: bool,
    final_copy_source: str,
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0
):
    """
    Writes a single audit record complying with step6_audit_log_schema.
    """
    os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)
    
    record = {
        "action_trade_id": str(action_trade_id),
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "validation_result": validation_result,
        "gate_errors": gate_errors,
        "fallback_used": fallback_used,
        "final_copy_source": final_copy_source,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens
    }
    
    with open(AUDIT_LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
