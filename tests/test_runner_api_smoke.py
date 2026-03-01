import urllib.request
import urllib.parse
import json
import time
import sys

API_URL = "http://localhost:8000/api/v1/jobs"
HEADERS = {
    "X-API-KEY": "smoke_test_key",
    "Content-Type": "application/json"
}

def make_request(method, url, data=None):
    req = urllib.request.Request(url, method=method, headers=HEADERS)
    if data:
        req.data = json.dumps(data).encode("utf-8")
        
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"HTTP Error {e.code}: {error_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"URL Error: {e.reason}. API mighty not be running.")
        sys.exit(1)

def run_smoke_test():
    print("--- Starting Runner API Smoke Test ---")
    
    # 1. Trigger the Pipeline
    payload = {
        "run_mode": "mock",
        "dataset": {
            "path": "./tests/fixtures",
            "name": "Smoke_Test_Fixtures"
        }
    }
    
    submit_url = f"{API_URL}/run"
    print(f"POST {submit_url}")
    res = make_request("POST", submit_url, data=payload)
    
    run_id = res.get("run_id")
    print(f"Job triggered successfully. ID: {run_id}")
    
    # 2. Poll the Status
    status_url = f"{API_URL}/{run_id}/status"
    max_retries = 30 # 30 * 2 = 60s timeout
    
    print(f"Polling {status_url} ...")
    for attempt in range(max_retries):
        status_res = make_request("GET", status_url)
        status = status_res.get("status")
        
        print(f"Attempt {attempt+1}/{max_retries}: Status = {status}")
        
        if status in ["SUCCEEDED", "FAILED"]:
            if status == "FAILED":
                print(f"Pipeline FAILED! Check error:\n{status_res.get('error')}")
                sys.exit(1)
            else:
                print("Pipeline SUCCEEDED!")
                
                # Check metrics actually aggregated
                metrics = status_res.get("metrics_summary", {})
                print(f"Overall LLM Pass Rate: {metrics.get('overall_llm_pass_rate', 'Not Found')}")
                
                # Verify paths
                paths = status_res.get("artifact_paths", {})
                print(f"Artifacts: {list(paths.keys())}")
                
                sys.exit(0)
                
        time.sleep(2)
        
    print("ERROR: Pipeline timed out during execution.")
    sys.exit(1)

if __name__ == "__main__":
    run_smoke_test()
