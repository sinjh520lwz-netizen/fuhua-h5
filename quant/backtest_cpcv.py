#!/usr/bin/env python3
"""
JH策略 CPCV回测 (优化版)
预计算所有股票评分，然后快速跑15种组合
"""
import json, os, sys, time, itertools
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze, score_early_entry

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')

TP_PCT = 6.0
SL_PCT = 4.0
MAX_HOLD = 7
MIN_SCORE = 15
TOP_N = 5
INIT_CAPITAL = 15000.0
SLIPPAGE = 0.005
COMMISSION_RATE = 0.0003
STAMP_TAX = 0.001
MIN_COMMISSION = 5.0

N_FOLDS = 6
N_TEST_FOLDS = 2
PURGE_DAYS = 5

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

def precompute_scores(all_klines, trading_dates):
    """预计算所有股票在所有交易日的评分和价格"""
    cache_file = os.path.join(DATA_DIR, 'score_cache.json')
    
    # 检查缓存是否有效
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cache = json.load(f)
            if len(cache) > 0:
                print(f"  📦 使用评分缓存 ({len(cache)}只)")
                return cache
        except:
            pass
    
    print(f"  ⏳ 预计算评分（{len(all_klines)}只 × {len(trading_dates)}天）...")
    cache = {}  # {code: {date: {score, price}}}
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
        
        # 构建dict格式K线
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
            
            if score >= MIN_SCORE:
                code_scores[td] = {'score': round(score, 1), 'price': round(day_close, 2)}
        
        if code_scores:
            cache[code] = code_scores
        
        if (idx + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"    进度: {idx+1}/{len(all_klines)} ({elapsed:.0f}s)")
    
    # 保存缓存
    with open(cache_file, 'w') as f:
        json.dump(cache, f)
    
    elapsed = time.time() - t0
    total_scores = sum(len(v) for v in cache.values())
    print(f"  ✅ 预计算完成: {len(cache)}只, {total_scores}条评分, 耗时{elapsed:.0f}s")
    return cache

def run_backtest(score_cache, trading_dates, test_date_set):
    """在指定日期集上跑回测"""
    test_dates = [d for d in trading_dates if d in test_date_set]
    
    trades = []
    holdings = {}
    recent_trades = {}
    capital = INIT_CAPITAL
    
    for di, date in enumerate(test_dates):
        # 1. 检查持仓
        closed = []
        for code, h in list(holdings.items()):
            kline_data = score_cache.get(code, {})
            # 找当日价格（从原始K线）
            # 需要high/low/close，从all_klines取
            # 这里简化：用score_cache的price做close，TP/SL用固定百分比
            
            # 更精确：直接查原始K线
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
            
            tp_price = entry_p * (1 + TP_PCT / 100)
            sl_price = entry_p * (1 - SL_PCT / 100)
            
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
                sell_cost = max(MIN_COMMISSION, h['alloc'] * (1 + ret_pct/100) * COMMISSION_RATE)
                stamp = h['alloc'] * (1 + ret_pct/100) * STAMP_TAX
                total_cost = (buy_cost + sell_cost + stamp) / h['alloc'] * 100
                net_ret = ret_pct - total_cost
                
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
        
        # 2. 选股（从预计算缓存读取）
        candidates = []
        for code, date_scores in score_cache.items():
            if date not in date_scores:
                continue
            if code in holdings:
                continue
            if code in recent_trades and di - recent_trades[code] < 7:
                continue
            sd = date_scores[date]
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

def run_cpcv():
    print("=" * 65)
    print("🔬 JH策略 CPCV回测 (优化版)")
    print("=" * 65)
    
    global all_klines_global
    t0 = time.time()
    all_klines_global = load_all_klines()
    trading_dates = get_trading_dates(all_klines_global)
    n_dates = len(trading_dates)
    
    print(f"📂 股票池: {len(all_klines_global)}只")
    print(f"📅 交易日: {trading_dates[0]} ~ {trading_dates[-1]} ({n_dates}天)")
    print(f"📋 CPCV: {N_FOLDS}折, 取{N_TEST_FOLDS}折测试, 隔离带{PURGE_DAYS}天")
    
    # 预计算评分
    score_cache = precompute_scores(all_klines_global, trading_dates)
    
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
        # 构建测试集日期
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
        
        # 在测试集上跑回测
        test_trades, final_capital = run_backtest(score_cache, trading_dates, test_date_set)
        
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
        
        print(f"  组合{ci+1}/{len(combinations)}: 测试折{list(test_fold_indices)} "
              f"→ {n_trades}笔 胜率{win_rate:.0f}% 收益{total_return:+.1f}% "
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
    
    print(f"\n{'='*65}")
    print(f"🔬 CPCV 回测结果 (耗时{elapsed:.0f}s)")
    print(f"{'='*65}")
    print(f"  总组合数: {total_combos}")
    print(f"  盈利组合: {profitable_count}/{total_combos}")
    print(f"  稳健性评分: {robustness_score:.1%}")
    verdict = '✅ 策略可信' if robustness_score >= 0.7 else '⚠️ 策略边缘' if robustness_score >= 0.5 else '❌ 策略不可信'
    print(f"  判定: {verdict}")
    print(f"\n  平均收益: {mean_return:+.2f}%")
    print(f"  收益标准差: {std_return:.2f}%")
    print(f"  Sharpe-like: {sharpe_like:.2f}")
    
    print(f"\n  📊 各组合收益:")
    for r in results:
        if r['trades'] == 0:
            continue
        bar_len = max(1, int(abs(r['total_return']) / 3))
        bar = '█' * bar_len
        print(f"    组合{r['combo']:2d}: {r['total_return']:+6.1f}% "
              f"({r['trades']}笔 胜率{r['win_rate']:.0f}%) "
              f"{'🟢' if r['profitable'] else '🔴'} {bar}")
    
    output = {
        'strategy': 'JH CPCV回测',
        'params': {
            'n_folds': N_FOLDS, 'n_test_folds': N_TEST_FOLDS,
            'purge_days': PURGE_DAYS, 'tp': TP_PCT, 'sl': SL_PCT,
            'max_hold': MAX_HOLD, 'min_score': MIN_SCORE,
            'top_n': TOP_N, 'capital': INIT_CAPITAL, 'slippage': SLIPPAGE,
            'commission': COMMISSION_RATE, 'stamp_tax': STAMP_TAX,
            'min_commission': MIN_COMMISSION,
        },
        'range': f'{trading_dates[0]}~{trading_dates[-1]}',
        'total_days': n_dates,
        'robustness_score': round(robustness_score, 3),
        'profitable_combos': profitable_count,
        'total_combos': total_combos,
        'mean_return': round(mean_return, 2),
        'std_return': round(std_return, 2),
        'sharpe_like': round(sharpe_like, 2),
        'verdict': verdict,
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'combinations': results,
    }
    
    out_file = os.path.join(DATA_DIR, 'cpcv_results.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 保存: {out_file}")

if __name__ == '__main__':
    run_cpcv()
