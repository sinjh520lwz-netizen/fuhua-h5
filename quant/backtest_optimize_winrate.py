#!/usr/bin/env python3
"""阈值调优 — 找胜率最高的参数组合"""
import json, os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_full import load_all_klines, get_trading_dates, collect_market_snapshot, build_hist_klines, quick_analyze
from screener import score_early_entry
from backtest_v31c import score_v31c
from backtest_v50 import score_v50

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def run_backtest(all_klines, trading_dates, score_fn, threshold, version_name, top_n=200):
    """简化回测：只统计胜率和止损率"""
    backtest_dates = trading_dates[-60:]
    all_results = []
    recent_codes = set()

    for date in backtest_dates:
        snapshot = collect_market_snapshot(all_klines, date)
        if not snapshot:
            continue
        top_stocks = snapshot[:top_n]
        day_picks = []
        for s in top_stocks:
            code = s['code']
            if code in recent_codes:
                continue
            hist = build_hist_klines(all_klines, code, date)
            if len(hist) < 30:
                continue
            ind = quick_analyze(hist)
            if not ind:
                continue
            score, factors = score_fn(ind, s['change'], 0)
            if score < threshold:
                continue

            # 获取未来收益
            info = all_klines.get(code, {})
            all_k = info.get('klines', [])
            idx = -1
            for i, k in enumerate(all_k):
                if isinstance(k, list) and k[0] == date:
                    idx = i
                    break
            if idx < 0:
                continue
            
            future = all_k[idx+1:]
            day_close = s['price']
            
            result = {'date': date, 'code': code, 'name': s['name'], 'score': round(score, 1), 'entry_price': s['price']}
            if len(future) >= 1:
                f1 = float(future[0][2])
                result['t1'] = round((f1/day_close - 1)*100, 2)
            if len(future) >= 3:
                f3 = float(future[2][2])
                result['t3'] = round((f3/day_close - 1)*100, 2)
            if len(future) >= 5:
                f5 = float(future[4][2])
                result['t5'] = round((f5/day_close - 1)*100, 2)
            
            daily_returns = []
            for k in future[:15]:
                kc = float(k[2]) if isinstance(k, list) else 0
                daily_returns.append(round((kc/day_close - 1)*100, 2))
            result['daily'] = daily_returns
            
            day_picks.append(result)

        day_picks.sort(key=lambda x: x['score'], reverse=True)
        if day_picks:
            recent_codes.update(p['code'] for p in day_picks[:3])
            if len(day_picks) > 5:
                day_picks = day_picks[:5]
        all_results.extend(day_picks)

    # 统计
    n = len(all_results)
    if n == 0:
        return {'n':0, 't1_w':0, 't1_n':0, 't3_w':0, 't3_n':0, 't5_w':0, 't5_n':0, 'stops':0, 'amax':0, 't1_mean':0, 't3_mean':0}
    
    t1 = [r['t1'] for r in all_results if 't1' in r]
    t3 = [r['t3'] for r in all_results if 't3' in r]
    t5 = [r['t5'] for r in all_results if 't5' in r]
    stops = sum(1 for r in all_results if any(d <= -6 for d in r.get('daily', [])))
    amax = np.mean([max(r.get('daily', [0])) for r in all_results]) if all_results else 0
    
    return {
        'n': n,
        't1_w': sum(1 for x in t1 if x>0), 't1_n': len(t1), 't1_wr': sum(1 for x in t1 if x>0)/len(t1)*100 if t1 else 0, 't1_m': np.mean(t1) if t1 else 0,
        't3_w': sum(1 for x in t3 if x>0), 't3_n': len(t3), 't3_wr': sum(1 for x in t3 if x>0)/len(t3)*100 if t3 else 0, 't3_m': np.mean(t3) if t3 else 0,
        't5_w': sum(1 for x in t5 if x>0), 't5_n': len(t5), 't5_wr': sum(1 for x in t5 if x>0)/len(t5)*100 if t5 else 0, 't5_m': np.mean(t5) if t5 else 0,
        'stops': stops, 'stop_rate': stops/n*100 if n else 0, 'amax': amax,
    }

# ===== 主流程 =====
print("加载全A股K线数据...")
all_klines = load_all_klines()
print(f"  已加载 {len(all_klines)}只股票")
trading_dates = get_trading_dates(all_klines, 80)
print(f"  交易日: {trading_dates[0]} ~ {trading_dates[-1]} ({len(trading_dates)}天)")
print()

strategies = [
    ('v5.0(均线粘合)', score_v50, [25, 30, 35, 40, 45, 50]),
    ('v4.0(趋势反转)', score_early_entry, [30, 35, 40, 45, 50, 55, 60]),
    ('v3.1c(趋势过滤)', score_v31c, [20, 25, 30, 35, 40]),
]

for name, score_fn, thresholds in strategies:
    print(f"{'='*70}")
    print(f"  {name} — 阈值扫描")
    print(f"{'='*70}")
    print(f"  {'阈值':>5} {'样本':>5} {'T+1胜率':>10} {'T+1均':>8} {'T+3胜率':>10} {'T+3均':>8} {'T+5胜率':>10} {'止损率':>8} {'均最高':>8}")
    print(f"  {'-'*5} {'-'*5} {'-'*10} {'-'*8} {'-'*10} {'-'*8} {'-'*10} {'-'*8} {'-'*8}")
    
    best_t1_wr, best_t3_wr = 0, 0
    best_t1_th, best_t3_th = None, None
    
    for th in thresholds:
        r = run_backtest(all_klines, trading_dates, score_fn, th, name)
        if r['n'] == 0:
            print(f"  {th:>5} {'无推荐':>5}")
            continue
        print(f"  {th:>5} {r['n']:>5} {r['t1_wr']:>8.1f}% {r['t1_m']:>+7.2f}% {r['t3_wr']:>8.1f}% {r['t3_m']:>+7.2f}% {r['t5_wr']:>8.1f}% {r['stop_rate']:>7.1f}% {r['amax']:>+7.2f}%")
        if r['t1_wr'] > best_t1_wr:
            best_t1_wr, best_t1_th = r['t1_wr'], th
        if r['t3_wr'] > best_t3_wr:
            best_t3_wr, best_t3_th = r['t3_wr'], th
    
    print(f"  {'-'*70}")
    print(f"  T+1最优: 阈值{best_t1_th} → 胜率{best_t1_wr:.1f}%")
    print(f"  T+3最优: 阈值{best_t3_th} → 胜率{best_t3_wr:.1f}%")
    print()
