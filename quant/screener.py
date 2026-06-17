#!/usr/bin/env python3
"""JH 智能选股引擎（自动版本）"""
import json, os, sys, math, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from ai_analyzer import batch_analyze, adjust_score_with_ai
from datetime import datetime, timedelta
import numpy as np
from cross_sectional_score import rank_and_filter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')

# ========== 自动版本号 ==========
VERSION_FILE = os.path.join(SCRIPT_DIR, 'version.json')

def _load_version():
    try:
        with open(VERSION_FILE) as f:
            return json.load(f)
    except:
        return {'version': 'v2.5', 'full_name': 'JH Screener v2.5', 'strategy': '埋伏策略v2.5（11因子评分）'}

_VER = _load_version()
SCREENER_FULL_NAME = _VER.get('full_name', 'JH Screener v2.5')
SCREENER_STRATEGY = _VER.get('strategy', '埋伏策略v2.5（11因子评分）')
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


def fetch_all_quotes_batch():
    """全A股批量行情初筛 — 5525只 → 候选200-500只"""
    import urllib.request
    
    list_file = os.path.join(DATA_DIR, 'a_stock_list.json')
    if not os.path.exists(list_file):
        print('[WARN] a_stock_list.json 不存在，回退到热门列表', file=sys.stderr)
        return fetch_hot_stocks()
    
    with open(list_file) as f:
        all_stocks = json.load(f)
    
    # 分批次拉行情（每批200只）
    batch_size = 200
    quotes = {}
    total = len(all_stocks)
    
    for i in range(0, total, batch_size):
        batch = all_stocks[i:i+batch_size]
        codes = [s['full'] for s in batch]
        url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = urllib.request.urlopen(req, timeout=15).read().decode('gbk')
        except Exception as e:
            print(f'  [WARN] 批次{i//batch_size+1}拉取失败: {e}', file=sys.stderr)
            continue
        
        # 解析每行行情
        for line in data.strip().split('\n'):
            if '="' not in line:
                continue
            try:
                parts = line.split('"')[1].split('~')
                if len(parts) < 40:
                    continue
                full_code = line.split('="')[0].replace('v_','')
                name = parts[1]
                price = float(parts[3]) if parts[3] else 0
                change = float(parts[32]) if parts[32] else 0
                amount = float(parts[37]) if parts[37] else 0  # 万元
                turnover = float(parts[38]) if parts[38] else 0
                high = float(parts[33]) if parts[33] else 0
                low = float(parts[34]) if parts[34] else 0
                prev_close = float(parts[4]) if parts[4] else 0
                
                # 初筛条件
                if price <= 0 or price > 500:  # 无效价格
                    continue
                if 'ST' in name or '*ST' in name:  # ST股
                    continue
                if full_code.startswith('sz300') or full_code.startswith('sz301') or full_code.startswith('sh688') or full_code[2:].startswith('920'):
                    continue  # 创业板+科创板+北交所（风险高/波动大/流动性差）
                if amount < 5000:  # 成交额<5000万（万元单位，腾讯API单位是万元）
                    continue
                if change <= 0 or change > 7:  # 涨幅范围
                    continue
                if name.endswith('退') or '退市' in name:
                    continue
                
                # 解析代码
                code = full_code[2:]  # 去掉sh/sz前缀
                
                quotes[code] = {
                    'code': code, 'name': name, 'change': change,
                    'price': price, 'amount': amount, 'turnover': turnover,
                    'high': high, 'low': low, 'prev_close': prev_close,
                    'popularity': '', 'concepts': [],
                }
            except (ValueError, IndexError):
                continue
        
        # 进度提示
        progress = min(i + batch_size, total)
        if progress % 600 == 0 or progress >= total:
            print(f'  行情进度: {progress}/{total} → 已筛出{len(quotes)}只')
        
        time.sleep(0.05)  # 批次间微小间隔
    
    result = list(quotes.values())
    result.sort(key=lambda x: x['amount'], reverse=True)  # 按成交额排序
    return result


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
    """二次预筛：大盘差时过滤弱势股"""
    if market_change >= -1:
        return stocks  # 大盘正常，不额外过滤
    
    # 大盘跌>1%时，只留涨幅>2%的
    candidates = []
    for s in stocks:
        if s['change'] >= 2:
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

    # 20日价格波动率（年化标准）
    vol_20d = np.std(C[-20:]) / np.mean(C[-20:]) * np.sqrt(252) * 100 if n >= 20 and np.mean(C[-20:]) > 0 else 0

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
        'breakout': breakout, 'vol_20d': vol_20d,
    }


def score_early_entry(ind, rt_change=0, market_change=0, adaptive_weights=None):
    """趋势反转评分 v4.0 — 找刚启动，不追高
    核心：买"刚转头向上"的，不买"已经涨了很久"的
    """
    if not ind:
        return 0.0, {}

    score = 10.0
    factors = {}

    close = ind['close']
    ma5, ma10, ma20 = ind['ma5'], ind['ma10'], ind['ma20']
    dif, dea = ind['dif'], ind['dea']
    prev_dif, prev_dea = ind['prev_dif'], ind['prev_dea']
    rsi14 = ind['rsi14']
    vr = ind['vol_ratio']
    ts = ind['trend_score']
    bo = ind['breakout']
    mom5 = ind['mom_5d']
    mom10 = ind['mom_10d']
    mom20 = ind['mom_20d']
    ma60 = ind['ma60']
    boll_pos = ind.get('boll_pos', 50)

    # ========== 核心趋势判断 ==========
    above_ma5 = not np.isnan(ma5) and close > ma5
    above_ma10 = not np.isnan(ma10) and close > ma10
    above_ma20 = not np.isnan(ma20) and close > ma20
    above_ma60 = not np.isnan(ma60) and close > ma60 if not np.isnan(ma60) else True
    above_all = above_ma5 and above_ma10 and above_ma20
    ma_bullish = all([not np.isnan(x) for x in [ma5, ma10, ma20]]) and ma5 > ma10 > ma20

    # ========== 硬性门槛（不满足直接扣重分） ==========
    # 必须站上MA5（起码今天有向上的意思）
    if not above_ma5:
        return 5.0, {'硬过滤': '未站上MA5'}
    # 不能涨太猛（5日内涨超8%的追高风险极大）
    if mom5 > 8:
        return 5.0, {'硬过滤': f'5日涨{mom5:.0f}%过热'}

    # ========== 动量新鲜度（核心创新） ==========
    # 最近5天涨了但10天还是跌的 → 刚转头（最佳）
    just_turned = mom5 > 0 and mom10 < -1
    recovering = mom5 > 0 and -1 <= mom10 <= 1.5  # 震荡后恢复
    extended = mom5 > 0 and mom10 > 1.5  # 已经涨了一段
    if just_turned:
        score += 6
        factors['刚转头'] = 6
    elif recovering:
        score += 4
        factors['恢复中'] = 4
    elif extended:
        # 已经涨了一段但动量还温和→可以接受，轻微加分
        if mom5 <= 4:
            score += 2
            factors['温和上涨'] = 2

    # ========== 量价启动信号 ==========
    # 温和放量（1.3-2.5倍最好——真金白银进场）
    if 1.3 <= vr <= 2.5:
        s = 8
    elif 2.5 < vr <= 4.0:
        s = 5
    elif 1.0 <= vr < 1.3:
        s = 3
    elif vr > 5:
        s = -6  # 异常放量出货
    elif vr > 4:
        s = -3
    else:
        s = 0
    if s != 0:
        score += s
        factors['量价启动'] = round(s, 1)

    # ========== MACD 新鲜金叉 ==========
    # 刚金叉（昨天死叉今天金叉）→ 最新鲜
    if dif > dea and prev_dif <= prev_dea:
        s = 10
        if dif < 0: s += 3  # 零轴下方金叉=底部反转信号更强
    elif dif > dea:
        s = 4  # 老多头
    elif dif < dea and abs(dif - dea) < 0.03 and mom5 > 0.5:
        s = 6  # 即将金叉+动量配合
    else:
        s = 0
    if s > 0:
        score += s
        factors['MACD'] = round(s, 1)

    # ========== RSI 刚脱离低位 ==========
    if not np.isnan(rsi14):
        if 45 <= rsi14 <= 55:
            s = 8  # 中性偏低→最佳：刚走强但还有空间
        elif 55 < rsi14 <= 62:
            s = 6  # 偏强但不过热
        elif 38 <= rsi14 < 45:
            s = 5  # 从低位反弹
        elif 62 < rsi14 <= 70:
            s = 3  # 偏强
        elif rsi14 > 75:
            s = -10  # 超买
        elif rsi14 > 70:
            s = -6
        elif rsi14 < 30:
            s = -6  # 超卖（可能继续跌）
        else:
            s = 0
        if s != 0:
            score += s
            factors['RSI'] = round(s, 1)

    # ========== 突破位置（越低越好——底部刚启动） ==========
    if 30 <= bo <= 50:
        s = 8  # 底部区域刚启动
    elif 50 < bo <= 65:
        s = 6  # 中低位
    elif 20 <= bo < 30:
        s = 4
    elif 65 < bo <= 80:
        s = 2
    elif bo > 90:
        s = -8  # 接近高点
    elif bo > 80:
        s = -4
    else:
        s = 0
    if s != 0:
        score += s
        factors['突破位置'] = round(s, 1)

    # ========== 涨幅控制（0-1%最佳，越小越好） ==========
    if 0.2 <= rt_change <= 1.0:
        s = 8
    elif 1.0 < rt_change <= 1.8:
        s = 5
    elif -0.5 <= rt_change < 0.2:
        s = 4
    elif rt_change > 3.0:
        s = -10
    elif rt_change > 1.8:
        s = -5
    elif rt_change < -1.5:
        s = -4
    else:
        s = 0
    if s != 0:
        score += s
        factors['涨幅控制'] = round(s, 1)

    # ========== 相对强度 ==========
    rel = rt_change - market_change
    if rel > 2:
        s = 4
    elif rel > 1:
        s = 2
    elif rel < -2:
        s = -4
    elif rel < -1:
        s = -2
    else:
        s = 0
    if s != 0:
        score += s
        factors['相对强度'] = round(s, 1)

    # ========== 趋势强度（不要太高、不要太低） ==========
    if 48 <= ts <= 60:
        s = 6  # 最近刚好一半时间涨=转向初期
    elif 60 < ts <= 70:
        s = 4
    elif 40 <= ts < 48:
        s = 2
    elif ts > 78:
        s = -5  # 涨太久了
    elif ts < 35:
        s = -4  # 跌太久了
    else:
        s = 0
    if s != 0:
        score += s
        factors['趋势强度'] = round(s, 1)

    # ========== 惩罚项 ==========
    if market_change < -2:
        score -= 8
    elif market_change < -1:
        score -= 3

    # 信号过载
    active = len([v for v in factors.values() if v > 0])
    if active >= 9:
        score -= 10
    elif active >= 8:
        score -= 5

    final = round(min(max(score, 0), 100), 1)
    if final > 75:
        final = round(75 + (final - 75) / 2, 1)
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

def _fetch_stock_batch(stock):
    """并行拉取单只股票的K线+实时行情（仅I/O）"""
    code = stock['code']
    klines = fetch_klines(code, 60)
    rt = fetch_realtime(code)
    return {'stock': stock, 'klines': klines, 'rt': rt}


def run_screener(skip_ai=False):
    start_time = time.time()
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] {SCREENER_FULL_NAME} 启动...")

    # 大盘情绪
    market_change = fetch_market_sentiment()
    print(f"  大盘涨跌幅: {market_change:+.2f}%")

    # 全A股批量初筛
    hot_stocks = fetch_all_quotes_batch()
    print(f"  全A股初筛: {len(hot_stocks)}只（成交额排序）")
    if not hot_stocks:
        return {'error': '获取全A股行情失败', 'stocks': []}

    # 预筛
    candidates = pre_filter(hot_stocks, market_change)
    print(f"  预筛通过: {len(candidates)}只")

    # 7天去重
    recent_codes = load_recent_codes(3)
    print(f"  3天内已推荐: {len(recent_codes)}只（跳过）")

    # 分析（取成交额TOP200，控制耗时在2分钟内）
    max_analyze = 200
    if len(candidates) > max_analyze:
        print(f"  候选太多({len(candidates)})，取成交额TOP{max_analyze}")
        candidates = candidates[:max_analyze]
    
    # 预去重
    skipped_dedup = sum(1 for s in candidates if s['code'] in recent_codes)
    fresh = [s for s in candidates if s['code'] not in recent_codes]
    print(f"  去重跳过: {skipped_dedup}只，待分析: {len(fresh)}只")
    
    # === 并行拉取K线+实时行情（5线程） ===
    print(f"  并行拉取K线 ({len(fresh)}只, 5线程)...")
    t_fetch = time.time()
    fetched = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_stock_batch, s): s for s in fresh}
        for i, future in enumerate(as_completed(futures)):
            try:
                data = future.result()
                if data and len(data['klines']) >= 30:
                    fetched.append(data)
            except Exception:
                pass
            if (i + 1) % 50 == 0:
                print(f"    拉取进度: {i+1}/{len(fresh)}")
    print(f"  拉取完成: {len(fetched)}只有效 ({time.time()-t_fetch:.0f}s)")
    
    # === 先收集所有候选（不设分数过滤） ===
    candidates = []
    for idx, data in enumerate(fetched):
        stock = data['stock']
        code = stock['code']
        klines = data['klines']
        rt = data['rt']
        
        rt_change = rt['change'] if rt else stock['change']
        rt_price = rt['price'] if rt else klines[-1]['close']
        rt_amount = rt['amount'] if rt else 0
        
        if rt_amount < 50000:
            continue
        
        # === v4.0 真实盘中评分：用实时价替换K线最后一日收盘价 ===
        if rt:
            klines[-1]['close'] = rt['price']
            klines[-1]['high'] = max(klines[-1]['high'], rt['high'])
            klines[-1]['low'] = min(klines[-1]['low'], rt['low'])
            klines[-1]['volume'] = rt['volume']
        
        ind = quick_analyze(klines)
        if not ind:
            continue
        
        candidates.append({
            'code': code,
            'name': stock['name'],
            'price': rt_price,
            'score_price': rt_price,  # 评分时的价格（不是收盘价）
            'change': rt_change,
            'amount': rt_amount,
            'indicators': ind,
            'concepts': stock.get('concepts', []),
            'popularity': stock.get('popularity', ''),
        })
        
        if (idx + 1) % 10 == 0:
            print(f"  已收集 {idx+1}/{len(fetched)}... ({time.time()-start_time:.0f}s)")
    
    # === 横截面排名筛选 ===
    print(f"  横截面排名筛选 ({len(candidates)}只候选)...")
    ranked = rank_and_filter(candidates, top_pct=0.10, min_score=20, market_change=market_change)
    print(f"  排名通过: {len(ranked)}只")
    
    results = []
    for r in ranked:
        results.append({
            'code': r['code'], 'name': r['name'], 'score': r['score'],
            'price': r['price'], 'score_price': r.get('score_price', r['price']),
            'change': r['change'],
            'amount': r['amount'], 'vol_ratio': r['indicators'].get('vol_ratio', 0),
            'signals': r['signals'], 'risk_tags': r['risk_tags'],
            'factors': {'排名分': round(r['score'], 1)},
            'concepts': r.get('concepts', []),
            'popularity': r.get('popularity', ''),
            'is_limit_up': False,
            'indicators': r['indicators'],
            'alpha': {
                'mom_5d': round(r['indicators'].get('mom_5d', 0), 2),
                'breakout': round(r['indicators'].get('breakout', 50), 1),
                'boll_pos': round(r['indicators'].get('boll_pos', 50), 1),
            },
        })

    # === DeepSeek AI 二次分析 ===
    if skip_ai:
        print(f"  ⏭ 跳过AI分析（量化评分模式）")
    elif results:
        print(f"  启动 DeepSeek AI 分析 ({len(results)}只)...")
        try:
            ai_results = batch_analyze(results, max_count=25, delay=0.8)
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
            'score': r['score'], 'price': r['price'], 
            'score_price': r.get('score_price', r['price']),
            'signals': r['signals'],
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
        'version': SCREENER_FULL_NAME,
        'model': 'DeepSeek V4-Flash',
        'ai_enabled': not skip_ai,
        'strategy': SCREENER_STRATEGY,
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
    parser = argparse.ArgumentParser(description='JH智能选股引擎')
    parser.add_argument('--skip-ai', action='store_true', help='跳过AI分析，仅跑量化评分')
    args = parser.parse_args()
    run_screener(skip_ai=args.skip_ai)
