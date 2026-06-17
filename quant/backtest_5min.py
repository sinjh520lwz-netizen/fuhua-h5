#!/usr/bin/env python3
"""五步选股 5分钟精确回测 — 14:30买入 次日10:00卖出"""
import json, os, sys, time
from collections import defaultdict
import baostock as bs

DATA_DIR = '/var/www/html/h5/quant/data'
_5min_cache = {}

def get_5min_bars(code, date):
    """获取单日5分钟K线"""
    ck = f'{code}|{date}'
    if ck in _5min_cache: return _5min_cache[ck]
    prefix = 'sh' if code.startswith('6') else 'sz'
    try:
        rs = bs.query_history_k_data_plus(f'{prefix}.{code}',
            'date,time,open,high,low,close,volume',
            start_date=date,end_date=date,frequency='5',adjustflag='2')
        if rs.error_code != '0': return None
        rows = []
        while rs.next(): rows.append(rs.get_row_data())
        if rows:
            _5min_cache[ck] = rows
            if len(_5min_cache) > 500: _5min_cache.clear()
        return rows if rows else None
    except: return None

def get_price_1430(bars):
    """获取14:30价格（买入点）"""
    for r in bars:
        t = r[1][8:12]  # HHMM
        if t == '1430':  # 14:30-14:35这根bar
            return float(r[5])  # close of this bar = 14:30
    # fallback
    for r in reversed(bars):
        t = r[1][8:12]
        if t <= '1430': return float(r[5])
    return None

def get_price_1000(code, buy_date):
    """获取次日10:00价格（卖出点）"""
    from datetime import datetime, timedelta
    dt = datetime.strptime(buy_date, '%Y-%m-%d')
    # 尝试后续5个日历日
    for d in range(1, 10):
        nd = (dt + timedelta(days=d)).strftime('%Y-%m-%d')
        bars = get_5min_bars(code, nd)
        if not bars: continue
        for r in bars:
            t = r[1][8:12]
            if t == '1000': return float(r[5])  # 10:00的收盘价
        # 没有10:00的bar，用最早一根的开盘价
        if bars: return float(bars[0][3])  # open of first bar
    return None

def run():
    t0 = time.time()
    
    with open(f'{DATA_DIR}/all_klines_60d.json') as f:
        all_data = json.load(f)
    with open(f'{DATA_DIR}/total_shares.json') as f:
        shares = json.load(f)
    
    print("连接baostock...", flush=True)
    lg = bs.login()
    if lg.error_code != '0': print(f"失败: {lg.error_msg}"); return
    print("OK", flush=True)
    
    # 交易日
    dc = defaultdict(int)
    for info in all_data.values():
        for k in info.get('klines',[]):
            if isinstance(k,list) and len(k)>=6: dc[k[0]] += 1
    dates = sorted([d for d,c in dc.items() if c>100])[-80:]
    
    # 预筛通过Step1+2+5
    pass_cache = {}
    for code, info in all_data.items():
        name = info.get('name','')
        if 'ST' in name or '*ST' in name: continue
        if code.startswith(('300','301','688','920')): continue
        if code not in shares: continue
        klines = info.get('klines',[])
        passed = []
        for di, date in enumerate(dates):
            if di == 0: continue
            tk = None; ti = -1
            for i,k in enumerate(klines):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    tk=k; ti=i; break
            if not tk or ti<1: continue
            pc = float(klines[ti-1][2]); tc = float(tk[2]); tv = float(tk[5])
            if pc<=0: continue
            chg = (tc/pc-1)*100
            if not (3<=chg<=5): continue
            vols = [float(klines[j][5]) for j in range(max(0,ti-6),ti)]
            av = sum(vols)/len(vols) if vols else 1
            if not (tv/av>1 if av>0 else False): continue
            lu = 0
            for j in range(max(0,ti-21),ti):
                if j>0 and float(klines[j][2])>=float(klines[j-1][2])*1.095:
                    lu+=1
            if not (lu>0): continue
            passed.append(date)
        if passed:
            pass_cache[code] = {'name':name,'dates':set(passed)}
    
    print(f"通过Step1+2+5: {len(pass_cache)}只", flush=True)
    
    trades = []
    day_count = 0
    skip_no_5min = 0
    bs_q = 0
    
    for di, date in enumerate(dates):
        if di == 0: continue
        candidates = []
        for code, pd in pass_cache.items():
            if date not in pd['dates']: continue
            ts = shares.get(code,0)
            if ts <= 0: continue
            klines = all_data[code].get('klines',[])
            tk = None; ti = -1
            for i,k in enumerate(klines):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    tk=k; ti=i; break
            if not tk: continue
            tc = float(tk[2]); tv = float(tk[5])
            turn = tv * 100 / ts * 100
            if not (5 <= turn <= 10): continue
            mcap = tc * ts / 100000000
            if not (50 <= mcap <= 200): continue
            chg = (tc/float(klines[ti-1][2])-1)*100
            candidates.append({'code':code,'name':pd['name'],'change':round(chg,2),'price':tc})
        
        if not candidates: continue
        day_count += 1
        candidates.sort(key=lambda x:abs(x['change']-4))
        
        for p in candidates[:3]:
            code = p['code']
            # 5分钟数据获取14:30买入价
            bars = get_5min_bars(code, date)
            bs_q += 1
            if not bars: skip_no_5min += 1; continue
            ep = get_price_1430(bars)
            if not ep or ep <= 0: skip_no_5min += 1; continue
            
            # 次日10:00卖出价
            ex = get_price_1000(code, date)
            bs_q += 1
            if not ex or ex <= 0: skip_no_5min += 1; continue
            
            ret = (ex/ep - 1) * 100
            trades.append({
                'date':date,'code':code,'name':p['name'],
                'entry':round(ep,2),'exit':round(ex,2),
                'return_pct':round(ret,2),'change':p['change'],
            })
        
        if (di+1)%10==0:
            el = time.time() - t0
            print(f"  日{di+1}/{len(dates)-1} 交易{len(trades)}笔 查询{bs_q}次 耗时{el:.0f}s", flush=True)
    
    bs.logout()
    
    total = len(trades)
    el = time.time() - t0
    print(f'\n{"="*65}', flush=True)
    print(f'📊 五步选股 5分钟精确回测', flush=True)
    print(f'  baostock查询: {bs_q}次 | 5分钟数据不足跳过: {skip_no_5min}', flush=True)
    print(f'{"="*65}', flush=True)
    print(f'  有票天数: {day_count}天', flush=True)
    
    if total == 0:
        print('\n❌ 无交易', flush=True)
        return
    
    wins = [t for t in trades if t['return_pct']>0]
    tr = sum(t['return_pct'] for t in trades)
    
    print(f'  总交易: {total}笔', flush=True)
    print(f'  ✅ 盈利: {len(wins)}笔 ({len(wins)/total*100:.1f}%)', flush=True)
    print(f'  ❌ 亏损: {total-len(wins)}笔 ({(total-len(wins))/total*100:.1f}%)', flush=True)
    print(f'  总收益率: {tr:+.2f}%', flush=True)
    print(f'  平均单笔: {tr/total:+.2f}%', flush=True)
    
    max_w = max(t['return_pct'] for t in wins) if wins else 0
    max_l = min(t['return_pct'] for t in trades)
    print(f'  最大盈利: +{max_w:.2f}%  最大亏损: {max_l:.2f}%', flush=True)
    
    print(f'\n📋 交易明细 (最近20笔):', flush=True)
    for t in trades[-20:]:
        ic = '✅' if t['return_pct']>0 else '❌'
        print(f'  {ic} {t["date"]} {t["name"]}({t["code"]}) +{t["change"]}% 买入{t["entry"]}→卖出{t["exit"]} {t["return_pct"]:+.2f}%', flush=True)
    
    out = f'{DATA_DIR}/backtest_5min_result.json'
    with open(out,'w') as f:
        json.dump({
            'total_trades':total,'win_rate':round(len(wins)/total*100,1),
            'total_return':round(tr,2),'avg_return':round(tr/total,2),
            'trades':trades[-100:],
        }, f, ensure_ascii=False, indent=2)
    print(f'\n💾 {out} | ⏱ {el:.0f}s', flush=True)

if __name__=='__main__':
    run()
