import sys
sys.path.insert(0, r"c:\shaneclary\Polytrade")
from db import get_pnl_by_period

# All time
data = get_pnl_by_period(None)
print("ALL TIME:")
for k, v in data.items():
    print(f"  {k}: {v}")

# Last 1 hour
data2 = get_pnl_by_period(60)
print("\nLAST 1 HOUR:")
for k, v in data2.items():
    print(f"  {k}: {v}")

# Last 24 hours
data3 = get_pnl_by_period(1440)
print("\nLAST 24 HOURS:")
for k, v in data3.items():
    print(f"  {k}: {v}")
