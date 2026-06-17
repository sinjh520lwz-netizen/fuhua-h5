#!/usr/bin/env python3
"""
JH 真实5分钟条件单回测 v2.1 — 当天评分当天买入
====================================================
修正逻辑：
  ① D日14:30~14:55 → 用D日当天数据评分
  ② 评分完立即以14:55收盘价买入
  ③ D~D+2 监控条件单
"""
import json, os, sys, time, math, re
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

# 日志输出到文件
log_file = '/tmp/bt21_out.log'
sys.stdout = open(log_file, 'w', buffering=1)
sys.stderr = sys.stdout

# Baostock兼容
def _append_compat(self, other, ignore_index=False, **kwargs):
    return pd.concat([self, pd.DataFrame(other) if not isinstance(other, pd.DataFrame) else other], ignore_index=ignore_index)
if not hasattr(pd.DataFrame, 'append'): pd.DataFrame.append = _append_compat
import baostock as bs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze
from cross_sectional_score import score_early_entry
from backtest_full import build_hist_klines

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────

def code_to_bs(code):
    code = str(code).strip().zfill(6)
    if code.startswith('6'): return f'sh.{code}'
    elif code.startswith(('0','3')): return f'sz.{code}'
    elif code.startswith(('4','8')): return f'bj.{code}'
    return f'sh.{code}'

# ──────────────────────────────────────────────
# 5分钟K线（Baostock + 缓存）
# ──────────────────────────────────────────────

_cache_5min = {}

def get_5min_bars(code, start_date, end_date):
    cache_key = f'{code}|{start_date}|{end_date}'
    if cache_key in _cache_5min:
        return _cache_5min[cache_key]
    bs_code = code_to_bs(code)
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,time,open,high,low,close,volume,amount",
            start_date=start_date, end_date=end_date,
            frequency='5', adjustflag='2'
        )
        if rs.error_code != '0': return None
        dl = []
        while rs.next(): dl.append(rs.get_row_data())
        if not dl: return None
        df = pd.DataFrame(dl, columns=rs.fields)
        for col in ['open','high','low','close','volume','amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        _cache_5min[cache_key] = df
        return df
    except:
        return None

def get_entry_at_1455(bars_5min, buy_date):
    """获取14:55收盘价作为买入价"""
    day_bars = bars_5min[bars_5min['date'] == buy_date]
    if len(day_bars) == 0: return None
    pm_bars = day_bars[day_bars['time'].str[8:12] == '1455']
    if len(pm_bars) > 0: return float(pm_bars.iloc[0]['close'])
    # 没有14:55就用最后一根
    return float(day_bars.iloc[-1]['close'])

def get_gap_info(bars_5min, buy_date, entry_price):
    """计算隔夜跳空: 买入日后第一根K线开盘价"""
    dates = sorted(bars_5min['date'].unique())
    buy_idx = -1
    for i, d in enumerate(dates):
        if d >= buy_date: buy_idx = i; break
    if buy_idx < 0 or buy_idx >= len(dates) - 1: return None, None
    next_date = dates[buy_idx + 1]
    next_bars = bars_5min[bars_5min['date'] == next_date]
    if len(next_bars) == 0: return None, None
    first_bar = next_bars.iloc[0]
    open_next = float(first_bar['open'])
    gap_pct = round((open_next / entry_price - 1) * 100, 2)
    return gap_pct, next_date

def simulate_tp_sl(bars_5min, buy_date, entry_price, tp_pct=5.0, sl_pct=4.0, max_hold_days=3):
    """条件单模拟 — 从买入日14:55之后开始监控"""
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    all_bars = bars_5min.sort_values(['date', 'time'])
    dates = sorted(all_bars['date'].unique())
    
    buy_idx = -1
    for i, d in enumerate(dates):
        if d >= buy_date: buy_idx = i; break
    if buy_idx < 0: return None
    
    end_idx = min(buy_idx + max_hold_days, len(dates))
    monitor_dates = dates[buy_idx:end_idx]
    if len(monitor_dates) == 0: return None
    
    monitor_bars = all_bars[all_bars['date'].isin(monitor_dates)]
    
    for _, bar in monitor_bars.iterrows():
        bar_date = bar['date']; bar_time = bar['time']
        bar_high = float(bar['high']); bar_low = float(bar['low'])
        
        # 买入日：只监控14:55之后
        if bar_date == buy_date and bar_time[8:12] <= '1455':
            continue
        
        if bar_high >= tp_price:
            return {'hit_type': 'TP', 'entry_price': entry_price, 'exit_price': tp_price,
                    'return_pct': tp_pct, 'exit_date': bar_date}
        if bar_low <= sl_price:
            return {'hit_type': 'SL', 'entry_price': entry_price, 'exit_price': sl_price,
                    'return_pct': -sl_pct, 'exit_date': bar_date}
    
    # 没触发，最后一个交易日最后一根K线平仓
    last_date = monitor_dates[-1]
    last_bars = all_bars[all_bars['date'] == last_date]
    if len(last_bars) > 0:
        exit_price = float(last_bars.iloc[-1]['close'])
        ret = round((exit_price / entry_price - 1) * 100, 2)
        return {'hit_type': 'HOLD', 'entry_price': entry_price, 'exit_price': exit_price,
                'return_pct': ret, 'exit_date': last_date}
    return None

# ──────────────────────────────────────────────
# 日线评分
# ──────────────────────────────────────────────

def load_daily_data():
    fpath = os.path.join(DATA_DIR, 'all_klines_60d.json')
    with open(fpath) as f: return json.load(f)

def get_trading_dates(all_klines):
    from collections import Counter
    dc = Counter()
    for code, info in all_klines.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6: dc[k[0]] += 1
    return sorted([d for d, c in dc.items() if c >= 2000])[-253:]

def score_stocks_on_date(all_klines, date, max_stocks=80, top_n=5):
    """当天评分"""
    # 取成交额前N
    snapshot = []
    for code, info in all_klines.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6 and k[0] == date:
                close = float(k[2]); o = float(k[1]); vol = float(k[5])
                snapshot.append({'code':code, 'name':info.get('name',''), 'price':close,
                                'change':(close/o-1)*100 if o>0 else 0,
                                'amount':close*vol/10000})
                break
    snapshot.sort(key=lambda x: -x['amount'])
    top = snapshot[:max_stocks]
    
    candidates = []
    for s in top:
        code = s['code']
        if code.startswith(('688','689','300','301','4','8')) or code.startswith('920'):
            continue
        hist = build_hist_klines(all_klines, code, date)
        if len(hist) < 30: continue
        ind = quick_analyze(hist)
        if not ind: continue
        score, _ = score_early_entry(ind, s['change'], 0)
        if score >= 6:
            candidates.append(s | {'score': score})
        if len(candidates) >= top_n: break
    
    candidates.sort(key=lambda x: -x['score'])
    return candidates[:top_n]

# ──────────────────────────────────────────────
# 主回测
# ──────────────────────────────────────────────

def run_backtest():
    print("=" * 72)
    print("  JH 5分钟条件单回测 v2.1")
    print("  Baostock | 当天评分当天买入 | TP+5%/SL-4%/T+3")
    print("=" * 72)
    
    print("\n📦 加载日线数据...")
    all_klines = load_daily_data()
    print(f"  ✓ {len(all_klines)}只股票")
    
    trading_dates = get_trading_dates(all_klines)
    print(f"  ✓ {len(trading_dates)}个交易日: {trading_dates[-1]} ~ {trading_dates[0]}")
    
    print("\n🔌 连接Baostock...")
    lg = bs.login()
    if lg.error_code != '0': print(f"  ❌ {lg.error_msg}"); return
    print(f"  ✓ 登录成功")
    
    # 回测区间：跳过前5天（无历史数据评分）和后3天（无未来数据监控）
    # 用D日数据评分，当天买入，监控到D+2
    # 所以D日期最晚不能超过最后第3个交易日
    backtest_dates = trading_dates[5:-3][-120:]
    print(f"\n📅 回测: {backtest_dates[-1]} ~ {backtest_dates[0]}, 共{len(backtest_dates)}天")
    
    all_trades = []
    gap_records = []
    t_start = time.time()
    
    for di, date in enumerate(backtest_dates):
        if (di+1) % 20 == 0:
            print(f"\n📅 [{di+1}/{len(backtest_dates)}] {date} ...")
        
        # 评分（用D日数据）
        picks = score_stocks_on_date(all_klines, date, top_n=5)
        if not picks: continue
        
        # 当天14:55买入
        buy_date = date
        
        # 找到监控截止日（D+2）
        date_idx = trading_dates.index(date)
        hold_end_idx = min(date_idx + 3, len(trading_dates) - 1)
        end_date = trading_dates[hold_end_idx]
        
        for pick in picks:
            code = pick['code']; name = pick['name']; score = pick['score']
            
            # 获取5分钟数据（买入日~持有结束）
            bars = get_5min_bars(code, buy_date, end_date)
            if bars is None or len(bars) < 3: continue
            
            # 14:55买入
            entry_price = get_entry_at_1455(bars, buy_date)
            if entry_price is None or entry_price <= 0: continue
            
            # 条件单模拟
            result = simulate_tp_sl(bars, buy_date, entry_price, tp_pct=5.0, sl_pct=4.0, max_hold_days=3)
            if result is None: continue
            
            # 隔夜跳空
            gap_pct, gap_date = get_gap_info(bars, buy_date, entry_price)
            
            trade = {
                'date': date, 'code': code, 'name': name, 'score': score,
                'entry_price': entry_price, 'hit_type': result['hit_type'],
                'return_pct': result['return_pct'], 'exit_date': result['exit_date'],
                'gap_pct': gap_pct if gap_pct is not None else 0,
            }
            all_trades.append(trade)
            if gap_pct is not None: gap_records.append(gap_pct)
        
        # 显示
        if (di+1) % 20 == 0 or di < 3:
            for t in all_trades[-len(picks):]:
                emoji = '🟢' if t['hit_type']=='TP' else ('🔴' if t['hit_type']=='SL' else '⚪')
                gap_str = f' 跳空{t["gap_pct"]:+.2f}%'
                print(f"  {emoji} {t['name']:6s}({t['code']}) 分{t['score']:.0f} 买{t['entry_price']:.2f}→{t['hit_type']} {t['return_pct']:+.2f}%{gap_str}")
        
        # 清理缓存
        if len(_cache_5min) > 100: _cache_5min.clear()
    
    bs.logout()
    
    # ===== 报告 =====
    elapsed = time.time() - t_start
    print(f"\n{'=' * 72}")
    print(f"  📊 回测报告 v2.1 — 当天评分当天买入")
    print(f"{'=' * 72}")
    
    if not all_trades: print("\n  无交易"); return
    
    total = len(all_trades)
    tp = sum(1 for t in all_trades if t['hit_type']=='TP')
    sl = sum(1 for t in all_trades if t['hit_type']=='SL')
    hold = sum(1 for t in all_trades if t['hit_type']=='HOLD')
    total_ret = sum(t['return_pct'] for t in all_trades)
    avg_ret = total_ret / total
    wins = sum(1 for t in all_trades if t['return_pct'] > 0)
    
    print(f"\n  总交易: {total}笔")
    print(f"  🟢 TP: {tp}笔 = {tp/total*100:.1f}%")
    print(f"  🔴 SL: {sl}笔 = {sl/total*100:.1f}%")
    print(f"  ⚪ HOLD: {hold}笔 = {hold/total*100:.1f}%")
    print(f"  🏆 胜率: {wins}/{total} = {wins/total*100:.1f}%")
    print(f"  💰 总收益: {total_ret:+.2f}%")
    print(f"     均每笔: {avg_ret:+.2f}%")
    print(f"     耗时: {elapsed:.0f}s")
    
    if gap_records:
        ga = np.array(gap_records)
        neg = sum(1 for g in gap_records if g < 0)
        print(f"\n  📉 隔夜跳空 (买入后次日开盘)")
        print(f"    平均: {np.mean(ga):+.2f}% | 中位: {np.median(ga):+.2f}%")
        print(f"    低开: {neg}/{len(gap_records)} = {neg/len(gap_records)*100:.1f}%")
        bad = sum(1 for g in gap_records if g < -2)
        print(f"    大幅低开(<-2%): {bad}笔 = {bad/len(gap_records)*100:.1f}%")
        # 跳空<-1.5%的交易
        bad_trades = [t for t in all_trades if t.get('gap_pct',0) < -1.5]
        if bad_trades:
            print(f"    跳空<-1.5%的{len(bad_trades)}笔均收益: {np.mean([t['return_pct'] for t in bad_trades]):.2f}%")
        # 相关性
        pairs = [(t['gap_pct'], t['return_pct']) for t in all_trades]
        corr = np.corrcoef([p[0] for p in pairs], [p[1] for p in pairs])[0,1]
        print(f"    跳空vs收益相关系数: {corr:.3f}")
    
    # 判定
    print(f"\n  {'✅' if total_ret > 0 else '❌'} 策略判定: {'盈利' if total_ret > 0 else '亏损'} ({total_ret:+.2f}%)")
    
    # 保存
    result_file = os.path.join(DATA_DIR, 'backtest_v21_5min_bs.json')
    with open(result_file, 'w') as f:
        json.dump({
            'total': total, 'tp': tp, 'sl': sl, 'hold': hold,
            'total_return': round(total_ret,2), 'avg_return': round(avg_ret,2),
            'win_rate': round(wins/total*100, 1),
            'gap_mean': round(float(np.mean(gap_records)), 2) if gap_records else 0,
            'gap_neg_rate': round(neg/len(gap_records)*100, 1) if gap_records else 0,
            'trades': all_trades,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 已保存: {result_file}")
    print(f"\n  ⏰ 完成: {datetime.now().strftime('%H:%M:%S')}")
    sys.stdout.flush()

if __name__ == '__main__':
    run_backtest()
