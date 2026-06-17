#!/usr/bin/env python3
"""
JH 三力共振 T+1 信号系统 v8.0
基于MiMo第三轮学习产出
三力：隔夜情绪力 + 尾盘驱动力 + 主力持仓力
当 >= 2个力共振时推荐
"""
import numpy as np

def score_v80(ind, rt_change=0, market_change=0):
    """
    v8.0 三力共振信号
    返回: (分数, {信号详情})
    分数=通过的力量数×20
    """
    if not ind:
        return 0.0, {}
    
    close = ind['close']
    ma5, ma10, ma20 = ind['ma5'], ind['ma10'], ind['ma20']
    ma60 = ind.get('ma60', np.nan)
    dif, dea = ind['dif'], ind['dea']
    rsi14 = ind['rsi14']
    vr = ind['vol_ratio']  # 当日量/5日均量
    mom5 = ind['mom_5d']
    mom10 = ind['mom_10d']
    ts = ind['trend_score']
    bo = ind.get('breakout', 50)
    boll_pos = ind.get('boll_pos', 50)
    ma_conv = ind.get('ma_convergence', 999)
    vol_momentum = ind.get('vol_momentum', vr)  # 5日均量/20日均量
    
    # 基础价量
    vol = ind.get('volume_change', 1.0)  # 今日量/前5日均量
    open_p = ind.get('close', close) / (1 + rt_change/100) if abs(rt_change) < 20 else close * 0.99
    high = ind.get('boll_upper', close * 1.03)
    low = ind.get('boll_lower', close * 0.97)
    
    signals = {}
    force_count = 0
    
    # ---- 硬性过滤 ----
    if mom5 > 9:
        return 0.0, {'out': f'5日涨{mom5:.0f}%过热'}
    if market_change < -2.5:
        return 0.0, {'out': '大盘大跌'}
    if not (not np.isnan(ma5) and close > ma5):
        return 0.0, {'out': '未站上MA5'}
    
    # ========== 力1: 隔夜情绪力 ==========
    force1 = False
    force1_details = []
    
    # 信号A: 收盘在日内上半区 (close > (high+low)/2)
    # 用boll_pos代替：boll_pos>50表示在布林上半区
    if boll_pos > 50:
        force1_details.append('收盘偏强')
    
    # 信号B: 温和上涨 (涨幅>0且<5%)
    if 0 < rt_change < 4:
        force1_details.append('涨幅温和')
    
    # 收盘站上MA10
    if not np.isnan(ma10) and close > ma10:
        force1_details.append('站上MA10')
    
    # MACD非死叉
    if dif > dea or abs(dif-dea) < 0.05:
        force1_details.append('MACD健康')
    
    if len(force1_details) >= 2:
        force1 = True
        force_count += 1
        signals['隔夜情绪力'] = '|'.join(force1_details[:2])
    
    # ========== 力2: 尾盘驱动力 ==========
    force2 = False
    force2_details = []
    
    # 信号C: 收盘站上MA5 + 成交量>5日均量
    if not np.isnan(ma5) and close > ma5 and vr > 1.0:
        force2_details.append('价站均线+放量')
    
    # 信号D: 日内下探反弹 (close > open 的代理)
    # mom_5d > 0 且 close > ma5 = 短期走强
    if mom5 > 0 and not np.isnan(ma5) and close > ma5:
        force2_details.append('短期走强')
    
    # RSI从低位回升 (40-60)
    if not np.isnan(rsi14) and 40 <= rsi14 <= 60:
        force2_details.append('RSI中位')
    
    if len(force2_details) >= 2:
        force2 = True
        force_count += 1
        signals['尾盘驱动力'] = '|'.join(force2_details[:2])
    
    # ========== 力3: 主力持仓力 ==========
    force3 = False
    force3_details = []
    
    # 信号E: 缩量盘整 (量<20日均量 且 振幅小)
    # vol_momentum < 1 = 近期缩量
    if vol_momentum < 1:
        force3_details.append('缩量')
    # ma_convergence小 = 均线粘合 = 盘整
    if not np.isnan(ma_conv) and ma_conv < 10:
        force3_details.append('均线粘合')
    
    # 信号F: 上涨缩量 (涨了但量不大)
    if rt_change > 0 and vr < 2.0:
        force3_details.append('上涨温和量')
    
    # 趋势强度适中（非极端）
    if 40 <= ts <= 60:
        force3_details.append('趋势稳定')
    
    if len(force3_details) >= 2:
        force3 = True
        force_count += 1
        signals['主力持仓力'] = '|'.join(force3_details[:2])
    
    # ========== 综合评分 ==========
    if force_count >= 3:
        # 三力共振：最强信号
        score = 70
        signals['共振'] = '三力共振'
    elif force_count == 2:
        score = 50
        signals['共振'] = '双力共振'
    elif force_count == 1:
        score = 20
        signals['共振'] = '单力'
    else:
        score = 0
        signals['共振'] = '无力'
    
    # 大盘加分
    if market_change > 1:
        score += 5
    elif market_change < -1.5:
        score -= 5
    
    final = min(max(score, 0), 90)
    return float(final), signals
