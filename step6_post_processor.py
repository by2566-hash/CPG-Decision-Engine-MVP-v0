import json
from step6_fallback_templates import get_fallback_copy
from step6_audit_logger import log_audit_event

def process_action_card(action_card: dict, llm_response_text: str, validator, mock_latency_ms: int = 150):
    """
    Runs the 4-gate validator. 
    If PASS, uses LLM copy and hydrates placeholders.
    If REJECT, uses deterministic fallback and logs the failure.
    Returns the final output JSON layout.
    """
    
    trade_id = action_card["id"]
    errors = validator.validate(llm_response_text, action_card)
    
    if not errors:
        # Passed!
        llm_json = json.loads(llm_response_text)
        final_channel = llm_json["copy"].get("channel", "sms")
        final_body = llm_json["copy"].get("body_template", "")
        fallback_used = False
        validation_result = "PASS"
        final_source = "LLM_GENERATED"
    else:
        # Rejected!
        fallback_copy = get_fallback_copy(action_card)
        final_channel = fallback_copy["channel"]
        final_body = fallback_copy["body_template"]
        fallback_used = True
        validation_result = "REJECT"
        final_source = "DETERMINISTIC_FALLBACK"

    # Hydrate placeholders (safe deterministic replacement)
    evidence = action_card.get("evidence", {})
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
        
    hydrated_body = final_body.replace("{PRODUCT}", str(evidence.get("product_id", "product")))
    pct_text = f"{int(action_card.get('parameter_value', 0.0) * 100)}%"
    hydrated_body = hydrated_body.replace("{DISCOUNT_TEXT}", pct_text)
    hydrated_body = hydrated_body.replace("{CTA}", "http://shop.co/r/" + trade_id[:5])
    hydrated_body = hydrated_body.replace("{SEGMENT_LABEL}", evidence.get("rfm_segment", "customer"))

    # Log to Audit JSONL
    log_audit_event(
        action_trade_id=trade_id,
        validation_result=validation_result,
        gate_errors=errors,
        fallback_used=fallback_used,
        final_copy_source=final_source,
        latency_ms=mock_latency_ms,
        input_tokens=0,
        output_tokens=0
    )

    # Return Output Format
    return {
        "action_trade_id": trade_id,
        "policy_echo": {
            "action_type": action_card.get("action_type"),
            "discount_pct": action_card.get("parameter_value")
        },
        "final_llm_copy": hydrated_body,
        "channel": final_channel,
        "validation_status": "FALLBACK" if fallback_used else "PASS"
    }
