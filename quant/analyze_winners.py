#!/usr/bin/env python3
"""分析v7.0赢家和输家的因子差异，优化规则"""
import json, sys, numpy as np
sys.path.insert(0, '.')
from backtest_full import load_all_klines, get_trading_dates, collect_market_snapshot, build_hist_klines, quick_analyze
from backtest_v70 import score_v70

all_klines = load_all_klines()
dates = get_trading_dates(all_klines, 80)
dates = sorted(dates)[-70:]
backtest_dates = dates[-60:]

winners = []
losers = []

for date in backtest_dates:
    snapshot = collect_market_snapshot(all_klines, date)
    if not snapshot: continue
    for s in snapshot[:200]:
        hist = build_hist_klines(all_klines, s['code'], date)
        if len(hist) < 30: continue
        ind = quick_analyze(hist)
        if not ind: continue
        score, factors = score_v70(ind, s['change'], 0)
        if score < 30: continue
        
        info = all_klines.get(s['code'], {})
        all_k = info.get('klines', [])
        idx = -1
        for i,k in enumerate(all_k):
            if isinstance(k,list) and k[0]==date: idx=i; break
        if idx<0 or idx>=len(all_k)-1: continue
        t1_close = float(all_k[idx+1][2])
        t1_return = (t1_close - s['price'])/s['price']
        
        entry = {
            'code': s['code'], 'name': s['name'], 'score': score,
            't1': t1_return, 'change': s['change'],
            'price': s['price'],
            'ma5': ind['ma5'], 'ma10': ind['ma10'], 'ma20': ind['ma20'],
            'rsi14': ind['rsi14'], 'vol_ratio': ind['vol_ratio'],
            'trend_score': ind['trend_score'],
            'boll_pos': ind.get('boll_pos', 50),
            'breakout': ind.get('breakout', 50),
            'mom5': ind['mom_5d'],
            'mom10': ind['mom_10d'],
            'ma_convergence': ind.get('ma_convergence', 999),
            'vol_momentum': ind.get('vol_momentum', 1),
        }
        if t1_return > 0: winners.append(entry)
        else: losers.append(entry)

print(f"获胜: {len(winners)} | 失败: {len(losers)}")

# 分析关键指标差异
def avg(lst, key): return np.mean([l[key] for l in lst if np.isfinite(l[key])])

keys = ['score','change','ma5','ma10','ma20','rsi14','vol_ratio','trend_score',
        'boll_pos','breakout','mom5','mom10','ma_convergence','vol_momentum','price']
print(f"\n{'指标':<20s} {'获胜均值':>10} {'失败均值':>10} {'差值':>10} {'说明':>10}")
print(f"{'-'*60}")
for k in keys:
    w = avg(winners, k)
    l = avg(losers, k)
    diff = w - l
    note = ''
    if abs(diff) > 0.05 * abs(l) if abs(l) > 0.01 else abs(diff) > 0.01:
        note = '***' if abs(diff/l) > 0.1 else '**'
    print(f"{k:<20s} {w:>+10.4f} {l:>+10.4f} {diff:>+10.4f} {note:>10}")

# 胜者vs败者的关键差异
print(f"\n关键发现:")
for k in keys:
    w = avg(winners, k)
    l = avg(losers, k)
    if abs(w-l) > 0.05 * abs(l) if abs(l) > 0.01 else abs(w-l) > 0.02:
        print(f"  {k}: 胜={w:.3f} 败={l:.3f} 差={w-l:+.3f} {'← 胜者更高' if w>l else '→ 败者更高'}")

# 最佳规则建议
print(f"\n基于分析的优化建议:")
print(f"  建议1: 降低 rsi14 下限（胜者{rsi_w} vs 败者{rsi_l}）")
print(f"  建议2: 对 ma_convergence 加更严格限制")
print(f"  建议3: 增加 volume_change 因子")
