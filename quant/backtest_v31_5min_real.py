#!/usr/bin/env python3
"""
JH 5分钟条件单回测 v3.1 — 完全真实 + 并行优化
"""
import json, os, sys, time
from datetime import datetime
from collections import defaultdict
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

log_file = '/tmp/bt31_out.log'
sys.stdout = open(log_file, 'w', buffering=1)
sys.stderr = sys.stdout

def _append_compat(*a,**kw): return pd.concat(*a,**kw)
if not hasattr(pd.DataFrame,'append'): pd.DataFrame.append = _append_compat
import baostock as bs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze
from cross_sectional_score import score_early_entry
from backtest_full import build_hist_klines

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def code_to_bs(code):
    code = str(code).strip().zfill(6)
    if code.startswith('6'): return f'sh.{code}'
    return f'sz.{code}'

# ──────────────────────────────────────────────
# 5分钟数据（线程安全缓存）
# ──────────────────────────────────────────────
import threading
_cache_lock = threading.Lock()
_5min_cache = {}

def get_5min_bars(code, start, end):
    ck = f'{code}|{start}|{end}'
    with _cache_lock:
        if ck in _5min_cache: return _5min_cache[ck]
    try:
        rs = bs.query_history_k_data_plus(
            code_to_bs(code),
            "date,time,open,high,low,close,volume",
            start_date=start, end_date=end,
            frequency='5', adjustflag='2')
        if rs.error_code != '0': return None
        dl = []
        while rs.next(): dl.append(rs.get_row_data())
        if not dl: return None
        df = pd.DataFrame(dl, columns=rs.fields)
        for c in ['open','high','low','close','volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
        with _cache_lock: _5min_cache[ck] = df
        return df
    except: return None

def get_intraday_1455(code, date):
    """获取截至14:55的盘中数据"""
    bars = get_5min_bars(code, date, date)
    if bars is None or len(bars) == 0: return None
    db = bars[bars['time'].str[8:12] <= '1455']
    if len(db) == 0: return None
    return {
        'open': float(db.iloc[0]['open']),
        'close': float(db.iloc[-1]['close']),
        'high': float(db['high'].max()),
        'low': float(db['low'].min()),
        'volume': float(db['volume'].sum()),
    }

def get_entry_price(code, date):
    bars = get_5min_bars(code, date, date)
    if bars is None: return None
    pm = bars[bars['time'].str[8:12] == '1455']
    return float(pm.iloc[0]['close']) if len(pm) > 0 else None

# ──────────────────────────────────────────────
# 并行获取盘中数据
# ──────────────────────────────────────────────
def batch_get_intraday(codes, date, max_workers=1):
    """顺序获取（Baostock不支持并发）"""
    results = {}
    for code in codes:
        try:
            r = get_intraday_1455(code, date)
            if r: results[code] = r
        except: pass
    return results

def batch_get_5min_range(codes, start, end, max_workers=1):
    """顺序获取"""
    results = {}
    for code in codes:
        try:
            r = get_5min_bars(code, start, end)
            if r is not None and len(r) > 0: results[code] = r
        except: pass
    return results

# ──────────────────────────────────────────────
# 评分+条件单
# ──────────────────────────────────────────────
def score_stock(hist, intraday, market_change=0):
    """用盘中数据评分"""
    hist[-1]['open'] = intraday['open']
    hist[-1]['close'] = intraday['close']
    hist[-1]['high'] = intraday['high']
    hist[-1]['low'] = intraday['low']
    hist[-1]['volume'] = intraday['volume']
    ind = quick_analyze(hist)
    if not ind: return None, None
    change = (intraday['close']/intraday['open']-1)*100 if intraday['open']>0 else 0
    return score_early_entry(ind, change, market_change)

def simulate_tp_sl(bars_5min, buy_date, entry, tp_pct=5.0, sl_pct=4.0, max_days=3):
    tp_p = entry*(1+tp_pct/100); sl_p = entry*(1-sl_pct/100)
    bars = bars_5min.sort_values(['date','time'])
    ds = sorted(bars['date'].unique())
    bi = next((i for i,d in enumerate(ds) if d>=buy_date), -1)
    if bi < 0: return None
    md = ds[bi:min(bi+max_days,len(ds))]
    mb = bars[bars['date'].isin(md)]
    for _, bar in mb.iterrows():
        bd, bt = bar['date'], bar['time']
        if bd==buy_date and bt[8:12]<='1455': continue
        h,l = float(bar['high']),float(bar['low'])
        if h>=tp_p: return ('TP',tp_p,tp_pct,bd)
        if l<=sl_p: return ('SL',sl_p,-sl_pct,bd)
    ld = md[-1]; lb = bars[bars['date']==ld]
    if len(lb)>0:
        ep = float(lb.iloc[-1]['close'])
        ret = round((ep/entry-1)*100,2)
        return ('HOLD',ep,ret,ld)
    return None

def get_gap(bars_5min, buy_dt, entry):
    ds = sorted(bars_5min['date'].unique())
    bi = next((i for i,d in enumerate(ds) if d>=buy_dt), -1)
    if bi<0 or bi>=len(ds)-1: return None
    nb = bars_5min[bars_5min['date']==ds[bi+1]]
    if len(nb)==0: return None
    return round((float(nb.iloc[0]['open'])/entry-1)*100,2)

# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
def run():
    print("="*72)
    print("  v3.1 完全真实回测 | 盘中评分+实时买入 | 并行优化")
    print("="*72)
    
    print("\n📦 加载...")
    with open(f'{DATA_DIR}/all_klines_60d.json') as f: all_klines = json.load(f)
    from collections import Counter
    dc = Counter()
    for ci in all_klines.values():
        for k in ci.get('klines',[]):
            if isinstance(k,list) and len(k)>=6: dc[k[0]]+=1
    dates = sorted([d for d,c in dc.items() if c>=2000])[-253:]
    print(f"  ✓ {len(all_klines)}只股票, {len(dates)}个交易日")
    
    print("🔌 Baostock...")
    lg = bs.login()
    if lg.error_code!='0': print(f"  ❌ {lg.error_msg}"); return
    print("  ✓ OK")
    
    btd = dates[5:-3][-60:]
    print(f"\n📅 {len(btd)}天: {btd[-1]}~{btd[0]}")
    
    all_trades = []; gaps = []; t0 = time.time()
    
    for di, date in enumerate(btd):
        if (di+1)%5==0 or di<3:
            print(f"\n📅 [{di+1}/{len(btd)}] {date} (已跑{time.time()-t0:.0f}s)...")
        
        # 取成交额TOP30（快速预筛）
        snap = []
        for code, info in all_klines.items():
            for k in info.get('klines',[]):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    close=float(k[2]); o=float(k[1]); vol=float(k[5])
                    snap.append({'code':code,'name':info.get('name',''),'amount':close*vol/10000})
                    break
        snap.sort(key=lambda x:-x['amount'])
        candidates = [s for s in snap[:30] if not s['code'].startswith(('688','689','300','301','4','8')) and not s['code'].startswith('920')]
        
        # 预筛：先用D-1数据判断是否站上MA5
        pre_codes = []
        for s in candidates:
            hist = build_hist_klines(all_klines, s['code'], date)
            if len(hist) < 30: continue
            # 检查MA5（用D-1收盘）
            closes = [h['close'] for h in hist[-5:]]
            ma5 = np.mean(closes) if len(closes)==5 else 0
            last_c = closes[-1] if closes else 0
            if last_c > ma5:  # 至少MA5条件通过
                pre_codes.append(s['code'])
        
        # 并行拉盘中数据
        intraday_data = batch_get_intraday(pre_codes, date, max_workers=10)
        
        # 评分
        scored = []
        for code in pre_codes:
            if code not in intraday_data: continue
            intra = intraday_data[code]
            if intra['close'] <= 0: continue
            hist = build_hist_klines(all_klines, code, date)
            score, _ = score_stock(hist, intra, 0)
            if score and score >= 6:
                name = next((s['name'] for s in candidates if s['code']==code), code)
                scored.append({'code':code,'name':name,'score':score})
        
        scored.sort(key=lambda x:-x['score'])
        picks = scored[:5]
        if not picks: continue
        
        # 买入日 = 当天
        di_idx = dates.index(date)
        end_d = dates[min(di_idx+3, len(dates)-1)]
        
        # 并行拉监控期5分钟数据
        pick_codes = [p['code'] for p in picks]
        mon_bars = batch_get_5min_range(pick_codes, date, end_d, max_workers=6)
        
        for pick in picks:
            code = pick['code']
            entry = get_entry_price(code, date)
            if entry is None: continue
            if code not in mon_bars: continue
            
            bars = mon_bars[code]
            if len(bars) < 3: continue
            
            result = simulate_tp_sl(bars, date, entry, 5.0, 4.0, 3)
            if result is None: continue
            
            gap_pct = get_gap(bars, date, entry) or 0
            
            trade = {'date':date,'code':code,'name':pick['name'],'score':pick['score'],
                     'entry_price':entry,'hit_type':result[0],'return_pct':result[2],'gap_pct':gap_pct}
            all_trades.append(trade)
            gaps.append(gap_pct)
        
        # 清理缓存
        with _cache_lock:
            if len(_5min_cache)>300: _5min_cache.clear()
        
        # 显示
        if (di+1)%5==0 or di<3:
            for t in all_trades[-len(picks):]:
                e = '🟢' if t['hit_type']=='TP' else ('🔴' if t['hit_type']=='SL' else '⚪')
                print(f"  {e} {t['name']:6s}({t['code']}) 分{t['score']:.0f} 买{t['entry_price']:.2f}→{t['hit_type']} {t['return_pct']:+.2f}% 跳空{t['gap_pct']:+.2f}%")
    
    bs.logout()
    
    elapsed = time.time()-t0
    print(f"\n{'='*72}")
    print(f"  📊 v3.1 完全真实回测 | {len(btd)}天 | {elapsed:.0f}s")
    print(f"{'='*72}")
    if not all_trades: print("  无交易"); return
    
    total=len(all_trades); tp=sum(1 for t in all_trades if t['hit_type']=='TP')
    sl=sum(1 for t in all_trades if t['hit_type']=='SL')
    hold=sum(1 for t in all_trades if t['hit_type']=='HOLD')
    tr=sum(t['return_pct'] for t in all_trades); wins=sum(1 for t in all_trades if t['return_pct']>0)
    
    print(f"  交易: {total} | TP:{tp}({tp/total*100:.1f}%) | SL:{sl}({sl/total*100:.1f}%) | HOLD:{hold}({hold/total*100:.1f}%)")
    print(f"  胜率: {wins}/{total}={wins/total*100:.1f}% | 总收益: {tr:+.2f}% | 均每笔: {tr/total:+.2f}%")
    
    ga=np.array(gaps); neg=sum(1 for g in gaps if g<0)
    print(f"  隔夜跳空: 均{np.mean(ga):+.2f}% 低开{neg}/{len(gaps)}={neg/len(gaps)*100:.1f}%")
    bad_ga=[t for t in all_trades if t['gap_pct']<-1.5]
    if bad_ga: print(f"  跳空<-1.5%: {len(bad_ga)}笔 均收益{np.mean([t['return_pct'] for t in bad_ga]):.2f}%")
    
    pairs=[(t['gap_pct'],t['return_pct']) for t in all_trades]
    if pairs: print(f"  相关系数: {np.corrcoef([p[0] for p in pairs],[p[1] for p in pairs])[0,1]:.3f}")
    print(f"\n  {'✅' if tr>0 else '❌'} 总收益: {tr:+.2f}%")
    
    with open(f'{DATA_DIR}/backtest_v31_5min_bs.json','w') as f:
        json.dump({'total':total,'tp':tp,'sl':sl,'hold':hold,'total_return':round(tr,2),
                   'win_rate':round(wins/total*100,1),'trades':all_trades}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 已保存")
    sys.stdout.flush()

if __name__=='__main__':
    run()
