#!/usr/bin/env python3
"""
JH 杀手级短线策略 vK — T+3目标5%, 跌幅<3%, 成功率55%+
挖掘什么样的股票能在3天内涨5%且跌幅不超过3%
"""
import json, os, sys, time
import numpy as np
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def load_all():
    with open(os.path.join(DATA_DIR, 'all_klines_60d.json')) as f:
        return json.load(f)

def get_dates(d):
    dc = defaultdict(int)
    for info in d.values():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                dc[k[0]] += 1
    return sorted([dt for dt, c in sorted(dc.items(), key=lambda x:-x[1]) if c > 100 and dt >= '2026-03-01'])

all_klines = load_all()
dates = get_dates(all_klines)
print(f"3024只股票, {len(dates)}交易日")

# 分析：哪些股票能在3天内涨5%+且最大回撤不超过3%？
# 搜索条件：排除sh688/sz300/sz301/920开头/ST
hits_data = []  # 符合条件的历史案例
fail_data = []  # 不符合的案例

for code, info in all_klines.items():
    name = info.get('name', '')
    # 排除规则
    if 'ST' in name or '*ST' in name or '退' in name: continue
    if code.startswith('sh688') or code.startswith('sz300') or code.startswith('sz301') or code.startswith('920'): continue
    
    klines = info.get('klines', [])
    for i in range(25, len(klines) - 3):
        k = klines[i]
        if not isinstance(k, list) or len(k) < 6: continue
        date = k[0]
        if date not in dates: continue
        
        close = float(k[2])
        if close <= 0 or close > 200: continue
        
        # T+3表现
        k3 = klines[i+3]
        if not isinstance(k3, list) or len(k3) < 6: continue
        close3 = float(k3[2])
        ret3 = (close3 / close - 1) * 100
        
        # 3天内的最大回撤
        max_dd = 0
        for j in range(1, 4):
            kj = klines[i+j]
            if isinstance(kj, list) and len(kj) >= 6:
                low_j = float(kj[4])
                dd = (low_j / close - 1) * 100
                if dd < max_dd: max_dd = dd
        
        # 目标条件：T+3涨5%+ 且 回撤不超过-3%
        hit = ret3 >= 5 and max_dd >= -3
        
        # 只记录部分样本（太多会内存溢出）
        entry = {
            'code': code, 'name': name, 'date': date,
            'close': close, 'ret3': round(ret3, 2),
            'max_dd': round(max_dd, 2),
            'open': float(k[1]), 'high': float(k[3]), 'low': float(k[4]),
            'volume': float(k[5]) / 10000,  # 万股
            'hit': hit,
        }
        if hit:
            hits_data.append(entry)
        elif len(fail_data) < 10000:
            fail_data.append(entry)

print(f"\n符合条件(T+3≥5%且回撤≥-3%): {len(hits_data)}个")
print(f"不符合条件: {len(fail_data)}个")

# 分析命中特征
if hits_data:
    h_closes = np.array([h['close'] for h in hits_data])
    h_volumes = np.array([h['volume'] for h in hits_data])
    h_returns = np.array([h['ret3'] for h in hits_data])
    
    print(f"\n命中股票特征:")
    print(f"  平均价格: {np.mean(h_closes):.1f}元")
    print(f"  中位价格: {np.median(h_closes):.1f}元")
    print(f"  价格<10元: {sum(1 for x in h_closes if x<10)/len(h_closes)*100:.0f}%")
    print(f"  价格10-30元: {sum(1 for x in h_closes if 10<=x<30)/len(h_closes)*100:.0f}%")
    print(f"  价格>30元: {sum(1 for x in h_closes if x>=30)/len(h_closes)*100:.0f}%")
    print(f"  平均成交量: {np.mean(h_volumes):.0f}万股")
    print(f"  中位成交量: {np.median(h_volumes):.0f}万股")
    print(f"  平均T+3收益: {np.mean(h_returns):.2f}%")
    print(f"  最高收益: {np.max(h_returns):.2f}%")
    print(f"  最低收益: {np.min(h_returns):.2f}%")
    
    # 看涨幅分布
    print(f"\n当日涨幅分布:")
    changes = np.array([h['high']/h['close']-1 for h in hits_data])*100
    print(f"  日内振幅均值: {np.mean(np.abs(changes)):.1f}%")

# 按价格区间看命中率
price_bins = [(0, 8), (8, 15), (15, 25), (25, 50), (50, 200)]
print(f"\n价格区间命中率:")
for lo, hi in price_bins:
    total = sum(1 for h in hits_data if lo <= h['close'] < hi) + sum(1 for f in fail_data if lo <= f['close'] < hi)
    hits_in_range = sum(1 for h in hits_data if lo <= h['close'] < hi)
    if total > 0:
        print(f"  {lo:>3}-{hi:>3}元: {hits_in_range}/{total} = {hits_in_range/total*100:.1f}%")

# 按名称展示一些典型命中
print(f"\n典型案例（部分）:")
for h in hits_data[:20]:
    print(f"  {h['name']:>6s}({h['code']}) 买入{h['close']}元 T+3:{h['ret3']:>+5.1f}% 回撤{h['max_dd']:>+.1f}% 量{h['volume']:.0f}万股")
