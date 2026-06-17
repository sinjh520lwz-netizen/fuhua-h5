#!/usr/bin/env python3
"""
情绪周期监控系统 — 市场情绪温度计
扫描全A股 → 统计涨停/跌停/连板 → 算情绪温度 → 调仓位建议
"""
import json, os, sys, time, urllib.request
from datetime import datetime
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
STATE_FILE = os.path.join(DATA_DIR, 'emotion_state.json')
os.makedirs(DATA_DIR, exist_ok=True)

def fetch_batch_quotes():
    """腾讯批量行情 — 扫描全A股"""
    list_file = os.path.join(DATA_DIR, 'a_stock_list.json')
    if not os.path.exists(list_file):
        return []
    with open(list_file) as f:
        all_stocks = json.load(f)
    
    quotes = []
    batch_size = 200
    for i in range(0, len(all_stocks), batch_size):
        batch = all_stocks[i:i+batch_size]
        codes = [s['full'] for s in batch]
        url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = urllib.request.urlopen(req, timeout=15).read().decode('gbk')
            for line in data.strip().split('\n'):
                if '="' not in line: continue
                try:
                    parts = line.split('"')[1].split('~')
                    if len(parts) < 40: continue
                    name = parts[1]
                    change = float(parts[32]) if parts[32] else 0
                    code = line.split('="')[0].replace('v_','')[2:]
                    # 排除北交所
                    if code.startswith('920'): continue
                    quotes.append({'code': code, 'name': name, 'change': change})
                except: continue
        except:
            continue
        time.sleep(0.05)
    return quotes

def detect_limit_up_down(quotes):
    """检测涨停跌停"""
    zt, dt = 0, 0  # 涨停/跌停计数
    limit_up_stocks = []
    limit_down_stocks = []
    
    for s in quotes:
        code = s['code']
        name = s['name']
        chg = s['change']
        
        # 主板±10%, 创业板/科创板±20%
        is_cy = code.startswith('300') or code.startswith('301') or code.startswith('688')
        limit_pct = 20 if is_cy else 10
        
        if chg >= limit_pct - 0.5:  # 涨停（留0.5%容忍）
            zt += 1
            limit_up_stocks.append(f"{name}({code})")
        elif chg <= -(limit_pct - 0.5):  # 跌停
            dt += 1
            limit_down_stocks.append(f"{name}({code})")
    
    return zt, dt, limit_up_stocks[:10], limit_down_stocks[:10]

def calculate_emotion_score(zt, dt):
    """计算情绪温度 0-100"""
    score = 50  # 中性
    
    # 涨停加分
    if zt >= 80: score += 20
    elif zt >= 50: score += 15
    elif zt >= 30: score += 10
    elif zt >= 15: score += 5
    elif zt <= 5: score -= 10
    elif zt == 0: score -= 15
    
    # 跌停减分
    if dt >= 30: score -= 20
    elif dt >= 15: score -= 10
    elif dt >= 10: score -= 5
    elif dt == 0: score += 5
    
    # 涨停/跌停比
    if zt > 0 and dt > 0:
        ratio = zt / dt
        if ratio >= 5: score += 10
        elif ratio >= 3: score += 5
        elif ratio >= 1: pass
        elif ratio >= 0.5: score -= 5
        else: score -= 10
    
    return max(0, min(100, round(score)))

def get_phase(score):
    """情绪阶段"""
    if score < 25: return '冰点期', '空仓观望，等待转折'
    elif score < 40: return '修复期', '轻仓试错，控制风险'
    elif score < 60: return '发酵期', '正常交易，把握机会'
    elif score < 75: return '高潮期', '重仓出击但警惕退潮'
    elif score < 85: return '过热期', '逐步减仓，谨慎追高'
    else: return '极端期', '警惕风险，减仓防守'

def get_position_advice(score):
    """根据情绪温度给出仓位建议"""
    if score < 25: return '≤20%', '空仓或极轻仓'
    elif score < 40: return '20-40%', '轻仓试错'
    elif score < 60: return '40-65%', '正常仓位'
    elif score < 75: return '65-80%', '可适当加重'
    elif score < 85: return '50-65%', '警惕高潮减仓'
    else: return '≤30%', '过热风险减仓'

def run_emotion_monitor():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 情绪周期监控启动...")
    
    t0 = time.time()
    quotes = fetch_batch_quotes()
    fetch_time = time.time() - t0
    
    print(f"  扫描完成: {len(quotes)}只, 耗时{fetch_time:.0f}s")
    
    zt, dt, zt_names, dt_names = detect_limit_up_down(quotes)
    score = calculate_emotion_score(zt, dt)
    phase, advice = get_phase(score)
    position_range, position_advice = get_position_advice(score)
    
    print(f"  涨停: {zt}只 | 跌停: {dt}只 | 情绪温度: {score} | 阶段: {phase}")
    print(f"  仓位建议: {position_range} ({position_advice})")
    
    state = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_stocks': len(quotes),
        'limit_up': zt,
        'limit_down': dt,
        'limit_up_stocks': zt_names,
        'limit_down_stocks': dt_names,
        'score': score,
        'phase': phase,
        'advice': advice,
        'position_range': position_range,
        'position_advice': position_advice,
        'last_updated': datetime.now().isoformat(),
    }
    
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    
    print(f"  已保存到 {STATE_FILE}")
    return state

if __name__ == '__main__':
    run_emotion_monitor()
