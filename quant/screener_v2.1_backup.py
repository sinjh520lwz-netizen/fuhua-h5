#!/usr/bin/env python3
"""
JH 智能选股引擎 v2.0 — 改进版
改进点：
1. 评分小数制（87.3分），区分度大幅提升
2. 大盘情绪过滤（跌>1%时提高门槛）
3. 7天内同一只股票不重复推荐
4. 止损/止盈标签
5. 扩大标的池
"""
import json, os, sys, math, time
from ai_analyzer import batch_analyze, adjust_score_with_ai
from datetime import datetime, timedelta
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)
RESULT_FILE = os.path.join(DATA_DIR, 'recommendations.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')

# ============================================================
# 1. 数据采集
# ============================================================
def fetch_hot_stocks():
    """从预下载的同花顺热门列表读取"""
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
    """获取大盘情绪（上证指数涨跌幅）"""
    import urllib.request
    try:
        url = "https://qt.gtimg.cn/q=sh000001"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=8).read().decode('gbk')
        vals = data.split('"')[1].split('~')
        return float(vals[32])  # 涨跌幅
    except:
        return 0


def pre_filter(stocks, market_change=0):
    """预筛：涨幅0-7%，排除涨停，大盘差时提高门槛"""
    # 大盘跌>1%时，只选涨幅>2%的强势股
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
    """腾讯日K线"""
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
    """腾讯实时行情"""
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
# 2. 技术分析（小数制评分）
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
    # ABN_TURN：异常换手率（20日成交量z-score）
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
        'close': C[i], 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'dif': dif[i], 'dea': dea[i], 'macd': macd_bar[i],
        'prev_dif': dif[i-1], 'prev_dea': dea[i-1],
        'rsi14': rsi14, 'rsi6': rsi6,
        'boll_upper': boll_upper, 'boll_mid': boll_mid, 'boll_lower': boll_lower,
        'boll_width': boll_width, 'boll_pos': boll_pos,
        'vol_ratio': vol_ratio, 'abn_turn': abn_turn,
        'mom_5d': mom_5d, 'mom_10d': mom_10d, 'mom_20d': mom_20d,
        'trend_score': trend_score, 'ma_convergence': ma_conv,
        'breakout': breakout,
    }


def score_early_entry(ind, rt_change=0, market_change=0, adaptive_weights=None):
    """埋伏型评分 — 小数制，返回(float_score, factors_dict)"""
    if not ind:
        return 0.0, {}

    # 加载自适应权重
    if adaptive_weights is None:
        try:
            from weight_optimizer import get_current_weights
            adaptive_weights = get_current_weights()
        except:
            adaptive_weights = {}

    # 基础分从25降到18，避免"免费高分"（回测显示85+分反而T+1均亏-1%）
    score = 18.0
    factors = {}

    # 均线粘合（越粘合越好，用自适应权重缩放）
    mc = ind['ma_convergence']
    aw = adaptive_weights.get('ma_convergence', 10) / 10  # 归一化系数
    if mc < 1.0:
        s = 12.0 * aw
    elif mc < 1.5:
        s = 10.0 * aw
    elif mc < 2.0:
        s = 8.0 * aw
    elif mc < 3.0:
        s = 5.0 * aw
    elif mc < 5.0:
        s = 2.0 * aw
    else:
        s = 0.0
    if s > 0:
        score += s
        factors['均线粘合'] = round(s, 1)

    # MACD（用自适应权重缩放）
    dif, dea = ind['dif'], ind['dea']
    prev_dif, prev_dea = ind['prev_dif'], ind['prev_dea']
    aw = adaptive_weights.get('MACD', 13) / 13
    if dif > dea and prev_dif <= prev_dea:
        s = 13.0 * aw  # 金叉
    elif dif > dea and dif < 0:
        s = 10.0 * aw  # 零轴下方多头
    elif dif > dea and dif > 0:
        s = 5.0 * aw   # 零轴上方多头
    elif dif < dea and abs(dif - dea) < 0.05:
        s = 7.0 * aw   # 即将金叉
    else:
        s = 0.0
    if s > 0:
        score += s
        factors['MACD'] = round(s, 1)

    # RSI（用自适应权重缩放）
    rsi = ind['rsi14']
    if not np.isnan(rsi):
        aw = adaptive_weights.get('RSI', 15) / 15
        if 35 <= rsi <= 45:
            s = 15.0 * aw  # 最佳埋伏区
        elif 45 < rsi <= 55:
            s = 12.0 * aw
        elif 30 <= rsi < 35:
            s = 9.0 * aw
        elif 55 < rsi <= 65:
            s = 7.0 * aw
        elif rsi > 80:
            s = -12.0  # 超买重扣（不受权重缩放）
        elif rsi > 70:
            s = -8.0   # 加重RSI超买惩罚
        elif rsi > 65:
            s = -2.0   # 新增：65-70区间轻微扣分，防止追高
        else:
            s = 0.0
        if s != 0:
            score += s
            factors['RSI'] = round(s, 1)

    # 布林收窄
    bw = ind['boll_width']
    aw = adaptive_weights.get('布林收窄', 4) / 4
    if bw < 4:
        s = 4.0 * aw
    elif bw < 6:
        s = 3.0 * aw
    elif bw < 8:
        s = 2.0 * aw
    elif bw < 12:
        s = 1.0 * aw
    else:
        s = 0.0
    if s > 0:
        score += s
        factors['布林收窄'] = round(s, 1)

    # 放量（用自适应权重缩放）
    vr = ind['vol_ratio']
    aw = adaptive_weights.get('放量', 5) / 5
    if 1.3 <= vr <= 2.0:
        s = 5.0 * aw
    elif 2.0 < vr <= 3.5:
        s = 3.5 * aw
    elif 1.1 <= vr < 1.3:
        s = 2.0 * aw
    elif 3.5 < vr <= 5.0:
        s = -4.0  # 3.5-5倍放量：拉升出货风险
    elif vr > 5:
        s = -6.0
    else:
        s = 0.0
    if s != 0:
        score += s
        factors['放量'] = round(s, 1)

    # ABN_TURN：异常换手率（z-score，检测主力资金异动）
    abn = ind.get('abn_turn', 0)
    if abn >= 4.0:
        s = -8.0  # 极度异常：出货概率大
    elif abn >= 3.0:
        s = -4.0  # 高度异常：谨慎
    elif abn >= 2.0:
        s = 8.0   # 温和异常：主力进场信号
    elif abn >= 1.5:
        s = 6.0   # 轻度异常：关注
    elif abn <= -1.5:
        s = -3.0  # 极度缩量：无人问津
    else:
        s = 0.0
    if s != 0:
        score += s
        factors['异常换手'] = round(s, 1)

    # 站上均线
    above = sum(1 for ma_v in [ind['ma5'], ind['ma10'], ind['ma20']]
                if not np.isnan(ma_v) and ind['close'] > ma_v)
    s = {3: 8.0, 2: 5.0, 1: 2.0}.get(above, 0)
    if s > 0:
        score += s
        factors['站上均线'] = round(s, 1)

    # 趋势（用自适应权重缩放）
    ts = ind['trend_score']
    aw = adaptive_weights.get('趋势', 12) / 12
    if 55 <= ts <= 65:
        s = 12.0 * aw
    elif 65 < ts <= 70:
        s = 9.0 * aw
    elif 50 <= ts < 55:
        s = 5.0 * aw
    elif 70 < ts <= 80:
        s = -3.0  # 连续上涨风险
    elif ts > 80:
        s = -6.0
    else:
        s = 0.0
    if s != 0:
        score += s
        factors['趋势'] = round(s, 1)

    # 突破位置（用自适应权重缩放）
    bo = ind['breakout']
    aw = adaptive_weights.get('突破位置', 7) / 7
    if 65 <= bo <= 80:
        s = 7.0 * aw
    elif 55 <= bo < 65:
        s = 5.0 * aw
    elif 80 < bo <= 88:
        s = 4.0 * aw
    elif bo > 92:
        s = -5.0
    else:
        s = 0.0
    if s != 0:
        score += s
        factors['突破位置'] = round(s, 1)

    # 涨幅控制
    if 0.5 <= rt_change <= 2.0:
        s = 10.0  # 小涨最佳
    elif 2.0 < rt_change <= 4.0:
        s = 4.0
    elif 4.0 < rt_change <= 6.0:
        s = 1.0
    elif rt_change > 6.5:
        s = -4.0
    else:
        s = 0.0
    if s != 0:
        score += s
        factors['涨幅控制'] = round(s, 1)

    # 均线多头排列
    if (not np.isnan(ind['ma5']) and not np.isnan(ind['ma10']) and not np.isnan(ind['ma20'])
        and ind['ma5'] > ind['ma10'] > ind['ma20']):
        if ind['ma_convergence'] < 3:
            score += 3.0
            factors['均线多头'] = 3.0

    # 大盘惩罚（大盘跌>1%时扣分）
    if market_change < -2:
        score -= 8
    elif market_change < -1:
        score -= 4

    # 信号过载惩罚：同时触发太多因子 = 过热
    active_count = len([v for v in factors.values() if v > 0])
    if active_count >= 9:
        score -= 18
    elif active_count >= 8:
        score -= 8
    elif active_count >= 7:
        score -= 4

    final = round(min(max(score, 0), 100), 1)
    # 高分惩罚：超过85分的部分减半
    if final > 85:
        final = round(85 + (final - 85) / 2, 1)
    return final, factors


# ============================================================
# 3. 7天去重
# ============================================================
def load_recent_codes(days=7):
    """加载最近N天推荐过的股票代码（去重计数基于唯一 code）"""
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
# 4. 主流程
# ============================================================
def run_screener():
    start_time = time.time()
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] JH智能选股引擎 v2.1 启动...")

    # 大盘情绪
    market_change = fetch_market_sentiment()
    print(f"  大盘涨跌幅: {market_change:+.2f}%")

    # 热门股
    hot_stocks = fetch_hot_stocks()
    print(f"  热门股: {len(hot_stocks)}只")
    if not hot_stocks:
        return {'error': '获取热门股失败', 'stocks': []}

    # 预筛
    candidates = pre_filter(hot_stocks, market_change)
    print(f"  预筛通过: {len(candidates)}只")

    # 7天去重
    recent_codes = load_recent_codes(3)
    print(f"  3天内已推荐: {len(recent_codes)}只（跳过）")

    # 分析
    results = []
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

        score, factors = score_early_entry(ind, rt_change, market_change)
        if score < 65:
            continue

        signals = []
        if factors.get('MACD', 0) >= 11: signals.append('MACD金叉' if factors['MACD'] >= 15 else 'MACD低位多头')
        elif factors.get('MACD', 0) >= 7: signals.append('MACD多头')
        if factors.get('RSI', 0) >= 8: signals.append('RSI回升')
        if factors.get('均线粘合', 0) >= 10: signals.append('均线粘合')
        if factors.get('布林收窄', 0) >= 9: signals.append('布林收窄')
        if factors.get('放量', 0) >= 7: signals.append('温和放量')
        if factors.get('异常换手', 0) >= 6: signals.append('异常换手')
        if factors.get('站上均线', 0) >= 8: signals.append('站上三线')
        if factors.get('突破位置', 0) >= 5: signals.append('突破位置佳')
        if factors.get('趋势', 0) >= 7: signals.append('趋势向好')

        risk_tags = []
        if factors.get('RSI', 0) < 0: risk_tags.append('RSI超买')
        if factors.get('突破位置', 0) < 0: risk_tags.append('接近高点')
        if factors.get('放量', 0) < 0: risk_tags.append('放量过大')
        if factors.get('趋势', 0) < 0: risk_tags.append('连续上涨')

        results.append({
            'code': code, 'name': stock['name'], 'score': score,
            'price': rt_price, 'change': rt_change,
            'amount': rt_amount, 'vol_ratio': round(ind['vol_ratio'], 2),
            'signals': signals, 'risk_tags': risk_tags,
            'factors': {k: v for k, v in factors.items() if v > 0},
            'concepts': stock.get('concepts', []),
            'popularity': stock.get('popularity', ''),
            'is_limit_up': False,
            'indicators': {
                'ma5': round(ind['ma5'], 2) if not np.isnan(ind['ma5']) else None,
                'ma10': round(ind['ma10'], 2) if not np.isnan(ind['ma10']) else None,
                'ma20': round(ind['ma20'], 2) if not np.isnan(ind['ma20']) else None,
                'rsi14': round(ind['rsi14'], 1) if not np.isnan(ind['rsi14']) else None,
                'macd': round(ind['macd'], 3),
                'dif': round(ind['dif'], 3), 'dea': round(ind['dea'], 3),
                'vol_ratio': round(ind['vol_ratio'], 2),
                'abn_turn': round(ind['abn_turn'], 2),
                'boll_width': round(ind['boll_width'], 1),
                'ma_convergence': round(ind['ma_convergence'], 2),
            },
            'alpha': {
                'mom_5d': round(ind['mom_5d'], 2),
                'mom_10d': round(ind['mom_10d'], 2),
                'mom_20d': round(ind['mom_20d'], 2),
                'trend_score': round(ind['trend_score'], 1),
                'breakout': round(ind['breakout'], 1),
                'boll_pos': round(ind['boll_pos'], 1),
                'boll_width': round(ind['boll_width'], 1),
                'ma_convergence': round(ind['ma_convergence'], 2),
            },
        })

        if (idx + 1) % 10 == 0:
            print(f"  已分析 {idx+1}/{len(candidates)}... ({time.time()-start_time:.0f}s)")
            time.sleep(0.2)

    # === DeepSeek AI 二次分析 ===
    if results:
        print(f"  启动 DeepSeek AI 分析 ({len(results)}只)...")
        try:
            ai_results = batch_analyze(results, max_count=25, delay=0.5)
            ai_success = 0
            for r in results:
                cid = r['code']
                if cid in ai_results:
                    ai = ai_results[cid]
                    r['ai'] = ai
                    old_score = r['score']
                    r['score'] = adjust_score_with_ai(old_score, ai)
                    if r['score'] != old_score:
                        ai_success += 1
            print(f"  AI分析完成: {len(ai_results)}只, 评分调整{ai_success}只")
        except Exception as e:
            print(f"  [WARN] AI分析失败: {e}", file=sys.stderr)

    results.sort(key=lambda x: x['score'], reverse=True)
    top_picks = results[:20]

    today = now.strftime("%Y-%m-%d")
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except:
        history = {'recommendations': [], 'performance': []}
    # 移除今日旧记录，替换为最新结果（不再无限追加）
    history['recommendations'] = [
        r for r in history.get('recommendations', []) if r.get('date') != today
    ]
    for r in top_picks:
        history.setdefault('recommendations', []).append({
            'date': today, 'code': r['code'], 'name': r['name'],
            'score': r['score'], 'price': r['price'], 'signals': r['signals'],
        })
    # 只保留最近30天
    cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    history['recommendations'] = [
        r for r in history['recommendations'] if r.get('date','') >= cutoff_30d
    ]
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time
    output = {
        'version': 'JH Screener v2.1-ABN',
        'strategy': '埋伏策略v2.1（+ABN_TURN异常换手率因子）',
        'date': today, 'time': now.strftime('%H:%M:%S'),
        'market_change': round(market_change, 2),
        'total_scanned': len(hot_stocks),
        'total_candidates': len(candidates),
        'skipped_dedup': skipped_dedup,
        'total_analyzed': len(results),
        'top_picks': top_picks,
        'limit_up': [],
        'all_stocks': results[:50],
        'weights': {
            'ma_convergence': 10, 'macd_cross': 13, 'rsi_recovery': 15,
            'boll_squeeze': 4, 'volume_amp': 5, 'abn_turn': 8,
            'above_ma': 8, 'trend': 12, 'breakout_pos': 7, 'change_ctrl': 10, 'ma_bull': 3,
        },
        'weight_names': {
            'ma_convergence': '均线粘合', 'macd_cross': 'MACD金叉',
            'rsi_recovery': 'RSI回升', 'boll_squeeze': '布林收窄',
            'volume_amp': '温和放量', 'abn_turn': '异常换手',
            'above_ma': '站上均线', 'trend': '趋势向好',
            'breakout_pos': '突破位置', 'change_ctrl': '涨幅控制', 'ma_bull': '均线多头',
        },
        'history_stats': {
            'total_recommendations': len(history.get('recommendations', [])),
            'total_performance': len(history.get('performance', [])),
            'win_rate': 0,
        },
        'elapsed_seconds': round(elapsed, 1),
    }
    with open(RESULT_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n[{now.strftime('%H:%M:%S')}] 完成! 耗时{elapsed:.0f}s")
    print(f"  大盘{market_change:+.2f}% | 热门{len(hot_stocks)}只 → 预筛{len(candidates)}只 → 去重跳过{skipped_dedup}只 → 推荐{len(top_picks)}只")
    if top_picks:
        print(f"\n=== TOP5 埋伏推荐 ===")
        for i, s in enumerate(top_picks[:5]):
            sigs = ' | '.join(s['signals'][:3])
            print(f"  {i+1}. {s['name']}({s['code']}) 分:{s['score']} 涨:{s['change']:.1f}% {sigs}")
    return output


if __name__ == '__main__':
    run_screener()
