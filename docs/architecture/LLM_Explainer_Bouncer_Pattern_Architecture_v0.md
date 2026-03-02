# LLM Explainer & Bouncer Pattern Architecture v0

**Goal:** Transform deterministic Action Cards (the "trade order") into customer-facing text using a Large Language Model, and strictly validate the output to prevent hallucinations or unauthorized offers.

**Execution Context:** Offline batch processing (no real-time integrations).

---

## 1. Component Architecture & Data Flow

```mermaid
flowchart TD
    A[Policy Engine Output\naction_cards_v0.csv] --> B(Render Builder)
    B -->|Context JSON + Prompt| C{LLM Engine}
    C -->|Generated Text| D[Bouncer Validator\n(4 Gates)]
    
    D -->|Gate 1: Format| G1{JSON parsing}
    G1 -->|Pass| G2
    
    D -->|Gate 2: Fact Check| G2{Discount & Math Match}
    G2 -->|Pass| G3
    
    D -->|Gate 3: Channel Rules| G3{Length & CTA Check}
    G3 -->|Pass| G4
    
    D -->|Gate 4: Safety| G4{Banned Phrases Check}
    
    G4 -->|Pass| E(Post-Processor)
    G1 & G2 & G3 & G4 -->|Fail| F(Fallback Generator)
    
    E --> H[Validated Outputs\nfinal_action_cards_with_copy.jsonl]
    F --> H
    
    H --> I(Campaign Events Logger)
    I --> J[telemetry_audit.jsonl APPEND]
```

---

## 2. Component Details

### 2.1 Render Builder (The Prompt Constructor)
Reads `action_cards_v0.csv`, filters for `policy_passed == True`, and constructs a rigid prompt that restricts the LLM from inventing new terms.
*   **Input Schema:** `{"action_id": "...", "segment": "...", "discount_pct": 0.15, "product_id": "..."}`
*   **System Prompt Constraint:** "You are a copywriter. Your outputs must only use the facts provided. Never invent discounts."

### 2.2 Bouncer Validator (The 4-Gate System)
Untrusted LLM text must pass all four gates sequentially:
1.  **Gate 1 (Format / JSON Parse):** The LLM response must be strict JSON containing a `campaign_copy` field.
2.  **Gate 2 (Fact Check / Math):** Extracts any percentages/numbers from the text using Regex. If the text says "20%" but the `parameter_value` is 0.15, the gate **FAILs** immediately.
3.  **Gate 3 (Channel Rules):** Enforces length limits and necessary keywords (e.g., must contain CTA, must be < 160 chars for SMS) based on `config/channel_rules.json`.
4.  **Gate 4 (Brand Safety):** Text must not contain any phrases listed in `config/banned_phrases.txt` (e.g., "guarantee", "spam").

### 2.3 Fallback Generator
If any of the 4 gates fail, the LLM output is discarded and marked `LLM_REJECTED`. The system injects a safe, deterministic fallback template:
*   *"As a valued {segment} customer, enjoy {discount}% off your next {product_id} order."*

### 2.4 Campaign Events Logger
After the bulk run, all final generated text (whether LLM-approved or Fallback) is recorded. A corresponding `ACTION_ISSUED` tracking stub is appended to the `campaign_events_v0.csv` tracker.

---

## 3. Exit Criteria for Step 6

The MVP is complete when the batch script successfully outputs:
1.  `final_action_cards_with_copy.jsonl`: Contains the ActionCard info + the validated `final_llm_copy` + the `validation_status` (PASS or FALLBACK).
2.  `telemetry_audit.jsonl`: Accurately updated with the issuance tracking rows.
3.  **Summary JSON Output:** Showing the exact Pass Rates, Grounding Rejects, and Schema failures inside `llm_gateway_metrics.json`.
