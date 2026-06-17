#!/usr/bin/env python3
"""验证横截面排名，排除重复影响，测不同因子组合"""
import numpy as np
from cross_sectional_ranking import load_all, get_dates, per_day_factor_values, cross_sectional_rank

all_klines = load_all()
dates = get_dates(all_klines)
raw_factors, t1_returns, stock_hist = per_day_factor_values(all_klines, dates)

# 排名因子
ranked_factors = {}
factor_names = list(list(raw_factors.values())[0].keys())
for fn in factor_names:
    ranked_factors[fn] = cross_sectional_rank({d: raw_factors[d][fn] for d in raw_factors if fn in raw_factors[d]})

test_dates = sorted(raw_factors.keys())

# 测试不同因子组合
combos = [
    (['gap', 'rsi', 'ma5_deviation', 'mom_5d', 'amplitude'], '5因子'),
    (['gap', 'rsi', 'ma5_deviation'], '3因子核心'),
    (['gap', 'rsi', 'ma5_deviation', 'vol_ratio', 'price_position'], '5因子另类'),
    (['gap', 'rsi', 'mom_5d'], '3因子动量'),
    (['rsi', 'ma5_deviation', 'amplitude', 'mom_5d'], '4因子无gap'),
]

for factor_list, name in combos:
    print(f"\n  {name} ({','.join(factor_list)})")
    print(f"  {'选前%':>6} {'总样本':>6} {'去重':>6} {'T+1胜率':>8} {'均收益':>8} {'止损率':>7}")
    print(f"  {'-'*48}")
    
    for select_pct in [0.05, 0.08, 0.10, 0.15]:
        all_picks = []
        seen_codes = set()
        unique_picks = []
        
        for date in test_dates:
            if date not in t1_returns: continue
            if not all(fn in ranked_factors and date in ranked_factors[fn] for fn in factor_list):
                continue
            
            code_list = set(ranked_factors[factor_list[0]][date].keys())
            for fn in factor_list[1:]:
                code_list &= set(ranked_factors[fn][date].keys())
            if not code_list: continue
            
            scores = {}
            for code in code_list:
                avg_rank = np.mean([ranked_factors[fn][date].get(code, 0.5) for fn in factor_list])
                scores[code] = avg_rank
            
            sorted_codes = sorted(scores.keys(), key=lambda c: -scores[c])
            n_select = max(1, int(len(sorted_codes) * select_pct))
            selected = sorted_codes[:min(n_select, 10)]
            
            for code in selected:
                if code in t1_returns[date]:
                    all_picks.append(t1_returns[date][code])
                    if code not in seen_codes:
                        seen_codes.add(code)
                        unique_picks.append(t1_returns[date][code])
        
        if not all_picks: continue
        all_arr = np.array(all_picks)
        uniq_arr = np.array(unique_picks)
        
        aw = np.mean(all_arr > 0) * 100
        uw = np.mean(uniq_arr > 0) * 100 if len(uniq_arr) > 0 else 0
        am = np.mean(all_arr) * 100
        astop = np.mean(all_arr <= -0.06) * 100
        
        mk = '🏆' if aw >= 55 else ('✅' if aw >= 50 else ' ')
        print(f"  {select_pct*100:>5.0f}%: {len(all_arr):>6d} {len(uniq_arr):>6d} {mk}{aw:>6.1f}% {am:+>+7.2f}% {astop:>6.1f}%")
