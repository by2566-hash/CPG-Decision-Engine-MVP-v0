import pandas as pd
from datetime import datetime

# 1. Configuration & Constants
OUTPUT_DIR = "./OUTPUT"
DATA_DIR = "./DATASETS"
ANCHOR_DATE = datetime(2023, 12, 31)  # Matching compute_rfm.py
RUN_ID = "run_v0_001"
MIN_SAMPLES_FOR_ROBUST_STAT = 5  # minimum gaps required to use empirical product average

def compute_replenishment_and_risk():
    print("Loading Canonical Tables...")
    customers = pd.read_csv(f"{OUTPUT_DIR}/customers_canonical_full.csv")
    orders = pd.read_csv(f"{OUTPUT_DIR}/orders_canonical_sample.csv") # Using the 1000 order sample for v0 execution speed
    
    print("Loading Raw Products and Items...")
    prior_raw = pd.read_csv(f"{DATA_DIR}/order_products__prior.csv")
    products_raw = pd.read_csv(f"{DATA_DIR}/products.csv")
    
    # Generate canonical products.mock_inventory for our constraint tests later
    import hashlib
    def stable_hash(val):
        return int(hashlib.md5(str(val).encode('utf-8')).hexdigest()[:8], 16)
    products_curr = products_raw[['product_id', 'product_name', 'department_id', 'aisle_id']].copy()
    products_curr['mock_inventory'] = products_curr['product_id'].apply(lambda x: stable_hash(x) % 501)
    
    # Join items to orders to get timestamps
    order_items = pd.merge(prior_raw, orders[['order_id', 'user_id', 'order_ts']], on='order_id')
    order_items['order_ts'] = pd.to_datetime(order_items['order_ts'])
    
    # Join to get category hierarchies
    order_items = pd.merge(order_items, products_curr[['product_id', 'aisle_id', 'department_id']], on='product_id')

    # --- 1) Build User-Product Sequences & Compute Gaps ---
    print("Computing Interpurchase Gaps...")
    
    # Sort chronological
    sorted_history = order_items.sort_values(by=['user_id', 'product_id', 'order_ts'])
    
    # Calculate difference between consecutive purchases of the EXACT SAME product by the SAME user
    sorted_history['prev_order_ts'] = sorted_history.groupby(['user_id', 'product_id'])['order_ts'].shift(1)
    sorted_history['purchase_gap_days'] = (sorted_history['order_ts'] - sorted_history['prev_order_ts']).dt.days
    
    valid_gaps = sorted_history.dropna(subset=['purchase_gap_days']).copy()
    valid_gaps = valid_gaps[valid_gaps['purchase_gap_days'] > 0] # Filter same-day multi-records if any

    # --- 2) Calculate Robust Averages (Trimmed Mean / Median) ---
    print("Aggregating Replenishment Cycles with Fallbacks...")
    
    # Product Level
    prod_stats = valid_gaps.groupby('product_id')['purchase_gap_days'].agg(
        prod_median='median',
        prod_count='count'
    ).reset_index()
    
    # Aisle Level Fallback
    aisle_stats = valid_gaps.groupby('aisle_id')['purchase_gap_days'].agg(
        aisle_median='median',
        aisle_count='count'
    ).reset_index()
    
    # Dept Level Fallback
    dept_stats = valid_gaps.groupby('department_id')['purchase_gap_days'].agg(
        dept_median='median'
    ).reset_index()

    # Create the lookup mapping starting from raw products
    replenishment_map = pd.merge(products_curr, prod_stats, on='product_id', how='left')
    replenishment_map = pd.merge(replenishment_map, aisle_stats, on='aisle_id', how='left')
    replenishment_map = pd.merge(replenishment_map, dept_stats, on='department_id', how='left')
    
    # Apply Fallback Logic
    def get_best_cycle(row):
        # 1. Product level if dense enough
        if pd.notna(row['prod_count']) and row['prod_count'] >= MIN_SAMPLES_FOR_ROBUST_STAT:
            return row['prod_median'], "Product"
        # 2. Aisle level if dense enough
        elif pd.notna(row['aisle_count']) and row['aisle_count'] >= MIN_SAMPLES_FOR_ROBUST_STAT:
            return row['aisle_median'], "Aisle"
        # 3. Dept level
        elif pd.notna(row['dept_median']):
            return row['dept_median'], "Department"
        else:
            return 30.0, "Global_Default" # Ultimate fallback

    applied_logic = replenishment_map.apply(get_best_cycle, axis=1, result_type='expand')
    replenishment_map['avg_replenishment_days'] = applied_logic[0].astype(float)
    replenishment_map['replenishment_source'] = applied_logic[1]
    
    # Save the canonical avg_replenishment_days back to product representation
    products_curr['avg_replenishment_days'] = replenishment_map['avg_replenishment_days']
    products_curr.to_csv(f"{OUTPUT_DIR}/products_canonical.csv", index=False)
    
    # --- 3) Calculate Overdue Ratios ---
    print("Scoring Current Risk (Overdue Ratio)...")
    
    # Get last purchase date per user per product
    last_purchases = order_items.groupby(['user_id', 'product_id'])['order_ts'].max().reset_index()
    last_purchases.rename(columns={'order_ts': 'last_product_order_ts'}, inplace=True)
    last_purchases['last_purchase_days_ago'] = (ANCHOR_DATE - last_purchases['last_product_order_ts']).dt.days
    
    # Join in the actual replenishment days and inventory
    last_purchases = pd.merge(last_purchases, products_curr[['product_id', 'avg_replenishment_days', 'mock_inventory']], on='product_id')
    last_purchases['overdue_ratio'] = last_purchases['last_purchase_days_ago'] / last_purchases['avg_replenishment_days']
    
    # Join in RFM
    risk_df = pd.merge(last_purchases, customers[['user_id', 'R_score', 'F_score', 'M_score', 'rfm_segment']], on='user_id')
    risk_df['risk_id'] = f"{RUN_ID}:" + risk_df['user_id'].astype(str) + ":" + risk_df['product_id'].astype(str)
    
    # Note: We skip the heavy ML bounds and stick to simple ratio filtering as requested.
    
    # Filter At-Risk Candidates: overdue_ratio > 1.2 AND mock_inventory > 50
    at_risk_candidates = risk_df[(risk_df['overdue_ratio'] > 1.2) & (risk_df['mock_inventory'] > 50)].copy()
    at_risk_candidates['overdue_ratio'] = at_risk_candidates['overdue_ratio'].round(2)
    at_risk_candidates = at_risk_candidates.sort_values(by='overdue_ratio', ascending=False)
    
    print("\n=============================================")
    print("Replenishment Cycle Mapping Summary")
    print("=============================================")
    source_dist = replenishment_map['replenishment_source'].value_counts(normalize=True).mul(100).round(1).astype(str) + "%"
    print(source_dist.to_string())
    
    print(f"\nDiscovered {len(at_risk_candidates)} Actionable At-Risk Candidates (Overdue > 1.2, Inventory > 50)")
    
    print("\n--- TOP 10 OVERDUE CANDIDATES (User x Product) ---")
    display_cols = ['user_id', 'product_id', 'rfm_segment', 'last_purchase_days_ago', 'avg_replenishment_days', 'overdue_ratio', 'mock_inventory']
    print(at_risk_candidates.head(10)[display_cols].to_string(index=False))
    
    # Save the targets
    at_risk_candidates.to_csv(f"{OUTPUT_DIR}/at_risk_candidates_v0.csv", index=False)

if __name__ == "__main__":
    compute_replenishment_and_risk()
