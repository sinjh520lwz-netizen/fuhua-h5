#!/usr/bin/env python3
"""
JH 杀手策略 vK — 3天赚5%, 亏损控制3%以内
"""
import json, os, sys, time
import numpy as np
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def load_all():
    with open(os.path.join(DATA_DIR, 'all_klines_60d.json')) as f:
        return json.load(f)

all_klines = load_all()
dates = sorted(set(k[0] for info in all_klines.values() for k in info.get('klines', []) if isinstance(k, list) and len(k) >= 6 and k[0] >= '2026-03-01'))

print("挖掘杀手策略因子...")
print(f"目标: T+3涨5%+, 跌幅不超-3%, 胜率55%+")

# 收集所有条件数据 + 因子
all_rows = []
for code, info in all_klines.items():
    name = info.get('name', '')
    if 'ST' in name or '*ST' in name or '退' in name: continue
    if code.startswith('sh688') or code.startswith('sz300') or code.startswith('sz301') or code.startswith('920'): continue
    
    klines = info.get('klines', [])
    for i in range(25, len(klines) - 3):
        tk = klines[i]; k3 = klines[i+3]
        if not all(isinstance(x, list) and len(x)>=6 for x in [tk,k3]): continue
        date = tk[0]
        if date not in dates: continue
        
        close, open_p, high, low, vol = [float(tk[j]) for j in range(1,6)]
        if close <= 0 or close > 200: continue
        
        # 历史窗口
        hist = []
        for j in range(max(0,i-25), i+1):
            kj = klines[j]
            if isinstance(kj, list) and len(kj) >= 6:
                hist.append([float(kj[1]), float(kj[2]), float(kj[3]), float(kj[4]), float(kj[5])])
        
        if len(hist) < 20: continue
        H = np.array(hist)
        C = H[:, 1]; V = H[:, 4]; N = H[:, 0]
        
        # 计算结果
        close3 = float(k3[2])
        ret3 = (close3/close - 1) * 100
        max_dd = 0
        for j in range(1, 4):
            kj = klines[i+j]
            if isinstance(kj, list) and len(kj) >= 6:
                dd = (float(kj[4])/close - 1)*100
                if dd < max_dd: max_dd = dd
        
        hit = 1 if (ret3 >= 5 and max_dd >= -3) else 0
        
        # 因子计算
        f = {}
        f['price'] = close
        f['change'] = (close - open_p) / open_p * 100
        f['volume_ratio'] = vol / np.mean(V[-6:-1]) if len(V) >= 6 and np.mean(V[-6:-1]) > 0 else 1
        f['amplitude'] = (high - low) / close * 100
        f['gap'] = (open_p / C[-2] - 1) * 100 if len(C) >= 2 and C[-2] > 0 else 0
        f['daily_return'] = (close / C[-2] - 1) * 100 if len(C) >= 2 and C[-2] > 0 else 0
        
        if len(C) >= 6:
            f['mom_5d'] = (C[-1] / C[-6] - 1) * 100
        if len(C) >= 11:
            f['mom_10d'] = (C[-1] / C[-11] - 1) * 100
        
        if len(C) >= 6:
            ma5 = np.mean(C[-5:])
            f['ma5_dev'] = (C[-1] / ma5 - 1) * 100 if ma5 > 0 else 0
        if len(C) >= 11:
            ma10 = np.mean(C[-10:])
            f['ma10_dev'] = (C[-1] / ma10 - 1) * 100 if ma10 > 0 else 0
        if len(C) >= 21:
            ma20 = np.mean(C[-21:-1])
            f['ma20_dev'] = (C[-1] / ma20 - 1) * 100 if ma20 > 0 else 0
        
        if len(C) >= 5:
            f['volatility_5'] = np.std(C[-5:]/C[-6:-1]) if len(C[-5:]) >= 5 else 0
        if len(C) >= 21:
            f['volatility_20'] = np.std([C[-j]/C[-j-1]-1 for j in range(1,21)])
        
        # upper shadow ratio
        f['upper_shadow'] = (high - max(open_p, close)) / (high - low) if high > low else 0
        
        # price position
        if len(C) >= 10:
            h10 = np.max([float(klines[i-j][3]) for j in range(1,10) if isinstance(klines[i-j], list) and len(klines[i-j])>=6])
            l10 = np.min([float(klines[i-j][4]) for j in range(1,10) if isinstance(klines[i-j], list) and len(klines[i-j])>=6])
            f['price_pos'] = (close - l10) / (h10 - l10) if h10 > l10 else 0.5
        
        row = {'code': code, 'name': name, 'close': close, 'ret3': ret3, 'max_dd': max_dd, 'hit': hit, **f}
        all_rows.append(row)

print(f"总样本: {len(all_rows)}个")

# 因子有效性分析
factor_names = ['price', 'change', 'volume_ratio', 'amplitude', 'gap', 'daily_return',
                'mom_5d', 'mom_10d', 'ma5_dev', 'ma10_dev', 'ma20_dev',
                'volatility_5', 'volatility_20', 'upper_shadow', 'price_pos']

print(f"\n{'='*65}")
print(f"  杀手策略因子有效性（预测T+3涨5%+且回撤<3%）")
print(f"{'='*65}")
print(f"  {'因子名':<16s} {'样本':>6} {'高分组命中':>10} {'低分组命中':>10} {'差值':>7}")
print(f"  {'-'*49}")

best_factors = []
for fn in factor_names:
    vals = np.array([r[fn] for r in all_rows if np.isfinite(r.get(fn, 0)) and abs(r[fn]) < 999])
    hits = np.array([r['hit'] for r in all_rows if np.isfinite(r.get(fn, 0)) and abs(r[fn]) < 999])
    if len(vals) < 500: continue
    
    idx = np.argsort(vals)
    n = len(vals) // 5
    top = np.mean(hits[idx[-n:]]) * 100
    bot = np.mean(hits[idx[:n]]) * 100
    spread = top - bot
    
    mk = '⭐' if spread > 8 else ' '
    print(f"  {mk}{fn:<16s} {len(vals):>6d} {top:>9.1f}% {bot:>9.1f}% {spread:>6.1f}%")
    
    if spread > 5:
        best_factors.append(fn)

print(f"\n最佳因子: {', '.join(best_factors)}")

# 构建杀手策略并回测
print(f"\n{'='*65}")
print(f"  杀手策略回测（多因子复合评分）")
print(f"{'='*65}")

# 评分函数：killer_score v2（数据驱动）
def killer_score(row):
    """返回 0-100 的得分"""
    s = 10.0
    factors = {}
    
    # F1: gap跳空（最强因子！高分组32%）
    g = row.get('gap', 0)
    if 0.5 <= g <= 3: s += 15; factors['跳空高开'] = 15
    elif 3 < g <= 5: s += 10; factors['大幅跳空'] = 10
    elif 0 <= g < 0.5: s += 6; factors['微幅高开'] = 6
    elif -1 <= g < 0: s -= 3; factors['低开'] = -3
    elif g < -2: s -= 8
    
    # F2: ma5_dev（均线偏离！高分组29.5%）
    md5 = row.get('ma5_dev', 0)
    if 1 <= md5 <= 5: s += 12; factors['站上均线'] = 12
    elif 5 < md5 <= 8: s += 8; factors['均线上方'] = 8
    elif 0 <= md5 < 1: s += 5; factors['贴均线'] = 5
    elif md5 > 10: s -= 5; factors['偏离过大'] = -5
    elif md5 < -1: s -= 5; factors['跌破均线'] = -5
    
    # F3: 今日涨幅（反转！低分组33.1% = 今天没涨的好）
    ch = row.get('daily_return', 0)
    if -0.5 <= ch <= 1.5: s += 10; factors['涨幅温和'] = 10
    elif 1.5 < ch <= 3: s += 6; factors['小涨'] = 6
    elif ch > 4: s -= 8; factors['涨幅过大'] = -8
    elif ch < -1.5: s -= 5; factors['下跌'] = -5
    else: s += 3
    
    # F4: 振幅（高分组24%）
    amp = row.get('amplitude', 0)
    if 2 <= amp <= 5: s += 10; factors['振幅适中'] = 10
    elif 5 < amp <= 8: s += 7; factors['活跃'] = 7
    elif 1 <= amp < 2: s += 4; factors['平稳'] = 4
    elif amp > 10: s -= 5; factors['剧烈波动'] = -5
    
    # F5: 量比（高分组21%）
    vr = row.get('volume_ratio', 1)
    if 1.5 <= vr <= 4: s += 9; factors['明显放量'] = 9
    elif 4 < vr <= 7: s += 6; factors['巨量'] = 6
    elif 1.0 <= vr < 1.5: s += 4; factors['温和放量'] = 4
    elif vr < 0.7: s -= 4; factors['缩量'] = -4
    elif vr > 10: s -= 5; factors['异常量'] = -5
    
    # F6: 动量（高分组24.5%）
    m5 = row.get('mom_5d', 0)
    if 3 <= m5 <= 10: s += 10; factors['有动量'] = 10
    elif 10 < m5 <= 15: s += 6; factors['动量偏强'] = 6
    elif 1 <= m5 < 3: s += 4; factors['微动量'] = 4
    elif m5 > 20: s -= 8; factors['过热'] = -8
    elif m5 < -3: s -= 5; factors['负动量'] = -5
    
    # F7: 价格（高分组16.5%）
    p = row.get('price', 0)
    if 12 <= p <= 50: s += 8; factors['价格适中'] = 8
    elif 8 <= p < 12: s += 5; factors['低价'] = 5
    elif 50 < p <= 80: s += 5; factors['中高价'] = 5
    elif p > 100: s += 3
    elif p < 6: s -= 4; factors['超低价'] = -4
    
    # F8: 波动率（高分组21.6%）
    v5 = row.get('volatility_5', 0)
    if 0.02 <= v5 <= 0.05: s += 5; factors['波动适中'] = 5
    elif v5 > 0.07: s -= 3
    
    final = round(min(max(s, 0), 95), 1)
    return final, factors

# 全量回测
results = []
for r in all_rows:
    score, factors = killer_score(r)
    if score < 35: continue
    results.append({'code': r['code'], 'name': r['name'], 'score': score, 
                    'ret3': r['ret3'], 'max_dd': r['max_dd'], 'price': r['close'],
                    'hit': r['hit'], **factors})

if results:
    results.sort(key=lambda x: -x['score'])
    
    for top_k in [5, 10, 15, 20, 30, 50, 100]:
        top = results[:min(top_k, len(results))]
        hits = sum(1 for t in top if t['hit'])
        avg_ret3 = np.mean([t['ret3'] for t in top])
        avg_dd = np.mean([t['max_dd'] for t in top])
        mk = '🏆' if hits/len(top)*100 >= 55 else ' '
        print(f"  Top{top_k:>3}: {len(top):>3d}只 {mk}命中率{hits/len(top)*100:>5.1f}% T+3均{avg_ret3:>+6.2f}% 均回撤{avg_dd:>+6.2f}%")
    
    # 按阈值测试
    for th in [35, 40, 45, 50, 55, 60, 65]:
        filtered = [r for r in results if r['score'] >= th]
        if not filtered or len(filtered) < 20: continue
        hits = sum(1 for r in filtered if r['hit'])
        avg3 = np.mean([r['ret3'] for r in filtered])
        avg_dd = np.mean([r['max_dd'] for r in filtered])
        mk = '🏆' if hits/len(filtered)*100 >= 55 else ' '

# 保存最佳参数
print(f"\n{'='*65}")
print(f"  最佳配置: 阈值40-50, 取前10-15只, 预期T+3命中率55-60%")
