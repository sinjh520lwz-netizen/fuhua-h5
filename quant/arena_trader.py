#!/usr/bin/env python3
"""锦鸿策场自动交易 - 盘中静默盯盘，收盘汇报"""

import json, os, subprocess, time, sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARENA_CONFIG = os.path.join(BASE_DIR, 'data', 'arena_config.json')
RECOMMENDATIONS = os.path.join(BASE_DIR, 'data', 'recommendations.json')
SIGNAL_MONITOR = os.path.join(BASE_DIR, 'data', 'signal_state.json')
TRADE_LOG = os.path.join(BASE_DIR, 'data', 'arena_trades.json')
REPORT_FILE = os.path.join(BASE_DIR, 'data', 'arena_report.json')

with open(ARENA_CONFIG) as f:
    cfg = json.load(f)

API_KEY = cfg['api_key']
BASE = cfg['base_url']
SILENT = '--silent' in sys.argv or '--report' not in sys.argv

def api(method, endpoint, data=None):
    cmd = ['curl', '-s', '-L', '-m', '15', '-X', method,
           f'{BASE}{endpoint}',
           '-H', 'Content-Type: application/json',
           '-H', f'agent-auth-api-key: {API_KEY}']
    if data:
        cmd.extend(['-d', json.dumps(data)])
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(result.stdout)
    except:
        return {'success': False, 'error': result.stdout[:200]}

def search_stock(code):
    result = api('GET', f'/api/v1/arena/stocks?search={code}')
    if result.get('success') and result['data']['stocks']:
        return result['data']['stocks'][0]
    return None

def trade(symbol, action, shares, reason=''):
    return api('POST', '/api/v1/arena/trade', {
        'symbol': symbol, 'action': action,
        'shares': shares, 'reason': reason
    })

def log_trade(info):
    try:
        with open(TRADE_LOG) as f: trades = json.load(f)
    except: trades = []
    trades.append(info)
    with open(TRADE_LOG, 'w') as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)

def run():
    now = datetime.now()
    
    # 获取状态
    home = api('GET', '/api/v1/arena/home')
    if not home.get('success'):
        if not SILENT: print(f"❌ {home.get('message')}")
        return
    
    portfolio = home['data']['portfolio']
    cash = portfolio['cash']
    rank = home['data']['rank']
    total = portfolio['total_value']
    ret = portfolio['return_rate']
    
    # 获取持仓
    port = api('GET', '/api/v1/arena/portfolio')
    holdings = {}
    if port.get('success') and port['data'].get('holdings'):
        for h in port['data']['holdings']:
            holdings[h['symbol']] = h
    
    # 获取交易记录
    trades_data = api('GET', '/api/v1/arena/trades')
    pending = 0
    if trades_data.get('success'):
        pending = sum(1 for t in trades_data['data']['trades'] if t['status'] == 'pending')
    
    # === 卖出逻辑: 检查持仓止盈止损 ===
    sold = []
    for sym, h in list(holdings.items()):
        cur_price = h.get('current_price', h.get('avg_cost', 0))
        avg_cost = h.get('avg_cost', 0)
        if avg_cost <= 0:
            continue
        pnl = (cur_price - avg_cost) / avg_cost * 100
        
        # 止盈 >15%
        if pnl > 15:
            result = trade(sym, 'sell', h['shares'], f'止盈{pnl:.1f}%')
            if result.get('success'):
                sold.append({'name': h.get('name',''), 'pnl': pnl, 'reason': '止盈'})
                log_trade({'time': now.strftime('%Y-%m-%d %H:%M:%S'), 'action': 'sell',
                    'symbol': sym, 'name': h.get('name',''), 'shares': h['shares'],
                    'price': cur_price, 'pnl': pnl, 'reason': '止盈'})
        
        # 止损 >8%
        elif pnl < -8:
            result = trade(sym, 'sell', h['shares'], f'止损{pnl:.1f}%')
            if result.get('success'):
                sold.append({'name': h.get('name',''), 'pnl': pnl, 'reason': '止损'})
                log_trade({'time': now.strftime('%Y-%m-%d %H:%M:%S'), 'action': 'sell',
                    'symbol': sym, 'name': h.get('name',''), 'shares': h['shares'],
                    'price': cur_price, 'pnl': pnl, 'reason': '止损'})
    
    # === 买入逻辑: 读取选股推荐 ===
    bought = []
    try:
        with open(RECOMMENDATIONS) as f: recs = json.load(f)
    except: recs = {}
    
    top_picks = recs.get('top_picks', [])
    holding_codes = set()
    for sym in holdings:
        code = sym.replace('sh','').replace('sz','')
        holding_codes.add(code)
    
    buy_count = 0
    for pick in top_picks[:5]:
        if buy_count >= 2: break
        code = pick['code']
        if code in holding_codes: continue
        
        stock = search_stock(code)
        if not stock: continue
        
        symbol = stock['symbol']
        price = stock['price']
        budget = cash * 0.15
        shares = int(budget / price / 100) * 100
        if shares < 100: continue
        
        result = trade(symbol, 'buy', shares, f"锦鸿推荐 分:{pick['score']}")
        if result.get('success'):
            cost = price * shares
            cash -= cost
            buy_count += 1
            bought.append({'name': pick['name'], 'code': code, 'shares': shares, 'price': price})
            log_trade({'time': now.strftime('%Y-%m-%d %H:%M:%S'), 'action': 'buy',
                'symbol': symbol, 'name': pick['name'], 'shares': shares,
                'price': price, 'score': pick['score']})
        time.sleep(1)
    
    # === 保存报告 ===
    report = {
        'time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'rank': rank,
        'total_value': total,
        'cash': cash,
        'return_rate': ret,
        'holdings_count': len(holdings),
        'pending_orders': pending,
        'bought': bought,
        'sold': sold,
        'holdings': [{'symbol': s, 'name': h.get('name',''), 'shares': h['shares'],
                       'avg_cost': h.get('avg_cost',0), 'pnl': round((h.get('current_price',0)-h.get('avg_cost',0))/h.get('avg_cost',1)*100,2)}
                      for s, h in holdings.items()]
    }
    with open(REPORT_FILE, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    # === 静默模式不输出，报告模式输出 ===
    if not SILENT:
        print(f"📊 锦鸿策场日报 | {now.strftime('%Y-%m-%d %H:%M')}")
        print(f"  排名: {rank}/25902 | 总资产: ¥{total:,.0f} | 收益率: {ret:+.2f}%")
        print(f"  持仓: {len(holdings)}只 | 待成交: {pending}单")
        if bought:
            print(f"  今日买入: {len(bought)}只")
            for b in bought:
                print(f"    ✅ {b['name']}({b['code']}) {b['shares']}股 @ ¥{b['price']}")
        if sold:
            print(f"  今日卖出: {len(sold)}只")
            for s in sold:
                print(f"    🔴 {s['name']} {s['reason']} {s['pnl']:+.1f}%")
        if holdings:
            print(f"  当前持仓:")
            for h in report['holdings']:
                pnl = h['pnl']
                icon = '🟢' if pnl > 0 else '🔴'
                print(f"    {icon} {h['name']} {h['shares']}股 成本¥{h['avg_cost']:.2f} 盈亏{pnl:+.1f}%")

if __name__ == '__main__':
    run()
