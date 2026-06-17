#!/usr/bin/env python3
"""v10.0 — 每日Top N精选模式"""
import sys, numpy as np
sys.path.insert(0, '.')
from backtest_full import load_all_klines, get_trading_dates, collect_market_snapshot, build_hist_klines, quick_analyze
from backtest_v70 import score_v70
from backtest_v80 import score_v80

all_klines = load_all_klines()
dates = get_trading_dates(all_klines, 80)
dates = sorted(dates)[-70:]
backtest_dates = dates[-60:]

for score_name, score_fn in [('v7.0规则分层', score_v70), ('v8.0三力共振', score_v80)]:
    print(f"\n  {score_name} — 每日TopN测试")
    for top_n in [3, 5, 7, 10]:
        all_picks = []
        recent_codes = set()
        for date in backtest_dates:
            snapshot = collect_market_snapshot(all_klines, date)
            if not snapshot: continue
            day_candidates = []
            for s in snapshot[:300]:
                code = s['code']
                if code in recent_codes: continue
                hist = build_hist_klines(all_klines, code, date)
                if len(hist) < 30: continue
                ind = quick_analyze(hist)
                if not ind: continue
                score, _ = score_fn(ind, s['change'], 0)
                if score <= 0: continue
                info = all_klines.get(code, {})
                all_k = info.get('klines', [])
                idx = -1
                for i,k in enumerate(all_k):
                    if isinstance(k,list) and k[0]==date: idx=i; break
                if idx<0 or idx>=len(all_k)-1: continue
                t1 = (float(all_k[idx+1][2])/s['price']-1)*100
                day_candidates.append({'code':code,'score':score,'t1':t1})
            day_candidates.sort(key=lambda x: -x['score'])
            top = day_candidates[:top_n]
            all_picks.extend(top)
            recent_codes.update(p['code'] for p in top[:3])
        if not all_picks: continue
        t1s = [p['t1'] for p in all_picks]
        wins = sum(1 for x in t1s if x>0)
        stops = sum(1 for x in t1s if x<=-6)
        mk = '🏆' if wins/len(t1s)*100>=55 else ' '
        print(f"    Top{top_n:>2}: {len(t1s):>3d}只 {mk}胜率{wins/len(t1s)*100:>4.1f}% 均{np.mean(t1s):>+6.2f}% 止{stops/len(t1s)*100:>4.1f}%")
