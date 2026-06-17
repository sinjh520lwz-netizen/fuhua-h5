"""JH 横截面排名评分 — 多因子复合评分 + 超短线条件单策略"""
import numpy as np

# ========== 超短线条件单策略 v12.0 ==========

def score_tp_sl(ind, rt_change=0, market_change=0):
    """超短线条件单评分 v12.0 — 专为TP+5%/SL-6%设计"""
    if not ind:
        return 0.0, {}
    score = 10.0; factors = {}
    close = ind.get('close', 0)
    ma5 = ind.get('ma5', np.nan); ma10 = ind.get('ma10', np.nan); ma20 = ind.get('ma20', np.nan)
    dif = ind.get('dif', np.nan); dea = ind.get('dea', np.nan)
    rsi14 = ind.get('rsi14', np.nan); vr = ind.get('vol_ratio', 1)
    ts = ind.get('trend_score', 50); bo = ind.get('breakout', 50)
    mom5 = ind.get('mom_5d', 0); vol_20d = ind.get('vol_20d', 40)
    high = ind.get('high', close); low = ind.get('low', close)
    daily_range = max(high - low, 0.01); close_pos = (close - low) / daily_range * 100

    # 硬性门槛
    if not (not np.isnan(ma5) and close > ma5): return 5.0, {'淘汰': '站MA5'}
    if rt_change < 0.3 or rt_change > 3.5: return 5.0, {'淘汰': f'涨幅{rt_change:.0f}%'}
    if not np.isnan(rsi14) and rsi14 > 72: return 5.0, {'淘汰': 'RSI超买'}
    if vr < 1.0: return 5.0, {'淘汰': '缩量'}
    if mom5 < 0: return 5.0, {'淘汰': '趋势向下'}
    if vol_20d < 25: return 5.0, {'淘汰': '波动低'}

    # 上冲动能因子
    up = 0
    if close_pos >= 85: up += 12; factors['收最高'] = 12
    elif close_pos >= 70: up += 8; factors['收高位'] = 8
    elif close_pos >= 55: up += 4
    else: up -= 4

    if 1.5 <= vr <= 3.0: up += 10; factors['放量好'] = 10
    elif 3.0 < vr <= 5.0: up += 5
    elif 1.2 <= vr < 1.5: up += 4
    elif vr > 5.0: up -= 8; factors['天量'] = -8

    if not np.isnan(ma5) and not np.isnan(ma10) and not np.isnan(ma20):
        if ma5 > ma10 > ma20: up += 8; factors['多头'] = 8
    if not np.isnan(dif) and not np.isnan(dea):
        if dif > dea and dif > 0: up += 8; factors['MACD'] = 8
        elif dif > dea: up += 4
    if not np.isnan(rsi14):
        if 55 <= rsi14 <= 65: up += 6; factors['RSI好'] = 6
        elif 50 <= rsi14 < 55: up += 4
    if 2 <= mom5 <= 5: up += 8; factors['动量好'] = 8
    elif 5 < mom5 <= 7: up += 5
    elif mom5 > 7: up -= 6
    if 55 <= bo <= 75: up += 5; factors['突破'] = 5

    # 下行保护
    dp = 0
    if rt_change > 3.0: dp -= 6
    elif rt_change > 2.5: dp -= 3
    if ts > 78: dp -= 5
    elif ts > 72: dp -= 3
    if dp < 0: factors['下行风险'] = dp

    # 大盘
    if market_change < -1.5: score -= 10; factors['大盘差'] = -10
    elif market_change < -0.8: score -= 5
    elif market_change > 1: score += 3

    # 信号过载
    pos = sum(1 for v in factors.values() if isinstance(v, (int,float)) and v > 0)
    if pos >= 7: score -= 8
    elif pos >= 6: score -= 4

    score += up + dp
    final = round(min(max(score, 0), 100), 1)
    if final > 80: final = round(80 + (final - 80) * 0.4, 1)
    return final, factors


# ========== 11因子复合评分（v4.0线上版） ==========

def score_early_entry(ind, rt_change=0, market_change=0):
    """11因子复合评分 — 找刚启动，不追高"""
    if not ind:
        return 0.0, {}
    score = 10.0; factors = {}
    close = ind.get('close', 0)
    ma5 = ind.get('ma5', np.nan); ma10 = ind.get('ma10', np.nan); ma20 = ind.get('ma20', np.nan)
    dif = ind.get('dif', np.nan); dea = ind.get('dea', np.nan)
    prev_dif = ind.get('prev_dif', np.nan); prev_dea = ind.get('prev_dea', np.nan)
    rsi14 = ind.get('rsi14', np.nan); vr = ind.get('vol_ratio', 1)
    ts = ind.get('trend_score', 50); bo = ind.get('breakout', 50)
    mom5 = ind.get('mom_5d', 0); mom10 = ind.get('mom_10d', 0)
    ma60 = ind.get('ma60', np.nan)

    above_ma5 = not np.isnan(ma5) and close > ma5
    if not above_ma5: return 5.0, {'硬过滤': '未站上MA5'}
    if mom5 > 8: return 5.0, {'硬过滤': '5日涨{:.0f}%过热'.format(mom5)}

    if mom5 > 0 and mom10 < -1: score += 6; factors['刚转头'] = 6
    elif mom5 > 0 and -1 <= mom10 <= 1.5: score += 4; factors['恢复中'] = 4
    elif mom5 > 0 and mom10 > 1.5 and mom5 <= 4: score += 2; factors['温和上涨'] = 2

    if 1.3 <= vr <= 2.5: s = 8
    elif 2.5 < vr <= 4.0: s = 5
    elif 1.0 <= vr < 1.3: s = 3
    elif vr > 5: s = -6
    elif vr > 4: s = -3
    else: s = 0
    if s: score += s; factors['量价'] = s

    if not np.isnan(dif) and not np.isnan(dea) and not np.isnan(prev_dif) and not np.isnan(prev_dea):
        if dif > dea and prev_dif <= prev_dea: s = 10 + (3 if dif < 0 else 0)
        elif dif > dea: s = 4
        elif dif < dea and abs(dif - dea) < 0.03 and mom5 > 0.5: s = 6
        else: s = 0
        if s: score += s; factors['MACD'] = s

    if not np.isnan(rsi14):
        if 45 <= rsi14 <= 55: s = 8
        elif 55 < rsi14 <= 62: s = 6
        elif 38 <= rsi14 < 45: s = 5
        elif 62 < rsi14 <= 70: s = 3
        elif rsi14 > 75: s = -10
        elif rsi14 > 70: s = -6
        elif rsi14 < 30: s = -6
        else: s = 0
        if s: score += s; factors['RSI'] = s

    if 30 <= bo <= 50: s = 8
    elif 50 < bo <= 65: s = 6
    elif 20 <= bo < 30: s = 4
    elif 65 < bo <= 80: s = 2
    elif bo > 90: s = -8
    elif bo > 80: s = -4
    else: s = 0
    if s: score += s; factors['突破位'] = s

    if 0.2 <= rt_change <= 1.0: s = 8
    elif 1.0 < rt_change <= 1.8: s = 5
    elif -0.5 <= rt_change < 0.2: s = 4
    elif rt_change > 3.0: s = -10
    elif rt_change > 1.8: s = -5
    elif rt_change < -1.5: s = -4
    else: s = 0
    if s: score += s; factors['涨幅'] = s

    rel = rt_change - market_change
    if rel > 2: s = 4
    elif rel > 1: s = 2
    elif rel < -2: s = -4
    elif rel < -1: s = -2
    else: s = 0
    if s: score += s; factors['相对强'] = s

    if 48 <= ts <= 60: s = 6
    elif 60 < ts <= 70: s = 4
    elif 40 <= ts < 48: s = 2
    elif ts > 78: s = -5
    elif ts < 35: s = -4
    else: s = 0
    if s: score += s; factors['趋势'] = s

    if market_change < -2: score -= 8
    elif market_change < -1: score -= 3

    active = sum(1 for v in factors.values() if isinstance(v, (int,float)) and v > 0)
    if active >= 9: score -= 10
    elif active >= 8: score -= 5

    final = round(min(max(score, 0), 100), 1)
    if final > 75: final = round(75 + (final - 75) / 2, 1)
    return final, factors


# ========== 横截面排名引擎 ==========

def compute_ranks(candidates, market_change=0):
    """多因子复合评分 + 横截面排名"""
    n = len(candidates)
    if n == 0: return []

    abs_scores = []; factor_details = []
    for c in candidates:
        ind = c.get('indicators', {})
        rt_change = c.get('change', 0)
        s, factors = score_early_entry(ind, rt_change, market_change)
        abs_scores.append(s)
        factor_details.append(factors)

    abs_arr = np.array(abs_scores)
    sorted_idx = np.argsort(np.argsort(abs_arr))
    rank_pct = sorted_idx / (n - 1) if n > 1 else sorted_idx
    final_scores = abs_arr * 0.7 + rank_pct * 30
    final_scores = np.minimum(final_scores, 100)

    results = []
    for i, c in enumerate(candidates):
        fd = factor_details[i]
        pos_factors = []; neg_factors = []
        for k, v in fd.items():
            if isinstance(v, (int, float)):
                if v > 0: pos_factors.append(k)
                elif v < 0: neg_factors.append(k)
            else: neg_factors.append(k)
        pos_factors = sorted(pos_factors, key=lambda k: -fd[k])[:5]
        neg_factors = sorted(neg_factors, key=lambda k: fd[k] if isinstance(fd[k], (int,float)) else 0)[:3]
        signals = [f'✅{k}' for k in pos_factors] + [f'⚠{k}' for k in neg_factors]
        if not signals: signals = ['关注中']
        results.append({
            'code': c['code'], 'name': c.get('name', ''),
            'score': round(float(final_scores[i]), 1),
            'raw_score': round(float(abs_arr[i]), 1),
            'price': c['price'], 'change': c.get('change', 0),
            'amount': c.get('amount', 0),
            'signals': signals,
            'risk_tags': ['过热' if any(isinstance(fd.get(k), (int,float)) and fd.get(k) < -5 for k in fd) else ''],
            'factors': {k: round(v, 1) for k, v in fd.items() if isinstance(v, (int,float)) and abs(v) >= 2},
            'indicators': c.get('indicators', {}),
            'concepts': c.get('concepts', []),
            'popularity': c.get('popularity', ''),
        })
    results = [r for r in results if r['raw_score'] > 5]
    return results


def rank_and_filter(candidates, top_pct=0.08, min_score=40, market_change=0):
    """多因子评分 + 截面前top_pct%"""
    if not candidates: return []
    results = compute_ranks(candidates, market_change)
    results.sort(key=lambda x: -x['score'])
    n = max(1, int(len(results) * top_pct))
    top = results[:min(n, 20)]
    return [t for t in top if t['score'] >= min_score]
