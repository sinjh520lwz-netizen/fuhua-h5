#!/usr/bin/env python3
"""
JH策略 v5 — 市场过滤 + 低频均值回归
v4已到53%边缘，v5加入市场状态过滤，目标≥70%
"""
import json, os, sys, time, itertools
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data', 'v2')

COMMISSION_RATE = 0.0003
STAMP_TAX = 0.001
MIN_COMMISSION = 5.0
SLIPPAGE = 0.005

TOP_N = 2
MAX_HOLD = 12
INIT_CAPITAL = 15000.0

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
    if len(closes) < period + 1:
        return 50
    deltas = np.diff(closes[-period-1:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def score_v4(kline_dicts, kidx):
    """与v4完全相同的评分"""
    if kidx < 30:
        return 0, {}
    closes = [k['close'] for k in kline_dicts[:kidx+1]]
    volumes = [k['volume'] for k in kline_dicts[:kidx+1]]
    close = closes[-1]
    score = 0
    factors = {}
    
    rsi = compute_rsi(closes)
    if rsi > 40:
        return 0, {}
    
    if len(closes) >= 6:
        mom5 = (closes[-1] / closes[-6] - 1) * 100
        if mom5 > -2:
            return 0, {}
    else:
        return 0, {}
    
    if kidx >= 1:
        day_chg = (closes[-1] / closes[-2] - 1) * 100
        if day_chg < -5:
            return 0, {}
    
    if rsi < 20: score += 12
    elif rsi < 30: score += 8
    else: score += 4
    
    if len(closes) >= 20:
        ma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        lower = ma20 - 2 * std20
        upper = ma20 + 2 * std20
        boll_pos = (close - lower) / (upper - lower) * 100 if upper != lower else 50
        if boll_pos < 10: score += 10
        elif boll_pos < 20: score += 7
    
    down_days = 0
    for i in range(kidx, max(kidx-10, 0), -1):
        if i > 0 and closes[i] < closes[i-1]: down_days += 1
        else: break
    if down_days >= 5: score += 10
    elif down_days >= 3: score += 6
    
    if len(volumes) >= 8:
        recent_vol = np.mean(volumes[-3:])
        prev_vol = np.mean(volumes[-8:-3])
        vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1
        if vol_ratio < 0.5: score += 8
        elif vol_ratio < 0.7: score += 5
    
    if len(closes) >= 60:
        ma60 = np.mean(closes[-60:])
        if close > ma60: score += 6
        else: score -= 4
    
    if len(closes) >= 20:
        ma20 = np.mean(closes[-20:])
        deviation = (close - ma20) / ma20 * 100
        if deviation < -10: score += 8
        elif deviation < -6: score += 5
    
    return max(score, 0), factors

def precompute_v5(all_klines, trading_dates):
    """预计算 + 市场状态"""
    cache_file = os.path.join(DATA_DIR, 'v5_score_cache.json')
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cache = json.load(f)
            if len(cache) > 0 and '_market' in cache:
                print(f"  📦 使用v5缓存 ({len(cache)-1}只)")
                return cache
        except:
            pass
    
    print(f"  ⏳ 预计算v5评分+市场状态...")
    cache = {}
    t0 = time.time()
    
    # 预计算市场状态：每日全市场涨跌中位数
    # 用采样500只股票计算
    sample_codes = []
    for code, info in all_klines.items():
        name = info.get('name', '')
        if 'ST' in name: continue
        if code.startswith(('300','301','688','920')): continue
        sample_codes.append(code)
        if len(sample_codes) >= 500:
            break
    
    market_daily = {}  # date -> median change
    for td in trading_dates:
        changes = []
        for code in sample_codes:
            klines = all_klines.get(code, {}).get('klines', [])
            idx = -1
            for i, k in enumerate(klines):
                if k[0] == td:
                    idx = i
                    break
            if idx < 1: continue
            pc = float(klines[idx-1][2])
            tc = float(klines[idx][2])
            if pc > 0:
                changes.append((tc - pc) / pc)
        if changes:
            market_daily[td] = np.median(changes)
    
    # 计算市场5日均值（平滑）
    market_smooth = {}
    dates_with_data = sorted(market_daily.keys())
    for i, td in enumerate(dates_with_data):
        window = [market_daily[dates_with_data[j]] for j in range(max(0,i-4), i+1)]
        market_smooth[td] = np.mean(window)
    
    cache['_market'] = market_smooth
    
    # 个股评分
    for idx, (code, info) in enumerate(all_klines.items()):
        name = info.get('name', '')
        if 'ST' in name or '*ST' in name or '退' in name: continue
        if code.startswith(('300','301','688','920')): continue
        
        klines = info.get('klines', [])
        if len(klines) < 35: continue
        
        kline_dicts = []
        for k in klines:
            if isinstance(k, list) and len(k) >= 6:
                kline_dicts.append({'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                                    'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])})
        
        dates_in_kline = [k['date'] for k in kline_dicts]
        code_scores = {}
        for td in trading_dates:
            if td not in dates_in_kline: continue
            kidx = dates_in_kline.index(td)
            if kidx < 30: continue
            score, _ = score_v4(kline_dicts, kidx)
            if score >= 15:
                code_scores[td] = {'score': round(score, 1), 'price': round(kline_dicts[kidx]['close'], 2)}
        if code_scores:
            cache[code] = code_scores
        
        if (idx + 1) % 500 == 0:
            print(f"    进度: {idx+1}/{len(all_klines)} ({time.time()-t0:.0f}s)")
    
    with open(cache_file, 'w') as f:
        json.dump(cache, f)
    print(f"  ✅ 完成: {len(cache)-1}只, 耗时{time.time()-t0:.0f}s")
    return cache

def run_backtest_v5(score_cache, all_klines, trading_dates, test_dates_set, params):
    tp_pct = params['tp']
    sl_pct = params['sl']
    min_score = params['min_score']
    cooldown = params['cooldown']
    market_filter = params.get('market_filter', True)
    
    market_smooth = score_cache.get('_market', {})
    test_dates = [d for d in trading_dates if d in test_dates_set]
    
    trades = []
    holdings = {}
    recent_trades = {}
    capital = INIT_CAPITAL
    last_trade_di = -999
    
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
            if not today_k: continue
            
            high = float(today_k[3])
            low = float(today_k[4])
            close = float(today_k[2])
            entry_p = h['entry_price']
            hold_days = di - h['entry_idx']
            
            if hold_days < 1: continue
            
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
        
        # 2. 冷却期
        if di - last_trade_di < cooldown:
            continue
        if len(holdings) >= TOP_N:
            continue
        
        # 3. 市场过滤
        if market_filter:
            mkt = market_smooth.get(date, 0)
            if mkt < -0.005:  # 市场5日均值跌>0.5%
                continue
        
        # 4. 选股
        candidates = []
        for code, date_scores in score_cache.items():
            if code == '_market': continue
            if date not in date_scores: continue
            if code in holdings: continue
            if code in recent_trades and di - recent_trades[code] < 15: continue
            sd = date_scores[date]
            if sd['score'] < min_score: continue
            candidates.append({'code': code, 'score': sd['score'], 'price': sd['price']})
        
        if not candidates: continue
        candidates.sort(key=lambda x: -x['score'])
        
        c = candidates[0]
        entry_price = c['price'] * (1 + SLIPPAGE)
        alloc = capital / TOP_N
        holdings[c['code']] = {
            'entry_price': entry_price, 'entry_date': date,
            'entry_idx': di, 'alloc': alloc,
        }
        last_trade_di = di
    
    return trades, capital

def run_cpcv_v5():
    print("=" * 65)
    print("🔬 JH策略 v5 市场过滤 + 低频均值回归")
    print("=" * 65)
    
    t0 = time.time()
    all_klines = load_all_klines()
    trading_dates = get_trading_dates(all_klines)
    n_dates = len(trading_dates)
    
    print(f"📂 {len(all_klines)}只 | 📅 {trading_dates[0]}~{trading_dates[-1]} ({n_dates}天)")
    
    score_cache = precompute_v5(all_klines, trading_dates)
    
    # 参数网格：重点测TP+10%/SL-6%附近 + 不同冷却期 + 市场过滤开关
    param_grid = [
        # 市场过滤 + 不同TP/SL
        {'tp': 10, 'sl': 6, 'min_score': 15, 'cooldown': 5, 'market_filter': True},
        {'tp': 10, 'sl': 6, 'min_score': 15, 'cooldown': 7, 'market_filter': True},
        {'tp': 10, 'sl': 6, 'min_score': 15, 'cooldown': 10, 'market_filter': True},
        {'tp': 10, 'sl': 5, 'min_score': 15, 'cooldown': 7, 'market_filter': True},
        {'tp': 10, 'sl': 7, 'min_score': 15, 'cooldown': 7, 'market_filter': True},
        {'tp': 8, 'sl': 6, 'min_score': 15, 'cooldown': 7, 'market_filter': True},
        {'tp': 12, 'sl': 6, 'min_score': 15, 'cooldown': 7, 'market_filter': True},
        {'tp': 10, 'sl': 6, 'min_score': 20, 'cooldown': 7, 'market_filter': True},
        # 无市场过滤对照
        {'tp': 10, 'sl': 6, 'min_score': 15, 'cooldown': 7, 'market_filter': False},
        {'tp': 10, 'sl': 6, 'min_score': 15, 'cooldown': 5, 'market_filter': False},
    ]
    
    fold_size = n_dates // N_FOLDS
    folds = []
    for i in range(N_FOLDS):
        start = i * fold_size
        end = start + fold_size if i < N_FOLDS - 1 else n_dates
        folds.append(list(range(start, end)))
    
    combinations = list(itertools.combinations(range(N_FOLDS), N_TEST_FOLDS))
    print(f"📊 {len(param_grid)}组 × {len(combinations)}组合\n")
    
    best_result = None
    best_params = None
    best_robustness = 0
    best_avg_return = -999
    all_param_results = []
    
    for pi, params in enumerate(param_grid):
        results = []
        for test_fold_indices in combinations:
            test_date_indices = set()
            for fi in test_fold_indices:
                test_date_indices.update(folds[fi])
            test_date_set = {trading_dates[i] for i in test_date_indices}
            
            test_trades, final_capital = run_backtest_v5(
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
        active = [r for r in results if r['trades'] > 0]
        avg_return = np.mean([r['total_return'] for r in active]) if active else 0
        
        mf = 'MKT✓' if params['market_filter'] else 'MKT✗'
        label = f"TP+{params['tp']}% SL-{params['sl']}% ≥{params['min_score']} CD{params['cooldown']} {mf}"
        icon = '✅' if robustness >= 0.7 else '⚠️' if robustness >= 0.5 else '❌'
        print(f"  {icon} [{pi+1}/{len(param_grid)}] {label} → {robustness:.0%} ({profitable_count}/{len(combinations)}) 平均{avg_return:+.1f}%")
        
        all_param_results.append({
            'tp': params['tp'], 'sl': params['sl'], 'score': params['min_score'],
            'cooldown': params['cooldown'], 'market_filter': params['market_filter'],
            'robustness': round(robustness, 3),
            'profitable_combos': profitable_count,
            'avg_return': round(avg_return, 2),
        })
        
        if robustness > best_robustness or (robustness == best_robustness and avg_return > best_avg_return):
            best_robustness = robustness
            best_avg_return = avg_return
            best_params = params
            best_result = results
    
    elapsed = time.time() - t0
    
    if best_params is None:
        best_params = param_grid[0]
        best_result = [{'profitable': False, 'total_return': 0, 'trades': 0, 'win_rate': 0}] * 15
    
    active = [r for r in best_result if r['trades'] > 0]
    mean_ret = np.mean([r['total_return'] for r in active]) if active else 0
    
    verdict = '✅ 可信' if best_robustness >= 0.7 else '⚠️ 边缘' if best_robustness >= 0.5 else '❌ 不可信'
    
    print(f"\n{'='*65}")
    print(f"🔬 v5 CPCV 最优 (耗时{elapsed:.0f}s)")
    print(f"{'='*65}")
    print(f"  参数: TP+{best_params['tp']}% SL-{best_params['sl']}% ≥{best_params['min_score']} 冷却{best_params['cooldown']}天 市场过滤{'开' if best_params['market_filter'] else '关'}")
    print(f"  稳健性: {best_robustness:.0%} ({sum(1 for r in best_result if r['profitable'])}/{len(best_result)})")
    print(f"  平均收益: {mean_ret:+.2f}%")
    print(f"  判定: {verdict}")
    
    # 完整回测
    print(f"\n📊 完整回测...")
    full_trades, full_capital = run_backtest_v5(
        score_cache, all_klines, trading_dates, set(trading_dates), best_params
    )
    
    fb = {}
    n = len(full_trades)
    if n > 0:
        wins = sum(1 for t in full_trades if t['return_pct'] > 0)
        tp_c = sum(1 for t in full_trades if t['hit_type'] == 'TP')
        sl_c = sum(1 for t in full_trades if t['hit_type'] == 'SL')
        hold_c = sum(1 for t in full_trades if t['hit_type'] == 'HOLD')
        total_ret = sum(t['return_pct'] for t in full_trades)
        avg_hold = np.mean([t['hold_days'] for t in full_trades])
        
        print(f"  交易: {n}笔 ({n/(n_dates/5):.1f}笔/周)")
        print(f"  胜率: {wins/n*100:.1f}%")
        print(f"  TP: {tp_c}({tp_c/n*100:.0f}%) SL: {sl_c}({sl_c/n*100:.0f}%) HOLD: {hold_c}({hold_c/n*100:.0f}%)")
        print(f"  平均持仓: {avg_hold:.1f}天")
        print(f"  总收益: {total_ret:+.2f}%")
        print(f"  本金: {INIT_CAPITAL:.0f} → {full_capital:.0f}")
        
        fb = {
            'range': f'{trading_dates[0]}~{trading_dates[-1]}',
            'trades': n, 'win_rate': round(wins/n*100, 1),
            'tp_count': tp_c, 'sl_count': sl_c, 'hold_count': hold_c,
            'avg_hold': round(avg_hold, 1),
            'trades_per_week': round(n/(n_dates/5), 1),
            'total_return': round(total_ret, 2),
            'final_capital': round(full_capital, 2),
        }
    
    output = {
        'strategy': 'JH v5 市场过滤均值回归',
        'version': '5.0',
        'core_change': 'v4基础上加入市场状态过滤（5日均值跌>0.5%不开仓）',
        'improvements': [
            '市场状态过滤（避免系统性下跌中买入）',
            f'冷却期{best_params["cooldown"]}天',
            f'TP+{best_params["tp"]}% SL-{best_params["sl"]}%',
            'RSI<40 + 5日跌>2% + 连跌3天',
            '完整交易成本',
            'CPCV验证',
        ],
        'params': {
            'tp': best_params['tp'], 'sl': best_params['sl'],
            'min_score': best_params['min_score'],
            'max_hold': MAX_HOLD, 'top_n': TOP_N,
            'cooldown': best_params['cooldown'],
            'market_filter': best_params['market_filter'],
            'capital': INIT_CAPITAL, 'slippage': SLIPPAGE,
        },
        'cpcv': {
            'robustness': round(best_robustness, 3),
            'profitable_combos': sum(1 for r in best_result if r['profitable']),
            'total_combos': len(best_result),
            'verdict': verdict,
            'mean_return': round(mean_ret, 2),
            'combinations': best_result,
        },
        'param_search': all_param_results,
        'full_backtest': fb,
        'trades': full_trades[-100:] if full_trades else [],
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    
    out_file = os.path.join(DATA_DIR, 'v5_results.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 保存: {out_file}")

if __name__ == '__main__':
    run_cpcv_v5()
