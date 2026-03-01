# Step 6: Bouncer Validator Contracts

This directory contains the strict schemas guiding the interface between the deterministic Decision Engine and the non-deterministic LLM Generation Layer (Bouncer Pattern).

## Schemas

### 1. `llm_render_output_schema.json`
*   **Purpose:** The strict system prompt forcing the LLM to output a precise, parseable JSON object answering to the exact parameters of the `action_trade_id`.
*   **Usage in Gates:**
    *   **Gate 1 (Format):** Validates the LLM string as syntactically correct JSON matching this exact schema (`additionalProperties: false`).
    *   **Gate 2 (Fact Check):** Extracts the `policy_echo` and compares it to the original Policy Engine payload to ensure the model didn't hallucinate a better deal.

### 2. `telemetry_audit_log_schema.json`
*   **Purpose:** The event telemetry contract documenting the life-cycle of every Action Card that enters the Step 6 compiler.
*   **Usage in Gates:**
    *   Created upon ingestion of the LLM output.
    *   If any of the 4 gates reject the text, `validation_result` = "REJECT" and the `gate_errors` array is populated with the specific failure reason (e.g., `["GATE_4_BANNED_PHRASE_DETECTED"]`).
    *   Documents if the deterministic template was injected implicitly via `fallback_used: true`.
