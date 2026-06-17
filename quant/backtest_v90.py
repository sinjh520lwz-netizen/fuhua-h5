"""v9.0 — 强势延续策略（基于赢家分析）
发现：T+1胜者=高价+高均线+强动量，非底部反弹
"""
import numpy as np

def score_v90(ind, rt_change=0, market_change=0):
    if not ind: return 0.0, {}
    
    close = ind['close']
    ma5, ma10, ma20 = ind['ma5'], ind['ma10'], ind['ma20']
    dif, dea = ind['dif'], ind['dea']
    prev_dif, prev_dea = ind['prev_dif'], ind['prev_dea']
    rsi14 = ind['rsi14']
    vr = ind['vol_ratio']
    ts = ind['trend_score']
    bo = ind.get('breakout', 50)
    mom5 = ind['mom_5d']
    boll_pos = ind.get('boll_pos', 50)
    f = {}
    
    if not (not np.isnan(ma5) and close > ma5):
        return 0.0, {'out': '未站MA5'}
    if mom5 > 9:
        return 0.0, {'out': f'{mom5:.0f}%过热'}
    if rt_change > 5 or rt_change < -2:
        return 0.0, {'out': f'涨幅{rt_change:.1f}%异常'}
    if market_change < -2.5:
        return 0.0, {'out': '大盘暴跌'}
    
    score = 10.0
    
    # F1: 强势价格
    if close > 30: score += 8; f['高价股'] = 8
    elif close > 15: score += 5; f['中价股'] = 5
    elif close > 8: score += 2; f['低价股'] = 2
    
    # F2: 均线多头
    if all(not np.isnan(x) for x in [ma5,ma10,ma20]) and ma5 > ma10 > ma20:
        score += 8; f['均线多头'] = 8
    elif not np.isnan(ma5) and not np.isnan(ma10) and ma5 > ma10:
        score += 5; f['均线向上'] = 5
    else:
        score -= 3; f['均线弱势'] = -3
    
    # F3: RSI强势
    if not np.isnan(rsi14):
        if 55 <= rsi14 <= 68: score += 8; f['RSI强势'] = 8
        elif 50 <= rsi14 < 55: score += 6; f['RSI中强'] = 6
        elif 45 <= rsi14 < 50: score += 4; f['RSI中性'] = 4
        elif 68 < rsi14 <= 75: score += 2; f['RSI偏强'] = 2
        elif rsi14 > 78: score -= 10; f['RSI超买'] = -10
        elif rsi14 < 35: score -= 6; f['RSI超卖'] = -6
    
    # F4: 动量确认
    if mom5 > 3: score += 7; f['强动量'] = 7
    elif mom5 > 1.5: score += 5; f['中动量'] = 5
    elif mom5 > 0: score += 3; f['微动量'] = 3
    elif mom5 < -3: score -= 5; f['负动量'] = -5
    
    # F5: 当日涨幅
    if 0.3 <= rt_change <= 1.5: score += 7; f['涨幅适中'] = 7
    elif 1.5 < rt_change <= 3: score += 5; f['涨幅偏大'] = 5
    elif -0.3 <= rt_change < 0.3: score += 4; f['平盘'] = 4
    elif 3 < rt_change <= 4: score += 2; f['涨幅较大'] = 2
    elif rt_change > 4: score -= 5; f['涨幅过大'] = -5
    
    # F6: 量能
    if 1.2 <= vr <= 2.5: score += 7; f['温和放量'] = 7
    elif 1.0 <= vr < 1.2: score += 4; f['平量'] = 4
    elif 2.5 < vr <= 4: score += 3; f['放量'] = 3
    elif vr > 5: score -= 6; f['巨量'] = -6
    elif vr < 0.5: score -= 5; f['缩量'] = -5
    
    # F7: MACD
    if dif > dea and prev_dif <= prev_dea: score += 7; f['MACD金叉'] = 7
    elif dif > dea: score += 4; f['MACD多头'] = 4
    elif dif < dea and abs(dif-dea) < 0.05: score += 3; f['MACD粘合'] = 3
    else: score -= 3; f['MACD死叉'] = -3
    
    # F8: 突破位置
    if 50 <= bo <= 75: score += 6; f['中高突破'] = 6
    elif 35 <= bo < 50: score += 5; f['中位启动'] = 5
    elif 75 < bo <= 85: score += 3; f['高位突破'] = 3
    elif bo < 25: score -= 5; f['底部弱势'] = -5
    elif bo > 90: score -= 8; f['顶部'] = -8
    
    # F9: 趋势
    if 52 <= ts <= 65: score += 5; f['上升趋势'] = 5
    elif 45 <= ts < 52: score += 3; f['震荡偏强'] = 3
    elif ts > 72: score -= 4; f['趋势末端'] = -4
    elif ts < 38: score -= 5; f['弱势'] = -5
    
    # 大盘
    if market_change > 1.5: score += 3
    elif market_change < -1.5: score -= 4
    
    final = round(min(max(score, 0), 100), 1)
    return float(final), f
