import subprocess
import threading
import os
from runner_api.storage import update_run_state, get_run_dir
from datetime import datetime

def run_pipeline_worker(run_id: str, mode: str, dataset_path: str = None):
    """
    Background worker that calls `run_pipeline.py` via subprocess.
    """
    update_run_state(run_id, {"status": "RUNNING"})
    
    cmd = ["python3", "run_pipeline.py", "--run-id", run_id, "--mode", mode]
    if dataset_path:
        cmd.extend(["--dataset-path", dataset_path])
        
    # We execute from the root directory of the project, assuming runner_api is a subdirectory.
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    try:
        # We capture output to a log file inside the run directory just in case
        run_dir = get_run_dir(run_id)
        stdout_path = os.path.join(run_dir, "stdout.log")
        stderr_path = os.path.join(run_dir, "stderr.log")
        
        with open(stdout_path, "w") as fout, open(stderr_path, "w") as ferr:
            result = subprocess.run(cmd, cwd=project_root, stdout=fout, stderr=ferr, text=True)
            
        if result.returncode == 0:
            # Note: storage.get_run_status syncing mechanism will read the final SUCCESS from run_receipt.json natively 
            update_run_state(run_id, {"status": "SUCCEEDED", "finished_at": datetime.utcnow().isoformat()})
        else:
            with open(stderr_path, "r") as ferr:
                error_msg = ferr.read().strip()
            update_run_state(run_id, {"status": "FAILED", "error": f"Exit Code {result.returncode}: {error_msg[-500:]}"})
            
    except Exception as e:
        update_run_state(run_id, {
            "status": "FAILED",
            "error": f"Worker Exception: {str(e)}",
            "finished_at": datetime.utcnow().isoformat()
        })

def spawn_run_job(run_id: str, mode: str, dataset_path: str = None):
    """Spawns the pipeline worker in a new background thread."""
    thread = threading.Thread(target=run_pipeline_worker, args=(run_id, mode, dataset_path), daemon=True)
    thread.start()
