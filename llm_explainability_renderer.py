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
    matching llm_render_output_schema.json.
    """
    raise NotImplementedError("Implement real LLM API call here.")

def generate_mock_llm_response(action_card: dict, inject_error=None) -> str:
    """
    Mock LLM response for testing the validator flow offline.
    """
    trade_id = action_card["id"]
    discount_pct = action_card["parameter_value"]
    action_type = action_card["action_type"]
    
    evidence = action_card.get("evidence", {})
    if isinstance(evidence, str):
         evidence = json.loads(evidence)
         
    product_id = evidence.get("product_id", "prod")
    segment = evidence.get("segment", "cust")
    
    resp = {
        "action_trade_id": trade_id,
        "policy_echo": {
            "action_type": action_type,
            "discount_pct": discount_pct
        },
        "grounding": {
            "facts_used": [
                {"field": "product_id", "value": product_id},
                {"field": "segment", "value": segment}
            ]
        },
        "copy": {
            "channel": "sms",
            "body_template": "Your {PRODUCT} is delayed. Here is {DISCOUNT_TEXT} off! Click {CTA}."
        }
    }
    
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
    passed_cards = cards[cards["policy_passed"] == True].to_dict(orient="records")
    
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
