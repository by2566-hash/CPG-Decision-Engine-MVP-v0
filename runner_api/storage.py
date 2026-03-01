import json
import os
from datetime import datetime
from typing import Dict, Any, Optional

RUNS_DIR = os.getenv("RUNS_DIR", "runs")

def get_run_dir(run_id: str) -> str:
    return os.path.join(RUNS_DIR, run_id)

def init_run_state(run_id: str, mode: str, dataset_path: Optional[str] = None):
    """Initialize the run_state.json for a new run."""
    run_dir = get_run_dir(run_id)
    os.makedirs(run_dir, exist_ok=True)
    
    state = {
        "run_id": run_id,
        "status": "QUEUED",
        "mode": mode,
        "dataset_path": dataset_path,
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": None,
        "error": None
    }
    
    state_path = os.path.join(run_dir, "run_state.json")
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)
    return state

def update_run_state(run_id: str, updates: Dict[str, Any]):
    """Update specific fields in run_state.json."""
    run_dir = get_run_dir(run_id)
    state_path = os.path.join(run_dir, "run_state.json")
    
    if not os.path.exists(state_path):
        return
        
    with open(state_path, "r") as f:
        state = json.load(f)
        
    state.update(updates)
    
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)

def get_run_status(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the merged status from run_state.json and run_receipt.json (if present)."""
    run_dir = get_run_dir(run_id)
    state_path = os.path.join(run_dir, "run_state.json")
    receipt_path = os.path.join(run_dir, "run_receipt.json")
    
    if not os.path.exists(state_path):
        return None
        
    with open(state_path, "r") as f:
        state = json.load(f)
        
    # If the pipeline has produced a receipt, merge it for rich metrics and artifacts
    if os.path.exists(receipt_path):
        try:
            with open(receipt_path, "r") as f:
                receipt = json.load(f)
                
            # Sync status if receipt has a definitive end state
            if receipt.get("status") in ["SUCCESS", "FAILED"]:
                mapped_status = "SUCCEEDED" if receipt["status"] == "SUCCESS" else "FAILED"
                if state["status"] != mapped_status:
                    state["status"] = mapped_status
                    state["finished_at"] = receipt.get("timestamps", {}).get("end")
                    if receipt.get("errors"):
                        state["error"] = "; ".join(receipt["errors"])
                    update_run_state(run_id, {"status": mapped_status, "finished_at": state["finished_at"], "error": state["error"]})

            state["artifact_paths"] = receipt.get("artifact_paths", {})
            state["metrics_summary"] = receipt.get("metrics_summary", {})
            if "errors" in receipt and receipt["errors"]:
                 state["error"] = receipt["errors"]
        except json.JSONDecodeError:
            pass # receipt might be partially written
            
    return state
