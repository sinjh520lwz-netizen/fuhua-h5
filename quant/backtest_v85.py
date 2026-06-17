"""v8.5 — v7.0(规则分层) + v8.0(三力共振) 双确认系统
只有同时通过v7.0规则的严格版 和 v8.0的双力共振，才推荐
目标是T+1胜率55%+
"""
import numpy as np

def score_v85(ind, rt_change=0, market_change=0):
    if not ind:
        return 0.0, {}
    
    close = ind['close']
    ma5, ma10, ma20 = ind['ma5'], ind['ma10'], ind['ma20']
    dif, dea = ind['dif'], ind['dea']
    rsi14 = ind['rsi14']
    vr = ind['vol_ratio']
    ts = ind['trend_score']
    bo = ind.get('breakout', 50)
    boll_pos = ind.get('boll_pos', 50)
    mom5 = ind['mom_5d']
    mom10 = ind['mom_10d']
    ma_conv = ind.get('ma_convergence', 999)
    vol_momentum = ind.get('vol_momentum', vr)
    
    factors = {}
    
    # ===== 硬过滤 =====
    if not (not np.isnan(ma5) and close > ma5):
        return 0.0, {'out': '未站上MA5'}
    if mom5 > 8:
        return 0.0, {'out': f'{mom5:.0f}%过热'}
    if market_change < -2:
        return 0.0, {'out': '大盘大跌'}
    if rt_change < -1.5 or rt_change > 4:
        return 0.0, {'out': f'涨幅{rt_change:.1f}%不合适'}
    
    # ===== v7.0 严格规则（需要过6条） =====
    rules_passed = 0
    
    # R1: 均线支撑 (ma5 > ma10 or ma_slope > -1)
    if not np.isnan(ma5) and not np.isnan(ma10) and ma10 > 0:
        if (ma5 - ma10)/ma10*100 > -1:
            rules_passed += 1
            factors['R1均线支撑'] = 1
    
    # R2: 均线偏离合理
    if not np.isnan(ma20) and ma20 > 0:
        dev = (close - ma20)/ma20*100
        if -1.5 <= dev <= 5:
            rules_passed += 1
            factors['R2偏离适中'] = 1
    
    # R3: RSI区间
    if not np.isnan(rsi14) and 42 <= rsi14 <= 62:
        rules_passed += 1
        factors['R3_RSI好'] = 1
    
    # R4: 底部区域
    if 20 <= bo <= 55:
        rules_passed += 1
        factors['R4底部'] = 1
    
    # R5: 量能配合
    if 0.9 <= vr <= 2.8:
        rules_passed += 1
        factors['R5量能好'] = 1
    
    # R6: MACD非死叉
    if dif > dea or abs(dif-dea) < 0.05:
        rules_passed += 1
        factors['R6_MACD'] = 1
    
    # R7: 趋势稳定
    if 42 <= ts <= 62:
        rules_passed += 1
        factors['R7趋势'] = 1
    
    # R8: Mom5不暴跌
    if mom5 > -3:
        rules_passed += 1
        factors['R8不暴跌'] = 1
    
    if rules_passed < 6:
        return 0.0, {'out': f'仅{rules_passed}/8规则'}
    
    # ===== v8.0 三力共振检查 =====
    forces = 0
    
    # 力1: 隔夜情绪力
    f1 = 0
    if boll_pos > 48: f1 += 1  # 收盘偏上
    if 0.1 < rt_change < 3: f1 += 1  # 温和涨
    if not np.isnan(ma10) and close > ma10: f1 += 1  # 站MA10
    if f1 >= 2: 
        forces += 1
        factors['F1隔夜'] = f1
    
    # 力2: 尾盘驱动力
    f2 = 0
    if not np.isnan(ma5) and close > ma5: f2 += 1
    if vr > 1.2: f2 += 1
    if not np.isnan(rsi14) and 40 <= rsi14 <= 60: f2 += 1
    if f2 >= 2:
        forces += 1
        factors['F2尾盘'] = f2
    
    # 力3: 主力持仓力
    f3 = 0
    if vol_momentum < 1.1: f3 += 1  # 近期缩量
    if not np.isnan(ma_conv) and ma_conv < 12: f3 += 1
    if 40 <= ts <= 58: f3 += 1
    if f3 >= 2:
        forces += 1
        factors['F3持仓'] = f3
    
    if forces < 2:
        return 0.0, {'out': f'仅{forces}/3力共振'}
    
    # ===== 通过！ =====
    score = 45 + rules_passed * 3 + forces * 5
    factors['双确认'] = f'规则{rules_passed}/8+力{forces}/3'
    
    return float(min(score, 85)), factors
