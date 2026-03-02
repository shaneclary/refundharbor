import sys
sys.path.insert(0, r"c:\shaneclary\Polytrade")
from db import get_conn

with get_conn() as conn:
    row = conn.execute("SELECT balance_usdc FROM paper_account WHERE id=1").fetchone()
    print(f"Paper balance: ${row['balance_usdc']:.2f}")

    rows = conn.execute("SELECT * FROM fund_accounts").fetchall()
    for r in rows:
        print(f"  Fund {r['fund_id']}: ${r['balance_usdc']:.2f}")

    r2 = conn.execute("SELECT MIN(timestamp) as f, MAX(timestamp) as l, COUNT(*) as cnt FROM trade_history").fetchone()
    print(f"Trades: {r2['cnt']}  |  {r2['f']}  to  {r2['l']}")
