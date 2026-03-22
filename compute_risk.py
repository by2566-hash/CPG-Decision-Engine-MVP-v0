"""
compute_risk.py

Step 2 of the CPG Decision Engine pipeline.

Responsibilities:
  1. Compute per-product average replenishment cycle (interpurchase gap) using
     a 4-tier fallback hierarchy: Product → Aisle → Department → Global_Default.
  2. Record which tier was used as `replenishment_source` — this is the primary
     signal for RECOMMENDATION vs HYPOTHESIS classification downstream.
  3. Compute `overdue_ratio` per user-product pair.
  4. Filter at-risk candidates (overdue_ratio > 1.2 AND mock_inventory > 50).

Output files written to OUTPUT_DIR:
  - products_canonical.csv      : avg_replenishment_days + replenishment_source
  - at_risk_candidates_v0.csv   : user×product pairs that need attention

Key correctness guarantees:
  - Reads the FULL orders_canonical.csv (not a truncated sample).
  - replenishment_source IS written to products_canonical.csv so that
    decision_card_builder.py can correctly classify cards as
    RECOMMENDATION (Product/Aisle) vs HYPOTHESIS (Department/Global_Default).
  - avg_replenishment_days = 0 is guarded against to prevent overdue_ratio = inf.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration & constants
# ---------------------------------------------------------------------------

DATA_DIR   = os.getenv("MVP_DATA_DIR",   "./DATASETS")
OUTPUT_DIR = os.getenv("MVP_OUTPUT_DIR", "./OUTPUT")
ANCHOR_DATE = datetime(2023, 12, 31)
RUN_ID = "run_v0_001"

# Minimum interpurchase gap observations required to trust the product-level median
MIN_SAMPLES_FOR_ROBUST_STAT = 5

# Global_Default cycle (days) used when no tier has enough data
GLOBAL_DEFAULT_CYCLE_DAYS = 30.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def stable_hash(val) -> int:
    return int(hashlib.md5(str(val).encode("utf-8")).hexdigest()[:8], 16)


def _safe_overdue_ratio(days_ago: float, avg_cycle: float) -> float:
    """
    Return overdue_ratio = days_ago / avg_cycle.

    Guards against avg_cycle == 0 (would produce inf or ZeroDivisionError).
    Falls back to GLOBAL_DEFAULT_CYCLE_DAYS when the stored cycle is zero or
    missing, which gives a sensible ratio rather than an unreliable extreme.
    """
    if pd.isna(avg_cycle) or avg_cycle <= 0:
        avg_cycle = GLOBAL_DEFAULT_CYCLE_DAYS
    return days_ago / avg_cycle


# ---------------------------------------------------------------------------
# Main pipeline function
# ---------------------------------------------------------------------------

def compute_replenishment_and_risk():
    print("Loading Canonical Tables...")
    customers = pd.read_csv(f"{OUTPUT_DIR}/customers_canonical_full.csv")

    # Read the FULL order history produced by compute_rfm.py.
    # (Previously read the truncated 1,000-row sample — that caused sparse
    #  interpurchase gap data and incorrect replenishment cycle estimates.)
    orders = pd.read_csv(f"{OUTPUT_DIR}/orders_canonical.csv")

    print("Loading Raw Products and Items...")
    prior_raw    = pd.read_csv(f"{DATA_DIR}/order_products__prior.csv")
    products_raw = pd.read_csv(f"{DATA_DIR}/products.csv")

    # Reconstruct products_curr with mock_inventory
    products_curr = products_raw[
        ["product_id", "product_name", "department_id", "aisle_id"]
    ].copy()
    products_curr["mock_inventory"] = products_curr["product_id"].apply(
        lambda x: stable_hash(x) % 501
    )

    # Join items → orders to attach timestamps and category hierarchy
    order_items = pd.merge(
        prior_raw, orders[["order_id", "user_id", "order_ts"]], on="order_id"
    )
    order_items["order_ts"] = pd.to_datetime(order_items["order_ts"])
    order_items = pd.merge(
        order_items,
        products_curr[["product_id", "aisle_id", "department_id"]],
        on="product_id",
    )

    # ── 1. Interpurchase gap sequences ───────────────────────────────────────
    print("Computing Interpurchase Gaps...")
    sorted_history = order_items.sort_values(
        by=["user_id", "product_id", "order_ts"]
    )
    sorted_history["prev_order_ts"] = sorted_history.groupby(
        ["user_id", "product_id"]
    )["order_ts"].shift(1)
    sorted_history["purchase_gap_days"] = (
        sorted_history["order_ts"] - sorted_history["prev_order_ts"]
    ).dt.days

    valid_gaps = sorted_history.dropna(subset=["purchase_gap_days"]).copy()
    valid_gaps = valid_gaps[valid_gaps["purchase_gap_days"] > 0]

    # ── 2. Aggregate replenishment cycles with fallback hierarchy ─────────────
    print("Aggregating Replenishment Cycles with Fallbacks...")

    prod_stats = (
        valid_gaps.groupby("product_id")["purchase_gap_days"]
        .agg(prod_median="median", prod_count="count")
        .reset_index()
    )
    aisle_stats = (
        valid_gaps.groupby("aisle_id")["purchase_gap_days"]
        .agg(aisle_median="median", aisle_count="count")
        .reset_index()
    )
    dept_stats = (
        valid_gaps.groupby("department_id")["purchase_gap_days"]
        .agg(dept_median="median")
        .reset_index()
    )

    replenishment_map = pd.merge(
        products_curr, prod_stats, on="product_id", how="left"
    )
    replenishment_map = pd.merge(
        replenishment_map, aisle_stats, on="aisle_id", how="left"
    )
    replenishment_map = pd.merge(
        replenishment_map, dept_stats, on="department_id", how="left"
    )

    def get_best_cycle(row) -> tuple[float, str]:
        """Return (avg_cycle_days, replenishment_source_tier)."""
        if (
            pd.notna(row["prod_count"])
            and row["prod_count"] >= MIN_SAMPLES_FOR_ROBUST_STAT
        ):
            return row["prod_median"], "Product"
        elif (
            pd.notna(row["aisle_count"])
            and row["aisle_count"] >= MIN_SAMPLES_FOR_ROBUST_STAT
        ):
            return row["aisle_median"], "Aisle"
        elif pd.notna(row["dept_median"]):
            return row["dept_median"], "Department"
        else:
            return GLOBAL_DEFAULT_CYCLE_DAYS, "Global_Default"

    applied_logic = replenishment_map.apply(
        get_best_cycle, axis=1, result_type="expand"
    )
    replenishment_map["avg_replenishment_days"] = applied_logic[0].astype(float)
    replenishment_map["replenishment_source"]   = applied_logic[1]

    # ── Write products_canonical with BOTH avg_replenishment_days AND
    #    replenishment_source so decision_card_builder.py can classify cards
    #    correctly as RECOMMENDATION vs HYPOTHESIS. ──────────────────────────
    products_curr["avg_replenishment_days"] = replenishment_map[
        "avg_replenishment_days"
    ].values
    products_curr["replenishment_source"] = replenishment_map[
        "replenishment_source"
    ].values
    products_curr.to_csv(f"{OUTPUT_DIR}/products_canonical.csv", index=False)

    # ── 3. Overdue ratio scoring ──────────────────────────────────────────────
    print("Scoring Current Risk (Overdue Ratio)...")

    last_purchases = (
        order_items.groupby(["user_id", "product_id"])["order_ts"]
        .max()
        .reset_index()
    )
    last_purchases.rename(
        columns={"order_ts": "last_product_order_ts"}, inplace=True
    )
    last_purchases["last_purchase_days_ago"] = (
        ANCHOR_DATE - last_purchases["last_product_order_ts"]
    ).dt.days

    last_purchases = pd.merge(
        last_purchases,
        products_curr[["product_id", "avg_replenishment_days", "mock_inventory"]],
        on="product_id",
    )

    # Guard against avg_replenishment_days == 0 → would produce inf ratio
    last_purchases["overdue_ratio"] = last_purchases.apply(
        lambda row: _safe_overdue_ratio(
            row["last_purchase_days_ago"], row["avg_replenishment_days"]
        ),
        axis=1,
    )

    risk_df = pd.merge(
        last_purchases,
        customers[["user_id", "R_score", "F_score", "M_score", "rfm_segment"]],
        on="user_id",
    )
    risk_df["risk_id"] = (
        f"{RUN_ID}:"
        + risk_df["user_id"].astype(str)
        + ":"
        + risk_df["product_id"].astype(str)
    )

    at_risk_candidates = risk_df[
        (risk_df["overdue_ratio"] > 1.2) & (risk_df["mock_inventory"] > 50)
    ].copy()
    at_risk_candidates["overdue_ratio"] = at_risk_candidates[
        "overdue_ratio"
    ].round(2)
    at_risk_candidates = at_risk_candidates.sort_values(
        by="overdue_ratio", ascending=False
    )

    # ── Diagnostics ───────────────────────────────────────────────────────────
    print("\n=============================================")
    print("Replenishment Cycle Mapping Summary")
    print("=============================================")
    source_dist = (
        replenishment_map["replenishment_source"]
        .value_counts(normalize=True)
        .mul(100)
        .round(1)
        .astype(str)
        + "%"
    )
    print(source_dist.to_string())

    print(
        f"\nDiscovered {len(at_risk_candidates)} Actionable At-Risk Candidates"
        f" (overdue_ratio > 1.2, inventory > 50)"
    )

    print("\n--- TOP 10 OVERDUE CANDIDATES (User × Product) ---")
    display_cols = [
        "user_id", "product_id", "rfm_segment",
        "last_purchase_days_ago", "avg_replenishment_days",
        "overdue_ratio", "mock_inventory",
    ]
    print(at_risk_candidates.head(10)[display_cols].to_string(index=False))

    at_risk_candidates.to_csv(
        f"{OUTPUT_DIR}/at_risk_candidates_v0.csv", index=False
    )


if __name__ == "__main__":
    compute_replenishment_and_risk()
