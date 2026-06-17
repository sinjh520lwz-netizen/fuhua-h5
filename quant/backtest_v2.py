#!/usr/bin/env python3
"""新版6条件选股 5分钟精确回测"""
import json, os, sys, time
from collections import defaultdict
import baostock as bs

LOG = '/tmp/bt_v2.log'
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
log(f"📂 {len(all_data)}只, 总股本{len(shares)}只")
log(f"📅 区间: {test_dates[0]}~{test_dates[-1]} ({len(test_dates)}天)")

# 候选库 — 新版6条件
# ①涨幅3-5% ②量比>1.4 ③换手5-10% ④市值<200亿 ⑤成交额>10亿 ⑥近20天涨停
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
        
        # ①涨幅3-5%
        chg = (tc/pc-1)*100
        if not (3<=chg<=5): continue
        
        # ②量比>1.4
        vols = [float(klines[j][5]) for j in range(max(0,ti-6),ti)]
        av = sum(vols)/len(vols) if vols else 1
        vr = tv/av if av>0 else 0
        if not (vr > 1.4): continue
        
        # ⑥近20天涨停
        lu = 0
        for j in range(max(0,ti-21),ti):
            if j>0 and float(klines[j][2])>=float(klines[j-1][2])*1.095: lu+=1
        if not (lu>0): continue
        
        # ③换手5-10%
        turn = tv * 100 / ts * 100
        if not (5 <= turn <= 10): continue
        
        # ④市值<200亿
        mcap = tc * ts / 100000000
        if not (mcap < 200): continue
        
        # ⑤成交额>10亿（万元）
        amount = tv * tc / 100  # 万元
        if not (amount > 100000): continue  # >10亿
        
        if code not in candidates_db: candidates_db[code] = {}
        candidates_db[code][date] = {
            'name':name,'change':round(chg,2),'price':tc,
            'turnover':round(turn,2),'mcap':round(mcap,2),'amount':round(amount/10000,2),
        }

total_cd = sum(len(v) for v in candidates_db.values())
log(f"📊 新版6条件候选: {total_cd}条, {len(candidates_db)}只")

if total_cd == 0:
    log("❌ 无候选，退出"); exit(1)

# baostock 5分钟数据
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

# 回测
trades = []; dc = 0; sk = 0
for date in test_dates:
    day_cd = []
    for code, cd in candidates_db.items():
        inf = cd.get(date)
        if not inf: continue
        day_cd.append((code, inf))
    if not day_cd: continue
    dc += 1
    day_cd.sort(key=lambda x: abs(x[1]['change']-4))
    
    for code, inf in day_cd[:3]:
        bars = get_5min(code, date)
        if not bars: sk+=1; continue
        ep = get_price(bars, '1430')
        if not ep: sk+=1; continue
        ex = get_exit(code, date)
        if not ex: sk+=1; continue
        ret = (ex/ep-1)*100
        trades.append({
            'date':date,'code':code,'name':inf['name'],
            'entry':round(ep,2),'exit':round(ex,2),
            'return_pct':round(ret,2),'change':inf['change'],
        })
    
    if (test_dates.index(date)+1)%20==0:
        log(f"  {test_dates.index(date)+1}/{len(test_dates)}天 交易{len(trades)}笔 查询{bs_q}次")

bs.logout()

total = len(trades)
elapsed = time.time()-t0
log(f'\n{"="*70}')
log(f'📊 新版6条件 5分钟精确回测 (耗时{elapsed:.0f}s, baostock查询{bs_q}次)')
log(f'{"="*70}')
log(f'  有票天数: {dc}天, 跳过(数据不足): {sk}次')

if total == 0:
    log('❌ 无交易'); exit(1)

wins = [t for t in trades if t['return_pct']>0]
tr = sum(t['return_pct'] for t in trades)
log(f'  总交易: {total}笔 ({total/max(dc,1):.1f}笔/天)')
log(f'  ✅ 盈利: {len(wins)}笔 ({len(wins)/total*100:.1f}%)')
log(f'  ❌ 亏损: {total-len(wins)}笔 ({(total-len(wins))/total*100:.1f}%)')
log(f'  总收益率: {tr:+.2f}%')
log(f'  平均单笔: {tr/total:+.2f}%')

max_w = max(t['return_pct'] for t in wins) if wins else 0
max_l = min(t['return_pct'] for t in trades)
log(f'  最大盈利: +{max_w:.2f}%  最大亏损: {max_l:.2f}%')

log(f'\n📋 最近20笔:')
for t in trades[-20:]:
    ic = '✅' if t['return_pct']>0 else '❌'
    log(f'  {ic} {t["date"]} {t["name"]}({t["code"]}) +{t["change"]}% → {t["return_pct"]:+.2f}%')

out = f'{DATA_DIR}/backtest_v2_result.json'
with open(out,'w') as f:
    json.dump({
        'total_trades':total,'win_rate':round(len(wins)/total*100,1),
        'total_return':round(tr,2),'avg_return':round(tr/total,2),
        'trades':trades[-100:],
    }, f, ensure_ascii=False, indent=2)
log(f'\n💾 {out}')
log(f'⏱ {elapsed:.0f}s')
