#!/usr/bin/env python3
"""
JH策略 v3 — 均值回归版（彻底重构）
核心改变：从"追涨"转向"回调买入"
信号：超卖反弹 + 量价背离 + 趋势支撑
"""
import json, os, sys, time, itertools
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data', 'v2')
os.makedirs(DATA_DIR, exist_ok=True)

# 交易成本
COMMISSION_RATE = 0.0003
STAMP_TAX = 0.001
MIN_COMMISSION = 5.0
SLIPPAGE = 0.005

# 策略参数
TOP_N = 3  # 减少同时持仓
MAX_HOLD = 5  # 缩短持仓
INIT_CAPITAL = 15000.0

# CPCV
N_FOLDS = 6
N_TEST_FOLDS = 2
PURGE_DAYS = 5

def load_all_klines():
    with open(os.path.join(SCRIPT_DIR, 'data', 'all_klines_60d.json')) as f:
        return json.load(f)

def get_trading_dates(all_klines):
    date_counts = defaultdict(int)
    for code, info in all_klines.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                date_counts[k[0]] += 1
    top = [d for d, c in sorted(date_counts.items(), key=lambda x: -x[1]) if c > 200]
    return sorted(top)

def compute_rsi(closes, period=14):
    """计算RSI"""
    if len(closes) < period + 1:
        return 50
    deltas = np.diff(closes[-period-1:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_ma(closes, period):
    """计算MA"""
    if len(closes) < period:
        return np.nan
    return np.mean(closes[-period:])

def compute_bollinger(closes, period=20, std_mult=2):
    """布林带"""
    if len(closes) < period:
        return np.nan, np.nan, np.nan
    ma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return ma + std_mult * std, ma, ma - std_mult * std

def score_mean_reversion(kline_dicts, kidx):
    """均值回归评分 — 核心信号"""
    if kidx < 30:
        return 0, {}
    
    closes = [k['close'] for k in kline_dicts[:kidx+1]]
    volumes = [k['volume'] for k in kline_dicts[:kidx+1]]
    highs = [k['high'] for k in kline_dicts[:kidx+1]]
    lows = [k['low'] for k in kline_dicts[:kidx+1]]
    
    close = closes[-1]
    score = 0
    factors = {}
    
    # === 信号1: RSI超卖反弹 (核心) ===
    rsi = compute_rsi(closes)
    prev_rsi = compute_rsi(closes[:-1])
    
    if rsi < 25:
        # 严重超卖
        score += 8
        factors['RSI超卖'] = 8
    elif rsi < 35:
        # 超卖区域
        score += 5
        factors['RSI偏弱'] = 5
    elif 35 <= rsi <= 45 and prev_rsi < 35:
        # 从超卖区反弹 — 最佳信号
        score += 10
        factors['RSI反弹'] = 10
    elif rsi > 70:
        score -= 8
        factors['RSI超买'] = -8
    elif rsi > 60:
        score -= 3
        factors['RSI偏强'] = -3
    
    # === 信号2: 布林带下轨支撑 ===
    upper, mid, lower = compute_bollinger(closes)
    if not np.isnan(lower):
        boll_pos = (close - lower) / (upper - lower) * 100 if upper != lower else 50
        
        if boll_pos < 15:
            # 接近下轨
            score += 8
            factors['布林下轨'] = 8
        elif boll_pos < 25:
            score += 5
            factors['布林偏下'] = 5
        elif boll_pos > 85:
            score -= 6
            factors['布林上轨'] = -6
    
    # === 信号3: 回调到MA20支撑 ===
    ma20 = compute_ma(closes, 20)
    ma60 = compute_ma(closes, 60)
    if not np.isnan(ma20) and not np.isnan(ma60):
        # 长期趋势向上 + 短期回调到MA20
        if close > ma60 and abs(close - ma20) / ma20 < 0.02:
            score += 7
            factors['MA20支撑'] = 7
        elif close > ma60 and close < ma20 and (ma20 - close) / ma20 < 0.03:
            # 跌破MA20但不远
            score += 5
            factors['MA20附近'] = 5
    
    # === 信号4: 量缩价稳 (底部特征) ===
    if len(volumes) >= 5:
        recent_vol = np.mean(volumes[-3:])
        prev_vol = np.mean(volumes[-8:-3])
        vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1
        
        if vol_ratio < 0.6 and (close - closes[-3]) / closes[-3] > -0.03:
            # 缩量但价格没大跌 — 底部蓄势
            score += 6
            factors['缩量蓄势'] = 6
        elif vol_ratio > 3:
            # 异常放量 — 可能出货
            score -= 5
            factors['放量异常'] = -5
    
    # === 信号5: 连续下跌后止跌 ===
    if kidx >= 3:
        d1 = (closes[-1] - closes[-2]) / closes[-2] * 100
        d2 = (closes[-2] - closes[-3]) / closes[-3] * 100
        d3 = (closes[-3] - closes[-4]) / closes[-4] * 100 if kidx >= 4 else 0
        
        # 连跌3天后今天企稳或微涨
        if d2 < -1 and d3 < -1 and d1 > -0.5 and d1 < 1.5:
            score += 7
            factors['止跌企稳'] = 7
        # 连跌2天后今天微涨
        elif d2 < -1.5 and d1 > 0.3 and d1 < 2:
            score += 5
            factors['反弹启动'] = 5
    
    # === 信号6: MACD底背离 ===
    if kidx >= 30:
        # 简化：当前DIF在低位且开始拐头向上
        # 用近10天的收盘价变化率近似
        mom5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
        mom10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) >= 11 else 0
        
        # 5天动量转正但10天还是负 → 刚转头
        if mom5 > 0 and mom10 < -2:
            score += 8
            factors['底部转头'] = 8
        elif mom5 > 0 and -2 <= mom10 <= 0:
            score += 5
            factors['弱转强'] = 5
    
    # === 惩罚项 ===
    # 当天涨幅太大
    if kidx >= 1:
        day_change = (closes[-1] / closes[-2] - 1) * 100
        if day_change > 5:
            score -= 10
            factors['当日涨过大'] = -10
        elif day_change > 3:
            score -= 5
            factors['当日涨偏大'] = -5
        elif day_change < -5:
            score -= 5
            factors['当日跌过大'] = -5
    
    # 5日涨幅太大（追高风险）
    if len(closes) >= 6:
        mom5 = (closes[-1] / closes[-6] - 1) * 100
        if mom5 > 10:
            score -= 10
            factors['5日涨过大'] = -10
        elif mom5 > 6:
            score -= 5
            factors['5日涨偏大'] = -5
    
    return max(score, 0), factors

def precompute_v3(all_klines, trading_dates):
    """预计算v3评分"""
    cache_file = os.path.join(DATA_DIR, 'v3_score_cache.json')
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cache = json.load(f)
            if len(cache) > 0:
                print(f"  📦 使用v3评分缓存 ({len(cache)}只)")
                return cache
        except:
            pass
    
    print(f"  ⏳ 预计算v3评分...")
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
            
            score, factors = score_mean_reversion(kline_dicts, kidx)
            
            if score >= 10:
                code_scores[td] = {
                    'score': round(score, 1),
                    'price': round(kline_dicts[kidx]['close'], 2),
                }
        
        if code_scores:
            cache[code] = code_scores
        
        if (idx + 1) % 500 == 0:
            print(f"    进度: {idx+1}/{len(all_klines)} ({time.time()-t0:.0f}s)")
    
    with open(cache_file, 'w') as f:
        json.dump(cache, f)
    
    print(f"  ✅ 完成: {len(cache)}只, 耗时{time.time()-t0:.0f}s")
    return cache

def run_backtest_v3(score_cache, all_klines, trading_dates, test_dates_set, params):
    """v3回测"""
    tp_pct = params['tp']
    sl_pct = params['sl']
    min_score = params['min_score']
    
    test_dates = [d for d in trading_dates if d in test_dates_set]
    
    trades = []
    holdings = {}
    recent_trades = {}
    capital = INIT_CAPITAL
    
    for di, date in enumerate(test_dates):
        # 1. 检查持仓
        closed = []
        for code, h in list(holdings.items()):
            info = all_klines.get(code, {})
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
            
            tp_price = entry_p * (1 + tp_pct / 100)
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
                ret_pct = (exit_price / entry_p - 1) * 100
                buy_cost = max(MIN_COMMISSION, h['alloc'] * COMMISSION_RATE)
                sell_amount = h['alloc'] * (1 + ret_pct/100)
                sell_cost = max(MIN_COMMISSION, sell_amount * COMMISSION_RATE)
                stamp = sell_amount * STAMP_TAX
                total_cost_pct = (buy_cost + sell_cost + stamp) / h['alloc'] * 100
                net_ret = ret_pct - total_cost_pct
                
                trades.append({
                    'entry_date': h['entry_date'], 'exit_date': date,
                    'code': code, 'return_pct': round(net_ret, 2),
                    'hit_type': hit_type, 'hold_days': hold_days,
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
            if code in recent_trades and di - recent_trades[code] < 7:
                continue
            sd = date_scores[date]
            if sd['score'] < min_score:
                continue
            candidates.append({'code': code, 'score': sd['score'], 'price': sd['price']})
        
        candidates.sort(key=lambda x: -x['score'])
        
        # 3. 买入
        slots = TOP_N - len(holdings)
        for c in candidates[:slots]:
            entry_price = c['price'] * (1 + SLIPPAGE)
            alloc = capital / TOP_N
            holdings[c['code']] = {
                'entry_price': entry_price,
                'entry_date': date,
                'entry_idx': di,
                'alloc': alloc,
            }
    
    return trades, capital

def run_cpcv_v3():
    print("=" * 65)
    print("🔬 JH策略 v3 均值回归版 CPCV回测")
    print("=" * 65)
    
    t0 = time.time()
    all_klines = load_all_klines()
    trading_dates = get_trading_dates(all_klines)
    n_dates = len(trading_dates)
    
    print(f"📂 股票池: {len(all_klines)}只")
    print(f"📅 交易日: {trading_dates[0]} ~ {trading_dates[-1]} ({n_dates}天)")
    
    # 预计算
    score_cache = precompute_v3(all_klines, trading_dates)
    
    # 参数网格
    param_grid = [
        {'tp': 6, 'sl': 4, 'min_score': 15},
        {'tp': 6, 'sl': 5, 'min_score': 15},
        {'tp': 6, 'sl': 6, 'min_score': 15},
        {'tp': 6, 'sl': 4, 'min_score': 20},
        {'tp': 6, 'sl': 5, 'min_score': 20},
        {'tp': 6, 'sl': 6, 'min_score': 20},
        {'tp': 5, 'sl': 4, 'min_score': 15},
        {'tp': 5, 'sl': 5, 'min_score': 15},
        {'tp': 5, 'sl': 5, 'min_score': 20},
        {'tp': 5, 'sl': 6, 'min_score': 20},
        {'tp': 8, 'sl': 5, 'min_score': 15},
        {'tp': 8, 'sl': 6, 'min_score': 15},
    ]
    
    # 划分N折
    fold_size = n_dates // N_FOLDS
    folds = []
    for i in range(N_FOLDS):
        start = i * fold_size
        end = start + fold_size if i < N_FOLDS - 1 else n_dates
        folds.append(list(range(start, end)))
    
    combinations = list(itertools.combinations(range(N_FOLDS), N_TEST_FOLDS))
    print(f"📊 {len(param_grid)}组参数 × {len(combinations)}种组合 = {len(param_grid)*len(combinations)}次回测\n")
    
    best_result = None
    best_params = None
    best_robustness = 0
    best_avg_return = -999
    all_param_results = []
    
    for pi, params in enumerate(param_grid):
        results = []
        for ci, test_fold_indices in enumerate(combinations):
            test_date_indices = set()
            for fi in test_fold_indices:
                test_date_indices.update(folds[fi])
            test_date_set = {trading_dates[i] for i in test_date_indices}
            
            test_trades, final_capital = run_backtest_v3(
                score_cache, all_klines, trading_dates, test_date_set, params
            )
            
            n_trades = len(test_trades)
            if n_trades == 0:
                results.append({'profitable': False, 'total_return': 0, 'trades': 0, 'win_rate': 0})
                continue
            
            wins = sum(1 for t in test_trades if t['return_pct'] > 0)
            total_return = sum(t['return_pct'] for t in test_trades)
            results.append({
                'profitable': total_return > 0,
                'total_return': round(total_return, 2),
                'trades': n_trades,
                'win_rate': round(wins / n_trades * 100, 1),
            })
        
        profitable_count = sum(1 for r in results if r['profitable'])
        robustness = profitable_count / len(results)
        active_results = [r for r in results if r['trades'] > 0]
        avg_return = np.mean([r['total_return'] for r in active_results]) if active_results else 0
        
        param_label = f"TP+{params['tp']}% SL-{params['sl']}% 评分≥{params['min_score']}"
        print(f"  [{pi+1}/{len(param_grid)}] {param_label} → 稳健性{robustness:.0%} 平均{avg_return:+.1f}%")
        
        all_param_results.append({
            'tp': params['tp'], 'sl': params['sl'], 'score': params['min_score'],
            'robustness': round(robustness, 3),
            'profitable_combos': profitable_count,
            'avg_return': round(avg_return, 2),
        })
        
        # 选择最优：优先稳健性≥50%，其次平均收益最高
        if robustness > best_robustness or (robustness == best_robustness and avg_return > best_avg_return):
            best_robustness = robustness
            best_avg_return = avg_return
            best_params = params
            best_result = results
    
    # ========== 汇总 ==========
    elapsed = time.time() - t0
    
    if best_params is None:
        best_params = param_grid[0]
        best_result = [{'profitable': False, 'total_return': 0, 'trades': 0, 'win_rate': 0}] * 15
    
    print(f"\n{'='*65}")
    print(f"🔬 v3 CPCV 最优结果 (耗时{elapsed:.0f}s)")
    print(f"{'='*65}")
    print(f"  最优参数: TP+{best_params['tp']}% SL-{best_params['sl']}% 评分≥{best_params['min_score']}")
    print(f"  稳健性: {best_robustness:.0%}")
    
    active = [r for r in best_result if r['trades'] > 0]
    if active:
        print(f"  平均收益: {np.mean([r['total_return'] for r in active]):+.2f}%")
    
    verdict = '✅ 可信' if best_robustness >= 0.7 else '⚠️ 边缘' if best_robustness >= 0.5 else '❌ 不可信'
    print(f"  判定: {verdict}")
    
    # 用最优参数跑完整回测
    print(f"\n📊 完整回测...")
    all_dates_set = set(trading_dates)
    full_trades, full_capital = run_backtest_v3(
        score_cache, all_klines, trading_dates, all_dates_set, best_params
    )
    
    n = len(full_trades)
    fb = {}
    if n > 0:
        wins = sum(1 for t in full_trades if t['return_pct'] > 0)
        tp_c = sum(1 for t in full_trades if t['hit_type'] == 'TP')
        sl_c = sum(1 for t in full_trades if t['hit_type'] == 'SL')
        hold_c = sum(1 for t in full_trades if t['hit_type'] == 'HOLD')
        total_ret = sum(t['return_pct'] for t in full_trades)
        
        print(f"  总交易: {n}笔")
        print(f"  胜率: {wins/n*100:.1f}%")
        print(f"  TP: {tp_c} SL: {sl_c} HOLD: {hold_c}")
        print(f"  总收益: {total_ret:+.2f}%")
        print(f"  本金: {INIT_CAPITAL:.0f} → {full_capital:.0f}")
        
        fb = {
            'range': f'{trading_dates[0]}~{trading_dates[-1]}',
            'trades': n, 'win_rate': round(wins/n*100, 1),
            'tp_count': tp_c, 'sl_count': sl_c, 'hold_count': hold_c,
            'total_return': round(total_ret, 2),
            'final_capital': round(full_capital, 2),
        }
    
    # 保存
    output = {
        'strategy': 'JH v3 均值回归版',
        'version': '3.0',
        'core_change': '从"追涨"转向"回调买入" — RSI超卖+布林下轨+MA20支撑+缩量蓄势+止跌企稳',
        'improvements': [
            '信号重构：均值回归替代追涨（RSI/布林/MA20/量价背离）',
            '持仓减少：TOP 5→3只，减少分散',
            '持仓缩短：7天→5天',
            '完整交易成本模型',
            'CPCV验证',
        ],
        'params': {
            'tp': best_params['tp'], 'sl': best_params['sl'],
            'min_score': best_params['min_score'],
            'max_hold': MAX_HOLD, 'top_n': TOP_N,
            'capital': INIT_CAPITAL, 'slippage': SLIPPAGE,
        },
        'cpcv': {
            'robustness': round(best_robustness, 3),
            'profitable_combos': sum(1 for r in best_result if r['profitable']),
            'total_combos': len(best_result),
            'verdict': verdict,
            'mean_return': round(np.mean([r['total_return'] for r in active]), 2) if active else 0,
            'combinations': best_result,
        },
        'param_search': all_param_results,
        'full_backtest': fb,
        'trades': full_trades[-100:] if full_trades else [],
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    
    out_file = os.path.join(DATA_DIR, 'v3_results.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 保存: {out_file}")

if __name__ == '__main__':
    run_cpcv_v3()
