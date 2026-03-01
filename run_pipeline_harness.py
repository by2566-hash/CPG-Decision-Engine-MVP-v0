import pandas as pd
import json
import os
from collections import Counter
from llm_safety_gateway import LLMSafetyGateway, ErrorCodes
from llm_response_hydrator import process_action_card
from llm_explainability_renderer import generate_mock_llm_response

OUTPUT_DIR = "./outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs("logs", exist_ok=True)

# Delete existing audit log to start fresh for the 30 count
audit_file = "logs/telemetry_audit.jsonl"
if os.path.exists(audit_file):
    os.remove(audit_file)

def run():
    print("Loading 30 PASS Action Cards...")
    cards_df = pd.read_csv("./OUTPUT/action_cards_v0.csv")
    passed_cards = cards_df[cards_df["policy_passed"] == True].head(30).to_dict(orient="records")
    
    validator = LLMSafetyGateway()
    
    # Tracking for metrics
    total = len(passed_cards)
    
    schema_errors = {
        ErrorCodes.E_JSON_PARSE, ErrorCodes.E_SCHEMA_INVALID, 
        ErrorCodes.E_SCHEMA_MISSING_FIELD, ErrorCodes.E_SCHEMA_ENUM_VIOLATION
    }
    policy_errors = {
        ErrorCodes.E_POLICY_ECHO_ACTION_MISMATCH, ErrorCodes.E_POLICY_ECHO_DISCOUNT_MISMATCH, 
        ErrorCodes.E_TRADE_ID_MISMATCH
    }
    grounding_errors = {
        ErrorCodes.E_GROUNDING_UNKNOWN_FIELD, ErrorCodes.E_GROUNDING_VALUE_MISMATCH, 
        ErrorCodes.E_NUMERIC_INJECTION, ErrorCodes.E_GROUNDING_TOO_THIN
    }
    
    schema_passes = 0
    policy_consistency_passes = 0
    grounding_rejects = 0
    overall_llm_passes = 0
    fallback_uses = 0
    
    all_error_codes = []
    final_targets = []
    
    for idx, card in enumerate(passed_cards):
        # Inject predictable errors into the mock response
        if idx % 10 == 1:
            llm_resp = generate_mock_llm_response(card, inject_error="hallucinate_discount")
        elif idx % 10 == 2:
            llm_resp = generate_mock_llm_response(card, inject_error="raw_numeric")
        elif idx % 10 == 3:
            llm_resp = generate_mock_llm_response(card, inject_error="banned_phrase")
        elif idx == 4:
            llm_resp = '{ "bad_json": true ' # Force JSON parse error
        else:
            llm_resp = generate_mock_llm_response(card)
            
        # Get raw errors to compute metrics before post processing
        errors = validator.validate(llm_resp, card)
        
        # Metric Logic
        if not any(e in schema_errors for e in errors):
            schema_passes += 1
            
        if not any(e in policy_errors for e in errors):
            policy_consistency_passes += 1
            
        if any(e in grounding_errors for e in errors):
            grounding_rejects += 1
            
        if not errors:
            overall_llm_passes += 1
        else:
            fallback_uses += 1
            all_error_codes.extend(errors)
            
        # Process and log (incorporates validator and fallback routing)
        final_record = process_action_card(card, llm_resp, validator)
        final_targets.append(final_record)
        
    out_jsonl = f"{OUTPUT_DIR}/final_action_cards_with_copy.jsonl"
    with open(out_jsonl, "w") as f:
        for t in final_targets:
            f.write(json.dumps(t) + "\n")
            
    # Compute percentages
    schema_pass_rate = schema_passes / total
    policy_pass_rate = policy_consistency_passes / total
    grounding_reject_rate = grounding_rejects / total
    overall_pass_rate = overall_llm_passes / total
    fallback_rate = fallback_uses / total
    
    top_errors = Counter(all_error_codes).most_common(3)
    
    # Generate Output Markdown
    md_content = f"""# Step 6 Bouncer Validator Metrics (30 Sample Harness)

## Gate Pass Rates & Metrics
- **Schema Pass Rate:** {schema_pass_rate:.1%}
- **Policy Consistency Rate:** {policy_pass_rate:.1%}
- **Grounding Reject Rate:** {grounding_reject_rate:.1%}
- **Overall LLM Pass Rate:** {overall_pass_rate:.1%}
- **Fallback Usage Rate:** {fallback_rate:.1%}

## Top 3 Failure Reasons
"""
    if not top_errors:
        md_content += "- None (100% pass)\n"
    else:
        for code, count in top_errors:
            md_content += f"- **{code}**: {count} occurrences\n"
            
    with open(f"{OUTPUT_DIR}/llm_gateway_metrics.md", "w") as f:
        f.write(md_content)
        
    print(f"Harness complete. Processed {total} cards.")
    print(f"Results written to {out_jsonl}")
    print(f"Metrics written to {OUTPUT_DIR}/llm_gateway_metrics.md")
    print(f"Audit log updated at {audit_file}")

if __name__ == "__main__":
    run()
