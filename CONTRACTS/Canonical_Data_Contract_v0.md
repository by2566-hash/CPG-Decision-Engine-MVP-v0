# [Canonical Data Contract v0] CPG Decision Engine — Standardized Tables

**Version:** v0  
**Owner:** Architecture  
**Purpose:** This contract is the system’s “customs gate.”  
Any external dataset (public/operator/Shopline) must be normalized into these tables before entering the engine.

---

## 0) Contract Principles
1) **Deterministic & Reproducible**
- No unseeded randomness. Any mock fields must be deterministic.

2) **Schema First**
- If a source lacks a field, we add a derived or placeholder field **with explicit assumptions**.

3) **Temporal Readiness**
- The engine requires a consistent time axis (`order_ts`) to support recency, versions (`as_of_date`), and future backtesting.

4) **License Awareness**
- Each dataset ingestion must carry metadata:
  - `source_url`, `license_type`, `license_status` (OK/UNKNOWN), `evidence_reference`

---

## 1) Core Tables (v0)

### 1.1 `customers` (User Table)
**Definition:** user-level attributes and aggregates used for segmentation and decisioning.

| Column | Type | Required | Notes |
|---|---|---:|---|
| `user_id` | string | ✅ | Primary identifier |
| `total_orders` | int | ✅ (Engine Generated) | Aggregated from `orders` |
| `m_proxy_value` | float | ✅ (Engine Generated) | Monetary proxy (e.g., `unique_skus_bought` or `total_items_bought`) |
| `rfm_segment` | string | ✅ (Engine Generated) | Output label (e.g., `High-Value At-Risk`) |
| `as_of_date` | date | ✅ | The evaluation cut date for segmentation |

**Notes**
- If true GMV is missing, `m_proxy_value` must be explicitly documented as a proxy.

---

### 1.2 `products` (SKU / Product Table)
**Definition:** product-level attributes + CPG cycle parameters + mock operational fields for v0 testing.

| Column | Type | Required | Notes |
|---|---|---:|---|
| `product_id` | string | ✅ | Primary identifier |
| `product_name` | string | ✅ | Human readable |
| `department_id` | string | ✅ | Category used for cycle fallback and policy rules |
| `aisle_id` | string | ✅ | Sub-category used for cycle fallback |
| `mock_inventory` | int | ✅ (Config Generated) | Deterministic mock for v0 |
| `avg_replenishment_days` | float | ✅ (Engine Generated) | Expected replenishment cycle (product-level if dense; else aisle/department fallback) |

**Mock Inventory Rule (Deterministic)**
- `mock_inventory = (stable_hash(product_id) % 501)` → produces integers in `[0, 500]`
- No randomness without a fixed seed.

---

### 1.3 `orders` (Order Header Table)
**Definition:** the transactional time axis.

| Column | Type | Required | Notes |
|---|---|---:|---|
| `order_id` | string | ✅ | Primary identifier |
| `user_id` | string | ✅ | FK → `customers.user_id` |
| `order_number` | int | ✅ | Nth order for the user |
| `days_since_prior_order` | float | ✅ | If missing (Shopline), derive from timestamps |
| `order_ts` | timestamp | ✅ | Required time axis for recency and backtesting |

**Order Timestamp Rules**
- **Public/Instacart-style sources:** generate `order_ts` deterministically using an anchor date + cumulative gaps per user.
- **Shopline sources:** use the real order timestamp.

---

### 1.4 `order_items` (Order Line Table)
**Definition:** item-level granularity for behavior signals and repurchase patterns.

| Column | Type | Required | Notes |
|---|---|---:|---|
| `order_id` | string | ✅ | FK → `orders.order_id` |
| `product_id` | string | ✅ | FK → `products.product_id` |
| `qty` | int | ✅ (Default=1) | If missing, default to 1 |
| `reordered_flag` | bool | ✅ | Whether the item is a reorder |

---

### 1.5 `campaign_events` (Tracking Stub Table)
**Definition:** feedback-loop events for future platform integration.

| Column | Type | Required | Notes |
|---|---|---:|---|
| `event_id` | string | ✅ | Primary identifier |
| `target_user_id` | string | ✅ | User targeted by action |
| `action_trade_id` | string | ✅ | Action Card ID |
| `event_type` | string | ✅ | Enum: `ACTION_ISSUED`, `CAMPAIGN_SENT`, `CONVERSION_HIT` |
| `event_ts` | timestamp | ✅ | When the event occurred |
| `metadata` | json | ❌ | Optional payload (channel, discount_pct, etc.) |

**v0 Behavior**
- v0 guarantees generating `ACTION_ISSUED`.
- `CAMPAIGN_SENT` and `CONVERSION_HIT` are schema-only in v0.

---

## 2) Data Quality Gates (Minimum Checks)
1) **Uniqueness**
- `orders.order_id` must be unique.

2) **FK Integrity**
- `order_items.order_id ⊆ orders.order_id`
- `order_items.product_id ⊆ products.product_id`

3) **Null Handling**
- `days_since_prior_order` may be null for first orders → treat as 0 or NA (document choice).
- `order_ts` must be non-null after derivation.

4) **Reproducibility**
- Mock fields must be deterministic.
- Derivations must be documented and versioned.

---

## 3) Dataset Metadata (Required for Each Ingestion)
For each dataset ingested, record:

- `dataset_name`
- `source_url`
- `license_type`
- `license_status` (OK / UNKNOWN)
- `license_evidence_reference` (file path, screenshot, or excerpt)

---

## 4) Notes on Modeling Compatibility
- `m_proxy_value` is a proxy; avoid claiming real GMV.
- Replenishment cycles should prefer robust estimates (median/trimmed mean).
- If product-level estimates are sparse, fallback to aisle/department-level cycles in v0.