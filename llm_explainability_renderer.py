import json
import argparse
import pandas as pd
from llm_safety_gateway import LLMSafetyGateway
from llm_response_hydrator import process_action_card

OUTPUT_DIR = "./OUTPUT"

def call_llm(payload: dict) -> str:
    """
    Adapter function to be wired to OpenAI/Anthropic/Gemini later.
    Takes a specific payload describing the ActionCard and returns a JSON string
    matching llm_render_output_schema.json. The response may include an optional
    'merchant_explanation' field built from the corresponding decision card.
    """
    raise NotImplementedError("Implement real LLM API call here.")


def generate_merchant_explanation(decision_card: dict) -> dict:
    """
    Build a structured merchant-facing explanation grounded strictly in the
    decision card produced by decision_card_builder.

    Rules (enforced by Gate E in LLMSafetyGateway):
      - 'pattern' must match decision_card['pattern_detected'] exactly.
      - 'summary' is taken verbatim from diagnosis_summary — no paraphrasing.
      - 'key_evidence' pulls only observed_value fields already present in the
        evidence chain; no new numbers are invented.
      - 'top_recommendation' is the first recommendation from the card as-is.
      - 'checks_required' is populated only for HYPOTHESIS cards.
      - 'uncertainty_note' must be present (non-null) for HYPOTHESIS cards.

    Pure function — no I/O, no side effects.
    """
    validation_status = decision_card.get("validation_status", "HYPOTHESIS")

    key_evidence = [
        f"Step {s['step']}: {s['observed_value']}"
        for s in decision_card.get("evidence_chain", [])
        if s.get("observed_value") is not None
    ]

    recs = decision_card.get("recommendations", [])
    top_rec = recs[0]["recommendation"] if recs else "No action recommended."

    is_hypothesis = validation_status == "HYPOTHESIS"
    checks_required = (
        [c["check_id"] for c in decision_card.get("checks_to_run", [])]
        if is_hypothesis else []
    )
    risk_notes = decision_card.get("risk_notes", [])
    uncertainty_note = risk_notes[0] if (is_hypothesis and risk_notes) else None

    return {
        "pattern": decision_card.get("pattern_detected", ""),
        "summary": decision_card.get("diagnosis_summary", ""),
        "key_evidence": key_evidence,
        "top_recommendation": top_rec,
        "checks_required": checks_required,
        "uncertainty_note": uncertainty_note,
    }


def generate_mock_llm_response(action_card: dict, inject_error=None, decision_card=None) -> str:
    """
    Mock LLM response for testing the validator flow offline.

    If decision_card is provided, the response includes a 'merchant_explanation'
    field built from it via generate_merchant_explanation().  The customer-facing
    'copy' field is always present and unchanged.
    """
    trade_id = action_card["id"]
    discount_pct = action_card["parameter_value"]
    action_type = action_card["action_type"]

    evidence = action_card.get("evidence", {})
    if isinstance(evidence, str):
        evidence = json.loads(evidence)

    product_id = evidence.get("product_id", "prod")
    rfm_segment = evidence.get("rfm_segment", evidence.get("segment", "cust"))

    resp = {
        "action_trade_id": trade_id,
        "policy_echo": {
            "action_type": action_type,
            "discount_pct": discount_pct,
        },
        "grounding": {
            "facts_used": [
                {"field": "product_id", "value": product_id},
                {"field": "rfm_segment", "value": rfm_segment},
            ]
        },
        "copy": {
            "channel": "sms",
            "body_template": "Your {PRODUCT} is due. Get {DISCOUNT_TEXT} off! Click {CTA}.",
        },
    }

    if decision_card is not None:
        resp["merchant_explanation"] = generate_merchant_explanation(decision_card)

    # Inject specific errors to test the gates in dry-run viewing
    if inject_error == "hallucinate_discount":
        resp["policy_echo"]["discount_pct"] = 0.99
    elif inject_error == "raw_numeric":
        resp["copy"]["body_template"] = "Your prod is delayed. Here is 50% off! Click {CTA}."
    elif inject_error == "banned_phrase":
        resp["copy"]["body_template"] = "This is not spam. Click {CTA}."

    return json.dumps(resp)


def run_batch(mock=True):
    print("Loading PASS Action Cards from Policy Engine...")
    cards = pd.read_csv(f"{OUTPUT_DIR}/action_cards_v0.csv")
    passed_cards = cards[cards["policy_passed"]].to_dict(orient="records")
    
    print(f"Found {len(passed_cards)} valid trade orders for rendering.")
    
    validator = LLMSafetyGateway()
    final_targets = []
    
    for idx, card in enumerate(passed_cards):
        # We simulate a mix of mock responses to demonstrate the Bouncer catching errors
        payload = card
        if mock:
            # 80% pass, 20% inject errors to demonstrate fallback
            if idx % 5 == 1:
                 llm_resp = generate_mock_llm_response(payload, inject_error="hallucinate_discount")
            elif idx % 5 == 2:
                 llm_resp = generate_mock_llm_response(payload, inject_error="raw_numeric")
            elif idx % 5 == 3:
                 llm_resp = generate_mock_llm_response(payload, inject_error="banned_phrase")
            else:
                 llm_resp = generate_mock_llm_response(payload)
        else:
            llm_resp = call_llm(payload)
            
        final_record = process_action_card(card, llm_resp, validator)
        final_targets.append(final_record)
        
    out_file = f"{OUTPUT_DIR}/final_campaign_targets_v0.jsonl"
    with open(out_file, "w") as f:
        for targ in final_targets:
            f.write(json.dumps(targ) + "\n")
            
    print("\n=============================================")
    print(" Bouncer Pattern Execution Summary")
    print("=============================================")
    passed = sum(1 for t in final_targets if t["validation_status"] == "PASS")
    fallback = len(final_targets) - passed
    print(f"Total Targets Evaluated: {len(final_targets)}")
    print(f"LLM Copies Allowed (PASS): {passed}")
    print(f"Hallucinations Rejected (FALLBACK): {fallback}")
    print(f"Saved to {out_file}")
    print("Audit Log written to logs/telemetry_audit.jsonl")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", default=True, help="Run offline without external API.")
    args = parser.parse_args()
    
    run_batch(mock=args.mock)
