# CPG Decision Engine - Runner API

A lightweight FastAPI wrapper to expose the deterministic, offline MVP pipeline to external orchestrators like **n8n**. 

This enforces "Plan B Architecture": the orchestration engine triggers the job via POST, but the heavy ML and Pandas computations are strictly isolated in a background python process.

## Local Execution
Ensure `fastapi` and `uvicorn` are installed via `requirements.txt`.

```bash
uvicorn runner_api.app:app --host 0.0.0.0 --port 8000
```

## Security 
The API is protected by a simple `X-API-KEY` header.
- To disable locally: `export DISABLE_SECURITY=true`
- To set custom key: `export RUNNER_API_KEY=your_key` (Default is `dev_mvp_key_001`)

## API Reference (curl examples)

### 1. Trigger a Run (`POST /api/v1/jobs/run`)
Starts the 4-step offline batch pipeline asynchronously.
```bash
curl -X POST "http://localhost:8000/api/v1/jobs/run" \
     -H "X-API-KEY: dev_mvp_key_001" \
     -H "Content-Type: application/json" \
     -d '{
           "run_mode": "mock",
           "dataset": {
             "path": "./DATASETS",
             "name": "Instacart"
           }
         }'
```
*Response Data:*
```json
{
  "run_id": "job_a1b2c3d4",
  "status": "QUEUED",
  "status_url": "/api/v1/jobs/job_a1b2c3d4/status"
}
```

### 2. Poll Status (`GET /api/v1/jobs/{run_id}/status`)
Checks if the job is `QUEUED`, `RUNNING`, `SUCCEEDED`, or `FAILED`. Once succeeded, it automatically merges the pipeline's metrics.
```bash
curl -X GET "http://localhost:8000/api/v1/jobs/job_a1b2c3d4/status" \
     -H "X-API-KEY: dev_mvp_key_001"
```
*Response Data (Completed):*
```json
{
  "run_id": "job_a1b2c3d4",
  "status": "SUCCEEDED",
  "started_at": "2023-10-27T10:00:00Z",
  "finished_at": "2023-10-27T10:01:00Z",
  "error": null,
  "metrics_summary": {
    "overall_llm_pass_rate": "66.7%",
    "fallback_usage_rate": "33.3%"
  },
  "artifact_paths": {
    "final_copies_jsonl": "runs/job_a1b2c3d4/outputs/final_action_cards_with_copy.jsonl",
    "audit_log": "runs/job_a1b2c3d4/logs/step6_audit.jsonl"
  }
}
```

### 3. Download Artifacts (GET Optional)
```bash
curl -X GET "http://localhost:8000/api/v1/jobs/job_a1b2c3d4/download?file=metrics" \
     -H "X-API-KEY: dev_mvp_key_001" \
     --output demo_metrics.md
```
