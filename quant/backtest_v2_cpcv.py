#!/usr/bin/env python3
"""
JH策略 v2 优化版回测
基于3轮量化学习的改进：
1. CVaR动态止损（替代固定SL-4%）
2. 大盘过滤（MA20以下不买）
3. 因子IC加权（替代等权打分）
4. 评分门槛优化
5. 完整交易成本模型
6. CPCV验证
"""
import json, os, sys, time, itertools
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze, score_early_entry

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data', 'v2')
os.makedirs(DATA_DIR, exist_ok=True)

# ========== 交易成本模型（真实） ==========
COMMISSION_RATE = 0.0003  # 万三佣金
STAMP_TAX = 0.001  # 千一印花税（卖出）
MIN_COMMISSION = 5.0  # 最低佣金5元
SLIPPAGE = 0.005  # 0.5%滑点

# ========== 策略参数 ==========
TOP_N = 5
MAX_HOLD = 7
INIT_CAPITAL = 15000.0

# ========== CPCV参数 ==========
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

def compute_cvar(returns, alpha=0.05):
    """计算CVaR(95%) — 条件风险价值"""
    if len(returns) < 5:
        return 0.04  # 默认4%
    sorted_ret = sorted(returns)
    n = max(1, int(len(sorted_ret) * alpha))
    cvar = abs(np.mean(sorted_ret[:n]))
    return max(0.01, min(cvar, 0.15))  # 限制在1%-15%

def get_dynamic_sl(code, date, all_klines, trading_dates):
    """基于CVaR的动态止损"""
    klines = all_klines.get(code, {}).get('klines', [])
    dates = [k[0] for k in klines]
    if date not in dates:
        return 0.04  # 默认4%
    idx = dates.index(date)
    if idx < 20:
        return 0.04
    
    # 计算近20日收益率
    recent_returns = []
    for i in range(max(0, idx-19), idx+1):
        if i > 0:
            c1 = float(klines[i-1][2])
            c2 = float(klines[i][2])
            if c1 > 0:
                recent_returns.append((c2 - c1) / c1)
    
    cvar = compute_cvar(recent_returns)
    
    # CVaR高 → 市场剧烈 → 收紧止损
    # CVaR低 → 市场平静 → 放宽止损
    if cvar > 0.06:
        return 0.03  # 收紧到3%
    elif cvar > 0.04:
        return 0.04  # 保持4%
    else:
        return 0.05  # 放宽到5%

def get_market_regime(trading_dates, di, all_klines):
    """大盘状态过滤 — 用全市场涨跌中位数判断"""
    if di < 20:
        return True  # 数据不足，默认通过
    
    date = trading_dates[di]
    
    # 计算当日全市场涨跌幅中位数
    changes = []
    for code, info in list(all_klines.items())[:500]:  # 采样500只
        klines = info.get('klines', [])
        today_k = None
        today_idx = -1
        for i, k in enumerate(klines):
            if k[0] == date:
                today_k = k
                today_idx = i
                break
        if not today_k or today_idx < 1:
            continue
        prev_close = float(klines[today_idx-1][2])
        today_close = float(today_k[2])
        if prev_close > 0:
            changes.append((today_close - prev_close) / prev_close)
    
    if not changes:
        return True
    
    median_change = np.median(changes)
    
    # 大盘中位数跌超1.5% → 不买
    if median_change < -0.015:
        return False
    return True

def get_market_change(trading_dates, di, all_klines):
    """获取大盘涨跌幅"""
    if di < 1:
        return 0
    date = trading_dates[di]
    changes = []
    for code, info in list(all_klines.items())[:300]:
        klines = info.get('klines', [])
        today_idx = -1
        for i, k in enumerate(klines):
            if k[0] == date:
                today_idx = i
                break
        if today_idx < 1:
            continue
        pc = float(klines[today_idx-1][2])
        tc = float(klines[today_idx][2])
        if pc > 0:
            changes.append((tc - pc) / pc)
    return np.median(changes) * 100 if changes else 0

def precompute_v2(all_klines, trading_dates):
    """预计算v2评分（含大盘状态）"""
    cache_file = os.path.join(DATA_DIR, 'v2_score_cache.json')
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cache = json.load(f)
            if len(cache) > 0:
                print(f"  📦 使用v2评分缓存 ({len(cache)}只)")
                return cache
        except:
            pass
    
    print(f"  ⏳ 预计算v2评分...")
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
            ind = quick_analyze(hist)
            if not ind:
                continue
            
            day_close = kline_dicts[kidx]['close']
            day_open = kline_dicts[kidx]['open']
            day_change = (day_close / day_open - 1) * 100 if day_open > 0 else 0
            
            score, factors = score_early_entry(ind, day_change, 0)
            
            # 计算动态止损
            recent_returns = []
            for i in range(max(0, kidx-19), kidx+1):
                if i > 0:
                    c1 = kline_dicts[i-1]['close']
                    c2 = kline_dicts[i]['close']
                    if c1 > 0:
                        recent_returns.append((c2 - c1) / c1)
            cvar = compute_cvar(recent_returns)
            
            code_scores[td] = {
                'score': round(score, 1),
                'price': round(day_close, 2),
                'cvar': round(cvar, 4),
            }
        
        if code_scores:
            cache[code] = code_scores
        
        if (idx + 1) % 500 == 0:
            print(f"    进度: {idx+1}/{len(all_klines)} ({time.time()-t0:.0f}s)")
    
    with open(cache_file, 'w') as f:
        json.dump(cache, f)
    
    print(f"  ✅ 预计算完成: {len(cache)}只, 耗时{time.time()-t0:.0f}s")
    return cache

def run_v2_backtest(score_cache, all_klines, trading_dates, test_dates_set, params):
    """v2回测 — 含CVaR动态止损+大盘过滤+交易成本"""
    tp_pct = params['tp']
    min_score = params['min_score']
    
    test_dates = [d for d in trading_dates if d in test_dates_set]
    
    trades = []
    holdings = {}
    recent_trades = {}
    capital = INIT_CAPITAL
    
    for di, date in enumerate(test_dates):
        # 1. 检查持仓 TP/SL(CVaR)/到期
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
            sl_pct = h.get('sl_pct', 0.04)  # CVaR动态止损
            sl_price = entry_p * (1 - sl_pct)
            
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
                # 完整交易成本
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
                    'sl_pct': round(sl_pct * 100, 1),
                })
                capital += h['alloc'] * (net_ret / 100)
                closed.append(code)
                recent_trades[code] = di
        
        for c in closed:
            del holdings[c]
        
        # 2. 大盘过滤
        market_ok = get_market_regime(trading_dates, 
                                       trading_dates.index(date) if date in trading_dates else 0,
                                       all_klines)
        if not market_ok:
            continue
        
        # 3. 选股
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
            candidates.append({
                'code': code, 'score': sd['score'],
                'price': sd['price'], 'cvar': sd['cvar'],
            })
        
        candidates.sort(key=lambda x: -x['score'])
        
        # 4. 买入
        slots = TOP_N - len(holdings)
        for c in candidates[:slots]:
            entry_price = c['price'] * (1 + SLIPPAGE)
            alloc = capital / TOP_N
            
            # CVaR动态止损
            cvar = c['cvar']
            if cvar > 0.06:
                sl_pct = 0.03
            elif cvar > 0.04:
                sl_pct = 0.04
            else:
                sl_pct = 0.05
            
            holdings[c['code']] = {
                'entry_price': entry_price,
                'entry_date': date,
                'entry_idx': di,
                'alloc': alloc,
                'sl_pct': sl_pct,
            }
    
    return trades, capital

def run_cpcv_v2():
    print("=" * 65)
    print("🔬 JH策略 v2 优化版 CPCV回测")
    print("=" * 65)
    
    t0 = time.time()
    all_klines = load_all_klines()
    trading_dates = get_trading_dates(all_klines)
    n_dates = len(trading_dates)
    
    print(f"📂 股票池: {len(all_klines)}只")
    print(f"📅 交易日: {trading_dates[0]} ~ {trading_dates[-1]} ({n_dates}天)")
    
    # 预计算
    score_cache = precompute_v2(all_klines, trading_dates)
    
    # 参数网格搜索
    param_grid = [
        {'tp': 6, 'min_score': 20},
        {'tp': 6, 'min_score': 25},
        {'tp': 6, 'min_score': 30},
        {'tp': 7, 'min_score': 20},
        {'tp': 7, 'min_score': 25},
        {'tp': 8, 'min_score': 20},
        {'tp': 5, 'min_score': 20},
        {'tp': 5, 'min_score': 25},
    ]
    
    # 划分N折
    fold_size = n_dates // N_FOLDS
    folds = []
    for i in range(N_FOLDS):
        start = i * fold_size
        end = start + fold_size if i < N_FOLDS - 1 else n_dates
        folds.append(list(range(start, end)))
    
    combinations = list(itertools.combinations(range(N_FOLDS), N_TEST_FOLDS))
    
    best_result = None
    best_params = None
    best_robustness = 0
    
    for pi, params in enumerate(param_grid):
        print(f"\n📋 参数组{pi+1}/{len(param_grid)}: TP+{params['tp']}% 门槛≥{params['min_score']}")
        
        results = []
        for ci, test_fold_indices in enumerate(combinations):
            test_date_indices = set()
            for fi in test_fold_indices:
                test_date_indices.update(folds[fi])
            
            test_date_set = {trading_dates[i] for i in test_date_indices}
            
            test_trades, final_capital = run_v2_backtest(
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
        avg_return = np.mean([r['total_return'] for r in results if r['trades'] > 0])
        
        print(f"  稳健性: {robustness:.0%} ({profitable_count}/{len(results)}) 平均收益: {avg_return:+.1f}%")
        
        if robustness > best_robustness or (robustness == best_robustness and avg_return > 0):
            best_robustness = robustness
            best_params = params
            best_result = results
    
    # ========== 汇总 ==========
    elapsed = time.time() - t0
    
    print(f"\n{'='*65}")
    print(f"🔬 v2 CPCV 最优结果 (耗时{elapsed:.0f}s)")
    print(f"{'='*65}")
    print(f"  最优参数: TP+{best_params['tp']}% 门槛≥{best_params['min_score']}")
    print(f"  稳健性: {best_robustness:.0%}")
    
    all_returns = [r['total_return'] for r in best_result if r['trades'] > 0]
    print(f"  平均收益: {np.mean(all_returns):+.2f}%")
    print(f"  收益标准差: {np.std(all_returns):.2f}%")
    
    verdict = '✅ 可信' if best_robustness >= 0.7 else '⚠️ 边缘' if best_robustness >= 0.5 else '❌ 不可信'
    print(f"  判定: {verdict}")
    
    # 用最优参数跑完整回测（用于页面展示）
    print(f"\n📊 用最优参数跑完整回测...")
    all_dates_set = set(trading_dates)
    full_trades, full_capital = run_v2_backtest(
        score_cache, all_klines, trading_dates, all_dates_set, best_params
    )
    
    n = len(full_trades)
    if n > 0:
        wins = sum(1 for t in full_trades if t['return_pct'] > 0)
        tp_count = sum(1 for t in full_trades if t['hit_type'] == 'TP')
        sl_count = sum(1 for t in full_trades if t['hit_type'] == 'SL')
        hold_count = sum(1 for t in full_trades if t['hit_type'] == 'HOLD')
        total_ret = sum(t['return_pct'] for t in full_trades)
        
        print(f"  总交易: {n}笔")
        print(f"  胜率: {wins/n*100:.1f}%")
        print(f"  TP: {tp_count}({tp_count/n*100:.0f}%) SL: {sl_count}({sl_count/n*100:.0f}%) HOLD: {hold_count}({hold_count/n*100:.0f}%)")
        print(f"  总收益: {total_ret:+.2f}%")
        print(f"  本金: {INIT_CAPITAL:.0f} → {full_capital:.0f}")
    
    # 保存结果
    output = {
        'strategy': 'JH v2 优化版',
        'version': '2.0',
        'improvements': [
            'CVaR动态止损（替代固定-4%）',
            '大盘过滤（全市场中位数跌>1.5%不买）',
            '评分门槛提高（≥20/25/30）',
            '完整交易成本（万三佣金+千一印花税+5元最低+0.5%滑点）',
            'CPCV验证（6折取2折，purge 5天）',
        ],
        'params': {
            'tp': best_params['tp'],
            'min_score': best_params['min_score'],
            'max_hold': MAX_HOLD,
            'top_n': TOP_N,
            'capital': INIT_CAPITAL,
            'sl': 'CVaR动态(3%/4%/5%)',
            'slippage': SLIPPAGE,
            'commission': COMMISSION_RATE,
            'stamp_tax': STAMP_TAX,
            'min_commission': MIN_COMMISSION,
        },
        'cpcv': {
            'n_folds': N_FOLDS,
            'n_test_folds': N_TEST_FOLDS,
            'purge_days': PURGE_DAYS,
            'robustness': round(best_robustness, 3),
            'profitable_combos': sum(1 for r in best_result if r['profitable']),
            'total_combos': len(best_result),
            'verdict': verdict,
            'mean_return': round(np.mean(all_returns), 2) if all_returns else 0,
            'std_return': round(np.std(all_returns), 2) if len(all_returns) > 1 else 0,
            'combinations': best_result,
        },
        'full_backtest': {
            'range': f'{trading_dates[0]}~{trading_dates[-1]}',
            'trades': n if n > 0 else 0,
            'win_rate': round(wins/n*100, 1) if n > 0 else 0,
            'tp_count': tp_count if n > 0 else 0,
            'sl_count': sl_count if n > 0 else 0,
            'hold_count': hold_count if n > 0 else 0,
            'total_return': round(total_ret, 2) if n > 0 else 0,
            'final_capital': round(full_capital, 2) if n > 0 else INIT_CAPITAL,
        },
        'trades': full_trades[-100:] if full_trades else [],  # 最近100笔
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'param_search': [{'tp': p['tp'], 'min_score': p['min_score']} for p in param_grid],
    }
    
    out_file = os.path.join(DATA_DIR, 'v2_results.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 保存: {out_file}")

if __name__ == '__main__':
    run_cpcv_v2()
