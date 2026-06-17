#!/usr/bin/env python3
"""v5.0 — 均线粘合突破策略
核心：找均线粘合（ma5≈ma10≈ma20）+ 布林收窄 → 刚放量突破
比v4.0更灵活：放宽"刚转头"条件，但加强量价确认
"""
import numpy as np

def score_v50(ind, rt_change=0, market_change=0):
    """v5.0 均线粘合突破评分"""
    if not ind:
        return 0.0, {}
    
    score = 15.0  # 更高基础分
    factors = {}

    close = ind['close']
    ma5, ma10, ma20 = ind['ma5'], ind['ma10'], ind['ma20']
    ma60 = ind.get('ma60', np.nan)
    dif, dea = ind['dif'], ind['dea']
    prev_dif, prev_dea = ind['prev_dif'], ind['prev_dea']
    rsi14 = ind['rsi14']
    vr = ind['vol_ratio']
    ts = ind['trend_score']
    bo = ind.get('breakout', 50)
    mom5 = ind['mom_5d']
    mom10 = ind['mom_10d']
    boll_width = ind.get('boll_width', 20)
    boll_pos = ind.get('boll_pos', 50)
    ma_conv = ind.get('ma_convergence', 999)

    # ========== 硬性过滤 ==========
    above_ma5 = not np.isnan(ma5) and close > ma5
    if not above_ma5:
        return 3.0, {'硬过滤': '未站上MA5'}
    if mom5 > 10:
        return 3.0, {'硬过滤': f'5日涨{mom5:.0f}%过热'}

    # ========== 均线粘合（核心创新） ==========
    # ma_convergence < 5 表示均线非常接近 = 粘合
    if not np.isnan(ma_conv) and ma_conv < 5:
        score += 12
        factors['均线粘合'] = round(12 - ma_conv, 1)
    elif not np.isnan(ma_conv) and ma_conv < 8:
        score += 7
        factors['均线发散'] = 7
    elif not np.isnan(ma_conv) and ma_conv < 15:
        score += 3
        factors['均线区间'] = 3
    else:
        score -= 3
        factors['均线发散'] = -3

    # ========== 布林收窄 ==========
    if boll_width < 6:
        score += 10
        factors['布林极窄'] = 10
    elif boll_width < 10:
        score += 7
        factors['布林收窄'] = 7
    elif boll_width < 15:
        score += 3
        factors['布林正常'] = 3
    else:
        score -= 3
        factors['布林过宽'] = -3

    # ========== 布林位置 ==========
    if 30 <= boll_pos <= 55:
        score += 6
        factors['布林中轨'] = 6
    elif 55 < boll_pos <= 65:
        score += 4
        factors['布林偏上'] = 4
    elif 20 <= boll_pos < 30:
        score += 3
        factors['布林偏下'] = 3
    elif boll_pos > 85:
        score -= 8
        factors['布林上轨'] = -8
    elif boll_pos > 75:
        score -= 3
        factors['布林近顶'] = -3

    # ========== 放量突破 ==========
    if 1.3 <= vr <= 2.5:
        score += 10
        factors['温和放量'] = 10
    elif 2.5 < vr <= 4:
        score += 5
        factors['显著放量'] = 5
    elif 1.0 <= vr < 1.3:
        score += 3
        factors['平量'] = 3
    elif vr < 0.5:
        score -= 8
        factors['缩量'] = -8
    elif vr > 5:
        score -= 5
        factors['巨量'] = -5

    # ========== MACD 金叉 ==========
    if dif > dea and prev_dif <= prev_dea:
        s = 8
        if dif < 0:
            s += 4
        factors['MACD金叉'] = round(s, 1)
    elif dif > dea and dif > -0.2:
        s = 4
        factors['MACD多头'] = s
    elif dif < dea and abs(dif - dea) < 0.03 and mom5 > 0.5:
        s = 5
        factors['MACD即将金叉'] = s
    else:
        s = 0
    score += s

    # ========== RSI 宽松范围 ==========
    if not np.isnan(rsi14):
        if 50 <= rsi14 <= 62:
            s = 7
            factors['RSI强势'] = 7
        elif 40 <= rsi14 < 50:
            s = 6
            factors['RSI回升'] = 6
        elif 62 < rsi14 <= 72:
            s = 4
            factors['RSI偏强'] = 4
        elif 30 <= rsi14 < 40:
            s = 3
            factors['RSI反弹'] = 3
        elif rsi14 > 75:
            s = -8
            factors['RSI超买'] = -8
        elif rsi14 < 30:
            s = -4
            factors['RSI超卖'] = -4
        else:
            s = 0
        score += s

    # ========== 突破位置 ==========
    if 25 <= bo <= 50:
        s = 7
        factors['突破低位'] = 7
    elif 50 < bo <= 65:
        s = 5
        factors['突破中位'] = 5
    elif 15 <= bo < 25:
        s = 3
        factors['超跌反弹'] = 3
    elif 65 < bo <= 80:
        s = 1
        factors['突破高位'] = 1
    elif bo > 90:
        s = -8
        factors['追高风险'] = -8
    else:
        s = 0
    score += s

    # ========== 涨幅控制 ==========
    if 0.2 <= rt_change <= 1.5:
        s = 7
        factors['涨幅适中'] = 7
    elif 1.5 < rt_change <= 2.5:
        s = 4
        factors['涨幅偏大'] = 4
    elif -0.5 <= rt_change < 0.2:
        s = 3
        factors['平盘'] = 3
    elif rt_change > 3.5:
        s = -10
        factors['涨幅过大'] = -10
    elif rt_change > 2.5:
        s = -4
        factors['涨幅较大'] = -4
    elif rt_change < -1.5:
        s = -3
        factors['跌幅过大'] = -3
    else:
        s = 0
    score += s

    # ========== 相对强度 ==========
    rel = rt_change - market_change
    if rel > 2:
        score += 5
        factors['强于大盘'] = 5
    elif rel > 1:
        score += 3
        factors['微强大盘'] = 3
    elif rel < -2:
        score -= 5
        factors['弱于大盘'] = -5
    elif rel < -1:
        score -= 2
        factors['略弱大盘'] = -2

    # ========== 趋势强度 ==========
    if 45 <= ts <= 55:
        score += 5
        factors['震荡企稳'] = 5
    elif 55 < ts <= 65:
        score += 3
        factors['温和向上'] = 3
    elif 35 <= ts < 45:
        score += 2
        factors['底部震荡'] = 2
    elif ts > 75:
        score -= 6
        factors['涨势末期'] = -6

    # ========== 大盘惩罚 ==========
    if market_change < -2:
        score -= 6
        factors['大盘大跌'] = -6
    elif market_change < -1:
        score -= 2
        factors['大盘下跌'] = -2

    # 最终分数
    final = round(min(max(score, 0), 100), 1)
    if final > 80:
        final = round(75 + (final - 75) / 2, 1)
    return final, factors
