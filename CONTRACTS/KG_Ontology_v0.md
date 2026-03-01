# Minimal KG Ontology v0

This ontology defines the entities and relationships for the CPG Decision Engine MVP (v0). It strictly supports RFM segmentation facts, risk scoring, action cards, and tracking events.

## Nodes (Entities)

| Node Label | Description | Expected Properties |
| :--- | :--- | :--- |
| **`Customer`** | The user/purchaser entity. | `id` (source: `user_id`), `total_orders`, `m_proxy_value` |
| **`Product`** | The physical CPG item being purchased. | `id` (source: `product_id`), `name`, `department_id`, `aisle_id`, `mock_inventory`, `avg_replenishment_days` |
| **`Order`** | The transaction envelope. | `id` (source: `order_id`), `user_id`, `order_ts` |
| **`Segment`** | The defined RFM behavioral group. | `label` |
| **`RiskScore`** | Replenishment risk evaluation. | `id`, `run_id`, `as_of_date`, `overdue_ratio`, `last_purchase_days_ago` |
| **`ActionCard`** | Generated trade/countermeasure. | `id`, `run_id`, `policy_version`, `action_type`, `parameter_value`, `policy_passed`, `evidence`, `constraints_applied`, `expected_value_proxy` (optional), `llm_explanation` |
| **`CampaignEvent`**| Auditing issuance stub. | `id`, `run_id`, `event_type` |

## Edges (Relationships)

| Source Node | Edge Label | Target Node | Properties |
| :--- | :--- | :--- | :--- |
| `Customer` | **`PLACED`** | `Order` | `order_number` |
| `Order` | **`HAS_ITEM`** | `Product` | `qty`, `reordered_flag` |
| `Customer` | **`IN_SEGMENT`** | `Segment` | `run_id`, `as_of_date` |
| `Customer` | **`HAS_RISK`** | `RiskScore` | `run_id`, `calculated_on` |
| `RiskScore` | **`RECOMMENDED_ACTION`** | `ActionCard`| `run_id`, `confidence_score` (optional) |
| `ActionCard` | **`EMITTED_EVENT`**| `CampaignEvent`| `run_id`, `emitted_at` |

## Canonical Tables → KG Writeback Mapping (v0)

| KG Mapping Target | Source Table / Derivation |
| :--- | :--- |
| **`Customer.id`** | `customers.user_id` |
| **`Product.department_id`** | `products.department_id` |
| **`Product.aisle_id`** | `products.aisle_id` |
| **`Order.user_id`** | `orders.user_id` |
| **`Order.order_ts`** | `orders.order_ts` |
| **`RiskScore.last_purchase_days_ago`** | Computed: diff between global `as_of_date` and user's latest `Order.order_ts` |
