import pandas as pd
import hashlib
from datetime import datetime, timedelta

# 1. Configuration & Constants
DATA_DIR = "./DATASETS"
OUTPUT_DIR = "./OUTPUT"
ANCHOR_DATE = datetime(2023, 12, 31)  # Fixed as_of_date for backtesting/mocking

def stable_hash(val: str) -> int:
    """Deterministic hash for mock generation."""
    return int(hashlib.md5(str(val).encode('utf-8')).hexdigest()[:8], 16)

def load_and_map_data():
    print("Loading datasets...")
    # Load raw data
    orders_raw = pd.read_csv(f"{DATA_DIR}/orders.csv")
    prior_raw = pd.read_csv(f"{DATA_DIR}/order_products__prior.csv")
    products_raw = pd.read_csv(f"{DATA_DIR}/products.csv")

    print("Mapping to Canonical Tables...")

    # --- 1. Products Table ---
    # canonical: product_id, product_name, department_id, aisle_id, mock_inventory, avg_replenishment_days
    products_curr = products_raw[['product_id', 'product_name', 'department_id', 'aisle_id']].copy()
    products_curr['mock_inventory'] = products_curr['product_id'].apply(lambda x: stable_hash(x) % 501)
    
    # Placeholder for avg_replenishment_days (will be computed later, set to NA for now)
    products_curr['avg_replenishment_days'] = None 
    
    # --- 2. Orders Table (Synthesizing order_ts) ---
    # canonical: order_id, user_id, order_number, days_since_prior_order, order_ts
    orders_curr = orders_raw[['order_id', 'user_id', 'order_number', 'days_since_prior_order']].copy()
    orders_curr['days_since_prior_order'] = orders_curr['days_since_prior_order'].fillna(0)

    # To synthesize order_ts retrospectively from ANCHOR_DATE:
    # 1. Sort by user_id and order_number (descending)
    orders_curr = orders_curr.sort_values(by=['user_id', 'order_number'], ascending=[True, False])
    
    # 2. Cumulative sum of days_since_prior_order going BACKWARDS from the last order
    # (The last order for a user is '0' days ago relative to their own timeline)
    # Notice: the Instacart dataset's days_since_prior_order is the gap *before* that specific order.
    # To map backwards, the shift logic is: 
    #   timestamp of order N = timestamp of order (N+1) - (days_since_prior_order of order N+1)
    orders_curr['days_to_subtract'] = orders_curr.groupby('user_id')['days_since_prior_order'].shift(1).fillna(0)
    orders_curr['cum_days_back'] = orders_curr.groupby('user_id')['days_to_subtract'].cumsum()
    
    # 3. Apply the time shift relative to the universal ANCHOR_DATE
    # Note: this makes the *latest* order for every user land exactly on ANCHOR_DATE. 
    # This ensures R (Recency) is roughly 0 for active users, which is fine for v0 mock data.
    orders_curr['order_ts'] = ANCHOR_DATE - pd.to_timedelta(orders_curr['cum_days_back'], unit='d')
    
    orders_canonical = orders_curr[['order_id', 'user_id', 'order_number', 'days_since_prior_order', 'order_ts']]

    # --- 3. Order Items Table ---
    # canonical: order_id, product_id, qty, reordered_flag
    order_items_canonical = prior_raw[['order_id', 'product_id', 'reordered']].copy()
    order_items_canonical.rename(columns={'reordered': 'reordered_flag'}, inplace=True)
    order_items_canonical['qty'] = 1 # Hardcoded for Instacart format
    
    # --- 4. Customers Table (RFM Aggregation) ---
    # canonical: user_id, total_orders, m_proxy_value, rfm_segment, as_of_date
    print("Computing RFM Segments...")
    
    # Frequency: total_orders
    freq_df = orders_canonical.groupby('user_id')['order_number'].max().reset_index(name='total_orders')
    
    # Recency: days since last order_ts relative to ANCHOR_DATE
    # (Since we anchored the max order_ts to ANCHOR_DATE, R will be exactly 0 for all users in this synthetic setup.
    #  To make R realistic, let's randomly but deterministically offset each user's max date by 0-60 days).
    def user_offset(uid):
        return timedelta(days=(stable_hash(uid) % 60))
        
    orders_canonical['order_ts'] = orders_canonical.apply(lambda row: row['order_ts'] - user_offset(row['user_id']), axis=1)
    
    recency_df = orders_canonical.groupby('user_id')['order_ts'].max().reset_index(name='max_order_ts')
    recency_df['recency_days'] = (ANCHOR_DATE - recency_df['max_order_ts']).dt.days

    # Monetary: m_proxy_value (total items bought across all prior orders)
    # Join items to orders to get user mapping
    items_with_user = pd.merge(order_items_canonical, orders_canonical[['order_id', 'user_id']], on='order_id')
    monetary_df = items_with_user.groupby('user_id')['qty'].sum().reset_index(name='m_proxy_value')
    
    # Combine RFM
    customers_canonical = pd.merge(freq_df, recency_df[['user_id', 'recency_days']], on='user_id')
    customers_canonical = pd.merge(customers_canonical, monetary_df, on='user_id', how='left')
    customers_canonical['m_proxy_value'] = customers_canonical['m_proxy_value'].fillna(0)
    customers_canonical['as_of_date'] = ANCHOR_DATE
    
    # R, F, M Quantile Scoring (1-5)
    # R: 5 is best (lowest recency days)
    # F: 5 is best (highest frequency)
    # M: 5 is best (highest monetary proxy)
    
    # We use qcut for quantiles. Handling duplicates requires drop-duplicates for binds.
    customers_canonical['R_score'] = pd.qcut(customers_canonical['recency_days'], 5, labels=[5, 4, 3, 2, 1], duplicates='drop')
    customers_canonical['F_score'] = pd.qcut(customers_canonical['total_orders'].rank(method='first'), 5, labels=[1, 2, 3, 4, 5])
    customers_canonical['M_score'] = pd.qcut(customers_canonical['m_proxy_value'].rank(method='first'), 5, labels=[1, 2, 3, 4, 5])
    
    # Convert scores to int
    customers_canonical['R_score'] = customers_canonical['R_score'].astype(int)
    customers_canonical['F_score'] = customers_canonical['F_score'].astype(int)
    customers_canonical['M_score'] = customers_canonical['M_score'].astype(int)
    
    # Create RFM aggregate score string
    customers_canonical['RFM_Cell'] = customers_canonical['R_score'].astype(str) + customers_canonical['F_score'].astype(str) + customers_canonical['M_score'].astype(str)
    
    # Define Segment Rules (Deterministic)
    def assign_segment(row):
        r, f, m = row['R_score'], row['F_score'], row['M_score']
        
        # High value = F or M >= 4
        # At-Risk = R <= 2
        # Champions/Loyal = R>=4, F>=4, M>=4
        # New = R>=4, F<=2
        # Low Value = R<=3, F<=2, M<=2
        
        if r >= 4 and f >= 4 and m >= 4:
            return 'Champion / Loyal'
        elif r <= 2 and (f >= 4 or m >= 4):
            return 'High-Value At-Risk'
        elif r >= 4 and f <= 2:
            return 'New / Promising'
        elif r <= 2 and f <= 2 and m <= 2:
            return 'Low-Value / Lost'
        elif r == 3 and (f >= 3 or m >= 3):
            return 'Need Attention'
        else:
            return 'Average Purchaser'
            
    customers_canonical['rfm_segment'] = customers_canonical.apply(assign_segment, axis=1)
    
    print("\n--- Segment Definitions (Rules) ---")
    print("1. Champion / Loyal: R >= 4 AND F >= 4 AND M >= 4")
    print("2. High-Value At-Risk: R <= 2 AND (F >= 4 OR M >= 4)")
    print("3. New / Promising: R >= 4 AND F <= 2")
    print("4. Low-Value / Lost: R <= 2 AND F <= 2 AND M <= 2")
    print("5. Need Attention: R == 3 AND (F >= 3 OR M >= 3)")
    print("6. Average Purchaser: All others")
    
    print("\n--- Segment Distribution Summary ---")
    dist = customers_canonical['rfm_segment'].value_counts(normalize=True).mul(100).round(1).astype(str) + "%"
    print(dist.to_string())
    
    print("\n--- Canonical Tables Sample ---")
    print("\nCustomers Head:")
    print(customers_canonical[['user_id', 'total_orders', 'm_proxy_value', 'recency_days', 'R_score', 'F_score', 'M_score', 'rfm_segment']].head())
    
    # Save outputs
    import os
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    customers_canonical.to_csv(f"{OUTPUT_DIR}/customers_canonical_full.csv", index=False)
    orders_canonical.head(1000).to_csv(f"{OUTPUT_DIR}/orders_canonical_sample.csv", index=False)
    
    return customers_canonical, orders_canonical, products_curr, order_items_canonical

if __name__ == "__main__":
    load_and_map_data()
