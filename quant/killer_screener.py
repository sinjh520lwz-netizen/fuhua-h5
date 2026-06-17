#!/usr/bin/env python3
"""杀手策略引擎 vK"""
import json, os, sys, math
import numpy as np
from datetime import datetime
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def fetch_candidates():
    """模拟screener的候选池，取今日全市场数据"""
    import urllib.request
    stocks = []
    list_file = os.path.join(DATA_DIR, 'a_stock_list.json')
    if not os.path.exists(list_file):
        return stocks
    with open(list_file) as f:
        all_stocks = json.load(f)
    # 只取排除规则外的股票(排除sh688/sz300/sz301/920)
    for s in all_stocks:
        code = s.get('full','')
        name = s.get('name','')
        if code.startswith('sh688') or code.startswith('sz300') or code.startswith('sz301') or code.startswith('920'):
            continue
        if 'ST' in name or '*ST' in name or '退' in name:
            continue
        stocks.append(s)
    return stocks

def k_score(ind, change=0):
    """杀手策略评分 0-100"""
    if not ind:
        return 0, {}
    
    close = ind['close']
    ma5 = ind.get('ma5', None)
    ma10 = ind.get('ma10', None)
    ma20 = ind.get('ma20', None)
    rsi14 = ind.get('rsi14', None)
    vol_ratio = ind.get('vol_ratio', 1)
    mom5 = ind.get('mom_5d', 0)
    boll_pos = ind.get('boll_pos', 50)
    ma_convergence = ind.get('ma_convergence', 999)
    gap = change  # 用change近似gap
    
    score = 10
    facts = {}
    
    # 硬过滤
    if mom5 > 15: return 0, {'过热': True}
    if change > 5: return 0, {'涨太多': True}
    if change < -3: return 0, {'跌太多': True}
    if close > 100: return 0, {'太贵': True}
    if close < 5: return 0, {'太便宜': True}
    
    # F1: gap/涨幅 (最强因子)
    if 0.5 <= change <= 2.5:
        score += 15; facts['高开'] = 15
    elif 2.5 < change <= 4:
        score += 10; facts['大幅高开'] = 10
    elif 0 <= change < 0.5:
        score += 6; facts['微涨'] = 6
    
    # F2: 站上均线
    if ma5 and not np.isnan(ma5) and ma5 > 0:
        dev = (close/ma5 - 1)*100
        if 1 <= dev <= 5:
            score += 12; facts['站上MA5'] = 12
        elif 5 < dev <= 8:
            score += 8; facts['均线上方'] = 8
        elif 0 <= dev < 1:
            score += 5; facts['贴均线'] = 5
        elif dev < -1:
            score -= 5; facts['破均线'] = -5
        elif dev > 10:
            score -= 5; facts='偏离过大'
    
    # F3: 量比
    if 1.5 <= vol_ratio <= 4:
        score += 10; facts['放量'] = 10
    elif 4 < vol_ratio <= 7:
        score += 7; facts['巨量'] = 7
    elif 1.0 <= vol_ratio < 1.5:
        score += 4; facts['温和'] = 4
    elif vol_ratio < 0.7:
        score -= 4; facts['缩量'] = -4
    
    # F4: 动量
    if 3 <= mom5 <= 10:
        score += 10; facts['有动量'] = 10
    elif 1 <= mom5 < 3:
        score += 5; facts['微动量'] = 5
    elif 10 < mom5 <= 15:
        score += 4; facts='动量偏强'
    elif mom5 < -2:
        score -= 4; facts['负动量'] = -4
    
    # F5: 价格
    if 12 <= close <= 50:
        score += 8; facts['价格好'] = 8
    elif 8 <= close < 12:
        score += 4; facts['偏低'] = 4
    elif 50 < close <= 80:
        score += 4; facts['中价'] = 4
    
    # F6: RSI不宜超买
    if rsi14 and not np.isnan(rsi14):
        if 50 <= rsi14 <= 65:
            score += 6; facts['RSI强'] = 6
        elif 40 <= rsi14 < 50:
            score += 4; facts['RSI中'] = 4
        elif rsi14 > 75:
            score -= 6; facts['RSI超买'] = -6
    
    final = round(min(max(score, 0), 95), 1)
    return final, facts

def run_killer(top_n=10, threshold=35):
    """主流程"""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 杀手策略启动...")
    print(f"目标: T+3涨5%, 回撤<3%, 胜率55%+\n")
    
    from screener import fetch_all_quotes_batch, quick_analyze, fetch_klines, fetch_realtime
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    candidates = fetch_all_quotes_batch()
    print(f"全A股初筛: {len(candidates)}只")
    
    from screener import pre_filter
    candidates = pre_filter(candidates, 0)
    print(f"预筛: {len(candidates)}只")
    
    # 提取K线数据
    from screener import fetch_klines
    results = []
    for s in candidates:
        code = s['code']
        klines = fetch_klines(code, 30)
        if len(klines) < 25: continue
        ind = quick_analyze(klines)
        if not ind: continue
        score, factors = k_score(ind, s.get('change', 0))
        if score < threshold: continue
        
        results.append({
            'code': code, 'name': s['name'], 'score': score,
            'price': ind['close'], 'change': s.get('change', 0),
            'amount': s.get('amount', 0),
            'factors': {k:v for k,v in factors.items() if isinstance(v, (int, float)) and v > 0},
            'target': 5.0,  # 目标收益5%
            'stop': -3.0,   # 止损-3%
            'hold_days': 3, # 持有3天
        })
    
    results.sort(key=lambda x: -x['score'])
    results = results[:top_n]
    
    print(f"推荐: {len(results)}只")
    for r in results:
        sigs = '|'.join(list(r['factors'].keys())[:3])
        print(f"  {r['name']:6s}({r['code']}) 分{r['score']:.0f} 价{r['price']:.2f} 涨{r['change']:+.1f}% 目标+5%止-3% {sigs}")
    
    return results

if __name__ == '__main__':
    run_killer(top_n=10, threshold=35)
