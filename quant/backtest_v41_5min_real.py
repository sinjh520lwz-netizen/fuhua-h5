#!/usr/bin/env python3
"""
JH 5分钟条件单回测 v4.1 — 真实市场节奏
====================================================
时间线：
  14:30 → 用截至14:30的盘中数据评分
  14:30~14:55 → 以评分价±1%执行买入
  D尾盘~D+2 → 监控条件单
"""
import json, os, sys, time
from collections import Counter
import numpy as np
import pandas as pd

tp_pct=float(os.environ.get('JH_TP', 5.0))
sl_pct=float(os.environ.get('JH_SL', 4.0))
min_score=float(os.environ.get('JH_MIN_SCORE', 6))
log_suffix=os.environ.get('JH_LOG_SUFFIX', '41')
log_file=f'/tmp/bt{log_suffix}_out.log'
sys.stdout = open(log_file, 'w', buffering=1)
sys.stderr = sys.stdout

def _ac(*a,**kw): return pd.concat(*a,**kw)
if not hasattr(pd.DataFrame,'append'): pd.DataFrame.append = _ac
import baostock as bs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze
from cross_sectional_score import score_early_entry
from backtest_full import build_hist_klines

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
_cache = {}
CACHE_FILE = os.path.join(DATA_DIR, '5min_cache.pkl')
# 磁盘缓存：延迟批量写入
_disk_cache = None
_disk_dirty = 0
def _load_disk_cache():
    global _disk_cache
    if _disk_cache is None:
        try:
            import pickle
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'rb') as f:
                    _disk_cache = pickle.load(f)
            else:
                _disk_cache = {}
        except:
            _disk_cache = {}
    return _disk_cache

def _save_disk_cache(force=False):
    global _disk_cache, _disk_dirty
    if _disk_cache is None: return
    if force or _disk_dirty >= 20:
        try:
            import pickle
            with open(CACHE_FILE, 'wb') as f:
                pickle.dump(_disk_cache, f, protocol=pickle.HIGHEST_PROTOCOL)
        except:
            pass
        _disk_dirty = 0

def get_5min(code, start, end):
    ck = f'{code}|{start}|{end}'
    if ck in _cache: return _cache[ck]
    # 查磁盘缓存
    dcache = _load_disk_cache()
    if ck in dcache:
        df = dcache[ck]
        _cache[ck] = df
        return df
    bs_code = f'sh.{code}' if code.startswith('6') else f'sz.{code}'
    try:
        rs = bs.query_history_k_data_plus(bs_code,"date,time,open,high,low,close,volume",
            start_date=start,end_date=end,frequency='5',adjustflag='2')
        if rs.error_code!='0': return None
        dl=[]
        while rs.next(): dl.append(rs.get_row_data())
        if not dl: return None
        df=pd.DataFrame(dl,columns=rs.fields)
        for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
        _cache[ck]=df
        # 写入磁盘缓存（攒批）
        global _disk_dirty
        dcache[ck] = df
        _disk_dirty += 1
        _save_disk_cache()
        return df
    except: return None

def get_intraday_1430(code, date):
    """获取截至14:30的盘中数据（用于评分）"""
    bars = get_5min(code, date, date)
    if bars is None or len(bars) == 0: return None, None
    # 取14:30及之前的数据（09:35~14:30，共11根）
    db = bars[bars['time'].str[8:12] <= '1430']
    if len(db) == 0: return None, None
    price_1430 = float(db.iloc[-1]['close'])  # 14:30收盘价
    intraday = {
        'open': float(db.iloc[0]['open']),
        'close': price_1430,
        'high': float(db['high'].max()),
        'low': float(db['low'].min()),
        'volume': float(db['volume'].sum()),
    }
    return intraday, price_1430

def get_1430_to_close(code, date):
    """获取14:30~15:00的价格区间（用于验证买入价是否在±1%内）"""
    bars = get_5min(code, date, date)
    if bars is None: return None, None
    db = bars[(bars['time'].str[8:12] >= '1435') & (bars['time'].str[8:12] <= '1500')]
    if len(db) == 0: return None, None
    min_p = float(db['low'].min())
    max_p = float(db['high'].max())
    return min_p, max_p

def simulate(bars, buy_date, entry, tp=5.0, sl=4.0, days=3):
    """条件单模拟 — 从14:30之后开始监控"""
    tp_p=entry*(1+tp/100); sl_p=entry*(1-sl/100)
    bars=bars.sort_values(['date','time'])
    ds=sorted(bars['date'].unique())
    bi=next((i for i,d in enumerate(ds) if d>=buy_date),-1)
    if bi<0: return None
    md=ds[bi:min(bi+days,len(ds))]
    for _,bar in bars[bars['date'].isin(md)].iterrows():
        bd,bt=bar['date'],bar['time']
        # 14:30之前不监控（买入发生在14:30）
        if bd==buy_date and bt[8:12]<='1430': continue
        h,l=float(bar['high']),float(bar['low'])
        if h>=tp_p: return ('TP',tp_p,tp_pct,bd)
        if l<=sl_p: return ('SL',sl_p,-sl_pct,bd)
    ld=md[-1]; lb=bars[bars['date']==ld]
    if len(lb)>0:
        ep=float(lb.iloc[-1]['close'])
        return ('HOLD',ep,round((ep/entry-1)*100,2),ld)
    return None

def get_gap_next(bars, buy_dt, entry):
    ds=sorted(bars['date'].unique())
    bi=next((i for i,d in enumerate(ds) if d>=buy_dt),-1)
    if bi<0 or bi>=len(ds)-1: return None
    nb=bars[bars['date']==ds[bi+1]]
    if len(nb)==0: return None
    return round((float(nb.iloc[0]['open'])/entry-1)*100,2)

def get_gap_1430_to_next_open(bars, date, price_1430):
    """次日开盘价 vs 14:30买入价（隔夜跳空）"""
    ds=sorted(bars['date'].unique())
    bi=next((i for i,d in enumerate(ds) if d==date),-1)
    if bi<0 or bi>=len(ds)-1: return None
    nb=bars[bars['date']==ds[bi+1]]
    if len(nb)==0: return None
    return round((float(nb.iloc[0]['open'])/price_1430-1)*100,2)

def run():
    print("="*72)
    print("  v4.1 真实市场节奏 | 14:30评分 | 评分价±1%买入")
    print("="*72)
    
    with open(f'{DATA_DIR}/all_klines_60d.json') as f: data=json.load(f)
    dc=Counter()
    for ci in data.values():
        for k in ci.get('klines',[]):
            if isinstance(k,list) and len(k)>=6: dc[k[0]]+=1
    dates=sorted([d for d,c in dc.items() if c>=2000])[-253:]
    print(f"  {len(data)}只股票, {len(dates)}个交易日")
    
    bs.login(); print("  Baostock OK")
    btd=dates[5:-3][-60:]
    print(f"  回测{len(btd)}天: {btd[-1]}~{btd[0]}\n")
    
    all_trades=[]; gaps=[]; t0=time.time()
    
    for di,date in enumerate(btd):
        print(f"[{di+1}/{len(btd)}] {date} ({time.time()-t0:.0f}s)...")
        
        # TOP20取成交额
        snap=[]
        for code,info in data.items():
            for k in info.get('klines',[]):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    c=float(k[2]); o=float(k[1]); v=float(k[5])
                    snap.append({'code':code,'name':info.get('name',''),'amount':c*v/10000})
                    break
        snap.sort(key=lambda x:-x['amount'])
        cands=[s for s in snap[:20] if not s['code'].startswith(('688','689','300','301','4','8','920'))]
        
        # 14:30评分
        scored=[]
        for s in cands:
            code=s['code']
            intra,price1430=get_intraday_1430(code,date)
            if intra is None or price1430<=0: continue
            
            h=build_hist_klines(data,code,date)
            if len(h)<30: continue
            # 替换最后一日为14:30数据
            h[-1]['open']=intra['open']; h[-1]['close']=intra['close']
            h[-1]['high']=intra['high']; h[-1]['low']=intra['low']; h[-1]['volume']=intra['volume']
            
            ind=quick_analyze(h)
            if not ind: continue
            chg=(intra['close']/intra['open']-1)*100 if intra['open']>0 else 0
            sc,_=score_early_entry(ind,chg,0)
            if sc and sc>=min_score:
                nm=next((x['name'] for x in cands if x['code']==code),code)
                scored.append({'code':code,'name':nm,'score':sc,'price_1430':price1430})
        
        scored.sort(key=lambda x:-x['score'])
        picks=scored[:5]
        if not picks: continue
        
        # 验证买入价在±1%内
        di_idx=dates.index(date)
        end_d=dates[min(di_idx+3,len(dates)-1)]
        
        for p in picks:
            code=p['code']; price1430=p['price_1430']
            
            # 获取14:30之后的实际交易价格范围
            min_p,max_p=get_1430_to_close(code,date)
            if min_p is None: continue
            
            # 评分价±1%范围
            p_low=price1430*0.99; p_high=price1430*1.01
            
            # 实际价格范围是否在±1%内？如果在，可成交
            # 用实际可成交价 = max(min_p, p_low) 到 min(max_p, p_high) 的范围
            # 取中间值作为实际买入价
            actual_min=max(min_p,p_low)
            actual_max=min(max_p,p_high)
            
            if actual_min>actual_max:
                continue  # 实际价格超出±1%范围，无法成交
            
            # 实际买入价取中间值（模拟合理成交价）
            entry_price=round((actual_min+actual_max)/2,2)
            deviation=round(abs(entry_price/price1430-1)*100,2)
            
            # 获取监控期完整5分钟数据
            bars=get_5min(code,date,end_d)
            if bars is None or len(bars)<3: continue
            
            result=simulate(bars,date,entry_price,tp_pct,sl_pct,3)
            if result is None: continue
            
            gap_pct=get_gap_1430_to_next_open(bars,date,entry_price) or 0
            
            trade={'date':date,'code':code,'name':p['name'],'score':p['score'],
                   'score_price':price1430,'entry_price':entry_price,'deviation':deviation,
                   'hit_type':result[0],'return_pct':result[2],'gap_pct':gap_pct}
            all_trades.append(trade); gaps.append(gap_pct)
            
            e='🟢' if result[0]=='TP' else ('🔴' if result[0]=='SL' else '⚪')
            print(f"  {e} {p['name']:6s}({code}) 评分价{price1430:.2f} 买{entry_price:.2f}(偏{deviation:.2f}%) 分{p['score']:.0f}→{result[0]} {result[2]:+.2f}% 跳{gap_pct:+.2f}%")
        
        if len(_cache)>200: _cache.clear()
    
    bs.logout()
    
    elapsed=time.time()-t0
    print(f"\n{'='*72}")
    print(f"  v4.1 真实市场节奏 | {len(btd)}天 | {elapsed:.0f}s")
    print(f"{'='*72}")
    if not all_trades: print("  无交易"); return
    
    total=len(all_trades); tp=sum(1 for t in all_trades if t['hit_type']=='TP')
    sl=sum(1 for t in all_trades if t['hit_type']=='SL')
    hold=sum(1 for t in all_trades if t['hit_type']=='HOLD')
    tr=sum(t['return_pct'] for t in all_trades); wins=sum(1 for t in all_trades if t['return_pct']>0)
    avg_dev=np.mean([t['deviation'] for t in all_trades])
    
    print(f"  交易:{total} | TP:{tp}({tp/total*100:.1f}%) SL:{sl}({sl/total*100:.1f}%) HOLD:{hold}({hold/total*100:.1f}%)")
    print(f"  胜率:{wins}/{total}={wins/total*100:.1f}% | 总收益:{tr:+.2f}% | 均每笔:{tr/total:+.2f}%")
    print(f"  买入价偏差: 均{avg_dev:.2f}%")
    
    ga=np.array(gaps); neg=sum(1 for g in gaps if g<0)
    print(f"  隔夜跳空: 均{np.mean(ga):+.2f}% 低开{neg}/{len(gaps)}={neg/len(gaps)*100:.1f}%")
    ba=[t for t in all_trades if t['gap_pct']<-1.5]
    if ba: print(f"  <-1.5%:{len(ba)}笔 均{np.mean([t['return_pct'] for t in ba]):.2f}%")
    pairs=[(t['gap_pct'],t['return_pct']) for t in all_trades]
    if pairs: print(f"  相关系数:{np.corrcoef([p[0]for p in pairs],[p[1]for p in pairs])[0,1]:.3f}")
    print(f"\n  {'✅' if tr>0 else '❌'} 总收益:{tr:+.2f}%")
    
    suffix=f"v41_{log_suffix}" if log_suffix!='41' else 'v41'
    with open(f'{DATA_DIR}/backtest_{suffix}_5min_bs.json','w') as f:
        json.dump({'total':total,'tp':tp,'sl':sl,'hold':hold,'total_return':round(tr,2),
                   'win_rate':round(wins/total*100,1),'avg_deviation':round(avg_dev,2),
                   'trades':all_trades}, f, ensure_ascii=False, indent=2, default=str)
    print(f"  💾 已保存")
    sys.stdout.flush()

if __name__=='__main__': run()
