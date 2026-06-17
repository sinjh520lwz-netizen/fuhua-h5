#!/usr/bin/env python3
"""
Alpha191 + JH v2.2 混合因子选股引擎
合并国泰君安Alpha191因子（20个）与JH v2.2因子（11个）
权重分配：JH因子60%，Alpha191因子40%
"""
import json, os, sys, math, time
from datetime import datetime, timedelta
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)
RESULT_FILE = os.path.join(DATA_DIR, 'alpha191_recommendations.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')

# ============================================================
# 1. 数据采集（与screener.py共用）
# ============================================================
def fetch_hot_stocks():
    hot_file = os.path.join(DATA_DIR, 'ths_hot_list.json')
    if not os.path.exists(hot_file):
        print("[ERROR] ths_hot_list.json 不存在", file=sys.stderr)
        return []
    with open(hot_file) as f:
        data = json.load(f)
    stocks = []
    for item in data.get('data', {}).get('stock_list', []):
        code = str(item.get('code', ''))
        name = item.get('name', '')
        change = item.get('rise_and_fall', 0)
        tag = item.get('tag', {})
        popularity = tag.get('popularity_tag', '')
        concepts = tag.get('concept_tag', [])
        if not code or not name or 'ST' in name:
            continue
        stocks.append({
            'code': code, 'name': name, 'change': change,
            'popularity': popularity, 'concepts': concepts,
        })
    return stocks


def fetch_market_sentiment():
    import urllib.request
    try:
        url = "https://qt.gtimg.cn/q=sh000001"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=8).read().decode('gbk')
        vals = data.split('"')[1].split('~')
        return float(vals[32])
    except:
        return 0


def pre_filter(stocks, market_change=0):
    min_change = 2 if market_change < -1 else 0
    candidates = []
    for s in stocks:
        chg = s['change']
        pop = s.get('popularity', '')
        if chg < min_change or chg > 7:
            continue
        if '涨停' in pop or '板' in pop:
            continue
        if s['code'].startswith('688'):
            continue
        candidates.append(s)
    return candidates


def fetch_klines(code, days=60):
    import urllib.request
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,{days},qfq"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        text = urllib.request.urlopen(req, timeout=8).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}') + 1]
        data = json.loads(json_str)
        raw = data.get('data', {}).get(f'{prefix}{code}', {})
        kdata = raw.get('qfqday', []) or raw.get('day', [])
        return [{
            'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
            'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])
        } for k in kdata]
    except:
        return []


def fetch_realtime(code):
    import urllib.request
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = urllib.request.urlopen(req, timeout=8).read().decode('gbk')
        vals = data.split('"')[1].split('~')
        return {
            'name': vals[1], 'price': float(vals[3]), 'prev_close': float(vals[4]),
            'change': float(vals[32]), 'volume': float(vals[36]),
            'amount': float(vals[37]), 'turnover': float(vals[38]),
            'high': float(vals[33]), 'low': float(vals[34]),
        }
    except:
        return None


# ============================================================
# 2. 工具函数（Alpha191常用操作）
# ============================================================
def rank(arr):
    """横截面排名，标准化到0-1"""
    n = len(arr)
    if n == 0:
        return arr
    order = arr.argsort()
    ranks = np.empty(n)
    ranks[order] = np.arange(n)
    return ranks / (n - 1) if n > 1 else np.full(n, 0.5)


def ts_rank(arr, window):
    """时序排名：最近window天中的排名"""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        seg = arr[i - window + 1:i + 1]
        result[i] = (seg < arr[i]).sum() / (window - 1) if window > 1 else 0.5
    return result


def ts_corr(x, y, window):
    """滚动相关系数"""
    n = len(x)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        sx = x[i - window + 1:i + 1]
        sy = y[i - window + 1:i + 1]
        if np.std(sx) < 1e-10 or np.std(sy) < 1e-10:
            result[i] = 0
        else:
            result[i] = np.corrcoef(sx, sy)[0, 1]
    return result


def ts_mean(arr, window):
    n = len(arr)
    result = np.full(n, np.nan)
    cs = np.cumsum(arr)
    result[window - 1:] = (cs[window - 1:] - np.concatenate([[0], cs[:-window]])) / window
    return result


def ts_max(arr, window):
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        result[i] = np.max(arr[i - window + 1:i + 1])
    return result


def ts_argmax(arr, window):
    """最近window天内最大值的位置（距今天数）"""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        seg = arr[i - window + 1:i + 1]
        result[i] = window - 1 - np.argmax(seg)
    return result


def ts_argmin(arr, window):
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        seg = arr[i - window + 1:i + 1]
        result[i] = window - 1 - np.argmin(seg)
    return result


def delta(arr, period):
    n = len(arr)
    result = np.full(n, np.nan)
    result[period:] = arr[period:] - arr[:-period]
    return result


def delay(arr, period):
    n = len(arr)
    result = np.full(n, np.nan)
    result[period:] = arr[:-period]
    return result


def scale(arr, a=1):
    """缩放使绝对值之和为a"""
    s = np.nansum(np.abs(arr))
    return arr / s * a if s > 0 else arr


def signed_power(arr, exp):
    return np.sign(arr) * (np.abs(arr) ** exp)


def decay_linear(arr, window):
    """线性衰减加权均值"""
    weights = np.arange(1, window + 1, dtype=float)
    weights /= weights.sum()
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        result[i] = np.dot(arr[i - window + 1:i + 1], weights)
    return result


# ============================================================
# 3. Alpha191因子计算（20个代表性因子）
# ============================================================
def calc_alpha191_factors(O, H, L, C, V):
    """输入：open/high/low/close/volume数组，返回每个因子最后一日的原始值（用于跨股票排名）"""
    n = len(C)
    if n < 20:
        return {}

    # 日收益率
    ret = np.full(n, np.nan)
    ret[1:] = (C[1:] - C[:-1]) / C[:-1]
    ret[0] = 0

    # VWAP近似 = (H+L+C)/3
    vwap = (H + L + C) / 3

    # ADV20
    adv20 = ts_mean(V, 20)

    raw = {}

    # Alpha#001: Ts_ArgMax(SignedPower(((ret<0)?std20:close),2),5) - 0.5
    std20_ret = np.array([np.std(ret[max(0,j-19):j+1]) if j >= 19 else 0 for j in range(n)])
    cond = np.where(ret < 0, std20_ret, C)
    a = ts_argmax(signed_power(cond, 2), 5)
    raw['alpha001'] = a[n-1] - 0.5 if not np.isnan(a[n-1]) else 0

    # Alpha#002: -corr(delta(log(vol),2), (C-O)/O, 6)
    log_vol = np.log(np.maximum(V, 1))
    dlog = delta(log_vol, 2)
    co = (C - O) / np.maximum(O, 0.01)
    a = -ts_corr(dlog, co, 6)
    raw['alpha002'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#005: -TSMax(corr(ADV10, MA10, 5), 3)
    c = ts_corr(ts_mean(V, 10), ts_mean(C, 10), 5)
    a = -ts_max(c, 3)
    raw['alpha005'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#006: -corr(open, vol, 10)
    a = -ts_corr(O, V, 10)
    raw['alpha006'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#008: -(sum(O,5)*sum(ret,5) - delay(...,10))
    so = np.array([np.sum(O[max(0,j-4):j+1]) for j in range(n)])
    sr = np.array([np.sum(ret[max(0,j-4):j+1]) for j in range(n)])
    p = so * sr
    a = -(p - delay(p, 10))
    raw['alpha008'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#010: delta(if C<C1 then std20(C) else C, 5)
    cd1 = delay(C, 1)
    std20c = np.array([np.std(C[max(0,j-19):j+1]) if j >= 19 else C[j] for j in range(n)])
    inner = np.where(C < cd1, std20c, C)
    a = delta(inner, 5)
    raw['alpha010'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#012: sign(Δvol) × (-Δclose)
    a = np.sign(delta(V, 1)) * (-delta(C, 1))
    raw['alpha012'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#014: -corr(O, V, 10)
    a = -ts_corr(O, V, 10)
    raw['alpha014'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#020: -(O-H1)*(O-C1)*(O-L1) — gap signal
    a = -(O - delay(H, 1)) * (O - delay(C, 1)) * (O - delay(L, 1))
    raw['alpha020'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#025: -ret * ADV20 * vwap * (H-C)
    a = -ret * adv20 * vwap * (H - C)
    raw['alpha025'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#026: -TSMax(corr(TSRank(vol,5), TSRank(high,5), 5), 3)
    c = ts_corr(ts_rank(V, 5), ts_rank(H, 5), 5)
    a = -ts_max(c, 3)
    raw['alpha026'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#033: -(1 - O/C)
    a = -(1 - O / np.maximum(C, 0.01))
    raw['alpha033'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#041: √(H*L) - vwap
    a = np.sqrt(np.maximum(H * L, 0)) - vwap
    raw['alpha041'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#042: (vwap-C) / (vwap+C)
    denom = vwap + C
    a = np.where(np.abs(denom) > 0.01, (vwap - C) / denom, 0)
    raw['alpha042'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#044: -corr(high, log(vol+1), 5)
    a = -ts_corr(H, np.log(np.maximum(V, 1)), 5)
    raw['alpha044'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#053: -delta(((C-L)-(H-C))/(C-L), 9)
    cl = np.maximum(C - L, 0.001)
    hc = H - C
    a = -delta((cl - hc) / cl, 9)
    raw['alpha053'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#054: -(L-C)*O^5 / ((L-H)*C^5)
    num = (L - C) * np.maximum(O, 0.01) ** 5
    den = np.minimum(L - H, -0.001) * np.maximum(C, 0.01) ** 5
    a = np.where(np.abs(den) > 1e-10, -num / den, 0)
    raw['alpha054'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#060: -(2*argmax(C,10)/10 - argmin(C,10)/10 - ΔV/V)
    am = ts_argmax(C, 10) / 10
    ami = ts_argmin(C, 10) / 10
    dv = delta(V, 1) / np.maximum(V, 1)
    a = -(2 * am - ami - dv)
    raw['alpha060'] = a[n-1] if not np.isnan(a[n-1]) else 0

    # Alpha#101: (C-O)/(H-L+0.001)
    a = (C - O) / (H - L + 0.001)
    raw['alpha101'] = a[n-1] if not np.isnan(a[n-1]) else 0

    return raw


def normalize_alpha_batch(all_raw):
    """跨股票排名标准化：所有股票的同一因子一起排名，映射到0-100"""
    if not all_raw:
        return []
    keys = list(all_raw[0].keys())
    n = len(all_raw)

    # 对每个因子做跨股票排名
    factor_ranks = {}
    for k in keys:
        vals = np.array([r.get(k, 0) for r in all_raw])
        # 处理NaN/Inf
        valid = np.isfinite(vals)
        if valid.sum() < 2:
            factor_ranks[k] = np.full(n, 50.0)
            continue
        # 排名到0-100
        ranks = np.zeros(n)
        valid_idx = np.where(valid)[0]
        valid_vals = vals[valid_idx]
        order = valid_vals.argsort()
        temp = np.zeros(len(valid_idx))
        temp[order] = np.arange(len(valid_idx))
        scale = (len(valid_idx) - 1) if len(valid_idx) > 1 else 1
        ranks[valid_idx] = temp / scale * 100
        # 无效的给50
        ranks[~valid] = 50
        factor_ranks[k] = ranks

    # 组装结果
    results = []
    for i in range(n):
        norm = {}
        for k in keys:
            val = factor_ranks[k][i]
            # 对alpha008(反转)、alpha012(量价反转)、alpha020(缺口信号)：低raw值=好信号，取反
            if k in ('alpha008', 'alpha012', 'alpha020', 'alpha041', 'alpha054', 'alpha060'):
                val = 100 - val
            norm[k] = round(val, 1)
        results.append(norm)
    return results


# ============================================================
# 4. JH v2.2因子评分（与screener.py相同逻辑）
# ============================================================
def quick_analyze(klines):
    """快速计算技术指标"""
    if len(klines) < 30:
        return None
    C = np.array([k['close'] for k in klines])
    H = np.array([k['high'] for k in klines])
    L = np.array([k['low'] for k in klines])
    V = np.array([k['volume'] for k in klines])
    O = np.array([k['open'] for k in klines])
    n = len(C)
    i = n - 1

    def ma(arr, period):
        return np.mean(arr[-period:]) if len(arr) >= period else np.nan

    ma5, ma10, ma20 = ma(C, 5), ma(C, 10), ma(C, 20)
    ma60 = ma(C, 60) if n >= 60 else np.nan

    def ema(arr, period):
        result = np.zeros(len(arr))
        k = 2 / (period + 1)
        result[0] = arr[0]
        for j in range(1, len(arr)):
            result[j] = arr[j] * k + result[j-1] * (1-k)
        return result

    ema12, ema26 = ema(C, 12), ema(C, 26)
    dif = ema12 - ema26
    dea = np.zeros(n)
    dea[0] = dif[0]
    for j in range(1, n):
        dea[j] = dif[j] * 0.2 + dea[j-1] * 0.8
    macd_bar = (dif - dea) * 2

    def rsi(arr, period):
        if len(arr) < period + 1: return np.nan
        gains, losses = 0, 0
        for j in range(len(arr) - period, len(arr)):
            chg = arr[j] - arr[j-1]
            if chg > 0: gains += chg
            else: losses -= chg
        ag, al = gains / period, losses / period
        return 100 - (100 / (1 + ag / al)) if al else 100

    rsi14, rsi6 = rsi(C, 14), rsi(C, 6)
    boll_mid = ma20
    boll_std = np.std(C[-20:]) if n >= 20 else 0
    boll_upper = boll_mid + 2 * boll_std
    boll_lower = boll_mid - 2 * boll_std
    boll_width = (boll_upper - boll_lower) / boll_mid * 100 if boll_mid > 0 else 0
    boll_pos = (C[i] - boll_lower) / (boll_upper - boll_lower) * 100 if boll_upper != boll_lower else 50

    vol_ma5 = ma(V, 5)
    vol_ratio = V[i] / vol_ma5 if vol_ma5 > 0 else 1
    vol_ma20 = ma(V, 20)
    vol_std20 = np.std(V[-20:]) if n >= 20 else 0
    abn_turn = (V[i] - vol_ma20) / vol_std20 if vol_std20 > 0 else 0
    mom_5d = (C[i] / C[i-5] - 1) * 100 if i >= 5 else 0
    mom_10d = (C[i] / C[i-10] - 1) * 100 if i >= 10 else 0
    mom_20d = (C[i] / C[i-20] - 1) * 100 if i >= 20 else 0

    up_days = sum(1 for j in range(max(0, i-19), i+1) if C[j] >= O[j])
    trend_score = up_days / min(20, i+1) * 100

    ma_vals = [v for v in [ma5, ma10, ma20] if not np.isnan(v)]
    ma_conv = np.std(ma_vals) / np.mean(ma_vals) * 100 if len(ma_vals) >= 2 and np.mean(ma_vals) > 0 else 999

    if n >= 20:
        high_20, low_20 = np.max(H[i-19:i+1]), np.min(L[i-19:i+1])
        breakout = (C[i] - low_20) / (high_20 - low_20) * 100 if high_20 != low_20 else 50
    else:
        breakout = 50

    return {
        'close': C[i], 'open': O[i], 'high': H[i], 'low': L[i],
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'dif': dif[i], 'dea': dea[i], 'macd': macd_bar[i],
        'prev_dif': dif[i-1], 'prev_dea': dea[i-1],
        'rsi14': rsi14, 'rsi6': rsi6,
        'boll_upper': boll_upper, 'boll_mid': boll_mid, 'boll_lower': boll_lower,
        'boll_width': boll_width, 'boll_pos': boll_pos,
        'vol_ratio': vol_ratio, 'abn_turn': abn_turn,
        'mom_5d': mom_5d, 'mom_10d': mom_10d, 'mom_20d': mom_20d,
        'trend_score': trend_score, 'ma_convergence': ma_conv,
        'breakout': breakout,
        # 传给alpha191用的原始数组
        '_C': C, '_H': H, '_L': L, '_V': V, '_O': O,
    }


def score_jh_factors(ind, rt_change=0, market_change=0):
    """JH v2.2因子评分，返回(float_score, factors_dict)"""
    if not ind:
        return 0.0, {}

    score = 18.0
    factors = {}

    # 均线粘合
    mc = ind['ma_convergence']
    if mc < 1.0: s = 10.0
    elif mc < 1.5: s = 8.0
    elif mc < 2.0: s = 6.0
    elif mc < 3.0: s = 4.0
    elif mc < 5.0: s = 2.0
    else: s = 0.0
    if s > 0: score += s; factors['均线粘合'] = round(s, 1)

    # MACD
    dif, dea = ind['dif'], ind['dea']
    prev_dif, prev_dea = ind['prev_dif'], ind['prev_dea']
    if dif > dea and prev_dif <= prev_dea: s = 15.0
    elif dif > dea and dif < 0: s = 12.0
    elif dif > dea and dif > 0: s = 6.0
    elif dif < dea and abs(dif - dea) < 0.05: s = 8.0
    else: s = 0.0
    if s > 0: score += s; factors['MACD'] = round(s, 1)

    # RSI
    rsi = ind['rsi14']
    if not np.isnan(rsi):
        if 35 <= rsi <= 45: s = 13.0
        elif 45 < rsi <= 55: s = 11.0
        elif 30 <= rsi < 35: s = 9.0
        elif 55 < rsi <= 65: s = 7.0
        elif rsi > 80: s = -12.0
        elif rsi > 70: s = -8.0
        elif rsi > 65: s = -2.0
        else: s = 0.0
        if s != 0: score += s; factors['RSI'] = round(s, 1)

    # 布林收窄
    bw = ind['boll_width']
    if bw < 4: s = 3.0
    elif bw < 6: s = 2.0
    elif bw < 8: s = 1.5
    elif bw < 12: s = 0.5
    else: s = 0.0
    if s > 0: score += s; factors['布林收窄'] = round(s, 1)

    # 放量
    vr = ind['vol_ratio']
    if 1.3 <= vr <= 2.0: s = 7.0
    elif 2.0 < vr <= 3.5: s = 5.0
    elif 1.1 <= vr < 1.3: s = 3.0
    elif 3.5 < vr <= 5.0: s = -3.0
    elif vr > 5: s = -5.0
    else: s = 0.0
    if s != 0: score += s; factors['放量'] = round(s, 1)

    # 异常换手
    abn = ind.get('abn_turn', 0)
    if abn >= 4.5: s = -8.0
    elif abn >= 3.5: s = -4.0
    elif abn >= 3.0: s = 2.0
    elif abn >= 2.5: s = 1.0
    elif abn <= -2.0: s = -2.0
    else: s = 0.0
    if s != 0: score += s; factors['异常换手'] = round(s, 1)

    # 站上均线
    above = sum(1 for ma_v in [ind['ma5'], ind['ma10'], ind['ma20']]
                if not np.isnan(ma_v) and ind['close'] > ma_v)
    s = {3: 8.0, 2: 5.0, 1: 2.0}.get(above, 0)
    if s > 0: score += s; factors['站上均线'] = round(s, 1)

    # 趋势
    ts = ind['trend_score']
    if 55 <= ts <= 65: s = 12.0
    elif 65 < ts <= 70: s = 10.0
    elif 50 <= ts < 55: s = 6.0
    elif 70 < ts <= 80: s = -2.0
    elif ts > 80: s = -5.0
    else: s = 0.0
    if s != 0: score += s; factors['趋势'] = round(s, 1)

    # 突破位置
    bo = ind['breakout']
    if 65 <= bo <= 80: s = 7.0
    elif 55 <= bo < 65: s = 5.0
    elif 80 < bo <= 88: s = 4.0
    elif bo > 92: s = -5.0
    else: s = 0.0
    if s != 0: score += s; factors['突破位置'] = round(s, 1)

    # 涨幅控制
    if 0.5 <= rt_change <= 1.5: s = 10.0
    elif 1.5 < rt_change <= 3.0: s = 7.0
    elif 3.0 < rt_change <= 5.0: s = 3.0
    elif rt_change > 6.0: s = -3.0
    else: s = 0.0
    if s != 0: score += s; factors['涨幅控制'] = round(s, 1)

    # 均线多头
    if (not np.isnan(ind['ma5']) and not np.isnan(ind['ma10']) and not np.isnan(ind['ma20'])
        and ind['ma5'] > ind['ma10'] > ind['ma20']):
        if ind['ma_convergence'] < 3:
            score += 5.0; factors['均线多头'] = 5.0
        elif ind['ma_convergence'] < 5:
            score += 3.0; factors['均线多头'] = 3.0

    # 大盘惩罚
    if market_change < -2: score -= 8
    elif market_change < -1: score -= 4

    # 信号过载惩罚
    active_count = len([v for v in factors.values() if v > 0])
    if active_count >= 9: score -= 18
    elif active_count >= 8: score -= 8
    elif active_count >= 7: score -= 4

    final = round(min(max(score, 0), 100), 1)
    if final > 85:
        final = round(85 + (final - 85) / 2, 1)
    return final, factors


def score_alpha191_factors(alpha_norm):
    """Alpha191因子综合评分（加权平均），返回0-100"""
    if not alpha_norm:
        return 0.0, {}

    # 8个精选Alpha191因子（经过IC筛选，剔除低预测力因子）
    weights = {
        'alpha001': 10, 'alpha002': 10, 'alpha006': 10, 'alpha012': 10,
        'alpha020': 10, 'alpha033': 10, 'alpha041': 10, 'alpha054': 10,
    }

    total_w = 0
    total_score = 0
    factor_details = {}
    for key, w in weights.items():
        val = alpha_norm.get(key, 50)
        total_score += val * w
        total_w += w
        # 中文名称映射
        name_map = {
            'alpha001': '动量偏度', 'alpha002': '量价背离', 'alpha006': '开量相关',
            'alpha012': '量价反转', 'alpha020': '缺口信号', 'alpha033': '日内动量',
            'alpha041': '价格重心', 'alpha054': 'K线强度',
        }
        factor_details[name_map.get(key, key)] = round(val, 1)

    final = total_score / total_w if total_w > 0 else 50
    return round(final, 1), factor_details


# ============================================================
# 5. 混合评分
# ============================================================
def hybrid_score(jh_score, jh_factors, alpha_score, alpha_factors):
    """JH 60% + Alpha191 40% 混合评分"""
    mixed = jh_score * 0.6 + alpha_score * 0.4
    # 合并因子详情
    all_factors = {}
    for k, v in jh_factors.items():
        all_factors[f'JH.{k}'] = v
    for k, v in alpha_factors.items():
        all_factors[f'A.{k}'] = v
    return round(mixed, 1), all_factors


# ============================================================
# 6. 去重
# ============================================================
def load_recent_codes(days=7):
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except:
        return set()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = set()
    for rec in history.get('recommendations', []):
        if rec.get('date', '') >= cutoff:
            code = rec.get('code', '')
            if code:
                recent.add(code)
    return recent


# ============================================================
# 7. 主流程
# ============================================================
def run_screener():
    start_time = time.time()
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] Alpha191+JH混合选股引擎 启动...")

    market_change = fetch_market_sentiment()
    print(f"  大盘涨跌幅: {market_change:+.2f}%")

    hot_stocks = fetch_hot_stocks()
    print(f"  热门股: {len(hot_stocks)}只")
    if not hot_stocks:
        return {'error': '获取热门股失败', 'stocks': []}

    candidates = pre_filter(hot_stocks, market_change)
    print(f"  预筛通过: {len(candidates)}只")

    recent_codes = load_recent_codes(3)
    print(f"  3天内已推荐: {len(recent_codes)}只（跳过）")

    results = []
    raw_data = []  # 收集原始数据用于批量标准化
    skipped_dedup = 0
    for idx, stock in enumerate(candidates):
        code = stock['code']
        if code in recent_codes:
            skipped_dedup += 1
            continue

        klines = fetch_klines(code, 60)
        if len(klines) < 30:
            continue

        rt = fetch_realtime(code)
        rt_change = rt['change'] if rt else stock['change']
        rt_price = rt['price'] if rt else klines[-1]['close']
        rt_amount = rt['amount'] if rt else 0

        if rt_amount < 50000:
            continue

        ind = quick_analyze(klines)
        if not ind:
            continue

        # JH v2.2评分
        jh_score, jh_factors = score_jh_factors(ind, rt_change, market_change)

        # Alpha191因子计算（原始值）
        alpha_raw = calc_alpha191_factors(ind['_O'], ind['_H'], ind['_L'], ind['_C'], ind['_V'])

        raw_data.append({
            'stock': stock, 'ind': ind, 'rt_change': rt_change,
            'rt_price': rt_price, 'rt_amount': rt_amount,
            'jh_score': jh_score, 'jh_factors': jh_factors,
            'alpha_raw': alpha_raw
        })

    # 批量标准化Alpha191分数
    if raw_data:
        all_alpha_raw = [d['alpha_raw'] for d in raw_data]
        all_alpha_norm = normalize_alpha_batch(all_alpha_raw)

        for i, d in enumerate(raw_data):
            alpha_norm = all_alpha_norm[i]
            alpha_score, alpha_details = score_alpha191_factors(alpha_norm)

            # 混合评分：JH×60% + Alpha191×40%
            hybrid, all_factors = hybrid_score(d['jh_score'], d['jh_factors'], alpha_score, alpha_details)

            # 阈值：混合分>=55才入选（比纯JH低一些）
            if hybrid < 55:
                continue

            signals = []
            if d['jh_factors'].get('MACD', 0) >= 11: signals.append('MACD金叉' if d['jh_factors']['MACD'] >= 15 else 'MACD低位多头')
            elif d['jh_factors'].get('MACD', 0) >= 7: signals.append('MACD多头')
            if d['jh_factors'].get('RSI', 0) >= 8: signals.append('RSI回升')
            if d['jh_factors'].get('均线粘合', 0) >= 8: signals.append('均线粘合')
            if d['jh_factors'].get('放量', 0) >= 7: signals.append('温和放量')
            if d['jh_factors'].get('站上均线', 0) >= 8: signals.append('站上三线')
            if d['jh_factors'].get('趋势', 0) >= 7: signals.append('趋势向好')
            # Alpha信号
            if alpha_details.get('量价反转', 50) >= 65: signals.append('Alpha量价反转')
            if alpha_details.get('动量偏度', 50) >= 65: signals.append('Alpha动量偏度')
            if alpha_details.get('K线强度', 50) >= 65: signals.append('AlphaK线强势')

            risk_tags = []
            if d['jh_factors'].get('RSI', 0) < 0: risk_tags.append('RSI超买')
            if d['jh_factors'].get('突破位置', 0) < 0: risk_tags.append('接近高点')
            if d['jh_factors'].get('放量', 0) < 0: risk_tags.append('放量过大')
            if d['jh_factors'].get('趋势', 0) < 0: risk_tags.append('连续上涨')

            results.append({
                'code': d['stock']['code'], 'name': d['stock']['name'], 'score': hybrid,
                'jh_score': d['jh_score'], 'alpha_score': alpha_score,
                'price': d['rt_price'], 'change': d['rt_change'],
                'amount': d['rt_amount'], 'vol_ratio': round(d['ind']['vol_ratio'], 2),
                'signals': signals, 'risk_tags': risk_tags,
                'factors': {k: v for k, v in all_factors.items() if v > 0},
                'concepts': d['stock'].get('concepts', []),
                'popularity': d['stock'].get('popularity', ''),
                'is_limit_up': False,
                'indicators': {
                    'ma5': round(d['ind']['ma5'], 2) if not np.isnan(d['ind']['ma5']) else None,
                    'ma10': round(d['ind']['ma10'], 2) if not np.isnan(d['ind']['ma10']) else None,
                    'ma20': round(d['ind']['ma20'], 2) if not np.isnan(d['ind']['ma20']) else None,
                    'rsi14': round(d['ind']['rsi14'], 1) if not np.isnan(d['ind']['rsi14']) else None,
                    'macd': round(d['ind']['macd'], 3),
                    'vol_ratio': round(d['ind']['vol_ratio'], 2),
                    'boll_width': round(d['ind']['boll_width'], 1),
                    'ma_convergence': round(d['ind']['ma_convergence'], 2),
                },
                'alpha': alpha_details,
                'alpha_raw': {k: round(v, 4) for k, v in alpha_norm.items()},
            })

            if (i + 1) % 10 == 0:
                print(f"  已分析 {i+1}/{len(raw_data)}... ({time.time()-start_time:.0f}s)")
                time.sleep(0.2)

    results.sort(key=lambda x: x['score'], reverse=True)
    top_picks = results[:20]

    today = now.strftime("%Y-%m-%d")
    elapsed = time.time() - start_time

    # 权重配置
    weights = {
        'ma_convergence': 6, 'macd_cross': 9, 'rsi_recovery': 8,
        'boll_squeeze': 2, 'volume_amp': 4, 'abn_turn': 5,
        'above_ma': 5, 'trend': 7, 'breakout_pos': 4, 'change_ctrl': 6, 'ma_bull': 3,
        'alpha_momentum': 5, 'alpha_reversal': 5, 'alpha_volatility': 4,
        'alpha_liquidity': 4, 'alpha_technical': 4,
    }
    weight_names = {
        'ma_convergence': '均线粘合(JH)', 'macd_cross': 'MACD金叉(JH)',
        'rsi_recovery': 'RSI回升(JH)', 'boll_squeeze': '布林收窄(JH)',
        'volume_amp': '温和放量(JH)', 'abn_turn': '异常换手(JH)',
        'above_ma': '站上均线(JH)', 'trend': '趋势向好(JH)',
        'breakout_pos': '突破位置(JH)', 'change_ctrl': '涨幅控制(JH)',
        'ma_bull': '均线多头(JH)',
        'alpha_momentum': '动量因子(A191)', 'alpha_reversal': '反转因子(A191)',
        'alpha_volatility': '波动率因子(A191)', 'alpha_liquidity': '流动性因子(A191)',
        'alpha_technical': '技术因子(A191)',
    }

    output = {
        'version': 'Alpha191+JH v1.0',
        'strategy': 'Alpha191(20因子)×40% + JH v2.2(11因子)×60% 混合策略',
        'date': today, 'time': now.strftime('%H:%M:%S'),
        'market_change': round(market_change, 2),
        'total_scanned': len(hot_stocks),
        'total_candidates': len(candidates),
        'skipped_dedup': skipped_dedup,
        'total_analyzed': len(results),
        'top_picks': top_picks,
        'limit_up': [],
        'all_stocks': results[:50],
        'weights': weights,
        'weight_names': weight_names,
        'alpha_factor_count': 20,
        'jh_factor_count': 11,
        'blend_ratio': {'JH': 60, 'Alpha191': 40},
        'elapsed_seconds': round(elapsed, 1),
    }
    with open(RESULT_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n[{now.strftime('%H:%M:%S')}] 完成! 耗时{elapsed:.0f}s")
    print(f"  大盘{market_change:+.2f}% | 热门{len(hot_stocks)}只 → 预筛{len(candidates)}只 → 去重跳过{skipped_dedup}只 → 推荐{len(top_picks)}只")
    if top_picks:
        print(f"\n=== TOP5 Alpha191+JH混合推荐 ===")
        for i, s in enumerate(top_picks[:5]):
            sigs = ' | '.join(s['signals'][:3])
            print(f"  {i+1}. {s['name']}({s['code']}) 混合:{s['score']} JH:{s['jh_score']} A191:{s['alpha_score']} 涨:{s['change']:.1f}% {sigs}")
    return output


if __name__ == '__main__':
    run_screener()
