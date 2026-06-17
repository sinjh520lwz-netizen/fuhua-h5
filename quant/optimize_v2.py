#!/usr/bin/env python3
"""原版参数优化续跑 — 5分钟精确回测"""
import json, os, sys, time
from collections import defaultdict

LOG = '/tmp/optimize_v2.log'
def log(m):
    with open(LOG,'a') as f: f.write(f'{m}\n')

DATA_DIR = '/var/www/html/h5/quant/data'
t0 = time.time()

with open(f'{DATA_DIR}/all_klines_60d.json') as f: all_data = json.load(f)
with open(f'{DATA_DIR}/total_shares.json') as f: shares = json.load(f)

dc = defaultdict(int)
for info in all_data.values():
    for k in info.get('klines',[]):
        if isinstance(k,list) and len(k)>=6: dc[k[0]]+=1
dates = sorted([d for d,c in dc.items() if c>100])[-80:]
test_dates = dates[1:]
log(f"📂 {len(all_data)}只, 总股本{len(shares)}只, 区间{test_dates[0]}~{test_dates[-1]}")

# 原版候选（量比>1，市值50-200亿）
candidates_db = {}
for code, info in all_data.items():
    name = info.get('name','')
    if 'ST' in name or '*ST' in name: continue
    if code.startswith(('300','301','688','920')): continue
    if code not in shares: continue
    ts = shares[code]
    if ts <= 0: continue
    klines = info.get('klines',[])
    for date in test_dates:
        tk = None; ti = -1
        for i,k in enumerate(klines):
            if isinstance(k,list) and len(k)>=6 and k[0]==date: tk=k; ti=i; break
        if not tk or ti<1: continue
        pc = float(klines[ti-1][2]); tc = float(tk[2]); tv = float(tk[5])
        if pc <= 0: continue
        chg = (tc/pc-1)*100
        if not (3<=chg<=5): continue
        vols = [float(klines[j][5]) for j in range(max(0,ti-6),ti)]
        av = sum(vols)/len(vols) if vols else 1
        if not (tv/av>1 if av>0 else False): continue
        lu = 0
        for j in range(max(0,ti-21),ti):
            if j>0 and float(klines[j][2])>=float(klines[j-1][2])*1.095: lu+=1
        if not (lu>0): continue
        turn = tv * 100 / ts * 100
        mcap = tc * ts / 100000000
        if not (50 <= mcap <= 200): continue
        if code not in candidates_db: candidates_db[code] = {}
        candidates_db[code][date] = {
            'name':name,'change':round(chg,2),'price':tc,
            'turnover':round(turn,2),'mcap':round(mcap,2),
        }

# 所有换手率参数组
param_sets = [
    ('标准5-10',5,10), ('换手3-8',3,8), ('换手4-12',4,12),
    ('换手4-10',4,10), ('换手3-10',3,10), ('换手5-8',5,8),
    ('换手3-7',3,7), ('换手3-12',3,12), ('换手4-15',4,15),
    ('换手5-12',5,12), ('换手2-6',2,6), ('换手2-8',2,8),
]

# 统计各参数候选数
log(f"\n参数扫描:")
valid_params = []
for pname, tmin, tmax in param_sets:
    cnt = sum(1 for cd in candidates_db.values() for inf in cd.values() if tmin<=inf['turnover']<=tmax)
    log(f"  {pname}: 候选{cnt}条")
    if 20 <= cnt <= 700: valid_params.append((pname, tmin, tmax, cnt))

log(f"\n将跑5分钟回测: {len(valid_params)}组")

# baostock
import baostock as bs
log("连接baostock...")
lg = bs.login()
if lg.error_code != '0': log(f"失败: {lg.error_msg}"); exit(1)
log("OK")

_5min_cache = {}
bs_q = 0

def get_5min(code, date):
    global bs_q
    ck = f'{code}|{date}'
    if ck in _5min_cache: return _5min_cache[ck]
    prefix = 'sh' if code.startswith('6') else 'sz'
    bs_q += 1
    try:
        rs = bs.query_history_k_data_plus(f'{prefix}.{code}',
            'date,time,open,high,low,close,volume',
            start_date=date,end_date=date,frequency='5',adjustflag='2')
        if rs.error_code != '0': return None
        rows = []
        while rs.next(): rows.append(rs.get_row_data())
        if rows:
            _5min_cache[ck] = rows
            if len(_5min_cache) > 1000: _5min_cache.clear()
        return rows if rows else None
    except: return None

def get_price(rows, target):
    for r in rows:
        if r[1][8:12] == target: return float(r[5])
    for r in reversed(rows):
        if r[1][8:12] <= target: return float(r[5])
    return None

def get_exit(code, buy_date):
    from datetime import datetime, timedelta
    dt = datetime.strptime(buy_date, '%Y-%m-%d')
    for d in range(1, 10):
        nd = (dt + timedelta(days=d)).strftime('%Y-%m-%d')
        bars = get_5min(code, nd)
        if not bars: continue
        p = get_price(bars, '1000')
        if p: return p
        if bars: return float(bars[0][3])
    return None

def run_bt(tmin, tmax):
    trades = []; dayc = 0; sk = 0
    for date in test_dates:
        day_cd = []
        for code, cd in candidates_db.items():
            inf = cd.get(date)
            if not inf: continue
            if not (tmin <= inf['turnover'] <= tmax): continue
            day_cd.append((code, inf))
        if not day_cd: continue
        dayc += 1
        day_cd.sort(key=lambda x: abs(x[1]['change']-4))
        for code, inf in day_cd[:3]:
            bars = get_5min(code, date)
            if not bars: sk+=1; continue
            ep = get_price(bars, '1430')
            if not ep: sk+=1; continue
            ex = get_exit(code, date)
            if not ex: sk+=1; continue
            trades.append((ex/ep-1)*100)
    return trades, dayc, sk

results = []
for i, (pname, tmin, tmax, _) in enumerate(valid_params):
    t1 = time.time()
    ts, dayc, sk = run_bt(tmin, tmax)
    el = time.time()-t1
    total = len(ts)
    if total == 0:
        log(f"  [{i+1}/{len(valid_params)}] {pname}: ❌ 无交易")
        results.append((pname, tmin, tmax, {'trades':0,'days':dayc,'skip':sk}))
        continue
    wins = [t for t in ts if t>0]
    r = {'trades':total,'days':dayc,'skip':sk,
         'win_rate':round(len(wins)/total*100,1),
         'total_return':round(sum(ts),2),
         'avg_return':round(sum(ts)/total,2),
         'max_win':round(max(ts),2) if wins else 0,
         'max_loss':round(min(ts),2)}
    results.append((pname, tmin, tmax, r))
    dpd = round(total/max(dayc,1),1)
    log(f"  [{i+1}/{len(valid_params)}] {pname}: {total}笔 {dpd}/天 胜率{r['win_rate']}% 收益{r['total_return']:+.2f}% ({el:.0f}s)")

bs.logout()
elapsed = time.time()-t0

log(f'\n{"="*70}')
log(f'📊 参数优化完成 (耗时{elapsed:.0f}s, baostock查询{bs_q}次)')
log(f'{"="*70}')
log(f"{'参数名':<12} {'交易':<6} {'日均':<6} {'胜率':<8} {'总收益':<10} {'单笔':<10} {'最大赚':<8} {'最大亏':<8}")
log('-'*70)
results.sort(key=lambda x: -x[3]['total_return'])
for pname, tmin, tmax, r in results:
    dpd = round(r['trades']/max(r['days'],1),1) if r['days'] else 0
    log(f"{pname:<12} {r['trades']:<6} {dpd:<6} {r['win_rate']:<7}% {r['total_return']:<+9}% {r['avg_return']:<+9}% {r['max_win']:<7}% {r['max_loss']:<7}%")

if results:
    best = max(results, key=lambda x: x[3]['total_return'])
    log(f'\n🏆 最优: {best[0]} — 收益{best[3]["total_return"]:+.2f}% 胜率{best[3]["win_rate"]}%')
log(f'\n💾 日志: {LOG}')
