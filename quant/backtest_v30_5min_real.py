#!/usr/bin/env python3
"""
JH 真实5分钟条件单回测 v3.0 — 完全真实模拟
====================================================
关键改进：
  ① 评分只用截至14:55的盘中数据（不含15:00收盘）
  ② 买入用14:55实时价
  ③ 两轮筛选：先用D-1数据快速预筛，再拉5分钟数据精确评分
"""
import json, os, sys, time
from datetime import datetime
from collections import defaultdict
import numpy as np
import pandas as pd

log_file = '/tmp/bt30_out.log'
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
# 5分钟数据缓存
# ──────────────────────────────────────────────
_cache_5min = {}
def get_5min_bars(code, start_date, end_date):
    """获取5分钟K线"""
    ck = f'{code}|{start_date}|{end_date}'
    if ck in _cache_5min: return _cache_5min[ck]
    try:
        rs = bs.query_history_k_data_plus(
            code_to_bs(code),
            "date,time,open,high,low,close,volume",
            start_date=start_date, end_date=end_date,
            frequency='5', adjustflag='2')
        if rs.error_code != '0': return None
        dl = []
        while rs.next(): dl.append(rs.get_row_data())
        if not dl: return None
        df = pd.DataFrame(dl, columns=rs.fields)
        for c in ['open','high','low','close','volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
        _cache_5min[ck] = df
        return df
    except: return None

def get_intraday_up_to_1455(code, date):
    """获取截至14:55的盘中K线数据（不含15:00收盘价）"""
    bars = get_5min_bars(code, date, date)
    if bars is None or len(bars) == 0: return None
    # 取14:55之前的bars（09:35~14:55，共13根）
    day_bars = bars[bars['time'].str[8:12] <= '1455']
    if len(day_bars) == 0: return None
    open_p = float(day_bars.iloc[0]['open'])
    close_1455 = float(day_bars.iloc[-1]['close'])
    high = float(day_bars['high'].max())
    low = float(day_bars['low'].min())
    volume = float(day_bars['volume'].sum())
    return {'open': open_p, 'close': close_1455, 'high': high, 'low': low, 'volume': volume}

def get_entry_at_1455(code, date):
    """获取14:55买入价"""
    bars = get_5min_bars(code, date, date)
    if bars is None: return None
    pm = bars[bars['time'].str[8:12] == '1455']
    if len(pm) > 0: return float(pm.iloc[0]['close'])
    return None

# ──────────────────────────────────────────────
# 条件单模拟（从买入后下一根K线开始）
# ──────────────────────────────────────────────
def simulate_tp_sl(bars_5min, buy_date, entry_price, tp_pct=5.0, sl_pct=4.0, max_days=3):
    tp_price = entry_price * (1 + tp_pct/100)
    sl_price = entry_price * (1 - sl_pct/100)
    bars = bars_5min.sort_values(['date','time'])
    dates = sorted(bars['date'].unique())
    
    buy_idx = -1
    for i, d in enumerate(dates):
        if d >= buy_date: buy_idx = i; break
    if buy_idx < 0: return None
    end_idx = min(buy_idx + max_days, len(dates))
    monitor = bars[bars['date'].isin(dates[buy_idx:end_idx])]
    
    for _, bar in monitor.iterrows():
        bd, bt = bar['date'], bar['time']
        if bd == buy_date and bt[8:12] <= '1455': continue
        h, l = float(bar['high']), float(bar['low'])
        if h >= tp_price:
            return {'hit_type':'TP','entry_price':entry_price,'exit_price':tp_price,'return_pct':tp_pct,'exit_date':bd}
        if l <= sl_price:
            return {'hit_type':'SL','entry_price':entry_price,'exit_price':sl_price,'return_pct':-sl_pct,'exit_date':bd}
    
    last_date = monitor['date'].iloc[-1]
    last_bars = bars[bars['date'] == last_date]
    if len(last_bars) > 0:
        exit_p = float(last_bars.iloc[-1]['close'])
        ret = round((exit_p/entry_price-1)*100, 2)
        return {'hit_type':'HOLD','entry_price':entry_price,'exit_price':exit_p,'return_pct':ret,'exit_date':last_date}
    return None

def get_gap(bars_5min, buy_date, entry_price):
    """买入后次日开盘跳空"""
    dates = sorted(bars_5min['date'].unique())
    bi = -1
    for i, d in enumerate(dates):
        if d >= buy_date: bi = i; break
    if bi < 0 or bi >= len(dates)-1: return None
    nd = dates[bi+1]
    nb = bars_5min[bars_5min['date']==nd]
    if len(nb)==0: return None
    o = float(nb.iloc[0]['open'])
    return round((o/entry_price-1)*100, 2)

# ──────────────────────────────────────────────
# 真实评分（只用截至14:55的数据）
# ──────────────────────────────────────────────
def load_data():
    fpath = os.path.join(DATA_DIR, 'all_klines_60d.json')
    with open(fpath) as f: return json.load(f)

def get_trading_dates(data):
    from collections import Counter
    dc = Counter()
    for ci in data.values():
        for k in ci.get('klines',[]):
            if isinstance(k,list) and len(k)>=6: dc[k[0]]+=1
    return sorted([d for d,c in dc.items() if c>=2000])[-253:]

def score_stock_with_intraday(all_klines, code, date, intraday):
    """用盘中截至14:55的数据评分"""
    # 构建历史K线（不含D日）
    hist = build_hist_klines(all_klines, code, date)
    if len(hist) < 30: return None, None
    
    # 替换最后一个K线为盘中数据
    hist[-1]['open'] = intraday['open']
    hist[-1]['close'] = intraday['close']
    hist[-1]['high'] = intraday['high']
    hist[-1]['low'] = intraday['low']
    hist[-1]['volume'] = intraday['volume']
    
    ind = quick_analyze(hist)
    if not ind: return None, None
    
    change = (intraday['close'] / intraday['open'] - 1) * 100 if intraday['open'] > 0 else 0
    score, factors = score_early_entry(ind, change, 0)
    return score, factors

# ──────────────────────────────────────────────
# 两轮筛选评分
# ──────────────────────────────────────────────
def real_score_stocks(all_klines, date, top_n=5):
    """真实评分：两轮筛选"""
    # 第1轮：用D-1数据快速预筛成交额前80
    snapshot = []
    for code, info in all_klines.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6 and k[0] == date:
                close = float(k[2]); o = float(k[1]); vol = float(k[5])
                snapshot.append({'code':code, 'name':info.get('name',''),
                                'price':close, 'amount':close*vol/10000})
                break
    snapshot.sort(key=lambda x: -x['amount'])
    candidates = snapshot[:80]
    
    # 第2轮：对每只拉5分钟数据→盘中评分
    scored = []
    for s in candidates:
        code = s['code']
        if code.startswith(('688','689','300','301','4','8')) or code.startswith('920'):
            continue
        
        # 获取截至14:55的盘中数据
        intraday = get_intraday_up_to_1455(code, date)
        if intraday is None: continue
        if intraday['close'] <= 0: continue
        
        # 真实评分
        score, _ = score_stock_with_intraday(all_klines, code, date, intraday)
        if score is None or score < 6: continue
        
        scored.append({'code':code, 'name':s['name'], 'score':score,
                       'price':intraday['close'], 'change':(intraday['close']/intraday['open']-1)*100})
        
        # 缓存太多时清理
        if len(_cache_5min) > 200: _cache_5min.clear()
    
    scored.sort(key=lambda x: -x['score'])
    return scored[:top_n]

# ──────────────────────────────────────────────
# 主回测
# ──────────────────────────────────────────────
def run_backtest():
    print("=" * 72)
    print("  JH 5分钟条件单回测 v3.0 — 完全真实模拟")
    print("  盘中14:55数据评分 | 14:55实时买入")
    print("=" * 72)
    
    print("\n📦 加载日线数据...")
    all_klines = load_data()
    print(f"  ✓ {len(all_klines)}只股票")
    
    trading_dates = get_trading_dates(all_klines)
    print(f"  ✓ {len(trading_dates)}个交易日")
    
    print("\n🔌 连接Baostock...")
    lg = bs.login()
    if lg.error_code != '0': print(f"  ❌ {lg.error_msg}"); return
    print("  ✓ 登录成功")
    
    # 回测区间：跳过头尾
    backtest_dates = trading_dates[5:-3][-60:]  # 60天（一轮约15-20分钟）
    print(f"\n📅 回测: {backtest_dates[-1]}~{backtest_dates[0]}, {len(backtest_dates)}天")
    
    all_trades = []; gaps = []
    t0 = time.time()
    
    for di, date in enumerate(backtest_dates):
        if (di+1)%5==0 or di<3:
            print(f"\n📅 [{di+1}/{len(backtest_dates)}] {date}...")
        
        # 真实评分（用盘中数据）
        picks = real_score_stocks(all_klines, date, top_n=5)
        if not picks: continue
        
        # 找买入日（D当天）和监控截止日（D+2）
        di_idx = trading_dates.index(date)
        end_date = trading_dates[min(di_idx+3, len(trading_dates)-1)]
        
        for pick in picks:
            code = pick['code']; name = pick['name']
            
            # 14:55买入价（直接从盘中数据拿）
            entry_price = get_entry_at_1455(code, date)
            if entry_price is None or entry_price <= 0: continue
            
            # 获取监控期的完整5分钟数据
            bars = get_5min_bars(code, date, end_date)
            if bars is None or len(bars) < 3: continue
            
            # 条件单模拟
            result = simulate_tp_sl(bars, date, entry_price, 5.0, 4.0, 3)
            if result is None: continue
            
            gap_pct = get_gap(bars, date, entry_price) or 0
            
            trade = {'date':date, 'code':code, 'name':name, 'score':pick['score'],
                     'entry_price':entry_price, 'hit_type':result['hit_type'],
                     'return_pct':result['return_pct'], 'exit_date':result['exit_date'],
                     'gap_pct':gap_pct}
            all_trades.append(trade)
            gaps.append(gap_pct)
        
        if (di+1)%5==0 or di<3:
            for t in all_trades[-len(picks):]:
                e = '🟢' if t['hit_type']=='TP' else ('🔴' if t['hit_type']=='SL' else '⚪')
                print(f"  {e} {t['name']:6s}({t['code']}) 分{t['score']:.0f} 买{t['entry_price']:.2f}→{t['hit_type']} {t['return_pct']:+.2f}% 跳空{t['gap_pct']:+.2f}%")
    
    bs.logout()
    
    # ===== 报告 =====
    elapsed = time.time() - t0
    print(f"\n{'='*72}")
    print(f"  📊 v3.0全真实回测报告")
    print(f"{'='*72}")
    
    if not all_trades: print("\n  无交易"); return
    
    total = len(all_trades)
    tp = sum(1 for t in all_trades if t['hit_type']=='TP')
    sl = sum(1 for t in all_trades if t['hit_type']=='SL')
    hold = sum(1 for t in all_trades if t['hit_type']=='HOLD')
    total_ret = sum(t['return_pct'] for t in all_trades)
    wins = sum(1 for t in all_trades if t['return_pct']>0)
    
    print(f"\n  总交易: {total}笔 | 天数: {len(backtest_dates)}")
    print(f"  🟢 TP: {tp}({tp/total*100:.1f}%) | 🔴 SL: {sl}({sl/total*100:.1f}%) | ⚪ HOLD: {hold}({hold/total*100:.1f}%)")
    print(f"  🏆 胜率: {wins}/{total} = {wins/total*100:.1f}%")
    print(f"  💰 总收益: {total_ret:+.2f}% | 均每笔: {total_ret/total:+.2f}% | 耗时: {elapsed:.0f}s")
    
    ga = np.array(gaps)
    neg = sum(1 for g in gaps if g<0)
    print(f"\n  📉 隔夜跳空: 平均{np.mean(ga):+.2f}% 低开{neg}/{len(gaps)}={neg/len(gaps)*100:.1f}%")
    bad = [t for t in all_trades if t['gap_pct']<-1.5]
    if bad: print(f"    跳空<-1.5%的{len(bad)}笔均收益: {np.mean([t['return_pct'] for t in bad]):.2f}%")
    
    pairs = [(t['gap_pct'],t['return_pct']) for t in all_trades]
    corr = np.corrcoef([p[0] for p in pairs],[p[1] for p in pairs])[0,1]
    print(f"    跳空vs收益相关系数: {corr:.3f}")
    print(f"\n  ✅ 总收益: {total_ret:+.2f}%")
    
    with open(os.path.join(DATA_DIR,'backtest_v30_5min_bs.json'),'w') as f:
        json.dump({'total':total,'tp':tp,'sl':sl,'hold':hold,'total_return':round(total_ret,2),
                   'win_rate':round(wins/total*100,1),'trades':all_trades}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 已保存")
    sys.stdout.flush()

if __name__ == '__main__':
    run_backtest()
