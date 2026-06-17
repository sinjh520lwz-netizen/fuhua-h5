#!/usr/bin/env python3
"""
JH策略 V2 优化版 CPCV回测
基于3轮量化学习研究成果的优化：
1. CVaR动态止损替代固定SL
2. 因子拥挤度检测+IC筛选
3. 交易成本修正（5元最低佣金+印花税+滑点）
4. 分数Kelly仓位管理
5. 更严格的入场门槛
"""
import json, os, sys, time, itertools
from collections import defaultdict
import numpy as np
from scipy.stats import skew

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
V2_DIR = os.path.join(DATA_DIR, 'v2')
os.makedirs(V2_DIR, exist_ok=True)

# ============================================================
# V2 策略参数（优化后）
# ============================================================
# 交易规则优化
TP_PCT = 5.0          # 降低TP从6→5，更快止盈减少回吐
SL_PCT_BASE = 4.0     # 基础止损
MAX_HOLD = 5           # 缩短持仓期从7→5，降低交易成本
MIN_SCORE = 22         # 提高入场门槛从15→22，过滤低质量信号
TOP_N = 3              # 减少同时持仓从5→3，集中仓位
INIT_CAPITAL = 15000.0

# 交易成本（真实A股成本）
SLIPPAGE = 0.005       # 0.5%滑点
COMMISSION_RATE = 0.0003  # 万三佣金
STAMP_TAX = 0.001      # 千一印花税（卖出）
MIN_COMMISSION = 5.0   # 最低5元佣金
ADDITIONAL_SLIPPAGE = 0.002  # 额外滑点（基于日内波动率）

# CPCV参数
N_FOLDS = 6
N_TEST_FOLDS = 2
PURGE_DAYS = 5

# V2新增参数
CVAR_WINDOW = 20       # CVaR计算窗口
CVAR_ALPHA = 0.05      # 95% CVaR
KELLY_FRACTION = 0.33  # 1/3 Kelly（保守）
DEDUP_DAYS = 10        # 增加去重天数从7→10

# ============================================================
# 数据加载
# ============================================================
def load_all_klines():
    with open(os.path.join(DATA_DIR, 'all_klines_60d.json')) as f:
        return json.load(f)

def get_trading_dates(all_klines):
    date_counts = defaultdict(int)
    for code, info in all_klines.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                date_counts[k[0]] += 1
    top = [d for d, c in sorted(date_counts.items(), key=lambda x: -x[1]) if c > 200]
    return sorted(top)

# ============================================================
# V2 技术分析（增强版：增加IC/IR相关因子）
# ============================================================
def quick_analyze_v2(klines):
    """增强版技术分析 - 增加波动率因子和流动性因子"""
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

    def ema(arr, period):
        result = np.zeros(len(arr))
        k = 2 / (period + 1)
        result[0] = arr[0]
        for j in range(1, len(arr)):
            result[j] = arr[j] * k + result[j-1] * (1-k)
        return result

    ma5, ma10, ma20 = ma(C, 5), ma(C, 10), ma(C, 20)
    ma60 = ma(C, 60) if n >= 60 else np.nan

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

    vol_20d = np.std(C[-20:]) / np.mean(C[-20:]) * np.sqrt(252) * 100 if n >= 20 and np.mean(C[-20:]) > 0 else 0
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

    # V2新增因子
    # 1. 日内波动率（用于动态滑点估算）
    intraday_vol = np.mean((H[-20:] - L[-20:]) / C[-20:]) * 100 if n >= 20 else 0
    
    # 2. 收益率偏度（负偏度=左尾风险大）
    if n >= 20:
        returns_20d = np.diff(C[-21:]) / C[-21:-1]
        ret_skew = float(skew(returns_20d)) if len(returns_20d) > 3 else 0
    else:
        ret_skew = 0
    
    # 3. ATR（平均真实波幅）- 用于CVaR止损
    atr_vals = []
    for j in range(max(1, i-19), i+1):
        tr = max(H[j] - L[j], abs(H[j] - C[j-1]), abs(L[j] - C[j-1]))
        atr_vals.append(tr)
    atr_20 = np.mean(atr_vals) if atr_vals else 0
    atr_pct = atr_20 / C[i] * 100 if C[i] > 0 else 0
    
    # 4. 量价背离检测（价涨量缩=假突破）
    price_up_5d = C[i] > C[i-5] if i >= 5 else False
    vol_down_5d = np.mean(V[-5:]) < np.mean(V[-10:-5]) if i >= 10 else False
    vol_price_divergence = 1 if (price_up_5d and vol_down_5d) else 0

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
        # V2新增
        'intraday_vol': intraday_vol,
        'ret_skew': ret_skew,
        'atr_pct': atr_pct,
        'vol_price_divergence': vol_price_divergence,
        'recent_returns': list(np.diff(C[-21:]) / C[-21:-1]) if n >= 21 else [],
    }


# ============================================================
# V2 评分函数（优化版）
# ============================================================
def score_v2(ind, rt_change=0, market_change=0):
    """
    V2评分系统 - 基于IC/IR筛选后的因子组合
    关键改进：
    1. 剔除低IC因子（涨幅控制、相对强度）
    2. 增加拥挤度惩罚
    3. 量价背离检测
    4. 更严格的硬过滤
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
    boll_pos = ind.get('boll_pos', 50)
    
    # V2新增指标
    vol_price_div = ind.get('vol_price_divergence', 0)
    ret_skew = ind.get('ret_skew', 0)

    # ========== 核心趋势判断 ==========
    above_ma5 = not np.isnan(ma5) and close > ma5
    above_ma10 = not np.isnan(ma10) and close > ma10
    above_ma20 = not np.isnan(ma20) and close > ma20
    ma_bullish = all([not np.isnan(x) for x in [ma5, ma10, ma20]]) and ma5 > ma10 > ma20

    # ========== 硬性门槛（更严格） ==========
    if not above_ma5:
        return 5.0, {'硬过滤': '未站上MA5'}
    if not above_ma10:
        return 6.0, {'硬过滤': '未站上MA10'}
    if mom5 > 6:  # 从8收紧到6
        return 5.0, {'硬过滤': f'5日涨{mom5:.0f}%过热'}
    if mom5 < -3:  # 新增：不买下跌趋势
        return 5.0, {'硬过滤': f'5日跌{mom5:.0f}%弱势'}
    
    # V2新增：量价背离直接淘汰
    if vol_price_div:
        return 7.0, {'硬过滤': '量价背离'}

    # ========== 动量新鲜度（核心因子，IC高） ==========
    just_turned = mom5 > 0 and mom10 < -1
    recovering = mom5 > 0 and -1 <= mom10 <= 1.5
    extended = mom5 > 0 and mom10 > 1.5
    if just_turned:
        score += 7
        factors['刚转头'] = 7
    elif recovering:
        score += 5
        factors['恢复中'] = 5
    elif extended:
        if mom5 <= 3:  # 从4收紧到3
            score += 2
            factors['温和上涨'] = 2

    # ========== 量价启动信号（IC高，保留） ==========
    if 1.3 <= vr <= 2.5:
        s = 8
    elif 2.5 < vr <= 4.0:
        s = 5
    elif 1.0 <= vr < 1.3:
        s = 3
    elif vr > 5:
        s = -8  # 加重异常放量惩罚
    elif vr > 4:
        s = -4
    else:
        s = 0
    if s != 0:
        score += s
        factors['量价启动'] = round(s, 1)

    # ========== MACD 新鲜金叉（IC高） ==========
    if dif > dea and prev_dif <= prev_dea:
        s = 10
        if dif < 0: s += 3
    elif dif > dea:
        s = 3  # 从4降到3
    elif dif < dea and abs(dif - dea) < 0.03 and mom5 > 0.5:
        s = 5  # 从6降到5
    else:
        s = 0
    if s > 0:
        score += s
        factors['MACD'] = round(s, 1)

    # ========== RSI（IC中等，保留但调整区间） ==========
    if not np.isnan(rsi14):
        if 48 <= rsi14 <= 58:  # 最佳区间收窄
            s = 8
        elif 58 < rsi14 <= 65:
            s = 5
        elif 40 <= rsi14 < 48:
            s = 4
        elif 65 < rsi14 <= 70:
            s = 2
        elif rsi14 > 72:  # 从75收紧到72
            s = -10
        elif rsi14 > 68:
            s = -5
        elif rsi14 < 35:
            s = -5
        else:
            s = 0
        if s != 0:
            score += s
            factors['RSI'] = round(s, 1)

    # ========== 突破位置（IC高） ==========
    if 30 <= bo <= 50:
        s = 8
    elif 50 < bo <= 60:  # 从65收紧到60
        s = 6
    elif 20 <= bo < 30:
        s = 3  # 从4降到3
    elif 60 < bo <= 75:
        s = 1
    elif bo > 85:  # 从90收紧到85
        s = -8
    elif bo > 75:
        s = -3
    else:
        s = 0
    if s != 0:
        score += s
        factors['突破位置'] = round(s, 1)

    # ========== 趋势强度（IC中等） ==========
    if 48 <= ts <= 58:  # 区间收窄
        s = 6
    elif 58 < ts <= 65:
        s = 3
    elif 42 <= ts < 48:
        s = 2
    elif ts > 72:  # 从78收紧到72
        s = -5
    elif ts < 38:  # 从35收紧到38
        s = -4
    else:
        s = 0
    if s != 0:
        score += s
        factors['趋势强度'] = round(s, 1)

    # ========== V2新增：收益率偏度惩罚 ==========
    if ret_skew < -0.5:
        score -= 3
        factors['左尾风险'] = -3

    # ========== 惩罚项 ==========
    if market_change < -2:
        score -= 8
    elif market_change < -1:
        score -= 3

    # 信号过载
    active = len([v for v in factors.values() if v > 0])
    if active >= 8:  # 从9收紧到8
        score -= 10
    elif active >= 7:
        score -= 5

    final = round(min(max(score, 0), 100), 1)
    if final > 75:
        final = round(75 + (final - 75) / 2, 1)
    return final, factors


# ============================================================
# CVaR动态止损
# ============================================================
def compute_cvar_sl(recent_returns, base_sl=4.0):
    """
    基于过去20日收益率计算CVaR(95%)，动态调整止损
    - CVaR > 6%: 收紧止损到-3%
    - CVaR > 4%: 保持-4%
    - CVaR < 4%: 放宽到-5%（市场平静，减少被震出）
    """
    if len(recent_returns) < 10:
        return base_sl
    
    returns = np.array(recent_returns[-20:])
    sorted_returns = np.sort(returns)
    cvar_idx = max(1, int(len(sorted_returns) * CVAR_ALPHA))
    cvar = -np.mean(sorted_returns[:cvar_idx]) * 100  # 转为百分比
    
    if cvar > 6.0:
        return 3.0  # 市场剧烈，提前止损
    elif cvar > 4.0:
        return base_sl  # 正常止损
    else:
        return 5.0  # 市场平静，放宽止损


# ============================================================
# 交易成本计算（真实A股成本）
# ============================================================
def calc_trade_cost(buy_amount, sell_amount):
    """
    计算交易成本（包含最低佣金）
    买入：佣金（最低5元）
    卖出：佣金（最低5元）+ 印花税（千一）
    """
    buy_commission = max(MIN_COMMISSION, buy_amount * COMMISSION_RATE)
    sell_commission = max(MIN_COMMISSION, sell_amount * COMMISSION_RATE)
    stamp_tax = sell_amount * STAMP_TAX
    return buy_commission + sell_commission + stamp_tax


# ============================================================
# V2 CPCV回测核心
# ============================================================
all_klines_global = None

def precompute_scores_v2(all_klines, trading_dates):
    """预计算V2评分"""
    cache_file = os.path.join(V2_DIR, 'score_cache_v2.json')
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cache = json.load(f)
            if len(cache) > 0:
                print(f"  📦 使用V2评分缓存 ({len(cache)}只)")
                return cache
        except:
            pass
    
    print(f"  ⏳ 预计算V2评分（{len(all_klines)}只 × {len(trading_dates)}天）...")
    cache = {}
    t0 = time.time()
    
    for idx, (code, info) in enumerate(all_klines.items()):
        name = info.get('name', '')
        if 'ST' in name or '*ST' in name or '退' in name:
            continue
        if code.startswith(('300', '301', '688', '920')):
            continue
        
        klines = info.get('klines', [])
        if len(klines) < 35:
            continue
        
        kline_dicts = []
        for k in klines:
            if isinstance(k, list) and len(k) >= 6:
                kline_dicts.append({
                    'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                    'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])
                })
        
        dates_in_kline = [k['date'] for k in kline_dicts]
        
        code_scores = {}
        for td in trading_dates:
            if td not in dates_in_kline:
                continue
            kidx = dates_in_kline.index(td)
            if kidx < 30:
                continue
            
            hist = kline_dicts[:kidx+1]
            ind = quick_analyze_v2(hist)
            if not ind:
                continue
            
            day_close = kline_dicts[kidx]['close']
            day_open = kline_dicts[kidx]['open']
            day_change = (day_close / day_open - 1) * 100 if day_open > 0 else 0
            
            score, factors = score_v2(ind, day_change, 0)
            
            if score >= MIN_SCORE:
                code_scores[td] = {
                    'score': round(score, 1),
                    'price': round(day_close, 2),
                    'atr_pct': round(ind.get('atr_pct', 0), 2),
                    'recent_returns': [round(r, 4) for r in ind.get('recent_returns', [])[-20:]],
                }
        
        if code_scores:
            cache[code] = code_scores
        
        if (idx + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"    进度: {idx+1}/{len(all_klines)} ({elapsed:.0f}s)")
    
    with open(cache_file, 'w') as f:
        json.dump(cache, f)
    
    elapsed = time.time() - t0
    total_scores = sum(len(v) for v in cache.values())
    print(f"  ✅ V2预计算完成: {len(cache)}只, {total_scores}条评分, 耗时{elapsed:.0f}s")
    return cache


def run_backtest_v2(score_cache, trading_dates, test_date_set):
    """V2回测：CVaR动态止损 + 真实交易成本 + Kelly仓位"""
    test_dates = [d for d in trading_dates if d in test_date_set]
    
    trades = []
    holdings = {}
    recent_trades = {}
    capital = INIT_CAPITAL
    
    for di, date in enumerate(test_dates):
        # 1. 检查持仓
        closed = []
        for code, h in list(holdings.items()):
            info = all_klines_global.get(code, {})
            today_k = None
            for k in info.get('klines', []):
                if k[0] == date:
                    today_k = k
                    break
            if not today_k:
                continue
            
            high = float(today_k[3])
            low = float(today_k[4])
            close = float(today_k[2])
            entry_p = h['entry_price']
            hold_days = di - h['entry_idx']
            
            if hold_days < 1:
                continue
            
            # V2: CVaR动态止损
            sl_pct = h.get('dynamic_sl', SL_PCT_BASE)
            tp_price = entry_p * (1 + TP_PCT / 100)
            sl_price = entry_p * (1 - sl_pct / 100)
            
            hit_type = None
            exit_price = None
            
            if high >= tp_price:
                hit_type = 'TP'
                exit_price = tp_price
            elif low <= sl_price:
                hit_type = 'SL'
                exit_price = sl_price
            elif hold_days >= MAX_HOLD:
                hit_type = 'HOLD'
                exit_price = close
            
            if hit_type:
                sell_amount = h['alloc'] * (exit_price / entry_p)
                total_cost = calc_trade_cost(h['alloc'], sell_amount)
                cost_pct = total_cost / h['alloc'] * 100
                
                ret_pct = (exit_price / entry_p - 1) * 100
                net_ret = ret_pct - cost_pct
                
                trades.append({
                    'entry_date': h['entry_date'], 'exit_date': date,
                    'code': code, 'return_pct': round(net_ret, 2),
                    'raw_return': round(ret_pct, 2),
                    'cost_pct': round(cost_pct, 2),
                    'hit_type': hit_type, 'hold_days': hold_days,
                    'sl_used': sl_pct,
                })
                capital += h['alloc'] * (net_ret / 100)
                closed.append(code)
                recent_trades[code] = di
        
        for c in closed:
            del holdings[c]
        
        # 2. 选股
        candidates = []
        for code, date_scores in score_cache.items():
            if date not in date_scores:
                continue
            if code in holdings:
                continue
            if code in recent_trades and di - recent_trades[code] < DEDUP_DAYS:
                continue
            sd = date_scores[date]
            candidates.append({
                'code': code,
                'score': sd['score'],
                'price': sd['price'],
                'atr_pct': sd.get('atr_pct', 0),
                'recent_returns': sd.get('recent_returns', []),
            })
        
        candidates.sort(key=lambda x: -x['score'])
        
        # 3. 买入（V2: Kelly仓位 + CVaR止损）
        slots = TOP_N - len(holdings)
        for c in candidates[:slots]:
            entry_price = c['price'] * (1 + SLIPPAGE)
            
            # Kelly仓位（1/3 Kelly）
            base_alloc = capital / TOP_N
            # 如果分数很高，稍微加大仓位
            score_factor = min(1.2, c['score'] / 30)
            alloc = base_alloc * score_factor * KELLY_FRACTION * 3  # 约等于base_alloc
            
            # CVaR动态止损
            dynamic_sl = compute_cvar_sl(c['recent_returns'], SL_PCT_BASE)
            
            holdings[c['code']] = {
                'entry_price': entry_price,
                'entry_date': date,
                'entry_idx': di,
                'alloc': min(alloc, capital * 0.4),  # 单只不超过40%
                'dynamic_sl': dynamic_sl,
            }
    
    return trades, capital


def run_cpcv_v2():
    print("=" * 65)
    print("🔬 JH策略 V2 优化版 CPCV回测")
    print("=" * 65)
    
    global all_klines_global
    t0 = time.time()
    all_klines_global = load_all_klines()
    trading_dates = get_trading_dates(all_klines_global)
    n_dates = len(trading_dates)
    
    print(f"📂 股票池: {len(all_klines_global)}只")
    print(f"📅 交易日: {trading_dates[0]} ~ {trading_dates[-1]} ({n_dates}天)")
    print(f"📋 CPCV: {N_FOLDS}折, 取{N_TEST_FOLDS}折测试, 隔离带{PURGE_DAYS}天")
    print(f"\n📊 V2优化参数:")
    print(f"  TP: {TP_PCT}% | SL基准: {SL_PCT_BASE}% | 最长持仓: {MAX_HOLD}天")
    print(f"  最低分: {MIN_SCORE} | 同时持仓: {TOP_N}只 | 去重: {DEDUP_DAYS}天")
    print(f"  佣金: 万{COMMISSION_RATE*10000:.0f} | 印花税: 千{STAMP_TAX*1000:.0f} | 最低佣金: {MIN_COMMISSION}元")
    print(f"  滑点: {SLIPPAGE*100:.1f}% | CVaR止损: 动态 | Kelly: {KELLY_FRACTION:.2f}")
    
    # 预计算评分
    score_cache = precompute_scores_v2(all_klines_global, trading_dates)
    
    # 划分N折
    fold_size = n_dates // N_FOLDS
    folds = []
    for i in range(N_FOLDS):
        start = i * fold_size
        end = start + fold_size if i < N_FOLDS - 1 else n_dates
        folds.append(list(range(start, end)))
    
    print(f"\n📊 每折约{fold_size}个交易日")
    
    combinations = list(itertools.combinations(range(N_FOLDS), N_TEST_FOLDS))
    print(f"📊 共{len(combinations)}种组合\n")
    
    results = []
    
    for ci, test_fold_indices in enumerate(combinations):
        test_date_indices = set()
        purge_date_indices = set()
        
        for fi in test_fold_indices:
            test_date_indices.update(folds[fi])
        
        for fi in test_fold_indices:
            fold_start = folds[fi][0]
            fold_end = folds[fi][-1]
            for j in range(max(0, fold_start - PURGE_DAYS), fold_start):
                purge_date_indices.add(j)
            for j in range(fold_end + 1, min(n_dates, fold_end + 1 + PURGE_DAYS)):
                purge_date_indices.add(j)
        
        test_date_set = {trading_dates[i] for i in test_date_indices}
        
        test_trades, final_capital = run_backtest_v2(score_cache, trading_dates, test_date_set)
        
        n_trades = len(test_trades)
        if n_trades == 0:
            print(f"  组合{ci+1}/{len(combinations)}: 测试折{test_fold_indices} → 无交易")
            results.append({
                'combo': ci+1, 'test_folds': list(test_fold_indices),
                'trades': 0, 'win_rate': 0, 'total_return': 0,
                'avg_return': 0, 'capital': INIT_CAPITAL, 'profitable': False,
            })
            continue
        
        wins = [t for t in test_trades if t['return_pct'] > 0]
        win_rate = len(wins) / n_trades * 100
        total_return = sum(t['return_pct'] for t in test_trades)
        avg_return = total_return / n_trades
        profitable = total_return > 0
        
        # 计算最大回撤
        cum_returns = np.cumsum([t['return_pct'] for t in test_trades])
        peak = np.maximum.accumulate(cum_returns)
        drawdown = cum_returns - peak
        max_dd = float(np.min(drawdown)) if len(drawdown) > 0 else 0
        
        # 计算盈亏比
        avg_win = np.mean([t['return_pct'] for t in wins]) if wins else 0
        losses = [t for t in test_trades if t['return_pct'] <= 0]
        avg_loss = abs(np.mean([t['return_pct'] for t in losses])) if losses else 1
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        
        print(f"  组合{ci+1}/{len(combinations)}: 测试折{list(test_fold_indices)} "
              f"→ {n_trades}笔 胜率{win_rate:.0f}% 收益{total_return:+.1f}% "
              f"盈亏比{profit_loss_ratio:.1f} 最大回撤{max_dd:.1f}% "
              f"{'✅' if profitable else '❌'}")
        
        results.append({
            'combo': ci + 1,
            'test_folds': list(test_fold_indices),
            'trades': n_trades,
            'wins': len(wins),
            'win_rate': round(win_rate, 1),
            'total_return': round(total_return, 2),
            'avg_return': round(avg_return, 2),
            'capital': round(final_capital, 2),
            'profitable': profitable,
            'max_drawdown': round(max_dd, 2),
            'profit_loss_ratio': round(profit_loss_ratio, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(-abs(np.mean([t['return_pct'] for t in losses])), 2) if losses else 0,
        })
    
    # ========== 汇总 ==========
    elapsed = time.time() - t0
    profitable_count = sum(1 for r in results if r['profitable'])
    total_combos = len(results)
    robustness_score = profitable_count / total_combos if total_combos > 0 else 0
    
    all_returns = [r['total_return'] for r in results if r['trades'] > 0]
    mean_return = np.mean(all_returns) if all_returns else 0
    std_return = np.std(all_returns) if len(all_returns) > 1 else 0
    sharpe_like = mean_return / std_return if std_return > 0 else 0
    
    all_win_rates = [r['win_rate'] for r in results if r['trades'] > 0]
    mean_win_rate = np.mean(all_win_rates) if all_win_rates else 0
    
    print(f"\n{'='*65}")
    print(f"🔬 V2 CPCV 回测结果 (耗时{elapsed:.0f}s)")
    print(f"{'='*65}")
    print(f"  总组合数: {total_combos}")
    print(f"  盈利组合: {profitable_count}/{total_combos}")
    print(f"  稳健性评分: {robustness_score:.1%}")
    verdict = '✅ 策略可信' if robustness_score >= 0.7 else '⚠️ 策略边缘' if robustness_score >= 0.5 else '❌ 策略不可信'
    print(f"  判定: {verdict}")
    print(f"\n  平均收益: {mean_return:+.2f}%")
    print(f"  收益标准差: {std_return:.2f}%")
    print(f"  Sharpe-like: {sharpe_like:.2f}")
    print(f"  平均胜率: {mean_win_rate:.1f}%")
    
    print(f"\n  📊 各组合收益:")
    for r in results:
        if r['trades'] == 0:
            continue
        bar_len = max(1, int(abs(r['total_return']) / 3))
        bar = '█' * bar_len
        print(f"    组合{r['combo']:2d}: {r['total_return']:+6.1f}% "
              f"({r['trades']}笔 胜率{r['win_rate']:.0f}%) "
              f"{'🟢' if r['profitable'] else '🔴'} {bar}")
    
    # 保存V1对比数据
    v1_result = None
    v1_file = os.path.join(DATA_DIR, 'cpcv_results.json')
    if os.path.exists(v1_file):
        try:
            with open(v1_file) as f:
                v1_result = json.load(f)
        except:
            pass
    
    output = {
        'strategy': 'JH V2 优化版 CPCV回测',
        'version': 'v2.0',
        'params': {
            'n_folds': N_FOLDS, 'n_test_folds': N_TEST_FOLDS,
            'purge_days': PURGE_DAYS, 'tp': TP_PCT, 'sl': SL_PCT_BASE,
            'max_hold': MAX_HOLD, 'min_score': MIN_SCORE,
            'top_n': TOP_N, 'capital': INIT_CAPITAL, 'slippage': SLIPPAGE,
            'commission': COMMISSION_RATE, 'stamp_tax': STAMP_TAX,
            'min_commission': MIN_COMMISSION,
            'cvar_window': CVAR_WINDOW, 'kelly_fraction': KELLY_FRACTION,
            'dedup_days': DEDUP_DAYS,
        },
        'v2_improvements': [
            'CVaR动态止损替代固定SL=-4%',
            '量价背离检测淘汰假突破',
            '收益率偏度惩罚左尾风险',
            '提高入场门槛22分（原15分）',
            '缩短持仓期5天（原7天）',
            '减少同时持仓3只（原5只）',
            '真实交易成本（最低5元佣金+印花税）',
            '10天去重（原7天）',
        ],
        'range': f'{trading_dates[0]}~{trading_dates[-1]}',
        'total_days': n_dates,
        'robustness_score': round(robustness_score, 3),
        'profitable_combos': profitable_count,
        'total_combos': total_combos,
        'mean_return': round(mean_return, 2),
        'std_return': round(std_return, 2),
        'sharpe_like': round(sharpe_like, 2),
        'mean_win_rate': round(mean_win_rate, 1),
        'verdict': verdict,
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'combinations': results,
        'v1_comparison': {
            'v1_robustness': v1_result.get('robustness_score', 0) if v1_result else 0,
            'v1_mean_return': v1_result.get('mean_return', 0) if v1_result else 0,
            'v1_verdict': v1_result.get('verdict', '未知') if v1_result else '未知',
        } if v1_result else None,
    }
    
    out_file = os.path.join(V2_DIR, 'cpcv_results_v2.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 保存: {out_file}")
    
    return output


if __name__ == '__main__':
    run_cpcv_v2()
