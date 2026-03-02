import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = r'c:\shaneclary\Polytrade\densewealth.db'
WALLET_LABELS = {
    '0x1979ae6b7e6534de9c4539d0c205e582ca637c9d': 'Square-Guy',
    '0x1d0034134e339a309700ff2d34e99fa2d48b0313': 'Canine-Commandment',
    '0x2d8b401d2f0e6937afebf18e19e11ca568a5260a': 'vidarx',
}

def label(wallet):
    w = wallet.lower()
    return WALLET_LABELS.get(w, w[:10] + '...')

def fmt_usd(val):
    return f'${val:,.2f}'

def fmt_pct(val):
    return f'{val:.1f}%'

def divider(title):
    line = '=' * 70
    print(f'\n{line}')
    print(f'  {title}')
    print(f'{line}')

def sub_divider(title):
    print(f'\n  --- {title} ---')

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

all_trades = cur.execute('SELECT * FROM trade_history WHERE success = 1 ORDER BY timestamp').fetchall()
resolves = [t for t in all_trades if t['side'] == 'RESOLVE']
buys = [t for t in all_trades if t['side'] == 'BUY']
sells = [t for t in all_trades if t['side'] == 'SELL']
paper = cur.execute('SELECT * FROM paper_account LIMIT 1').fetchone()
positions = cur.execute('SELECT * FROM positions').fetchall()
allocations = cur.execute('SELECT * FROM allocations ORDER BY created_at').fetchall()

line70 = '=' * 70
print('\n' + line70)
print('       DENSEWEALTH STRATEGY ANALYSIS REPORT')
now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
print(f'       Generated: {now_str}')
print(line70)

divider('ACCOUNT OVERVIEW')
balance = paper['balance_usdc']
total_pnl = paper['total_pnl']
total_trades_count = paper['total_trades']
starting_balance = balance - total_pnl
print(f'  Current Balance:     {fmt_usd(balance)}')
print(f'  Total P&L:           {fmt_usd(total_pnl)}')
print(f'  Est. Starting Cap:   {fmt_usd(starting_balance)}')
roc = fmt_pct((total_pnl / starting_balance) * 100) if starting_balance > 0 else 'N/A'
print(f'  Return on Capital:   {roc}')
print(f'  Total Trades:        {total_trades_count}')
print(f'    BUY trades:        {len(buys)}')
print(f'    SELL trades:       {len(sells)}')
print(f'    RESOLVE trades:    {len(resolves)}')

divider('RESOLVE TRADE ANALYSIS (Win/Loss)')
profit_factor = 0
if resolves:
    wins = [t for t in resolves if t['price'] == 1.0]
    losses = [t for t in resolves if t['price'] == 0.0]
    ambiguous = [t for t in resolves if t['price'] not in (0.0, 1.0)]
    win_count = len(wins)
    loss_count = len(losses)
    total_resolved = win_count + loss_count
    win_rate = (win_count / total_resolved * 100) if total_resolved > 0 else 0
    print(f'  Total Resolutions:   {len(resolves)}')
    print(f'  Wins (price=1.0):    {win_count}')
    print(f'  Losses (price=0.0):  {loss_count}')
    if ambiguous:
        print(f'  Ambiguous:           {len(ambiguous)} (price neither 0 nor 1)')
    print(f'  Overall Win Rate:    {fmt_pct(win_rate)}')
    sub_divider('Profit / Loss Breakdown')
    buy_cost_by_market = defaultdict(float)
    buy_shares_by_market = defaultdict(float)
    for b in buys:
        buy_cost_by_market[b['market_id']] += b['usdc_amount']
        buy_shares_by_market[b['market_id']] += b['shares']
    total_win_profit = 0
    total_loss_amount = 0
    win_profits = []
    loss_amounts = []
    resolve_by_market = defaultdict(list)
    for r in resolves:
        resolve_by_market[r['market_id']].append(r)
    for market_id, res_list in resolve_by_market.items():
        cost = buy_cost_by_market.get(market_id, 0)
        total_shares_resolved = sum(r['shares'] for r in res_list)
        outcome_price = res_list[0]['price']
        if outcome_price == 1.0:
            payout = total_shares_resolved
            profit = payout - cost
            total_win_profit += profit
            win_profits.append(profit)
        elif outcome_price == 0.0:
            total_loss_amount += cost
            loss_amounts.append(cost)
    avg_win = (total_win_profit / len(win_profits)) if win_profits else 0
    avg_loss = (total_loss_amount / len(loss_amounts)) if loss_amounts else 0
    profit_factor = (total_win_profit / total_loss_amount) if total_loss_amount > 0 else float('inf')
    print(f'  Gross Win Profit:    {fmt_usd(total_win_profit)}')
    print(f'  Gross Loss Amount:   {fmt_usd(total_loss_amount)}')
    net = total_win_profit - total_loss_amount
    print(f'  Net P&L (resolves):  {fmt_usd(net)}')
    print(f'  Avg Profit/Win Mkt:  {fmt_usd(avg_win)}')
    print(f'  Avg Loss/Loss Mkt:   {fmt_usd(avg_loss)}')
    print(f'  Profit Factor:       {profit_factor:.2f}x')
    sub_divider('Win Rate by Trader Wallet')
    wallet_wins = defaultdict(int)
    wallet_losses = defaultdict(int)
    for t in wins:
        wallet_wins[t['trader_wallet']] += 1
    for t in losses:
        wallet_losses[t['trader_wallet']] += 1
    all_wallets = set(list(wallet_wins.keys()) + list(wallet_losses.keys()))
    for w in sorted(all_wallets):
        ww = wallet_wins[w]
        wl = wallet_losses[w]
        total_w = ww + wl
        wr = (ww / total_w * 100) if total_w > 0 else 0
        print(f'  {label(w):25s}  W:{ww:3d}  L:{wl:3d}  Total:{total_w:3d}  WR:{fmt_pct(wr)}')
    sub_divider('Win/Loss Streak Analysis')
    resolve_outcomes = []
    for r in sorted(resolves, key=lambda x: x['timestamp']):
        if r['price'] == 1.0:
            resolve_outcomes.append('W')
        elif r['price'] == 0.0:
            resolve_outcomes.append('L')
    if resolve_outcomes:
        max_win_streak = 0
        max_loss_streak = 0
        current_streak = 1
        for i in range(1, len(resolve_outcomes)):
            if resolve_outcomes[i] == resolve_outcomes[i-1]:
                current_streak += 1
            else:
                if resolve_outcomes[i-1] == 'W':
                    max_win_streak = max(max_win_streak, current_streak)
                else:
                    max_loss_streak = max(max_loss_streak, current_streak)
                current_streak = 1
        if resolve_outcomes[-1] == 'W':
            max_win_streak = max(max_win_streak, current_streak)
        else:
            max_loss_streak = max(max_loss_streak, current_streak)
        current_type = resolve_outcomes[-1]
        current_len = 1
        for i in range(len(resolve_outcomes) - 2, -1, -1):
            if resolve_outcomes[i] == current_type:
                current_len += 1
            else:
                break
        print(f'  Max Win Streak:      {max_win_streak}')
        print(f'  Max Loss Streak:     {max_loss_streak}')
        streak_lbl = 'WIN' if current_type == 'W' else 'LOSS'
        print(f'  Current Streak:      {current_len} {streak_lbl}(s)')
        streaks_w = []
        streaks_l = []
        s_len = 1
        for i in range(1, len(resolve_outcomes)):
            if resolve_outcomes[i] == resolve_outcomes[i-1]:
                s_len += 1
            else:
                if resolve_outcomes[i-1] == 'W':
                    streaks_w.append(s_len)
                else:
                    streaks_l.append(s_len)
                s_len = 1
        if resolve_outcomes[-1] == 'W':
            streaks_w.append(s_len)
        else:
            streaks_l.append(s_len)
        avg_w_streak = sum(streaks_w) / len(streaks_w) if streaks_w else 0
        avg_l_streak = sum(streaks_l) / len(streaks_l) if streaks_l else 0
        print(f'  Avg Win Streak:      {avg_w_streak:.1f}')
        print(f'  Avg Loss Streak:     {avg_l_streak:.1f}')
else:
    print('  No RESOLVE trades found.')

divider('BUY TRADE ANALYSIS')
if buys:
    usdc_amounts = [b['usdc_amount'] for b in buys]
    total_usdc_deployed = sum(usdc_amounts)
    avg_trade_size = total_usdc_deployed / len(buys)
    sorted_amounts = sorted(usdc_amounts)
    median_trade_size = sorted_amounts[len(sorted_amounts) // 2]
    print(f'  Total BUY Trades:    {len(buys)}')
    print(f'  Total USDC Deployed: {fmt_usd(total_usdc_deployed)}')
    print(f'  Current Balance:     {fmt_usd(balance)}')
    print(f'  Avg Trade Size:      {fmt_usd(avg_trade_size)}')
    print(f'  Median Trade Size:   {fmt_usd(median_trade_size)}')
    print(f'  Min Trade Size:      {fmt_usd(min(usdc_amounts))}')
    print(f'  Max Trade Size:      {fmt_usd(max(usdc_amounts))}')
    sub_divider('Trade Size Distribution')
    buckets = [('<$5', 0, 5), ('$5-$25', 5, 25), ('$25-$100', 25, 100), ('$100-$500', 100, 500), ('$500+', 500, float('inf'))]
    bucket_counts = {}
    bucket_volume = {}
    for bname, lo, hi in buckets:
        bucket_counts[bname] = 0
        bucket_volume[bname] = 0.0
    for amt in usdc_amounts:
        for bname, lo, hi in buckets:
            if lo <= amt < hi:
                bucket_counts[bname] += 1
                bucket_volume[bname] += amt
                break
    dash53 = '-' * 53
    hdr = f'  {"Bucket":15s} {"Count":>8s} {"Pct":>8s} {"Volume":>14s} {"Vol%":>8s}'
    print(hdr)
    print(f'  {dash53}')
    for bname, lo, hi in buckets:
        cnt = bucket_counts[bname]
        pct = cnt / len(buys) * 100 if len(buys) > 0 else 0
        vol = bucket_volume[bname]
        vol_pct = vol / total_usdc_deployed * 100 if total_usdc_deployed > 0 else 0
        bar = '#' * int(pct / 2)
        print(f'  {bname:15s} {cnt:8d} {fmt_pct(pct):>8s} {fmt_usd(vol):>14s} {fmt_pct(vol_pct):>8s}  {bar}')
    sub_divider('Trades per Wallet (BUY)')
    wallet_buys = defaultdict(lambda: {'count': 0, 'volume': 0.0})
    for b in buys:
        wallet_buys[b['trader_wallet']]['count'] += 1
        wallet_buys[b['trader_wallet']]['volume'] += b['usdc_amount']
    for w in sorted(wallet_buys.keys()):
        d = wallet_buys[w]
        avg = d['volume'] / d['count']
        print(f'  {label(w):25s}  Trades:{d["count"]:4d}  Volume:{fmt_usd(d["volume"]):>12s}  Avg:{fmt_usd(avg):>10s}')
else:
    print('  No BUY trades found.')

divider('POSITION CONCENTRATION (Open Positions)')
if positions:
    print(f'  Open Positions:      {len(positions)}')
    print(f'  Current Balance:     {fmt_usd(balance)}')
    print()
    total_exposure = 0
    dash83 = '-' * 83
    hdr2 = f'  {"Wallet":25s} {"USDC Spent":>12s} {"Shares":>12s} {"% of Bal":>10s} {"Market (short)":20s}'
    print(hdr2)
    print(f'  {dash83}')
    for p in positions:
        spent = p['usdc_spent']
        total_exposure += spent
        pct = (spent / balance * 100) if balance > 0 else 0
        market_short = p['market_id'][:16] + '...'
        wlbl = label(p['trader_wallet'])
        print(f'  {wlbl:25s} {fmt_usd(spent):>12s} {p["shares"]:>12.2f} {fmt_pct(pct):>10s} {market_short:20s}')
    print(f'\n  Total Exposure:      {fmt_usd(total_exposure)}')
    exp_pct = fmt_pct(total_exposure / balance * 100) if balance > 0 else 'N/A'
    print(f'  Exposure % of Bal:   {exp_pct}')
else:
    print('  No open positions.')

divider('PROPORTIONAL SIZING ANALYSIS')
trade_pcts = []
avg_pct = 0
if buys:
    events = list(all_trades)
    events.sort(key=lambda x: x['timestamp'])
    running_balance = starting_balance
    cap_5pct_hits = 0
    for e in events:
        if e['side'] == 'BUY':
            tp = (e['usdc_amount'] / running_balance * 100) if running_balance > 0 else 0
            trade_pcts.append(tp)
            if tp >= 4.8:
                cap_5pct_hits += 1
            running_balance -= e['usdc_amount']
        elif e['side'] == 'SELL':
            running_balance += e['usdc_amount']
        elif e['side'] == 'RESOLVE':
            if e['price'] == 1.0:
                running_balance += e['shares']
    if trade_pcts:
        avg_pct = sum(trade_pcts) / len(trade_pcts)
        max_pct = max(trade_pcts)
        min_pct = min(trade_pcts)
        sorted_pcts = sorted(trade_pcts)
        median_pct = sorted_pcts[len(sorted_pcts) // 2]
        print(f'  Avg Trade as % of Bal:     {fmt_pct(avg_pct)}')
        print(f'  Median Trade as % of Bal:  {fmt_pct(median_pct)}')
        print(f'  Min Trade as % of Bal:     {fmt_pct(min_pct)}')
        print(f'  Max Trade as % of Bal:     {fmt_pct(max_pct)}')
        cap_pct_s = fmt_pct(cap_5pct_hits / len(trade_pcts) * 100)
        print(f'  5% Cap Near-Hits (>=4.8%): {cap_5pct_hits} / {len(trade_pcts)} ({cap_pct_s})')
        pct_bucket_names = ['<1%', '1-2%', '2-3%', '3-4%', '4-5%', '>=5%']
        pbc = {n: 0 for n in pct_bucket_names}
        for p in trade_pcts:
            if p < 1: pbc['<1%'] += 1
            elif p < 2: pbc['1-2%'] += 1
            elif p < 3: pbc['2-3%'] += 1
            elif p < 4: pbc['3-4%'] += 1
            elif p < 5: pbc['4-5%'] += 1
            else: pbc['>=5%'] += 1
        print(f'\n  Trade Size % Distribution:')
        for bn in pct_bucket_names:
            cnt = pbc[bn]
            pt = cnt / len(trade_pcts) * 100
            bar = '#' * int(pt / 2)
            print(f'    {bn:8s} {cnt:5d} ({fmt_pct(pt):>6s})  {bar}')
    sub_divider('30% Wallet Concentration Cap')
    wallet_exposure = defaultdict(float)
    for p in positions:
        wallet_exposure[p['trader_wallet']] += p['usdc_spent']
    for w in sorted(wallet_exposure.keys()):
        exp = wallet_exposure[w]
        pct = (exp / balance * 100) if balance > 0 else 0
        status = ' ** NEAR/OVER 30% CAP **' if pct >= 28 else ''
        print(f'  {label(w):25s}  Exposure: {fmt_usd(exp):>12s}  ({fmt_pct(pct)} of balance){status}')
else:
    print('  No BUY trades to analyze.')

divider('TIME-BASED ANALYSIS')
buy_markets = set(b['market_id'] for b in buys)
resolve_markets = set(r['market_id'] for r in resolves)
resolved_buy_markets = buy_markets & resolve_markets
unresolved_markets = buy_markets - resolve_markets
print(f'  Unique Markets Bought:     {len(buy_markets)}')
print(f'  Markets Resolved:          {len(resolved_buy_markets)}')
print(f'  Markets Still Open:        {len(unresolved_markets)}')
rr = len(resolved_buy_markets) / len(buy_markets) * 100 if buy_markets else 0
print(f'  Resolution Rate:           {fmt_pct(rr)}')

sub_divider('Hold Time Analysis')
first_buy_time = {}
for b in sorted(buys, key=lambda x: x['timestamp']):
    mid = b['market_id']
    if mid not in first_buy_time:
        first_buy_time[mid] = b['timestamp']
resolve_time = {}
for r in sorted(resolves, key=lambda x: x['timestamp']):
    mid = r['market_id']
    if mid not in resolve_time:
        resolve_time[mid] = r['timestamp']
hold_times = []
for mid in resolved_buy_markets:
    if mid in first_buy_time and mid in resolve_time:
        try:
            buy_dt = datetime.strptime(first_buy_time[mid], '%Y-%m-%d %H:%M:%S')
            res_dt = datetime.strptime(resolve_time[mid], '%Y-%m-%d %H:%M:%S')
            hh = (res_dt - buy_dt).total_seconds() / 3600
            if hh >= 0:
                hold_times.append(hh)
        except Exception:
            pass
if hold_times:
    avg_hold = sum(hold_times) / len(hold_times)
    min_hold = min(hold_times)
    max_hold = max(hold_times)
    sorted_holds = sorted(hold_times)
    median_hold = sorted_holds[len(sorted_holds) // 2]
    def fmt_hours(h):
        if h < 1: return f'{h*60:.0f}m'
        elif h < 24: return f'{h:.1f}h'
        else: return f'{h/24:.1f}d'
    print(f'  Avg Hold Time:       {fmt_hours(avg_hold)} ({avg_hold:.1f} hours)')
    print(f'  Median Hold Time:    {fmt_hours(median_hold)} ({median_hold:.1f} hours)')
    print(f'  Min Hold Time:       {fmt_hours(min_hold)}')
    print(f'  Max Hold Time:       {fmt_hours(max_hold)}')
    hbl = [('<1h', 0, 1), ('1-6h', 1, 6), ('6-24h', 6, 24), ('1-3d', 24, 72), ('3-7d', 72, 168), ('7d+', 168, 999999)]
    hbc = {}
    for name, lo, hi in hbl:
        hbc[name] = 0
    for h in hold_times:
        for name, lo, hi in hbl:
            if lo <= h < hi:
                hbc[name] += 1
                break
    print(f'\n  Hold Time Distribution:')
    for name, lo, hi in hbl:
        cnt = hbc[name]
        pct = cnt / len(hold_times) * 100
        bar = '#' * int(pct / 2)
        print(f'    {name:8s} {cnt:5d} ({fmt_pct(pct):>6s})  {bar}')
else:
    print('  No hold time data available.')

sub_divider('Trading Activity Timeline')
trade_days = defaultdict(lambda: {'buys': 0, 'resolves': 0, 'sells': 0, 'volume': 0.0})
for t in all_trades:
    day = t['timestamp'][:10]
    if t['side'] == 'BUY':
        trade_days[day]['buys'] += 1
        trade_days[day]['volume'] += t['usdc_amount']
    elif t['side'] == 'RESOLVE':
        trade_days[day]['resolves'] += 1
    elif t['side'] == 'SELL':
        trade_days[day]['sells'] += 1
dash51 = '-' * 51
print(f'  {"Date":12s} {"Buys":>6s} {"Sells":>6s} {"Resolves":>9s} {"Volume":>14s}')
print(f'  {dash51}')
for day in sorted(trade_days.keys()):
    d = trade_days[day]
    print(f'  {day:12s} {d["buys"]:6d} {d["sells"]:6d} {d["resolves"]:9d} {fmt_usd(d["volume"]):>14s}')

divider('FUND ALLOCATIONS')
fund_totals = defaultdict(float)
fund_counts = defaultdict(int)
if allocations:
    dash78 = '-' * 78
    print(f'  {"Fund":20s} {"Amount":>12s} {"Source P&L":>12s} {"Status":>10s} {"Date":20s}')
    print(f'  {dash78}')
    for a in allocations:
        fn = a['fund_name']
        amt = fmt_usd(a['amount'])
        sp = fmt_usd(a['source_pnl'])
        st = a['status']
        dt = a['created_at']
        print(f'  {fn:20s} {amt:>12s} {sp:>12s} {st:>10s} {dt:20s}')
        fund_totals[a['fund_name']] += a['amount']
        fund_counts[a['fund_name']] += 1
    sub_divider('Fund Allocation Summary')
    total_allocated = sum(fund_totals.values())
    for fund in sorted(fund_totals.keys()):
        pct = fund_totals[fund] / total_allocated * 100 if total_allocated > 0 else 0
        print(f'  {fund:20s}  Total: {fmt_usd(fund_totals[fund]):>12s}  ({fmt_pct(pct)})  Entries: {fund_counts[fund]}')
    print(f'  {"TOTAL":20s}  Total: {fmt_usd(total_allocated):>12s}')
    ap = fmt_pct(total_allocated / total_pnl * 100) if total_pnl > 0 else 'N/A'
    print(f'  Allocation % of P&L: {ap}')
else:
    print('  No allocation data found.')

divider('EXECUTIVE SUMMARY')
if resolves and buys:
    wc = len([t for t in resolves if t['price'] == 1.0])
    lc = len([t for t in resolves if t['price'] == 0.0])
    tr = wc + lc
    wr = (wc / tr * 100) if tr > 0 else 0
    print(f'  Strategy Performance:')
    print(f'    Win Rate:              {fmt_pct(wr)}')
    print(f'    Profit Factor:         {profit_factor:.2f}x')
    rp = fmt_pct(total_pnl / starting_balance * 100) if starting_balance > 0 else 'N/A'
    print(f'    Total Return:          {fmt_usd(total_pnl)} ({rp})')
    at = sum(fund_totals.values()) if allocations else 0
    print(f'    Total Allocated:       {fmt_usd(at)}')
    tpe = sum(p['usdc_spent'] for p in positions) if positions else 0
    print(f'\n  Risk Assessment:')
    rpct = fmt_pct(tpe / balance * 100) if balance > 0 else 'N/A'
    print(f'    Open Position Risk:    {fmt_usd(tpe)} ({rpct} of balance)')
    print(f'    Unresolved Markets:    {len(unresolved_markets)}')
    if trade_pcts:
        print(f'    Avg Position Size:     {fmt_pct(avg_pct)} of balance')
        po5 = len([p for p in trade_pcts if p >= 5]) / len(trade_pcts) * 100
        print(f'    Trades at/over 5%:     {fmt_pct(po5)}')

print(f'\n{line70}')
print(f'  End of Report')
print(f'{line70}\n')
conn.close()