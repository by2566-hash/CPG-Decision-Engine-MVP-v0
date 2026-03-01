import os
import uuid
from typing import Optional, Dict, Any
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from runner_api.storage import init_run_state, get_run_status, get_run_dir
from runner_api.job_manager import spawn_run_job

app = FastAPI(
    title="CPG Decision Engine Runner API",
    description="Plan B Runner API for MVP Architecture. Integrates with n8n via non-blocking job queuing.",
    version="v0.1.0"
)

# Minimal Security Configuration 
API_KEY = os.getenv("RUNNER_API_KEY", "dev_mvp_key_001")
DISABLE_SECURITY = os.getenv("DISABLE_SECURITY", "true").lower() in ("true", "1", "yes")

def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if DISABLE_SECURITY:
        return
    if x_api_key != API_KEY:
         raise HTTPException(status_code=401, detail="Invalid X-API-KEY")

# Models
class DatasetSpec(BaseModel):
    path: Optional[str] = None
    name: Optional[str] = None

class RunRequest(BaseModel):
    run_mode: str = "mock" # mock | real
    dataset: Optional[DatasetSpec] = None
    params: Optional[Dict[str, Any]] = None

@app.post("/api/v1/jobs/run", status_code=202, dependencies=[Depends(verify_api_key)])
def submit_run(request: RunRequest):
    """
    Starts an offline batch execution of the Decision Engine MVP.
    Returns immediately with a QUEUED status.
    """
    if request.run_mode not in ["mock", "real"]:
        raise HTTPException(status_code=400, detail="run_mode must be 'mock' or 'real'")
        
    run_id = f"job_{uuid.uuid4().hex[:8]}"
    dataset_path = request.dataset.path if request.dataset else None
    
    # 1. Initialize State
    init_run_state(run_id, mode=request.run_mode, dataset_path=dataset_path)
    
    # 2. Spawn Background Worker
    spawn_run_job(run_id, mode=request.run_mode, dataset_path=dataset_path)
    
    return {
        "run_id": run_id,
        "status": "QUEUED",
        "status_url": f"/api/v1/jobs/{run_id}/status"
    }

@app.get("/api/v1/jobs/{run_id}/status", dependencies=[Depends(verify_api_key)])
def get_status(run_id: str):
    """
    Check the current lifecycle state of a previously submitted job.
    Includes rich metrics and artifact paths upon COMPLETED/SUCCEEDED states.
    """
    state = get_run_status(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run ID {run_id} not found")
        
    return JSONResponse(content=state)

@app.get("/api/v1/jobs/{run_id}/download", dependencies=[Depends(verify_api_key)])
def download_artifact(run_id: str, file: str):
    """
    Optional helper to download raw artifacts.
    file: 'metrics' | 'audit' | 'final' | 'receipt'
    """
    state = get_run_status(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
        
    if state.get("status") not in ["SUCCEEDED", "FAILED"]:
        raise HTTPException(status_code=400, detail="Artifacts are not ready yet. Job is RUNNING or QUEUED.")
        
    run_dir = get_run_dir(run_id)
    out_dir = os.path.join(run_dir, "outputs")
    logs_dir = os.path.join(run_dir, "logs")
    
    file_map = {
        "metrics": os.path.join(out_dir, "llm_gateway_metrics.md"),
        "audit": os.path.join(logs_dir, "telemetry_audit.jsonl"),
        "final": os.path.join(out_dir, "final_action_cards_with_copy.jsonl"),
        "receipt": os.path.join(run_dir, "run_receipt.json")
    }
    
    target = file_map.get(file)
    if not target or not os.path.exists(target):
         raise HTTPException(status_code=404, detail="Artifact file not found on disk")
         
    return FileResponse(target)
