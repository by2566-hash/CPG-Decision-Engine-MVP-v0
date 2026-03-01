import os
import argparse
import sys
import json
from datetime import datetime
import uuid
import uuid

# Import the existing modules
# We dynamically patch their OUTPUT_DIR to funnel all artifacts to the run folder.
import compute_rfm
import compute_risk
import policy_engine
import run_pipeline_harness

def generate_run_id() -> str:
    """Generates an ISO timestamp + random suffix run ID."""
    now_iso = datetime.now().strftime("%Y%m%dT%H%M%S")
    suffix = str(uuid.uuid4())[:6]
    return f"run_{now_iso}_{suffix}"

def setup_run_dir(run_id: str, out_dir_base: str):
    """Creates the run folder structure and returns paths."""
    run_dir = os.path.join(out_dir_base, run_id)
    outputs_dir = os.path.join(run_dir, "outputs")
    logs_dir = os.path.join(run_dir, "logs")
    
    os.makedirs(outputs_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)
    
    return run_dir, outputs_dir, logs_dir

def execute_pipeline(args):
    """Orchestrates the 4 steps of the MVP and writes artifacts."""
    run_id = args.run_id or generate_run_id()
    run_dir, outputs_dir, logs_dir = setup_run_dir(run_id, args.out_dir)
    
    receipt = {
        "run_id": run_id,
        "mode": args.mode,
        "status": "RUNNING",
        "timestamps": {"start": datetime.now().isoformat()},
        "metrics_summary": {},
        "artifact_paths": {
            "run_dir": run_dir,
            "outputs_dir": outputs_dir,
            "logs_dir": logs_dir
        },
        "errors": []
    }
    
    receipt_path = os.path.join(run_dir, "run_receipt.json")
    
    def write_receipt():
        receipt["timestamps"]["end"] = datetime.now().isoformat()
        with open(receipt_path, "w") as f:
            json.dump(receipt, f, indent=2)

    try:
        # Patch Global Output Directories to ensure everything writes to the run folder
        compute_rfm.OUTPUT_DIR = outputs_dir
        compute_risk.OUTPUT_DIR = outputs_dir
        policy_engine.OUTPUT_DIR = outputs_dir
        
        # Optionally patch Data Dir if a test fixture is provided
        if args.dataset_path:
             compute_rfm.DATA_DIR = args.dataset_path
             # Note: compute_risk has harcoded DATASETS path in the current script v0. 
             # We let it default to standard for now, but log it.
             
        # Patch the Step 6 harness globals
        run_pipeline_harness.OUTPUT_DIR = outputs_dir
        run_pipeline_harness.audit_file = os.path.join(logs_dir, "step6_audit.jsonl")

        print(f"=== Starting Run {run_id} ===")
        print(f"Artifacts writing to: {run_dir}")

        # Step 1: RFM Computation
        print("\n[1/4] Executing RFM Computation & Canonical Mapping...")
        compute_rfm.load_and_map_data()
        
        # Step 2: Risk Scoring
        print("\n[2/4] Executing Replenishment Risk Scoring...")
        # compute_risk uses RUN_ID globally
        compute_risk.RUN_ID = run_id
        compute_risk.compute_replenishment_and_risk()
        
        # Step 3: Policy Engine
        print("\n[3/4] Executing Policy Engine Constraints & Action Generation...")
        policy_engine.build_action_cards_dry_run()
        
        # Step 4: LLM Explainability Bouncer (Renderer)
        print("\n[4/4] Executing LLM Bouncer Validator & Representation...")
        is_mock = (args.mode == "mock")
        # run_pipeline_harness.py acts as the wrapper for step 6 metric generation.
        # We invoke the underlying runner. 
        # Note: the harness originally loaded action_cards_v0.csv hardcoded. 
        # It needs to read from the *new* outputs_dir.
        
        # Temporary patch via pandas directly in run_pipeline_harness if possible,
        # but the module hardcodes "./OUTPUT/action_cards_v0.csv".
        # We overwrite that hardcoded path in memory.
        import pandas as pd
        def patched_run():
            cards_df = pd.read_csv(os.path.join(outputs_dir, "action_cards_v0.csv"))
            # The rest of the logic inside the harness... we can call it directly, or adapt.
            # To ensure compatibility with the unmodified script, we just execute its run() method,
            # but we must mock pd.read_csv globally inside that module.
            original_read_csv = pd.read_csv
            def mock_read_csv(filepath, **kwargs):
                if "action_cards_v0.csv" in filepath:
                    return original_read_csv(os.path.join(outputs_dir, "action_cards_v0.csv"), **kwargs)
                return original_read_csv(filepath, **kwargs)
            
            run_pipeline_harness.pd.read_csv = mock_read_csv
            try:
                run_pipeline_harness.run()
            finally:
                run_pipeline_harness.pd.read_csv = original_read_csv

        patched_run()
        
        # 5. Extract Metrics for receipt
        try:
             import re
             metrics_path = os.path.join(outputs_dir, "llm_gateway_metrics.md")
             if os.path.exists(metrics_path):
                 with open(metrics_path, "r") as f:
                     content = f.read()
                     receipt["metrics_summary"]["overall_llm_pass_rate"] = re.search(r'Overall LLM Pass Rate:\*\*\s*(.+%)', content).group(1)
                     receipt["metrics_summary"]["fallback_usage_rate"] = re.search(r'Fallback Usage Rate:\*\*\s*(.+%)', content).group(1)
        except Exception as e:
             receipt["metrics_summary"]["error"] = f"Failed to parse metrics: {str(e)}"
             
        # Add target final paths
        receipt["artifact_paths"]["final_copies_jsonl"] = os.path.join(outputs_dir, "final_action_cards_with_copy.jsonl")
        receipt["artifact_paths"]["audit_log"] = os.path.join(logs_dir, "step6_audit.jsonl")

        receipt["status"] = "SUCCESS"
        write_receipt()
        
        print("\n=== Pipeline Execution Completed Successfully ===")
        print(f"Receipt written to {receipt_path}")
        sys.exit(0)

    except Exception as e:
        print(f"\n[FATAL ERROR] Pipeline failed: {str(e)}")
        receipt["status"] = "FAILED"
        receipt["errors"].append(str(e))
        write_receipt()
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CPG Decision Engine - Single Entrypoint Runner")
    parser.add_argument("--run-id", type=str, help="Optional UUID for the run. If missing, auto-generates.")
    parser.add_argument("--mode", type=str, choices=["mock", "real"], default="mock", help="Execute LLM via external API or deterministic mock generator.")
    parser.add_argument("--dataset-path", type=str, help="Optional override for the DATASETS/ directory.")
    parser.add_argument("--out-dir", type=str, default="runs/", help="Base directory for execution artifacts.")
    parser.add_argument("--sample-users-pct", type=float, default=0.1, help="Downsample to save memory. (Not fully wired in MVP v0 logic).")
    
    args = parser.parse_args()
    execute_pipeline(args)
