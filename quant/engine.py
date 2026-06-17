#!/usr/bin/env python3
"""
JH多因子智能分析引擎 v2.1
融入量化因子：动量/价值/质量/波动/反转
修复：RSI超买降分、涨停标注、胜率计算
"""
import json, os, sys, time, math
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

WEIGHTS_FILE = os.path.join(DATA_DIR, 'weights.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
RESULT_FILE = os.path.join(DATA_DIR, 'recommendations.json')

# ============================================================
# 1. 数据采集层
# ============================================================
def fetch_hot_stocks():
    """同花顺强势股（排除已涨停，拉涨幅榜5-8%的可操作标的）"""
    import urllib.request
    from datetime import date
    stocks = []

    # 涨幅榜：拉还在涨但没涨停的（排除涨停池）
    url = "http://zx.10jqka.com.cn/event/api/getharden/date/" + date.today().strftime("%Y-%m-%d") + "/orderby/date/orderway/desc/charset/GBK/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for item in data.get('data', []):
            code = item.get('code', '')
            name = item.get('name', '')
            reason = item.get('reason', '')
            if code and not name.startswith('*ST') and not name.startswith('ST'):
                stocks.append({'code': code, 'name': name, 'reason': reason, 'source': 'limit_pool'})
    except Exception as e:
        print(f"[WARN] fetch_hot_stocks 涨停池: {e}", file=sys.stderr)

    # 涨幅榜：从预下载的文件读取（update_all.sh用curl提前下载）
    try:
        east_file = os.path.join(DATA_DIR, 'east_momentum.json')
        if os.path.exists(east_file):
            import time as _time
            # 文件超过5分钟则跳过
            if _time.time() - os.path.getmtime(east_file) < 300:
                with open(east_file) as f:
                    data3 = json.load(f)
                existing_codes = {s['code'] for s in stocks}
                for item in data3.get('data', {}).get('diff', []):
                    code = str(item.get('f12', ''))
                    name = item.get('f14', '')
                    change = item.get('f3', 0)
                    if code and code not in existing_codes and change >= 5:
                        if not name.startswith('*ST') and not name.startswith('ST'):
                            stocks.append({'code': code, 'name': name, 'reason': f'涨幅{change}%', 'source': 'momentum'})
    except Exception as e:
        print(f"[WARN] fetch_hot_stocks 涨幅榜: {e}", file=sys.stderr)

    print(f"  涨停池: {len([s for s in stocks if s.get('source')=='limit_pool'])}只, 强势可操作: {len([s for s in stocks if s.get('source')=='momentum'])}只")
    return stocks


def fetch_klines(code, days=120):
    """腾讯日K线"""
    import urllib.request
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,{days},qfq"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
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
        data = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
        vals = data.split('"')[1].split('~')
        return {
            'name': vals[1], 'price': float(vals[3]), 'prev_close': float(vals[4]),
            'change': float(vals[32]), 'volume': float(vals[36]),
            'amount': float(vals[37]), 'turnover': float(vals[38]),
            'pe': float(vals[39]) if vals[39] else 0,
            'high': float(vals[33]), 'low': float(vals[34])
        }
    except:
        return None


# ============================================================
# 2. Alpha Zoo 因子计算引擎
# ============================================================
import numpy as np

def _ma(arr, n):
    result = np.full(len(arr), np.nan)
    for i in range(n - 1, len(arr)):
        result[i] = np.mean(arr[i - n + 1:i + 1])
    return result

def _ema(arr, n):
    result = np.zeros(len(arr))
    k = 2 / (n + 1)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result

def _std(arr, n):
    result = np.full(len(arr), np.nan)
    for i in range(n - 1, len(arr)):
        result[i] = np.std(arr[i - n + 1:i + 1])
    return result

def _rsi(close, n=14):
    result = np.full(len(close), np.nan)
    for i in range(n, len(close)):
        gains, losses = 0, 0
        for j in range(i - n + 1, i + 1):
            change = close[j] - close[j - 1]
            if change > 0: gains += change
            else: losses -= change
        ag = gains / n; al = losses / n
        result[i] = 100 - (100 / (1 + ag / al)) if al else 100
    return result


class AlphaEngine:
    """量化因子计算引擎 — 融合MyTT + Alpha Zoo"""

    def __init__(self, klines):
        self.n = len(klines)
        if self.n < 60:
            return
        self.CLOSE = np.array([k['close'] for k in klines])
        self.HIGH = np.array([k['high'] for k in klines])
        self.LOW = np.array([k['low'] for k in klines])
        self.VOL = np.array([k['volume'] for k in klines])
        self.OPEN = np.array([k['open'] for k in klines])
        self.dates = [k['date'] for k in klines]

    def compute_all(self):
        """计算全部因子，返回最新值"""
        if self.n < 60:
            return None
        C, H, L, V, O = self.CLOSE, self.HIGH, self.LOW, self.VOL, self.OPEN
        n = self.n
        i = n - 1

        # ====== 技术指标层（MyTT） ======
        ma5 = _ma(C, 5); ma10 = _ma(C, 10); ma20 = _ma(C, 20); ma60 = _ma(C, 60)
        ema12 = _ema(C, 12); ema26 = _ema(C, 26)
        dif = ema12 - ema26
        dea = np.zeros(n); dea[0] = dif[0]
        for j in range(1, n): dea[j] = dif[j] * 0.2 + dea[j-1] * 0.8
        macd_bar = (dif - dea) * 2

        rsi14 = _rsi(C, 14); rsi6 = _rsi(C, 6)

        boll_mid = _ma(C, 20); boll_std = _std(C, 20)
        boll_upper = boll_mid + 2 * boll_std; boll_lower = boll_mid - 2 * boll_std

        # KDJ
        rsv = np.full(n, np.nan)
        for j in range(8, n):
            hh = np.max(H[j-8:j+1]); ll = np.min(L[j-8:j+1])
            rsv[j] = (C[j] - ll) / (hh - ll) * 100 if hh != ll else 50
        k_val = np.full(n, np.nan); d_val = np.full(n, np.nan); j_val = np.full(n, np.nan)
        for j in range(8, n):
            k_val[j] = 2/3 * (k_val[j-1] if j > 8 else 50) + 1/3 * rsv[j]
            d_val[j] = 2/3 * (d_val[j-1] if j > 8 else 50) + 1/3 * k_val[j]
            j_val[j] = 3 * k_val[j] - 2 * d_val[j]

        # ATR
        atr = np.full(n, np.nan)
        for j in range(1, n):
            tr = max(H[j]-L[j], abs(H[j]-C[j-1]), abs(L[j]-C[j-1]))
            atr[j] = tr if j < 14 else (atr[j-1]*13 + tr) / 14

        vol_ma5 = _ma(V, 5); vol_ma10 = _ma(V, 10)

        # ====== Alpha Zoo 因子层 ======

        # 因子1: 动量 MOM_12_1（Carhart 12月-1月动量）
        mom_12m = (C[i] / C[max(0, i-252)] - 1) * 100 if i >= 252 else None
        mom_1m = (C[i] / C[max(0, i-21)] - 1) * 100 if i >= 21 else None
        mom_12_1 = (mom_12m - mom_1m) if mom_12m and mom_1m else None

        # 因子2: 短期动量 MOM_5D / MOM_10D / MOM_20D
        mom_5d = (C[i] / C[i-5] - 1) * 100
        mom_10d = (C[i] / C[i-10] - 1) * 100
        mom_20d = (C[i] / C[i-20] - 1) * 100

        # 因子3: 波动率 VOL_20D（20日标准差年化）
        vol_20d = np.std(C[i-19:i+1]) / np.mean(C[i-19:i+1]) * np.sqrt(252) * 100

        # 因子4: 成交量异动 VOL_RATIO（5日量比）
        vol_ratio = V[i] / vol_ma5[i] if vol_ma5[i] > 0 else 1

        # 因子5: 趋势一致性 TREND_SCORE（过去20日有多少天收盘>开盘）
        up_days = sum(1 for j in range(i-19, i+1) if C[j] >= O[j])
        trend_score = up_days / 20 * 100

        # 因子6: 均线乖离率 BIAS（偏离MA20的程度）
        bias_20 = (C[i] / ma20[i] - 1) * 100 if not np.isnan(ma20[i]) else 0

        # 因子7: 突破强度 BREAKOUT（价格在近N日高点的位置）
        high_20 = np.max(H[i-19:i+1])
        low_20 = np.min(L[i-19:i+1])
        breakout = (C[i] - low_20) / (high_20 - low_20) * 100 if high_20 != low_20 else 50

        # 因子8: 资金强度 MONEY_FLOW（量价相关性）
        price_chg = np.diff(C[i-10:i+1])
        vol_chg = np.diff(V[i-10:i+1])
        if len(price_chg) > 1 and np.std(price_chg) > 0 and np.std(vol_chg) > 0:
            money_flow = np.corrcoef(price_chg, vol_chg)[0, 1] * 100
        else:
            money_flow = 0

        # 因子9: 相对强弱 RSI_COMPOSITE
        rsi_comp = (rsi6[i] * 0.4 + rsi14[i] * 0.6) if not np.isnan(rsi6[i]) and not np.isnan(rsi14[i]) else 50

        # 因子10: 布林带位置 BOLL_POS
        if not np.isnan(boll_upper[i]) and not np.isnan(boll_lower[i]):
            boll_pos = (C[i] - boll_lower[i]) / (boll_upper[i] - boll_lower[i]) * 100
        else:
            boll_pos = 50

        return {
            # 技术指标
            'close': C[i],
            'ma5': ma5[i], 'ma10': ma10[i], 'ma20': ma20[i],
            'ma60': ma60[i] if not np.isnan(ma60[i]) else None,
            'dif': dif[i], 'dea': dea[i], 'macd': macd_bar[i],
            'prev_dif': dif[i-1], 'prev_dea': dea[i-1],
            'rsi14': rsi14[i], 'rsi6': rsi6[i],
            'boll_upper': boll_upper[i], 'boll_mid': boll_mid[i], 'boll_lower': boll_lower[i],
            'k': k_val[i], 'd': d_val[i], 'j': j_val[i],
            'atr': atr[i], 'vol_ratio': vol_ratio,
            # Alpha Zoo 因子
            'mom_5d': mom_5d, 'mom_10d': mom_10d, 'mom_20d': mom_20d,
            'mom_12_1': mom_12_1, 'vol_20d': vol_20d,
            'trend_score': trend_score, 'bias_20': bias_20,
            'breakout': breakout, 'money_flow': money_flow,
            'rsi_comp': rsi_comp, 'boll_pos': boll_pos,
        }


# ============================================================
# 3. 多因子评分引擎 v2
# ============================================================
DEFAULT_WEIGHTS = {
    # 技术面（原版）
    'ma_trend': 12, 'macd_golden': 10, 'rsi_sweet': 8,
    'volume_amp': 10, 'boll_position': 6, 'kdj_golden': 6, 'price_above_ma': 8,
    # Alpha Zoo 因子（新增）
    'momentum': 12,       # 短期动量（5D/10D/20D综合）
    'trend_consist': 10,  # 趋势一致性（上涨天数占比）
    'breakout': 8,        # 突破强度
    'money_flow': 5,      # 资金强度（量价相关）
    'volatility': 5,      # 波动率控制（低波加分）
}


def load_weights():
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE) as f:
            return json.load(f)
    return DEFAULT_WEIGHTS.copy()


def save_weights(weights):
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump(weights, f, indent=2)


def score_stock(ind, weights=None):
    """多因子综合评分 0-100"""
    if not ind:
        return 0
    w = weights or DEFAULT_WEIGHTS
    total = sum(w.values())
    score = 0

    # --- 技术面 ---

    # 1. 均线多头
    if ind['ma5'] and ind['ma10'] and ind['ma20']:
        if ind['ma5'] > ind['ma10'] > ind['ma20']:
            score += w['ma_trend']
        elif ind['ma5'] > ind['ma10']:
            score += w['ma_trend'] * 0.5

    # 2. MACD金叉
    if ind['dif'] > ind['dea'] and ind['prev_dif'] <= ind['prev_dea']:
        score += w['macd_golden']
    elif ind['dif'] > ind['dea']:
        score += w['macd_golden'] * 0.6

    # 3. RSI甜蜜区（超买严重扣分）
    rsi = ind['rsi14']
    if rsi and not math.isnan(rsi):
        if 40 <= rsi <= 70: score += w['rsi_sweet']
        elif 30 <= rsi < 40: score += w['rsi_sweet'] * 0.7
        elif 70 < rsi <= 75: score += w['rsi_sweet'] * 0.3
        elif rsi > 80: score -= w['rsi_sweet'] * 0.5  # 超买扣分

    # 4. 放量
    vr = ind['vol_ratio']
    if 1.5 <= vr <= 5: score += w['volume_amp']
    elif 1.2 <= vr < 1.5: score += w['volume_amp'] * 0.5

    # 5. 布林带位置
    bp = ind['boll_pos']
    if 30 <= bp <= 80: score += w['boll_position']
    elif bp > 90: score += w['boll_position'] * 0.2  # 过热

    # 6. KDJ金叉
    if ind['k'] and ind['d'] and ind['k'] > ind['d'] and ind['j'] < 100:
        score += w['kdj_golden']

    # 7. 站上均线
    above = sum(1 for ma in [ind['ma5'], ind['ma10'], ind['ma20']] if ma and ind['close'] > ma)
    score += w['price_above_ma'] * (above / 3)

    # --- Alpha Zoo 因子 ---

    # 8. 短期动量（5D+10D+20D综合，正向动量加分）
    m5 = ind['mom_5d']; m10 = ind['mom_10d']; m20 = ind['mom_20d']
    avg_mom = (m5 * 0.5 + m10 * 0.3 + m20 * 0.2)
    if avg_mom > 10: score += w['momentum']
    elif avg_mom > 5: score += w['momentum'] * 0.8
    elif avg_mom > 0: score += w['momentum'] * 0.5
    elif avg_mom > -5: score += w['momentum'] * 0.2

    # 9. 趋势一致性（过去20日上涨天数）
    ts = ind['trend_score']
    if ts >= 70: score += w['trend_consist']
    elif ts >= 60: score += w['trend_consist'] * 0.7
    elif ts >= 50: score += w['trend_consist'] * 0.4

    # 10. 突破强度（在近20日区间的位置）
    bo = ind['breakout']
    if bo >= 90: score += w['breakout']  # 接近新高
    elif bo >= 75: score += w['breakout'] * 0.7
    elif bo >= 50: score += w['breakout'] * 0.4

    # 11. 资金强度（量价正相关）
    mf = ind['money_flow']
    if mf > 50: score += w['money_flow']
    elif mf > 20: score += w['money_flow'] * 0.6

    # 12. 波动率控制（低波动加分，高波动减分）
    vol = ind['vol_20d']
    if vol < 30: score += w['volatility']
    elif vol < 50: score += w['volatility'] * 0.6
    elif vol < 80: score += w['volatility'] * 0.3

    return round(score / total * 100, 1)


def get_factor_scores(ind, weights=None):
    """返回各因子的单独得分（用于页面展示）"""
    if not ind:
        return {}
    w = weights or DEFAULT_WEIGHTS
    total = sum(w.values())
    factors = {}

    # 技术面
    if ind['ma5'] and ind['ma10'] and ind['ma20'] and ind['ma5'] > ind['ma10'] > ind['ma20']:
        factors['均线多头'] = round(w['ma_trend'] / total * 100, 1)

    if ind['dif'] > ind['dea']:
        factors['MACD多头'] = round(w['macd_golden'] * (1 if ind['prev_dif'] <= ind['prev_dea'] else 0.6) / total * 100, 1)

    rsi = ind['rsi14']
    if rsi and not math.isnan(rsi) and 40 <= rsi <= 70:
        factors['RSI健康'] = round(w['rsi_sweet'] / total * 100, 1)

    if ind['vol_ratio'] >= 1.5:
        factors['放量'] = round(w['volume_amp'] / total * 100, 1)

    # Alpha因子
    avg_mom = ind['mom_5d'] * 0.5 + ind['mom_10d'] * 0.3 + ind['mom_20d'] * 0.2
    if avg_mom > 0:
        factors['动量正向'] = round(min(avg_mom / 10, 1) * w['momentum'] / total * 100, 1)

    if ind['trend_score'] >= 50:
        factors['趋势一致'] = round(ind['trend_score'] / 100 * w['trend_consist'] / total * 100, 1)

    if ind['breakout'] >= 70:
        factors['突破强势'] = round(ind['breakout'] / 100 * w['breakout'] / total * 100, 1)

    if ind['money_flow'] > 20:
        factors['资金流入'] = round(min(ind['money_flow'] / 100, 1) * w['money_flow'] / total * 100, 1)

    return factors


# ============================================================
# 4. 自迭代系统
# ============================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {'recommendations': [], 'performance': []}


def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def track_performance(history):
    """跟踪历史推荐表现"""
    today = datetime.now().strftime("%Y-%m-%d")
    updated = False
    for rec in history.get('recommendations', []):
        rec_date = rec.get('date', '')
        if rec_date == today: continue
        perf_key = f"{rec['code']}_{rec_date}"
        if any(p.get('key') == perf_key for p in history.get('performance', [])):
            continue
        days_since = (datetime.now() - datetime.strptime(rec_date, "%Y-%m-%d")).days
        if days_since < 1 or days_since > 10: continue
        klines = fetch_klines(rec['code'], 30)
        rec_price = rec.get('price', 0)
        if not klines or not rec_price: continue
        future = [k for k in klines if k['date'] > rec_date]
        if not future: continue
        perf = {'key': perf_key, 'code': rec['code'], 'name': rec.get('name', ''),
                'rec_date': rec_date, 'rec_price': rec_price, 'rec_score': rec.get('score', 0)}
        if len(future) >= 1: perf['day1_return'] = round((future[0]['close']/rec_price-1)*100, 2)
        if len(future) >= 3: perf['day3_return'] = round((future[2]['close']/rec_price-1)*100, 2)
        if len(future) >= 5: perf['day5_return'] = round((future[4]['close']/rec_price-1)*100, 2)
        if future: perf['max_return'] = round((max(k['high'] for k in future[:5])/rec_price-1)*100, 2)
        history.setdefault('performance', []).append(perf)
        updated = True
    return updated


def optimize_weights(history):
    """根据历史表现优化权重"""
    perfs = history.get('performance', [])
    if len(perfs) < 5:
        return DEFAULT_WEIGHTS.copy()
    weights = load_weights()
    winners = [p for p in perfs if (p.get('day3_return') or 0) > 3]
    losers = [p for p in perfs if (p.get('day3_return') or 0) < -2]
    if len(winners) < 2 or len(losers) < 2:
        return weights
    win_rate = len(winners) / len(perfs)
    adj = 0.05
    if win_rate > 0.6:
        weights['momentum'] = min(20, weights.get('momentum', 12) * (1 + adj))
        weights['trend_consist'] = min(15, weights.get('trend_consist', 10) * (1 + adj))
    else:
        weights['rsi_sweet'] = min(15, weights.get('rsi_sweet', 8) * (1 + adj))
        weights['volatility'] = min(10, weights.get('volatility', 5) * (1 + adj))
    total = sum(weights.values())
    weights = {k: round(v / total * 100, 1) for k, v in weights.items()}
    save_weights(weights)
    return weights


# ============================================================
# 5. 主流程
# ============================================================
def run_analysis():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] JH多因子智能分析引擎 v2.0 启动...")

    hot_stocks = fetch_hot_stocks()
    print(f"  热点股: {len(hot_stocks)}只")
    if not hot_stocks:
        return {'error': '获取热点股失败', 'stocks': []}

    history = load_history()
    track_performance(history)
    weights = optimize_weights(history)

    results = []
    for i, stock in enumerate(hot_stocks):
        code = stock['code']
        klines = fetch_klines(code, 120)
        if len(klines) < 60: continue

        rt = fetch_realtime(code)
        engine = AlphaEngine(klines)
        ind = engine.compute_all()
        if not ind: continue

        score = score_stock(ind, weights)
        factors = get_factor_scores(ind, weights)

        signals = []
        # 涨停检测
        change_pct = rt['change'] if rt else 0
        is_limit_up = abs(change_pct) >= 9.8
        rsi = ind['rsi14']

        # 风险标签
        risk_tags = []
        if rsi and not math.isnan(rsi) and rsi > 75:
            risk_tags.append('追高风险')
        if is_limit_up:
            risk_tags.append('已涨停')

        # 信号
        if ind['ma5'] > ind['ma10'] > ind['ma20']: signals.append('均线多头')
        if ind['dif'] > ind['dea'] and ind['prev_dif'] <= ind['prev_dea']: signals.append('MACD金叉')
        elif ind['dif'] > ind['dea']: signals.append('MACD多头')
        if ind['vol_ratio'] > 2: signals.append(f'放量{ind["vol_ratio"]:.1f}倍')
        if rsi and not math.isnan(rsi):
            if rsi > 80: signals.append('RSI超买⚠️')
            elif rsi > 70: signals.append('RSI偏高')
            elif rsi < 30: signals.append('RSI超卖✅')
        if ind['mom_5d'] > 5: signals.append(f'5日涨{ind["mom_5d"]:.1f}%')
        if ind['breakout'] > 90: signals.append('逼近新高')
        if ind['money_flow'] > 50: signals.append('资金强流入')

        results.append({
            'code': code, 'name': stock['name'], 'score': score,
            'price': rt['price'] if rt else ind['close'],
            'change': change_pct,
            'amount': rt['amount'] if rt else 0,
            'turnover': rt['turnover'] if rt else 0,
            'signals': signals, 'reason': stock.get('reason', ''),
            'factors': factors,
            'is_limit_up': is_limit_up,
            'risk_tags': risk_tags,
            'indicators': {
                'ma5': round(ind['ma5'], 2) if ind['ma5'] else None,
                'ma10': round(ind['ma10'], 2) if ind['ma10'] else None,
                'ma20': round(ind['ma20'], 2) if ind['ma20'] else None,
                'rsi14': round(rsi, 1) if rsi and not math.isnan(rsi) else None,
                'macd': round(ind['macd'], 3),
                'dif': round(ind['dif'], 3), 'dea': round(ind['dea'], 3),
                'vol_ratio': round(ind['vol_ratio'], 2),
            },
            'alpha': {
                'mom_5d': round(ind['mom_5d'], 2),
                'mom_10d': round(ind['mom_10d'], 2),
                'mom_20d': round(ind['mom_20d'], 2),
                'trend_score': round(ind['trend_score'], 1),
                'breakout': round(ind['breakout'], 1),
                'money_flow': round(ind['money_flow'], 1),
                'vol_20d': round(ind['vol_20d'], 1),
                'boll_pos': round(ind['boll_pos'], 1),
            },
        })

        if (i + 1) % 10 == 0:
            print(f"  已分析 {i+1}/{len(hot_stocks)}...")
            time.sleep(0.3)

    results.sort(key=lambda x: x['score'], reverse=True)

    today = datetime.now().strftime("%Y-%m-%d")
    for r in results:
        if r['score'] >= 60:
            history.setdefault('recommendations', []).append({
                'date': today, 'code': r['code'], 'name': r['name'],
                'score': r['score'], 'price': r['price'], 'signals': r['signals'],
            })
    save_history(history)

    output = {
        'version': 'JH v2.1',
        'date': today, 'time': datetime.now().strftime('%H:%M:%S'),
        'total_scanned': len(hot_stocks), 'total_analyzed': len(results),
        'top_picks': [r for r in results if r['score'] >= 55 and not r['is_limit_up']][:20],
        'limit_up': [r for r in results if r['is_limit_up'] and r['score'] >= 60][:20],
        'all_stocks': results[:50],
        'weights': weights,
        'weight_names': {
            'ma_trend': '均线趋势', 'macd_golden': 'MACD金叉', 'rsi_sweet': 'RSI健康',
            'volume_amp': '放量信号', 'boll_position': '布林位置', 'kdj_golden': 'KDJ金叉',
            'price_above_ma': '站上均线', 'momentum': '动量因子', 'trend_consist': '趋势一致性',
            'breakout': '突破强度', 'money_flow': '资金强度', 'volatility': '波动率控制',
        },
        'history_stats': {
            'total_recommendations': len(history.get('recommendations', [])),
            'total_performance': len(history.get('performance', [])),
            'win_rate': _calc_win_rate(history),
        }
    }

    with open(RESULT_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成! 推荐{len(output['top_picks'])}只 | 胜率{output['history_stats']['win_rate']}%")
    return output


def _calc_win_rate(history):
    perfs = history.get('performance', [])
    if not perfs: return 0
    wins = len([p for p in perfs if (p.get('day3_return') or 0) > 0])
    return round(wins / len(perfs) * 100, 1)


if __name__ == '__main__':
    result = run_analysis()
    print(f"\n=== TOP5 推荐 ===")
    for i, s in enumerate(result['top_picks'][:5]):
        sigs = ' | '.join(s['signals'][:3])
        print(f"{i+1}. {s['name']} {s['code']} | 评分:{s['score']} | {sigs}")
