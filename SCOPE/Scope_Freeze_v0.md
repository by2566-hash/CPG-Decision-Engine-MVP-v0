# [MVP v0 Scope Freeze] CPG Decision Engine — Boundary Lock

**Version:** v0  
**Owner:** Architecture  
**Status:** Active (Frozen for MVP execution)

---

## 1) Objective
Deliver a closed-loop MVP for CPG subscription merchants: **from raw data ingestion → RFM segmentation + replenishment signals → constraint-checked Action Cards with LLM explanations**.

We treat every operational intervention as a **“trade”**:
- **Signal (Alpha)**: identify churn / replenishment risk
- **Risk Gateway (Policy Engine)**: enforce constraints before execution
- **Order Execution (Action Card)**: produce a structured, auditable payload

---

## 2) IN SCOPE (Must Deliver in v0)

### 2.1 Canonical Data Ingestion (Standardized Data Foundation)
- Map public datasets (e.g., Instacart-style data) into our **Canonical Data Contract v0**.
- **License status must be verified**. If license evidence is missing: mark `LICENSE=UNKNOWN` and proceed only for internal prototyping.

### 2.2 CPG-Specific Signals (Replenishment-Aware Risk)
- Compute **Overdue Ratio** instead of a fixed “90-day churn” rule:
  - `overdue_ratio = days_since_last_purchase / expected_replenishment_cycle`
- Support robust fallbacks if product-level cycles are sparse:
  - product-level → aisle-level → department-level

### 2.3 Dual-Track Constraints via Policy Engine (Risk Gateway)
Implement a Policy Engine that enforces two categories of constraints:

- **Data-driven constraints** (derived from behavior):
  - frequency thresholds, overdue_ratio thresholds, segment eligibility, etc.
- **Config-driven constraints** (merchant or mock configs in v0):
  - `inventory_floor`, `margin_floor`, `max_discount_pct`, `frequency_cap`, etc.

> **Hard rule:** The Policy Engine decides actions and parameters. The LLM cannot override constraints.

### 2.4 Bouncer Pattern LLM (LLM as Explainer Only)
- LLM can only generate **copy/explanations** from factual inputs (signals, segments, constraints).
- LLM must **NOT**:
  - choose actions
  - compute discount amounts
  - perform arithmetic that determines money/risk outcomes

### 2.5 Tracking Schema Stub (Loop Readiness)
- Define tracking tables (schemas only) for future Shopline loop:
  - `action_issued` (v0 produces this)
  - `campaign_sent`, `conversion_hit` (v0 defines schema only; no real sending/attribution)

---

## 3) OUT OF SCOPE (Explicitly NOT in v0)

### 3.1 No Real-Time Platform Integration
- No direct Shopline GraphQL/Webhook integrations in v0.
- v0 is **100% offline batch processing**.

### 3.2 No Funnel / Front-End Behavioral Data
- No session/cart/checkout modeling.
- No abandoned cart / checkout events; order-driven only.

### 3.3 No Graph Database Deployment
- No Neo4j or managed graph DB in v0.
- KG logic is represented in lightweight **Python dicts / JSON** and/or tables.

### 3.4 No Multi-Touch Attribution / Causal Uplift
- Only minimal tracking schema for future iteration.
- No uplift modeling, MMM, multi-touch attribution in v0.

### 3.5 No Heavy Modeling Stack as a Dependency
- No requirement to ship deep sequence models (GRU/Attention/TFT) in v0.
- Focus on deterministic segmentation + baseline scoring + explainable action outputs.

---

## 4) Definition of Done (v0 Exit Criteria)
MVP v0 is complete when we can demo:

1. **Ingest** public data → map into Canonical Data Contract v0.
2. Compute **RFM segments** and assign every user a segment label.
3. Compute **overdue_ratio** signals and identify “at-risk candidates”.
4. Generate **Action Cards** where:
   - action and parameters come from Policy Engine
   - constraints are explicitly PASS/FAIL
   - LLM provides a human-readable explanation from facts only
5. Produce **action_issued** records (tracking stub) for auditability.

---

## 5) Known Risks / Notes
- Distribution mismatch between public datasets and Shopline merchant data is expected and acceptable for MVP.
- License evidence must be collected before any external demo or commercial use.
- Funnel subgraph and attribution are reserved for v1 based on data availability and CPG expert feedback.