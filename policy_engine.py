import os
import pandas as pd
import uuid
import json
import hashlib
from datetime import datetime

# 1. Configuration & Constants
OUTPUT_DIR = os.getenv("MVP_OUTPUT_DIR", "./OUTPUT")
RUN_ID = "run_v0_001"
POLICY_VERSION = "v0_baseline"
EMITTED_AT = datetime(2023, 12, 31).isoformat()  # Using the ANCHOR_DATE context for consistency

# --- POLICY DEFINITIONS (v0 Mock Config) ---
POLICY_RULES = {
    # 1. Segment-based max discount limits (Overridden by global max_discount_pct=15% below)
    "max_discount_pct_global": 0.15,
    
    # 2. Minimum inventory floor required to even issue a discount
    "inventory_floor": 50,
    
    # 3. Frequency Cap (Max actions per user per run)
    "max_actions_per_user": 1,
    
    # 4. Margin Floor (Mocked: requires product margin to be > 15% after discount)
    "margin_floor": 0.15
}

def stable_hash(val):
    return int(hashlib.md5(str(val).encode('utf-8')).hexdigest()[:8], 16)

def build_action_cards_dry_run():
    print("Loading At-Risk Candidates...")
    candidates = pd.read_csv(f"{OUTPUT_DIR}/at_risk_candidates_v0.csv")
    customers = pd.read_csv(f"{OUTPUT_DIR}/customers_canonical_full.csv")
    
    # Merge in m_proxy_value which is needed for the policy engine's expected_value_proxy
    candidates = pd.merge(candidates, customers[['user_id', 'm_proxy_value']], on='user_id', how='left')
    
    # Optional: ensure we are only looking at the user's HIGHEST risk item to enforce frequency cap natively
    candidates = candidates.sort_values(by=['user_id', 'overdue_ratio'], ascending=[True, False])
    
    # Group by user and take only the top 1 (Frequency Cap constraint: 1 per user)
    target_claims = candidates.groupby('user_id').head(POLICY_RULES["max_actions_per_user"]).copy()
    
    # Generate mock intrinsic gross margin for products (e.g. 10% to 50%)
    target_claims['mock_gross_margin'] = target_claims['product_id'].apply(lambda x: 0.10 + ((stable_hash(x) % 40) / 100.0))
    
    action_cards = []
    
    print("Evaluating policies for DRY RUN...")
    for _, row in target_claims.iterrows():
        action_id = f"act_{uuid.uuid4().hex[:8]}"
        
        segment = row['rfm_segment']
        mock_inv = row['mock_inventory']
        gross_margin = row['mock_gross_margin']
        
        # 1. Determine Discount % 
        # Base request: (overdue_ratio % past 1.0) / 2 -> maxes quickly
        base_discount_request = min((row['overdue_ratio'] - 1.0) / 2.0, 0.3) 
        
        # Cap at global 15%
        final_discount = min(base_discount_request, POLICY_RULES["max_discount_pct_global"])
        final_discount = round(final_discount, 2)
        
        # Calculate post-discount margin
        post_action_margin = gross_margin - final_discount
        
        # 2. Check Constraints
        passed_inventory = mock_inv >= POLICY_RULES["inventory_floor"]
        passed_margin = post_action_margin >= POLICY_RULES["margin_floor"]
        passed_discount = final_discount > 0.0
        
        policy_passed = passed_inventory and passed_margin and passed_discount
        
        if policy_passed:
            action_type = "DISCOUNT"
            parameter_value = final_discount
        else:
            action_type = "NO_ACTION"
            parameter_value = 0.0
            
        evidence_dict = {
            "customer_id": str(row['user_id']),
            "product_id": str(row['product_id']),
            "rfm_segment": segment,
            "overdue_ratio": float(row['overdue_ratio']),
            "inventory_level": int(mock_inv),
            "gross_margin_pct": round(gross_margin, 2)
        }
        
        constraints_applied = [
            {"rule": "max_actions_per_user", "value": POLICY_RULES["max_actions_per_user"], "check": "PASS"},
            {"rule": "inventory_floor", "value": POLICY_RULES["inventory_floor"], "check": "PASS" if passed_inventory else f"FAIL (inv={mock_inv})"},
            {"rule": "margin_floor", "value": POLICY_RULES["margin_floor"], "check": "PASS" if passed_margin else f"FAIL (post_margin={round(post_action_margin,2)})"},
            {"rule": "max_discount_pct", "value": POLICY_RULES["max_discount_pct_global"], "check": "PASS" if passed_discount else "FAIL (0% requested)"}
        ]
        
        # LLM Expalaination stub
        if policy_passed:
            llm_explainer = f"Since your {row['product_id']} replenishment is {(row['overdue_ratio']-1.0)*100:.0f}% overdue, we are automatically offering a {int(final_discount*100)}% discount. All constraints passed: margin is healthy ({round(post_action_margin*100)}%) and inventory is above floor."
        else:
            fail_reasons = [c['rule'] for c in constraints_applied if 'FAIL' in c['check']]
            llm_explainer = f"Action Suppressed. Failed constraints: {fail_reasons}."

        card = {
            "id": action_id,
            "run_id": RUN_ID,
            "policy_version": POLICY_VERSION,
            "action_type": action_type,
            "parameter_value": parameter_value,
            "policy_passed": policy_passed,
            "expected_value_proxy": round(final_discount * row['m_proxy_value'], 2), 
            "evidence": json.dumps(evidence_dict),
            "constraints_applied": json.dumps(constraints_applied),
            "llm_explanation": llm_explainer
        }
        action_cards.append(card)
        
    cards_df = pd.DataFrame(action_cards)
    
    # Select 3 diverse samples for the Dry Run
    sample_pass = cards_df[cards_df['policy_passed']].head(1)
    
    # Find one blocked by margin
    sample_margin_block = cards_df[cards_df['constraints_applied'].astype(str).str.contains('margin_floor\', \'value\': 0.15, \'check\': \'FAIL')].head(1)
    
    # Find one blocked by inventory
    sample_inv_block = cards_df[cards_df['constraints_applied'].astype(str).str.contains('inventory_floor\', \'value\': 50, \'check\': \'FAIL')].head(1)
    
    samples = pd.concat([sample_pass, sample_margin_block, sample_inv_block])
    
    out_json = samples.to_dict(orient='records')
    print("\n=============================================")
    print(" DRY RUN: 3 DIVERSE ACTION CARD SAMPLES")
    print("=============================================")
    print(json.dumps(out_json, indent=2))
    
    # Save the full output payload for Step 6 to consume
    cards_df.to_csv(f"{OUTPUT_DIR}/action_cards_v0.csv", index=False)
    
    # Print summary stats
    passed_count = len(cards_df[cards_df['policy_passed']])
    failed_count = len(cards_df) - passed_count
    
    print("\n=============================================")
    print(" Batch Summary Stats Preview")
    print("=============================================")
    print(f"Total Unique Users Checked: {len(cards_df)}")
    print(f"Total PASS: {passed_count}")
    print(f"Total FAIL: {failed_count}")
    
    # Very rough block reason counts
    blocks = cards_df[~cards_df['policy_passed']]
    margin_fails = sum(blocks['constraints_applied'].astype(str).str.contains('margin_floor\', \'value\': 0.15, \'check\': \'FAIL'))
    inv_fails = sum(blocks['constraints_applied'].astype(str).str.contains('inventory_floor\', \'value\': 50, \'check\': \'FAIL'))
    print("\nTop Block Reasons:")
    print(f" - Margin Floor Violations: {margin_fails}")
    print(f" - Inventory Floor Violations: {inv_fails}")

if __name__ == "__main__":
    build_action_cards_dry_run()
