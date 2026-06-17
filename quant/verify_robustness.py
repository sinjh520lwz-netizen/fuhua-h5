#!/usr/bin/env python3
"""验证横截面排名：严格样本外测试 + 因子寻优"""
import numpy as np
from cross_sectional_ranking import load_all, get_dates, per_day_factor_values, cross_sectional_rank

all_klines = load_all()
dates = get_dates(all_klines)
raw_factors, t1_returns, stock_hist = per_day_factor_values(all_klines, dates)

# 排名
ranked_factors = {}
factor_names = list(list(raw_factors.values())[0].keys())
for fn in factor_names:
    rf = {d: raw_factors[d][fn] for d in raw_factors if fn in raw_factors[d]}
    ranked_factors[fn] = cross_sectional_rank(rf)

test_dates = sorted(raw_factors.keys())

# ===== 1. 严格样本外测试 =====
# 前40天训练(找最佳参数) → 后20天测试
split_idx = len(test_dates) * 2 // 3
train_dates = test_dates[:split_idx]
test_dates_out = test_dates[split_idx:]

print(f"样本内: {train_dates[0]}~{train_dates[-1]} ({len(train_dates)}天)")
print(f"样本外: {test_dates_out[0]}~{test_dates_out[-1]} ({len(test_dates_out)}天)")

# 测试不同的因子组合和选择比例
all_combos = [
    (['gap', 'rsi', 'ma5_deviation'], 'gap+rsi+ma5'),
    (['gap', 'rsi', 'mom_5d'], 'gap+rsi+mom5'),
    (['gap', 'rsi', 'ma5_deviation', 'mom_5d'], '4因子'),
    (['gap', 'rsi', 'amplitude'], 'gap+rsi+amp'),
    (['rsi', 'ma5_deviation', 'mom_5d'], '3因子无gap'),
    (['gap', 'ma5_deviation', 'mom_5d'], 'gap+ma5+mom5'),
]

print(f"\n{'='*75}")
print(f"  样本外测试（关键验证！）")
print(f"{'='*75}")
print(f"  {'组合':<22s} {'选前%':>5} {'样本内胜率':>10} {'样本外胜率':>10} {'均收益':>8} {'止损':>6}")
print(f"  {'-'*61}")

for factor_list, name in all_combos:
    for select_pct_v in [0.08, 0.12, 0.15, 0.20]:
        all_train, all_test = [], []
        
        for is_test, date_set, result_list in [
            (False, train_dates, all_train), (True, test_dates_out, all_test)]:
            
            for date in date_set:
                if date not in t1_returns: continue
                if not all(fn in ranked_factors and date in ranked_factors[fn] for fn in factor_list):
                    continue
                
                codes = set(ranked_factors[factor_list[0]][date].keys())
                for fn in factor_list[1:]:
                    codes &= set(ranked_factors[fn][date].keys())
                if not codes: continue
                
                scores = {}
                for code in codes:
                    ranks = [ranked_factors[fn][date].get(code, 0.5) for fn in factor_list]
                    scores[code] = np.mean(ranks)
                
                sorted_codes = sorted(scores.keys(), key=lambda c: -scores[c])
                n = max(1, int(len(sorted_codes) * select_pct_v))
                selected = sorted_codes[:min(n, 15)]
                
                for code in selected:
                    if code in t1_returns[date]:
                        result_list.append(t1_returns[date][code])
        
        train_arr = np.array(all_train)
        test_arr = np.array(all_test)
        
        if len(test_arr) < 50: continue
        
        train_w = np.mean(train_arr > 0)*100 if len(train_arr)>0 else 0
        test_w = np.mean(test_arr > 0)*100
        test_m = np.mean(test_arr)*100
        test_s = np.mean(test_arr <= -0.06)*100
        
        mk = '🏆' if test_w >= 55 else ('✅' if test_w >= 50 else ' ')
        print(f"  {name:<22s} {select_pct_v*100:>4.0f}%: {train_w:>8.1f}% {test_w:>10.1f}% {test_m:+>+7.2f}% {test_s:>5.1f}% {mk}")

# ===== 2. 最佳组合详细测试 =====
print(f"\n{'='*75}")
print(f"  最佳组合(gap+rsi+ma5) 全周期详细报告")
print(f"{'='*75}")

factor_list = ['gap', 'rsi', 'ma5_deviation']
for select_pct_v in [0.05, 0.08, 0.10, 0.12, 0.15]:
    all_picks, seen, unique = [], set(), []
    
    for date in test_dates:
        if date not in t1_returns: continue
        if not all(fn in ranked_factors and date in ranked_factors[fn] for fn in factor_list):
            continue
        codes = set(ranked_factors[factor_list[0]][date].keys())
        for fn in factor_list[1:]:
            codes &= set(ranked_factors[fn][date].keys())
        if not codes: continue
        
        scores = {c: np.mean([ranked_factors[fn][date].get(c, 0.5) for fn in factor_list]) for c in codes}
        sorted_codes = sorted(scores.keys(), key=lambda c: -scores[c])
        n = max(1, int(len(sorted_codes) * select_pct_v))
        selected = sorted_codes[:min(n, 15)]
        
        for code in selected:
            if code in t1_returns[date]:
                all_picks.append(t1_returns[date][code])
                if code not in seen:
                    seen.add(code)
                    unique.append(t1_returns[date][code])
    
    if not all_picks: continue
    a, u = np.array(all_picks), np.array(unique)
    aw, uw = np.mean(a>0)*100, np.mean(u>0)*100
    am, um = np.mean(a)*100, np.mean(u)*100
    astop = np.mean(a<=-0.06)*100
    
    print(f"\n  选前{select_pct_v*100:.0f}%:")
    print(f"    全部: {len(a):>4d}次  胜率{aw:>5.1f}%  均{am:+>+6.2f}%  止损{astop:>4.1f}%")
    print(f"    去重: {len(u):>4d}只  胜率{uw:>5.1f}%  均{um:+>+6.2f}%")
    
    # 按分数段看胜率
    print(f"    按排名分:")
    all_scores = []
    for date in test_dates:
        if date not in t1_returns: continue
        if not all(fn in ranked_factors and date in ranked_factors[fn] for fn in factor_list): continue
        codes = set(ranked_factors[factor_list[0]][date].keys())
        for fn in factor_list[1:]:
            codes &= set(ranked_factors[fn][date].keys())
        for code in codes:
            avg = np.mean([ranked_factors[fn][date].get(code, 0.5) for fn in factor_list])
            all_scores.append((avg, t1_returns[date][code] if code in t1_returns[date] else 0))
    
    all_scores.sort(key=lambda x: -x[0])
    for pct in [5, 10, 15, 20]:
        n = max(10, len(all_scores)*pct//100)
        top = all_scores[:n]
        wr = sum(1 for _, r in top if r>0)/len(top)*100
        mr = np.mean([r for _, r in top])*100
        print(f"      Top{pct}%: {n:>4d}只  胜率{wr:>5.1f}%  均{mr:+>+6.2f}%")
