# CPG Decision Engine MVP

**A Bouncer-Patterned Agentic Marketing Engine tailored for CPG / Retail.**

This repository contains the v0 Minimum Viable Product (MVP) of the CPG Decision Engine. It is an end-to-end Python pipeline designed to ingest raw transactional data, deterministically compute RFM segmentation and replenishment risk signals, construct strict policy-constrained "Trade Orders" (Action Cards), and safely render them into customer-facing copy via Large Language Models (LLMs) protected by a rigorous 4-Gate "Bouncer Pattern" validator.

## 🎯 Architecture Philosophy: The "Brain-Mouth Decoupling"

The core innovation of this MVP is its strict separation between decision-making and text generation.

1.  **The Brain (Deterministic Data & Policy Layer):** LLMs **never** do math, make commercial decisions, or calculate discounts. RFM clustering, replenishment cycles, and discount ceilings are computed by traditional, deterministic Python/Pandas logic against a strict Canonical Data Contract.
2.  **The Mouth (LLM Explainer Layer):** The LLM is strictly a "copywriter/renderer." It receives a highly constrained JSON Action Card and hydrates templates based *only* on the provided facts.
3.  **The Bouncer (4-Gate Validator):** Every LLM output is parsed, fact-checked, and safety-scanned. If the LLM hallucinates an unauthorized discount or violates brand safety, the Bouncer intercepts the payload, triggers a deterministic fallback, and logs the incident.

---

## 🏗 System Components

| Component | Responsibility | Relevant Files |
| :--- | :--- | :--- |
| **Data Ingestion & Mapping** | Maps raw datasets (e.g., Instacart CSVs) to the strict `v0` Canonical Contract (Orders, Items, Customers, Products). Synthesizes timestamps and mock inventories deterministically for testing. | `compute_rfm.py`, `CONTRACTS/Canonical_Data_Contract_v0.md` |
| **Signal Engine** | Computes traditional RFM (Recency, Frequency, Monetary Proxy) and assigns business-actionable segments (e.g., *Champion*, *High-Value At-Risk*). Computes dynamic `overdue_ratio` based on empirical SKU/Category replenishment gaps. | `compute_rfm.py`, `compute_risk.py` |
| **Policy Engine (Risk Gateway)** | Applies hard business constraints (e.g., `inventory_floor >= 50`, `margin_floor >= 15%`, Segment-Specific Discount Caps). Transforms candidates into actionable "Trade Orders" (Action Cards). | `policy_engine.py` |
| **Bouncer Pattern (LLM Validator)** | A rigid 4-Gate safety pipeline for LLM outputs: **A) Schema** (JSON parsing), **B) Policy Echo** (Did the math change?), **C) Grounding** (Were facts invented?), **D) Copy Safety** (Banned phrases/Length). | `step6_bouncer_validator.py`, `step6_fallback_templates.py`, `step6_audit_logger.py` |
| **Knowledge Graph Ontology** | A predefined graph schema mapping how nodes (Customers, Products, Segments, Actions) and edges interact for future Graph Database (e.g., Neo4j) ingestion and causal loop tracking. | `CONTRACTS/KG_Ontology_v0.json` |

---

## 🚀 Execution Workflow

The pipeline is completely deterministic and can be run sequentially offline.

### Installation Requirements
- Python 3.10+
- Pandas
- PyTest (for Bouncer unit testing)

```bash
pip install pandas pytest
```

### Step-by-Step Execution

#### 1. Data Ingestion & Signal Generation
This script processes the raw `/DATASETS`, maps them to canonical tables, calculates RFM percentiles, and assigns customer segments.
```bash
python3 compute_rfm.py
```
*Outputs:* `OUTPUT/customers_canonical_full.csv`, `OUTPUT/orders_canonical_sample.csv`

#### 2. Risk & Replenishment Scoring
Calculates empirical interpurchase gaps to determine true replenishment cycles (with SKU -> Aisle -> Department fallback handling). Identifies candidates with an `overdue_ratio > 1.2`.
```bash
python3 compute_risk.py
```
*Outputs:* `OUTPUT/products_canonical.csv`, `OUTPUT/risk_scores_v0.csv`, `OUTPUT/at_risk_candidates_v0.csv`

#### 3. Policy Evaluation (The Risk Gateway)
Takes the At-Risk candidates and applies strict business constraints (Inventory limitations, Margin Floors, Frequency Caps). Only candidates passing all gates are issued an Action Card.
```bash
python3 policy_engine.py
```
*Outputs:* `OUTPUT/action_cards_v0.csv`, `OUTPUT/campaign_events_v0.csv`

#### 4. The LLM Rendering Harness (Bouncer Pattern)
Runs the generated Action Cards through the mock LLM renderer. Deliberately injects a ~30% hallucination rate (inflated discounts, missing placeholders, banned words) to demonstrate the 4-Gate Validator catching errors and gracefully falling back to deterministic templates.
```bash
python3 run_pipeline_harness.py
```
*Outputs:*
- `outputs/final_action_cards_with_copy.jsonl` (The final deployable payload)
- `logs/step6_audit.jsonl` (Telemetry of every LLM validation attempt)
- `outputs/step6_metrics.md` (Performance and rejection reporting)

---

## 🛡 Validating the Bouncer Pattern

To ensure the integrity of the AI Security layer, a comprehensive PyTest suite explicitly asserts every possible failure pathway across the 4 Gates (e.g., `E_POLICY_ECHO_DISCOUNT_MISMATCH`, `E_NUMERIC_INJECTION`).

```bash
PYTHONPATH=. pytest tests/test_step6_validator.py
```

*Expected output: 14/14 tests passing flawlessly.*

---

## 📈 Roadmap & Next Steps (V1)
- **Data:** Ingest live Merchant Data (e.g., Shopline APIs) replacing the Instacart sandbox.
- **Modeling:** Implement BTYD (Buy Till You Die) probabilistic models to replace simple `overdue_ratio` heuristics.
- **Graphing:** Export the Canonical Tables directly into a Neo4j instance following the established `KG_Ontology_v0.json`.
- **LLM Integration:** Replace the `generate_mock_llm_response` function with real calls to OpenAI/Anthropic APIs for dynamic copy variation.
