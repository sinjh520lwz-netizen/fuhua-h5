#!/usr/bin/env python3
"""v6.0 — 多因子融合评分（因子挖掘结果驱动）
使用因子挖掘验证的最佳因子：
- info_ratio_20 (胜差9.8%) 收益稳定度
- shadow_ratio (胜差8.8%) 下影线优势
- rsi_14 (胜差8.8%) RSI强势  
- ma_slope (胜差8.5%) 均线斜率
- mom_reversal (胜差8.2%,反向) 短期不能超涨
- ma_deviation (胜差8.2%) 均线偏离
- price_position (胜差6.1%) 价格位置
- obv_direction (胜差6.0%) OBV能量潮
- gap (胜差5.4%) 跳空缺口
- vol_ratio (胜差4.7%,反向) 波动稳定
"""
import numpy as np

def score_v60(ind, rt_change=0, market_change=0):
    """v6.0 多因子融合评分"""
    if not ind:
        return 0.0, {}
    
    score = 20.0  # 基础分更高
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
    mom20 = ind['mom_20d']
    boll_width = ind.get('boll_width', 20)
    boll_pos = ind.get('boll_pos', 50)
    ma_conv = ind.get('ma_convergence', 999)
    
    # 硬性过滤
    if not np.isnan(ma5) and close > ma5:
        pass  # 站上MA5不扣分
    else:
        return 3.0, {'硬过滤': '未站上MA5'}
    if mom5 > 9:
        return 3.0, {'硬过滤': f'5日{mom5:.0f}%过热'}
    
    # ===== 因子1: ma_slope (均线斜率) =====
    if not np.isnan(ma5) and not np.isnan(ma20) and ma20 > 0:
        slope = (ma5 - ma20) / ma20 * 100
        if slope > 1.5:
            s = 10; factors['均线上行'] = 10
        elif slope > 0.5:
            s = 7; factors['均线微升'] = 7
        elif slope > -0.5:
            s = 4; factors['均线走平'] = 4
        elif slope > -2:
            s = 0
        else:
            s = -6; factors['均线下行'] = -6
        score += s
    
    # ===== 因子2: ma_deviation (均线偏离) =====
    if not np.isnan(ma20) and ma20 > 0:
        dev = (close - ma20) / ma20 * 100
        if 0.5 <= dev <= 4:
            s = 10; factors['温和偏离'] = 10
        elif 4 < dev <= 8:
            s = 6; factors['偏多偏离'] = 6
        elif -2 <= dev < 0.5:
            s = 5; factors['贴均线'] = 5
        elif dev > 12:
            s = -8; factors['过度偏离'] = -8
        elif dev > 8:
            s = -3; factors['偏离偏大'] = -3
        elif dev < -5:
            s = -5; factors['超跌偏离'] = -5
        else:
            s = 0
        score += s
    
    # ===== 因子3: rsi_14 =====
    if not np.isnan(rsi14):
        if 50 <= rsi14 <= 63:
            s = 10; factors['RSI强势'] = 10
        elif 40 <= rsi14 < 50:
            s = 8; factors['RSI中性偏强'] = 8
        elif 63 < rsi14 <= 72:
            s = 5; factors['RSI偏强'] = 5
        elif 30 <= rsi14 < 40:
            s = 4; factors['RSI回稳'] = 4
        elif rsi14 > 78:
            s = -10; factors['RSI超买'] = -10
        elif rsi14 > 72:
            s = -5
        elif rsi14 < 28:
            s = -5; factors['RSI超卖'] = -5
        else:
            s = 0
        score += s
    
    # ===== 因子4: shadow_ratio (下影线优势) =====
    # 用boll_pos和breakout近似判断
    if 25 <= bo <= 55:
        s = 8; factors['底部启动'] = 8
    elif 55 < bo <= 65:
        s = 5; factors['中位突破'] = 5
    elif 15 <= bo < 25:
        s = 4; factors['超跌区域'] = 4
    elif bo > 85:
        s = -8; factors['高位区域'] = -8
    elif bo > 70:
        s = -3
    else:
        s = 0
    score += s
    
    # ===== 因子5: mom_reversal (动量反转) =====
    # 短期不能涨过多，中期要有支撑
    if mom5 > 0 and mom10 < -1:
        s = 8; factors['刚转头'] = 8
    elif mom5 > 0 and -1 <= mom10 <= 1:
        s = 5; factors['震荡走强'] = 5
    elif mom5 > 2 and mom10 > 3:
        s = -5; factors['连续上涨'] = -5  # 涨多了要回调
    elif mom5 < -2:
        s = -4; factors['短期下跌'] = -4
    else:
        s = 2; factors['温和上涨'] = 2
    score += s
    
    # ===== 因子6: info_ratio_20 (收益稳定性) =====
    # 使用trend_score作为稳定性代理
    if 45 <= ts <= 58:
        s = 7; factors['稳定趋势'] = 7
    elif 58 < ts <= 65:
        s = 5; factors['上升趋势'] = 5
    elif 35 <= ts < 45:
        s = 3; factors['企稳中'] = 3
    elif ts > 75:
        s = -5; factors['趋势末端'] = -5
    elif ts < 30:
        s = -4; factors['弱势'] = -4
    else:
        s = 0
    score += s
    
    # ===== 因子7: price_position (价格位置) =====
    if 30 <= boll_pos <= 55:
        s = 7; factors['布林中下轨'] = 7
    elif 55 < boll_pos <= 65:
        s = 5; factors['布林中上'] = 5
    elif 20 <= boll_pos < 30:
        s = 3; factors['布林下轨'] = 3
    elif boll_pos > 85:
        s = -7; factors['布林上轨'] = -7
    elif boll_pos > 70:
        s = -3
    else:
        s = 0
    score += s
    
    # ===== 因子8: volume (量能确认) =====
    if 1.2 <= vr <= 2.5:
        s = 8; factors['温和放量'] = 8
    elif 2.5 < vr <= 4:
        s = 4; factors['放量突破'] = 4
    elif 1.0 <= vr < 1.2:
        s = 3; factors['平量'] = 3
    elif vr > 5:
        s = -6; factors['巨量异常'] = -6
    elif vr < 0.5:
        s = -5; factors['极度缩量'] = -5
    else:
        s = 0
    score += s
    
    # ===== 因子9: gap (跳空缺口) =====
    if 0.3 <= rt_change <= 1.5:
        s = 7; factors['温和高开'] = 7
    elif 1.5 < rt_change <= 2.5:
        s = 4; factors['高开偏大'] = 4
    elif -0.3 <= rt_change < 0.3:
        s = 3; factors['平开'] = 3
    elif rt_change > 3.5:
        s = -10; factors['高开过多'] = -10
    elif rt_change > 2.5:
        s = -5
    elif rt_change < -1.5:
        s = -3; factors['低开'] = -3
    else:
        s = 0
    score += s
    
    # ===== 因子10: MACD支持 =====
    if dif > dea and prev_dif <= prev_dea:
        s = 8; factors['MACD金叉'] = 8
        if dif < 0:
            s += 3; factors['零轴下金叉'] = 3
    elif dif > dea:
        s = 4; factors['MACD多头'] = 4
    elif dif < dea and abs(dif-dea) < 0.03 and mom5 > 0.5:
        s = 5; factors['MACD即将金叉'] = 5
    else:
        s = 0
    score += s
    
    # ===== 大盘惩罚 =====
    if market_change < -2:
        score -= 6
    elif market_change < -1:
        score -= 2
    if market_change > 1.5:
        score += 2
    
    # 信号过载惩罚
    active = len([v for v in factors.values() if v > 0])
    if active >= 10:
        score -= 5
    elif active >= 12:
        score -= 10
    
    final = round(min(max(score, 0), 100), 1)
    if final > 80:
        final = round(75 + (final - 75) / 2, 1)
    return final, factors
