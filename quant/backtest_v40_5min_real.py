#!/usr/bin/env python3
"""
JH 真实5分钟条件单回测 v4.0 — 评分价=买入价
核心原则：
  评分用14:55盘中数据 → 买入也用同一个14:55价
  两条路径用的价格完全一致
"""
import json, os, sys, time
from collections import Counter
import numpy as np
import pandas as pd

log_file = '/tmp/bt40_out.log'
sys.stdout = open(log_file, 'w', buffering=1)
sys.stderr = sys.stdout

# Baostock兼容
def _ac(*a,**kw): return pd.concat(*a,**kw)
if not hasattr(pd.DataFrame,'append'): pd.DataFrame.append = _ac
import baostock as bs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze
from cross_sectional_score import score_early_entry
from backtest_full import build_hist_klines

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
_cache = {}

def get_5min(code, start, end):
    """带缓存的Baostock 5分钟数据"""
    ck = f'{code}|{start}|{end}'
    if ck in _cache: return _cache[ck]
    bs_code = f'sh.{code}' if code.startswith('6') else f'sz.{code}'
    try:
        rs = bs.query_history_k_data_plus(bs_code, "date,time,open,high,low,close,volume",
            start_date=start, end_date=end, frequency='5', adjustflag='2')
        if rs.error_code != '0': return None
        dl = []
        while rs.next(): dl.append(rs.get_row_data())
        if not dl: return None
        df = pd.DataFrame(dl, columns=rs.fields)
        for c in ['open','high','low','close','volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
        _cache[ck] = df
        return df
    except: return None

def get_intraday(code, date):
    """获取截至14:55的盘中数据 + 14:55收盘价（同时用于评分和买入）"""
    bars = get_5min(code, date, date)
    if bars is None or len(bars) == 0: return None, None
    db = bars[bars['time'].str[8:12] <= '1455']
    if len(db) == 0: return None, None
    # 14:55收盘价=评分价=买入价
    price_1455 = float(db.iloc[-1]['close'])
    intraday = {
        'open': float(db.iloc[0]['open']),
        'close': price_1455,
        'high': float(db['high'].max()),
        'low': float(db['low'].min()),
        'volume': float(db['volume'].sum()),
    }
    return intraday, price_1455

def simulate(bars, buy_date, entry, tp=5.0, sl=4.0, days=3):
    """条件单模拟"""
    tp_p = entry*(1+tp/100); sl_p = entry*(1-sl/100)
    bars = bars.sort_values(['date','time'])
    ds = sorted(bars['date'].unique())
    bi = next((i for i,d in enumerate(ds) if d>=buy_date), -1)
    if bi < 0: return None
    md = ds[bi:min(bi+days, len(ds))]
    for _, bar in bars[bars['date'].isin(md)].iterrows():
        bd, bt = bar['date'], bar['time']
        if bd==buy_date and bt[8:12]<='1455': continue
        h,l = float(bar['high']),float(bar['low'])
        if h>=tp_p: return ('TP', tp_p, tp_pct, bd)
        if l<=sl_p: return ('SL', sl_p, -sl_pct, bd)
    # HOLD
    ld = md[-1]; lb = bars[bars['date']==ld]
    if len(lb)>0:
        ep = float(lb.iloc[-1]['close'])
        return ('HOLD', ep, round((ep/entry-1)*100,2), ld)
    return None

def get_gap(bars, buy_dt, entry):
    ds = sorted(bars['date'].unique())
    bi = next((i for i,d in enumerate(ds) if d>=buy_dt), -1)
    if bi<0 or bi>=len(ds)-1: return None
    nb = bars[bars['date']==ds[bi+1]]
    if len(nb)==0: return None
    return round((float(nb.iloc[0]['open'])/entry-1)*100, 2)

def run():
    print("="*72)
    print("  v4.0 完全真实回测 | 评分价=买入价 | 14:55")
    print("="*72)
    
    # 加载数据
    with open(f'{DATA_DIR}/all_klines_60d.json') as f: data = json.load(f)
    dc = Counter()
    for ci in data.values():
        for k in ci.get('klines',[]):
            if isinstance(k,list) and len(k)>=6: dc[k[0]]+=1
    dates = sorted([d for d,c in dc.items() if c>=2000])[-253:]
    print(f"  {len(data)}只股票, {len(dates)}个交易日")
    
    bs.login(); print("  Baostock OK")
    
    btd = dates[5:-3][-60:]
    print(f"  回测{len(btd)}天: {btd[-1]}~{btd[0]}\n")
    
    all_trades = []; gaps = []; t0 = time.time()
    
    for di, date in enumerate(btd):
        print(f"[{di+1}/{len(btd)}] {date} ({time.time()-t0:.0f}s)...")
        
        # 成交额TOP20
        snap = []
        for code, info in data.items():
            for k in info.get('klines',[]):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    c=float(k[2]); o=float(k[1]); v=float(k[5])
                    snap.append({'code':code,'name':info.get('name',''),'amount':c*v/10000})
                    break
        snap.sort(key=lambda x:-x['amount'])
        cands = [s for s in snap[:20] if not s['code'].startswith(('688','689','300','301','4','8','920'))]
        
        # 直接对所有候选拉盘中数据评分（不预筛，预筛用的D日收盘价不对）
        scored = []
        for s in cands:
            code = s['code']
            intra, price = get_intraday(code, date)
            if intra is None or price<=0: continue
            h = build_hist_klines(data, code, date)
            h[-1]['open']=intra['open']; h[-1]['close']=intra['close']
            h[-1]['high']=intra['high']; h[-1]['low']=intra['low']; h[-1]['volume']=intra['volume']
            ind = quick_analyze(h)
            if not ind: continue
            chg = (intra['close']/intra['open']-1)*100 if intra['open']>0 else 0
            sc, _ = score_early_entry(ind, chg, 0)
            if sc and sc>=6:
                nm = next((s['name'] for s in cands if s['code']==code), code)
                scored.append({'code':code,'name':nm,'score':sc,'entry_price':price})
        
        scored.sort(key=lambda x:-x['score'])
        picks = scored[:5]
        if not picks: continue
        
        # 监控期
        di_idx = dates.index(date)
        end_d = dates[min(di_idx+3, len(dates)-1)]
        
        for p in picks:
            code=p['code']; entry=p['entry_price']
            bars = get_5min(code, date, end_d)
            if bars is None or len(bars)<3: continue
            
            result = simulate(bars, date, entry, 5.0, 4.0, 3)
            if result is None: continue
            
            gap_pct = get_gap(bars, date, entry) or 0
            all_trades.append({'date':date,'code':code,'name':p['name'],'score':p['score'],
                'entry_price':entry,'hit_type':result[0],'return_pct':result[2],'gap_pct':gap_pct})
            gaps.append(gap_pct)
        
        # 显示
        for t in all_trades[-len(picks):]:
                e='🟢' if t['hit_type']=='TP' else ('🔴' if t['hit_type']=='SL' else '⚪')
                print(f"  {e} {t['name']:6s}({t['code']}) 分{t['score']:.0f} 买{t['entry_price']:.2f}→{t['hit_type']} {t['return_pct']:+.2f}% 跳{t['gap_pct']:+.2f}%")
        # 清理缓存
        if len(_cache)>200: _cache.clear()
    
    bs.logout()
    
    # 报告
    elapsed=time.time()-t0
    print(f"\n{'='*72}")
    print(f"  v4.0 完全真实回测 | {len(btd)}天 | {elapsed:.0f}s")
    print(f"{'='*72}")
    if not all_trades: print("  无交易"); return
    
    total=len(all_trades); tp=sum(1 for t in all_trades if t['hit_type']=='TP')
    sl=sum(1 for t in all_trades if t['hit_type']=='SL')
    hold=sum(1 for t in all_trades if t['hit_type']=='HOLD')
    tr=sum(t['return_pct'] for t in all_trades); wins=sum(1 for t in all_trades if t['return_pct']>0)
    
    print(f"  交易:{total} | TP:{tp}({tp/total*100:.1f}%) SL:{sl}({sl/total*100:.1f}%) HOLD:{hold}({hold/total*100:.1f}%)")
    print(f"  胜率:{wins}/{total}={wins/total*100:.1f}% | 总收益:{tr:+.2f}% | 均每笔:{tr/total:+.2f}%")
    
    ga=np.array(gaps); neg=sum(1 for g in gaps if g<0)
    print(f"  隔夜跳空: 均{np.mean(ga):+.2f}% 低开{neg}/{len(gaps)}={neg/len(gaps)*100:.1f}%")
    ba=[t for t in all_trades if t['gap_pct']<-1.5]
    if ba: print(f"  <-1.5%:{len(ba)}笔 均{np.mean([t['return_pct'] for t in ba]):.2f}%")
    pairs=[(t['gap_pct'],t['return_pct']) for t in all_trades]
    if pairs: print(f"  相关系数:{np.corrcoef([p[0]for p in pairs],[p[1]for p in pairs])[0,1]:.3f}")
    print(f"\n  {'✅' if tr>0 else '❌'} 总收益:{tr:+.2f}%")
    
    with open(f'{DATA_DIR}/backtest_v40_5min_bs.json','w') as f:
        json.dump({'total':total,'tp':tp,'sl':sl,'hold':hold,'total_return':round(tr,2),
                   'win_rate':round(wins/total*100,1),'trades':all_trades}, f, ensure_ascii=False, indent=2, default=str)
    print(f"  💾 已保存")
    sys.stdout.flush()

tp_pct=5.0; sl_pct=4.0
if __name__=='__main__': run()
