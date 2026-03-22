"""
compute_rfm.py

Step 1 of the CPG Decision Engine pipeline.

Responsibilities:
  1. Load raw Instacart-format data (orders, order_products__prior, products).
  2. Map to canonical tables: products, orders, order_items, customers.
  3. Compute RFM (Recency, Frequency, Monetary) scores and segment labels.

Output files written to OUTPUT_DIR:
  - customers_canonical_full.csv   : RFM scores + segment per user
  - orders_canonical.csv           : full order history with synthetic timestamps
  - (products canonical is written by compute_risk.py after cycle computation)

Small-dataset robustness:
  - _rfm_score() replaces pd.qcut directly to handle datasets with fewer
    unique values than the target number of bins (avoids ValueError crash).
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration & constants
# ---------------------------------------------------------------------------

DATA_DIR   = os.getenv("MVP_DATA_DIR",   "./DATASETS")
OUTPUT_DIR = os.getenv("MVP_OUTPUT_DIR", "./OUTPUT")
ANCHOR_DATE = datetime(2023, 12, 31)   # fixed as_of_date for backtesting / mocking


def stable_hash(val: str) -> int:
    """Deterministic hash for synthetic value generation."""
    return int(hashlib.md5(str(val).encode("utf-8")).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Robust quantile scorer (replaces pd.qcut directly)
# ---------------------------------------------------------------------------

def _rfm_score(series: pd.Series, n_bins: int = 5, ascending: bool = True) -> pd.Series:
    """
    Assign integer scores 1–n_bins using quantile ranking.

    Robust to small datasets: when fewer than 2 distinct values exist every
    customer receives the neutral mid-point score (3). Falls back to linear
    interpolation when pd.qcut still raises.

    Args:
        series:    Numeric series to score.
        n_bins:    Target number of bins (default 5 for standard RFM).
        ascending: True  → higher value  = higher score (Frequency, Monetary).
                   False → lower  value  = higher score (Recency: fewer days = better).

    Returns:
        Integer Series aligned to series.index, values clipped to [1, n_bins].
    """
    if len(series) == 0:
        return pd.Series(dtype=int)

    ranked = series.rank(method="first")
    actual_bins = min(n_bins, len(series), int(ranked.nunique()))

    if actual_bins < 2:
        # Not enough distinct values — assign neutral midpoint
        return pd.Series(3, index=series.index, dtype=int)

    labels = list(range(1, actual_bins + 1))
    try:
        scores = pd.qcut(ranked, actual_bins, labels=labels).astype(float)
    except ValueError:
        # Last-resort linear interpolation across the ranked range
        scores = (
            (ranked - ranked.min()) / max(ranked.max() - ranked.min(), 1)
            * (actual_bins - 1) + 1
        ).round()

    scores = scores.fillna(1)

    # Scale up to n_bins if actual_bins < n_bins (small dataset normalisation)
    if actual_bins < n_bins:
        scores = (
            (scores - 1) / (actual_bins - 1) * (n_bins - 1) + 1
        ).round()

    scores = scores.clip(1, n_bins).astype(int)

    if not ascending:
        scores = n_bins + 1 - scores

    return scores


# ---------------------------------------------------------------------------
# Main pipeline function
# ---------------------------------------------------------------------------

def load_and_map_data():
    print("Loading datasets...")
    orders_raw   = pd.read_csv(f"{DATA_DIR}/orders.csv")
    prior_raw    = pd.read_csv(f"{DATA_DIR}/order_products__prior.csv")
    products_raw = pd.read_csv(f"{DATA_DIR}/products.csv")

    print("Mapping to Canonical Tables...")

    # ── 1. Products Table ────────────────────────────────────────────────────
    # Canonical: product_id, product_name, department_id, aisle_id,
    #            mock_inventory, avg_replenishment_days (set by compute_risk.py)
    products_curr = products_raw[
        ["product_id", "product_name", "department_id", "aisle_id"]
    ].copy()
    products_curr["mock_inventory"] = products_curr["product_id"].apply(
        lambda x: stable_hash(x) % 501
    )
    products_curr["avg_replenishment_days"] = None  # filled by compute_risk.py

    # ── 2. Orders Table (synthesise order_ts) ────────────────────────────────
    # Canonical: order_id, user_id, order_number, days_since_prior_order, order_ts
    orders_curr = orders_raw[
        ["order_id", "user_id", "order_number", "days_since_prior_order"]
    ].copy()
    orders_curr["days_since_prior_order"] = orders_curr[
        "days_since_prior_order"
    ].fillna(0)

    # Reconstruct order_ts by walking backwards from ANCHOR_DATE
    orders_curr = orders_curr.sort_values(
        by=["user_id", "order_number"], ascending=[True, False]
    )
    orders_curr["days_to_subtract"] = (
        orders_curr.groupby("user_id")["days_since_prior_order"].shift(1).fillna(0)
    )
    orders_curr["cum_days_back"] = orders_curr.groupby("user_id")[
        "days_to_subtract"
    ].cumsum()
    orders_curr["order_ts"] = ANCHOR_DATE - pd.to_timedelta(
        orders_curr["cum_days_back"], unit="d"
    )

    orders_canonical = orders_curr[
        ["order_id", "user_id", "order_number", "days_since_prior_order", "order_ts"]
    ].copy()  # .copy() prevents SettingWithCopyWarning on the next line

    # ── 3. Order Items Table ─────────────────────────────────────────────────
    # Canonical: order_id, product_id, qty, reordered_flag
    order_items_canonical = prior_raw[["order_id", "product_id", "reordered"]].copy()
    order_items_canonical.rename(columns={"reordered": "reordered_flag"}, inplace=True)
    order_items_canonical["qty"] = 1  # Instacart format has no qty column

    # ── 4. Customers Table (RFM aggregation) ─────────────────────────────────
    print("Computing RFM Segments...")

    # Frequency: max order_number per user = total orders placed
    freq_df = (
        orders_canonical.groupby("user_id")["order_number"]
        .max()
        .reset_index(name="total_orders")
    )

    # Recency: add a deterministic per-user jitter so not all users land on
    # ANCHOR_DATE (otherwise recency_days = 0 for everyone and R is meaningless)
    def _user_offset(uid) -> timedelta:
        return timedelta(days=(stable_hash(uid) % 60))

    orders_canonical["order_ts"] = orders_canonical.apply(
        lambda row: row["order_ts"] - _user_offset(row["user_id"]), axis=1
    )
    recency_df = (
        orders_canonical.groupby("user_id")["order_ts"]
        .max()
        .reset_index(name="max_order_ts")
    )
    recency_df["recency_days"] = (
        ANCHOR_DATE - recency_df["max_order_ts"]
    ).dt.days

    # Monetary proxy: total items bought across all prior orders
    items_with_user = pd.merge(
        order_items_canonical,
        orders_canonical[["order_id", "user_id"]],
        on="order_id",
    )
    monetary_df = (
        items_with_user.groupby("user_id")["qty"]
        .sum()
        .reset_index(name="m_proxy_value")
    )

    # Combine R, F, M
    customers_canonical = pd.merge(
        freq_df, recency_df[["user_id", "recency_days"]], on="user_id"
    )
    customers_canonical = pd.merge(
        customers_canonical, monetary_df, on="user_id", how="left"
    )
    customers_canonical["m_proxy_value"] = customers_canonical[
        "m_proxy_value"
    ].fillna(0)
    customers_canonical["as_of_date"] = ANCHOR_DATE

    # ── RFM Scoring (1–5, robust to small datasets) ──────────────────────────
    # R: lower recency_days = more recent = better → ascending=False
    # F: higher total_orders = better              → ascending=True
    # M: higher m_proxy_value = better             → ascending=True
    customers_canonical["R_score"] = _rfm_score(
        customers_canonical["recency_days"], ascending=False
    )
    customers_canonical["F_score"] = _rfm_score(
        customers_canonical["total_orders"], ascending=True
    )
    customers_canonical["M_score"] = _rfm_score(
        customers_canonical["m_proxy_value"], ascending=True
    )

    # RFM cell string (e.g. "453")
    customers_canonical["RFM_Cell"] = (
        customers_canonical["R_score"].astype(str)
        + customers_canonical["F_score"].astype(str)
        + customers_canonical["M_score"].astype(str)
    )

    # ── Segment assignment (deterministic rules) ─────────────────────────────
    def assign_segment(row) -> str:
        r, f, m = row["R_score"], row["F_score"], row["M_score"]
        if r >= 4 and f >= 4 and m >= 4:
            return "Champion / Loyal"
        elif r <= 2 and (f >= 4 or m >= 4):
            return "High-Value At-Risk"
        elif r >= 4 and f <= 2:
            return "New / Promising"
        elif r <= 2 and f <= 2 and m <= 2:
            return "Low-Value / Lost"
        elif r == 3 and (f >= 3 or m >= 3):
            return "Need Attention"
        else:
            return "Average Purchaser"

    customers_canonical["rfm_segment"] = customers_canonical.apply(
        assign_segment, axis=1
    )

    # ── Diagnostics ──────────────────────────────────────────────────────────
    print("\n--- Segment Definitions (Rules) ---")
    print("1. Champion / Loyal:    R>=4 AND F>=4 AND M>=4")
    print("2. High-Value At-Risk:  R<=2 AND (F>=4 OR M>=4)")
    print("3. New / Promising:     R>=4 AND F<=2")
    print("4. Low-Value / Lost:    R<=2 AND F<=2 AND M<=2")
    print("5. Need Attention:      R==3 AND (F>=3 OR M>=3)")
    print("6. Average Purchaser:   All others")

    print("\n--- Segment Distribution Summary ---")
    dist = (
        customers_canonical["rfm_segment"]
        .value_counts(normalize=True)
        .mul(100)
        .round(1)
        .astype(str)
        + "%"
    )
    print(dist.to_string())

    print("\n--- Canonical Tables Sample ---")
    print("\nCustomers Head:")
    print(
        customers_canonical[
            [
                "user_id", "total_orders", "m_proxy_value",
                "recency_days", "R_score", "F_score", "M_score", "rfm_segment",
            ]
        ].head()
    )

    # ── Write outputs ─────────────────────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    customers_canonical.to_csv(
        f"{OUTPUT_DIR}/customers_canonical_full.csv", index=False
    )
    # Save the FULL order history (not a truncated sample) so compute_risk.py
    # can compute accurate interpurchase gaps across all users.
    orders_canonical.to_csv(
        f"{OUTPUT_DIR}/orders_canonical.csv", index=False
    )

    return customers_canonical, orders_canonical, products_curr, order_items_canonical


if __name__ == "__main__":
    load_and_map_data()
