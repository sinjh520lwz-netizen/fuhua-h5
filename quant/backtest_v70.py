#!/usr/bin/env python3
"""v7.0 — 规则分层筛选系统（非评分）
核心：用最优条件组合逐层过滤，而不是加权评分
每一层窄化候选池，最终留下高胜率股票
"""
import numpy as np

def score_v70(ind, rt_change=0, market_change=0):
    """
    v7.0 规则分层筛选
    不计算分数，而是检查通过几层过滤
    返回: (通过层数, {通过的条件名})
    """
    if not ind:
        return 0.0, {}
    
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
    
    passed = {}
    total = 0
    
    # ===== 硬性过滤（不通过直接out） =====
    if not (not np.isnan(ma5) and close > ma5):
        return 0.0, {'out': '未站上MA5'}
    if mom5 > 9:
        return 0.0, {'out': f'5日涨{mom5:.0f}%过热'}
    if market_change < -2:
        return 0.0, {'out': '大盘大跌'}
    
    # ===== 条件1: 均线斜率向上 or 走平 =====
    if not np.isnan(ma5) and not np.isnan(ma20) and ma20 > 0:
        slope = (ma5 - ma20) / ma20 * 100
        if slope > -1:
            total += 1
            passed['均线支撑'] = True
    
    # ===== 条件2: 均线偏离合理 =====
    if not np.isnan(ma20) and ma20 > 0:
        dev = (close - ma20) / ma20 * 100
        if -2 <= dev <= 6:
            total += 1
            passed['均线偏离适中'] = True
    
    # ===== 条件3: RSI在合理区间 =====
    if not np.isnan(rsi14):
        if 40 <= rsi14 <= 65:
            total += 1
            passed['RSI合理'] = True
    
    # ===== 条件4: 底部区域启动 =====
    if 20 <= bo <= 55:
        total += 1
        passed['底部启动'] = True
    
    # ===== 条件5: 温和放量 or 平量 =====
    if 1.0 <= vr <= 3.0:
        total += 1
        passed['量能配合'] = True
    
    # ===== 条件6: 涨幅控制 =====
    if -0.5 <= rt_change <= 2.0:
        total += 1
        passed['涨幅适中'] = True
    
    # ===== 条件7: MACD not dead =====
    if dif > dea or abs(dif - dea) < 0.05:
        total += 1
        passed['MACD非死叉'] = True
    
    # ===== 条件8: 大盘环境 =====
    if market_change >= -1:
        total += 1
        passed['大盘温和'] = True
    elif -2 <= market_change < -1:
        pass  # 不扣分也不加分
    
    # ===== 条件9: 趋势强度 =====
    if 40 <= ts <= 65:
        total += 1
        passed['趋势稳定'] = True
    
    # ===== 将通过层数转为分数 =====
    if total >= 7:
        # 通过7+层：高置信度
        base_score = 40 + (total - 7) * 8
    elif total >= 5:
        base_score = 30 + (total - 5) * 5
    elif total >= 3:
        base_score = 15 + (total - 3) * 5
    else:
        base_score = 0
    
    final = min(base_score, 75)
    return float(final), {k: 5 for k in passed.keys()}
