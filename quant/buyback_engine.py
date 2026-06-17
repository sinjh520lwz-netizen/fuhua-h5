#!/usr/bin/env python3
"""
接回推荐 v2.0 — 工业富联(601138)空仓后的买回信号分析

v2.0 优化：
- RSI<30超卖触发接回
- 价格回到支撑位附近 + 放量企稳 = 接回信号
- 大盘跌>1%时信号更保守
- 具体的三档触发条件（不再"等待更好价位"）
"""
import json, os, sys
from datetime import datetime
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from t_engine import fetch_realtime, fetch_minute_data, fetch_daily_klines, fetch_index_realtime, ReverseTAnalyzer

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
OUTPUT_FILE = os.path.join(DATA_DIR, 'buyback_signal.json')

STOCK_CODE = '601138'
SELL_PRICE = 77.40  # 用户卖出价


def check_volume_stability(minutes, window=10):
    """检测近N分钟是否放量企稳（价格不再创新低，成交量放大）"""
    if not minutes or len(minutes) < window * 2:
        return {'stable': False, 'desc': '数据不足'}

    recent = minutes[-window:]
    prev = minutes[-window*2:-window]

    recent_prices = [m['price'] for m in recent]
    prev_prices = [m['price'] for m in prev]
    recent_vols = [m['volume'] for m in recent]
    prev_vols = [m['volume'] for m in prev]

    # 企稳条件：最近窗口内价格不再创新低，且波动收窄
    price_stable = min(recent_prices) > min(prev_prices) * 0.998  # 不再创新低
    recent_range = (max(recent_prices) - min(recent_prices)) / min(recent_prices) * 100
    vol_up = np.mean(recent_vols) > np.mean(prev_vols) * 1.2  # 量能放大20%+

    if price_stable and vol_up:
        return {'stable': True, 'desc': f'近{window}分钟放量企稳（量能+{(np.mean(recent_vols)/max(np.mean(prev_vols),1)-1)*100:.0f}%，振幅{recent_range:.2f}%）'}
    elif price_stable:
        return {'stable': False, 'partial': True, 'desc': f'价格企稳但量能未放大，等待放量确认'}
    else:
        return {'stable': False, 'desc': '价格仍在探底'}


def calc_rsi_from_daily(daily, period=14):
    """从日K线计算RSI"""
    if not daily or len(daily) < period + 1:
        return None
    closes = [k['close'] for k in daily]
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    recent = deltas[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def analyze_buyback():
    """分析接回时机（增强版）"""
    # 拉数据
    rt = fetch_realtime(STOCK_CODE)
    minutes = fetch_minute_data(STOCK_CODE)
    daily = fetch_daily_klines(STOCK_CODE, 30)
    index_data = fetch_index_realtime()

    if not rt:
        return {'error': '获取行情失败'}

    current = rt['price']
    change = rt['change']

    # 用反T分析器获取支撑阻力（传入大盘数据）
    analyzer = ReverseTAnalyzer(minutes, rt, daily, index_data)
    result = analyzer.analyze()

    levels = result.get('levels', {})
    support = levels.get('support', [])
    resistance = levels.get('resistance', [])
    rsi = result.get('rsi')
    daily_rsi = calc_rsi_from_daily(daily)
    market = result.get('market_sentiment', {})
    m_filter = market.get('filter', 'normal')

    # 计算接回价位建议
    sell_p = SELL_PRICE
    diff_pct = (current - sell_p) / sell_p * 100

    # ---- 检测触发条件 ----
    triggers = []

    # 条件1: RSI超卖
    effective_rsi = daily_rsi if daily_rsi else rsi
    if effective_rsi and effective_rsi < 30:
        triggers.append({
            'name': 'RSI超卖',
            'met': True,
            'detail': f'RSI={effective_rsi}（{"日线" if daily_rsi else "分时"}），超卖区',
            'weight': 3 if effective_rsi < 20 else 2,
        })
    elif effective_rsi and effective_rsi < 40:
        triggers.append({
            'name': 'RSI偏低',
            'met': True,
            'detail': f'RSI={effective_rsi}，偏低位',
            'weight': 1,
        })
    else:
        triggers.append({
            'name': 'RSI超卖',
            'met': False,
            'detail': f'RSI={effective_rsi}，未到超卖区（需<30）',
            'weight': 0,
        })

    # 条件2: 价格回到支撑位附近（距支撑<1%）
    near_support = None
    for sup in support:
        dist = (current - sup['price']) / current * 100
        if 0 < dist < 1.5:
            near_support = sup
            break
    if near_support:
        triggers.append({
            'name': '接近支撑位',
            'met': True,
            'detail': f'距{near_support["type"]}{near_support["price"]}仅{(current - near_support["price"]) / current * 100:.1f}%',
            'weight': 3 if near_support['strength'] == '强' else 2,
        })
    else:
        near_sup = support[0] if support else None
        if near_sup:
            dist = (current - near_sup['price']) / current * 100
            triggers.append({
                'name': '接近支撑位',
                'met': False,
                'detail': f'最近支撑{near_sup["price"]}({near_sup["type"]})，距{dist:.1f}%，需<1.5%',
                'weight': 0,
            })

    # 条件3: 放量企稳
    vol_stable = check_volume_stability(minutes)
    if vol_stable.get('stable'):
        triggers.append({
            'name': '放量企稳',
            'met': True,
            'detail': vol_stable['desc'],
            'weight': 2,
        })
    else:
        triggers.append({
            'name': '放量企稳',
            'met': False,
            'detail': vol_stable['desc'],
            'weight': 0,
        })

    # 条件4: 价格低于卖出价足够多
    if diff_pct <= -3:
        triggers.append({
            'name': '价格折价',
            'met': True,
            'detail': f'低于卖出价{abs(diff_pct):.1f}%，成本优势明显',
            'weight': 2,
        })
    elif diff_pct <= -1:
        triggers.append({
            'name': '价格折价',
            'met': True,
            'detail': f'低于卖出价{abs(diff_pct):.1f}%，小有成本优势',
            'weight': 1,
        })
    else:
        triggers.append({
            'name': '价格折价',
            'met': False,
            'detail': f'{"高于" if diff_pct > 0 else "低于"}卖出价{abs(diff_pct):.1f}%，成本优势不足',
            'weight': 0,
        })

    # ---- 综合评分 ----
    total_weight = sum(t['weight'] for t in triggers)
    met_count = sum(1 for t in triggers if t['met'])

    # 大盘情绪过滤
    if m_filter == 'very_conservative':
        total_weight = int(total_weight * 0.6)  # 大盘暴跌打6折
    elif m_filter == 'conservative':
        total_weight = int(total_weight * 0.8)  # 大盘跌打8折

    # ---- 接回建议分三档 ----
    recommendations = []

    # 档位1：试探区（总分3-4分）
    if total_weight >= 3:
        target_price = round(current, 2)
        recommendations.append({
            'level': 'probe',
            'label': '🟢 试探接回',
            'desc': f'满足{met_count}个条件（得分{total_weight}），可小仓试探',
            'price': target_price,
            'action': '轻仓（1/3仓位）',
            'conditions': [t['detail'] for t in triggers if t['met']],
        })

    # 档位2：机会区（总分5-7分）
    if total_weight >= 5:
        target_price = round(current * 0.995, 2)  # 略低于现价
        recommendations.append({
            'level': 'good',
            'label': '🟢 好机会',
            'desc': f'满足{met_count}个条件（得分{total_weight}），性价比不错',
            'price': target_price,
            'action': '半仓（1/2仓位）',
            'conditions': [t['detail'] for t in triggers if t['met']],
        })

    # 档位3：重仓区（总分8+分）
    if total_weight >= 8:
        target_price = round(current * 0.99, 2)
        recommendations.append({
            'level': 'great',
            'label': '🟢 绝佳机会',
            'desc': f'满足{met_count}个条件（得分{total_weight}），机会难得',
            'price': target_price,
            'action': '重仓（2/3仓位）',
            'conditions': [t['detail'] for t in triggers if t['met']],
        })

    # ---- 信号判定 ----
    if total_weight >= 8:
        signal = 'strong_buy'
        signal_text = '强烈建议接回'
        signal_color = '#00ff88'
        urgency = 'high'
    elif total_weight >= 5:
        signal = 'buy'
        signal_text = '可以考虑接回'
        signal_color = '#44cc44'
        urgency = 'medium'
    elif total_weight >= 3:
        signal = 'watch_buy'
        signal_text = '关注中，可小仓试探'
        signal_color = '#ffaa00'
        urgency = 'low'
    elif diff_pct > 3:
        signal = 'wait_high'
        signal_text = '当前价高于卖出价，坚决不追高'
        signal_color = '#ff4444'
        urgency = 'low'
    else:
        signal = 'wait'
        signal_text = '条件不足，继续等待'
        signal_color = '#888888'
        urgency = 'low'

    # ---- 生成具体建议文字 ----
    advice_parts = []
    if signal in ('strong_buy', 'buy'):
        advice_parts.append(f'满足{met_count}项触发条件（{", ".join(t["name"] for t in triggers if t["met"])}）')
        advice_parts.append(f'建议分批接回，目标价{recommendations[0]["price"] if recommendations else current}')
    elif signal == 'watch_buy':
        advice_parts.append(f'部分条件满足（{", ".join(t["name"] for t in triggers if t["met"])}）')
        advice_parts.append('可轻仓试探，等更多信号确认')
    else:
        unmet = [t for t in triggers if not t['met']]
        if unmet:
            advice_parts.append(f'还差：{"、".join(t["name"] + "(" + t["detail"].split("，")[0] + ")" for t in unmet[:2])}')

    if m_filter in ('conservative', 'very_conservative'):
        advice_parts.append(f'⚠️ 大盘{market.get("change", 0)}%，信号已降级处理')

    advice = '。'.join(advice_parts)

    output = {
        'stock': '工业富联',
        'code': STOCK_CODE,
        'time': datetime.now().strftime('%H:%M:%S'),
        'current': current,
        'change': change,
        'sell_price': sell_p,
        'diff_pct': round(diff_pct, 2),
        'rsi': effective_rsi,
        'daily_rsi': daily_rsi,
        'intraday_rsi': rsi,
        'signal': signal,
        'signal_text': signal_text,
        'signal_color': signal_color,
        'urgency': urgency,
        'total_score': total_weight,
        'triggers': triggers,
        'advice': advice,
        'recommendations': recommendations,
        'support': support[:3],
        'resistance': resistance[:3],
        'vwap': result.get('vwap', 0),
        'deviation': result.get('deviation', 0),
        'market': market,
        'vol_stable': vol_stable,
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return output


if __name__ == '__main__':
    result = analyze_buyback()
    print(f"\n{'='*50}")
    print(f"  工业富联 接回分析 v2.0")
    print(f"{'='*50}")
    print(f"当前价: {result.get('current')} | 卖出价: {result.get('sell_price')}")
    print(f"价差: {result.get('diff_pct')}% | RSI: 日线{result.get('daily_rsi')} 分时{result.get('intraday_rsi')}")
    print(f"信号: {result.get('signal')} ({result.get('urgency')}) | {result.get('signal_text')}")
    print(f"总分: {result.get('total_score')}")
    print(f"\n触发条件:")
    for t in result.get('triggers', []):
        icon = '✅' if t['met'] else '❌'
        print(f"  {icon} {t['name']}({t['weight']}分): {t['detail']}")
    print(f"\n建议: {result.get('advice')}")
    if result.get('recommendations'):
        print(f"\n接回方案:")
        for r in result['recommendations']:
            print(f"  {r['label']}: {r['price']} → {r['action']}")
            for c in r.get('conditions', []):
                print(f"    ✓ {c}")
