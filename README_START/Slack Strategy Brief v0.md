Title: CPG Decision Engine – MVP Strategy & Next Steps (v0)
Owner: Architecture (Bo)
Date: (fill in)
1) Goal
Ship a fast, demo-ready MVP for CPG brands focused on RFM segmentation + action-card style outputs (recommendations + explainability), while preparing the pipeline/KG for future Shopline merchant data fine-tuning.
2) Data Strategy (multi-source, parallel)
We will not wait for a single data source. We will progress with 3 tracks in parallel:
Public datasets (selected by partner/Yila)
Used for MVP prototyping: RFM segmentation + baseline predictive signals
Operator merchant data (real merchants, may not be CPG-only)
Used to increase scale + diversify patterns; treat vertical mismatch as known risk
Shopline developer store sample data
Used mainly to understand Shopline-like schema/format, validate mapping & KG schema alignment
Shopline merchant training data will be used once the contract/compliance process completes. We assume distribution mismatch vs public data; acceptable for MVP showcase, then mitigate via fine-tuning later.
3) MVP Scope Freeze (what we will build first)
In-scope (v0 MVP)
RFM segmentation (must-have, primary deliverable)
Canonical data schema + ingestion/profiling pipeline (so datasets can be standardized)
KG v0 to support:
Customer–Order–Product facts
Segment assignment (Customer → IN_SEGMENT)
Action cards (recommend action + evidence + constraints)
“Action Cards” output (UI-friendly JSON/CSV) with deterministic logic and basic explainability
Out-of-scope (defer to v1 unless confirmed needed)
Full funnel subgraph Session → Cart → Checkout → Purchase
Keep as v1 design; only implement if Shopline provides data and CPG expert confirms it’s a critical pain point
Multi-touch attribution / true uplift measurement (needs marketing event chain; can be simulated in MVP if missing)
Heavy deep models (GRU/Attention, complex recommenders) before baseline is stable
4) KG Strategy (v0 vs v1)
KG v0 (MVP)
Minimum nodes/properties to standardize training data + enable explainable actions:
Nodes: Customer, Order, OrderItem, Product/Variant, Segment, (optional RiskScore), Action, Constraint/FailureCase
Edges: PLACED, HAS_ITEM, OF_PRODUCT, IN_SEGMENT, (HAS_RISK), RECOMMENDED_ACTION, GUARDED_BY_CONSTRAINT
KG v1 (Expansion)
Add Product Taxonomy / Collection / Promotion / Attribution / Supplier
Add Funnel subgraph: Session/Cart/Checkout with drop-off edges if data exists
Add campaign events for real measurement & causal evaluation
5) Model Strategy (fast-first)
Immediate: RFM segmentation as MVP model output
Next (after data readiness): baseline churn or time-to-next-purchase (tree model / survival)
Training plan once Shopline data arrives:
Preferred: Pretrain on public/operator → fine-tune on Shopline
Alternative: Shopline-only if quality/volume sufficient
6) Deliverables & “Done” Criteria (MVP)
MVP is “done” when we can demo:
Ingest a dataset → map to canonical schema
Compute RFM → assign segments
Write segments into KG v0
Produce action cards for segments (with evidence + constraints)
Show a simple UI/CSV output that looks like product
7) Open Questions (needs confirmation)
What exactly will Shopline provide (events? funnel? subscription contracts?) once contract is processed?
Whether operator data must be CPG-only vs acceptable cross-vertical for pretraining
CPG expert insights: which pain points to prioritize (replenishment cycle vs checkout friction vs promotion strategy)
8) Immediate Next Steps (this week)
Finalize MVP Scope Freeze v0 (1-pager)
Finalize KG Ontology v0 (MVP) + canonical schema + data contract v0
Start RFM segmentation prototype on public dataset
Confirm dataset license/commercial usage terms (see “license evidence” below)