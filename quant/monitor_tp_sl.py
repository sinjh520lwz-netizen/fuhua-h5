#!/usr/bin/env python3
"""
盘中TP/SL监控
每5分钟检查持仓股票，触发TP+5%/SL-4%时记录到settlement.json
解决"用户没开页面导致止损检测不及时"的问题
"""
import json, os, time, urllib.request, re
from datetime import datetime

BASE = '/var/www/html/h5/quant'
RECS_FILE = os.path.join(BASE, 'data', 'jh_summary.json')
SETTLE_FILE = os.path.join(BASE, 'data', 'settlement.json')
HISTORY_FILE = os.path.join(BASE, 'data', 'history.json')

TP_PCT = 5.0
SL_PCT = 4.0

def get_prefix(code):
    return 'sh' if code.startswith('6') else 'sz'

def fetch_prices(codes):
    """从腾讯行情获取实时价格"""
    if not codes:
        return {}
    symbols = ','.join(f'{get_prefix(c)}{c}' for c in codes)
    url = f'http://qt.gtimg.cn/q={symbols}'
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode('gbk', errors='ignore')
    except Exception as e:
        print(f'  ❌ 行情获取失败: {e}')
        return {}
    
    prices = {}
    for line in data.split(';'):
        m = re.search(r'v_(\w+)="(.*)"', line)
        if not m:
            continue
        fields = m.group(2).split('~')
        if len(fields) < 4:
            continue
        code = fields[2]
        price = float(fields[3]) if fields[3] else 0
        if price > 0:
            prices[code] = price
    return prices

def load_recommendations():
    """加载今日推荐（作为持仓源）"""
    try:
        with open(RECS_FILE, 'r') as f:
            data = json.load(f)
        return data.get('recommendations', [])
    except Exception as e:
        print(f'  ❌ 加载推荐失败: {e}')
        return []

def load_settlements():
    """加载已结算记录"""
    try:
        with open(SETTLE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {'date': '', 'settled': []}

def save_settlements(data):
    with open(SETTLE_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_history():
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    except:
        return {'records': []}

def save_history(data):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')
    
    # 只在交易时间运行 (9:30-11:30, 13:00-15:00)
    hour, minute = now.hour, now.minute
    t = hour * 60 + minute
    if not ((9*60+30 <= t <= 11*60+30) or (13*60 <= t <= 15*60)):
        print(f'[{time_str}] 非交易时间，跳过')
        return
    
    # 加载今日推荐
    recs = load_recommendations()
    if not recs:
        print(f'[{time_str}] 无今日推荐')
        return
    
    # 加载已结算
    settle = load_settlements()
    if settle['date'] != today:
        settle = {'date': today, 'settled': []}
    
    settled_codes = {s['code'] for s in settle['settled']}
    
    # 过滤未结算的
    active = [r for r in recs if r['code'] not in settled_codes]
    if not active:
        print(f'[{time_str}] 全部已结算')
        return
    
    # 获取实时价格
    codes = [r['code'] for r in active]
    prices = fetch_prices(codes)
    if not prices:
        print(f'[{time_str}] 获取价格失败')
        return
    
    # 检查TP/SL
    triggered = []
    for r in active:
        code = r['code']
        entry = r.get('score_price') or r.get('price', 0)
        if entry <= 0 or code not in prices:
            continue
        
        cur = prices[code]
        pct = (cur - entry) / entry * 100
        
        if pct >= TP_PCT:
            triggered.append({
                'code': code,
                'name': r['name'],
                'entry_price': entry,
                'exit_price': cur,
                'change_pct': round(pct, 2),
                'status': 'tp',
                'exit_reason': f'TP止盈 +{pct:.2f}%',
                'triggered_at': now.strftime('%Y-%m-%d %H:%M')
            })
            print(f'  🎯 TP触发: {r["name"]} {code} 入{entry} → {cur} (+{pct:.2f}%)')
        elif pct <= -SL_PCT:
            triggered.append({
                'code': code,
                'name': r['name'],
                'entry_price': entry,
                'exit_price': cur,
                'change_pct': round(pct, 2),
                'status': 'sl',
                'exit_reason': f'SL止损 {pct:.2f}%',
                'triggered_at': now.strftime('%Y-%m-%d %H:%M')
            })
            print(f'  🛑 SL触发: {r["name"]} {code} 入{entry} → {cur} ({pct:.2f}%)')
        else:
            print(f'  📊 {r["name"]} {code}: ¥{cur} ({pct:+.2f}%) 未触发')
    
    if triggered:
        settle['settled'].extend(triggered)
        save_settlements(settle)
        
        # 同步写入history
        hist = load_history()
        for t in triggered:
            key = f"{t['code']}_{today}"
            # 去重
            if any(p.get('key') == key for p in hist.get('performance', [])):
                continue
            hist.setdefault('performance', []).append({
                'key': key,
                'code': t['code'],
                'name': t['name'],
                'rec_date': today,
                'rec_price': t['entry_price'],
                'rec_score': 0,
                'exit_price': t['exit_price'],
                'exit_type': t['status'],
                'return_pct': t['change_pct'],
                'exit_reason': t['exit_reason'],
                'settled_at': t['triggered_at']
            })
        save_history(hist)
        print(f'  ✅ {len(triggered)}只已触发，写入settlement.json + history.json')
    else:
        print(f'  [{time_str}] 无触发')

if __name__ == '__main__':
    main()
