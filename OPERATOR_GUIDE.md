# DenseWealth Operator Guide

**Dashboard:** http://89.167.68.109:8050

---

## 1. Login

1. Go to **http://89.167.68.109:8050/login**
2. Enter your credentials:
   - **Username:** Brad
   - **Password:** s&aJTGjINBX&JcmM
3. Click **Login**

---

## 2. Switch from Paper to Live Trading

> **IMPORTANT:** This will execute REAL trades with REAL money.

1. Look at the top-right of the dashboard
2. Find the **Paper / Live** toggle switch
3. Click the toggle to switch to **Live**
4. A confirmation popup will appear - click **Confirm**
5. The badge will change from "PAPER MODE" to "LIVE GLOBAL"

**To switch back to Paper:** Click the toggle again (no confirmation needed)

---

## 3. Enable Auto-Trade

When Auto-Trade is ON, the bot executes trades immediately without approval.
When OFF (Manual mode), you must approve each trade.

1. Find the **Manual / Auto** toggle at the top
2. Click to switch to **Auto**
3. The toggle will turn blue when Auto is enabled

---

## 4. Dashboard Overview

### Top Bar
| Element | Description |
|---------|-------------|
| **PAPER/LIVE** | Current trading mode (amber = paper, green = live) |
| **Manual/Auto** | Trade approval mode |
| **Realloc** | Redistributes inactive trader budgets (purple = on) |

### Main Sections
- **Balance** - Current USDC balance and P&L
- **Tracked Wallets** - Traders being copied and their budget usage
- **Open Positions** - Active trades
- **Trade Feed** - Real-time trade activity
- **Pending Trades** - Trades awaiting approval (Manual mode only)

---

## 5. Manual Trade Approval

When in **Manual** mode:

1. New trades appear in the **Pending Trades** section
2. Click **Approve** to execute the trade
3. Click **Reject** to skip the trade
4. Use **Approve All** to approve everything at once

Trades expire after 10 minutes if not decided.

---

## 6. Quick Reference

| Action | How To |
|--------|--------|
| Go Live | Toggle Paper → Live, confirm popup |
| Go Paper | Toggle Live → Paper |
| Auto-execute trades | Toggle Manual → Auto |
| Require approval | Toggle Auto → Manual |
| View logs | Scroll down to Trade Feed |
| Check balance | Top-left card |
| Logout | Click **Logout** (top-right) |

---

## 7. Emergency: Stop All Trading

If something goes wrong:

1. **Toggle to Paper mode** - This stops all real trades immediately
2. Or toggle to **Manual mode** - Trades will queue instead of executing

---

## 8. Troubleshooting

**Can't login?**
- Check username/password (case-sensitive)
- Clear browser cookies and try again

**Dashboard not loading?**
- Contact Shane to restart the dashboard

**Trades not executing?**
- Check if mode is Paper (won't execute real trades)
- Check if mode is Manual (trades waiting for approval)

---

## Contact

**Shane (Viewer account):** Has read-only access to monitor
**Technical issues:** Contact Shane to SSH into server

---

*Last updated: March 2, 2026*
