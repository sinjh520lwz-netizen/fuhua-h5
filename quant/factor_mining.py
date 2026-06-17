#!/usr/bin/env python3
"""
JH 因子挖掘引擎 — 系统性发现A股有效预测因子
使用3024只股票60天K线数据，计算30+候选因子并检验T+1预测能力
按用户要求：只关注胜率(win rate)，不在意收益率大小
"""
import json, os, sys, math, time
import numpy as np
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
RESULT_DIR = os.path.join(DATA_DIR, 'factor_mining')
os.makedirs(RESULT_DIR, exist_ok=True)

# ============================================================
# 1. 数据加载
# ============================================================
def load_all_klines():
    fpath = os.path.join(DATA_DIR, 'all_klines_60d.json')
    with open(fpath) as f:
        return json.load(f)

def get_trading_dates(all_klines_dict, min_count=100):
    """提取交易日列表"""
    date_counts = defaultdict(int)
    for code, info in all_klines_dict.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                date_counts[k[0]] += 1
    sorted_dates = sorted(date_counts.items(), key=lambda x: -x[1])
    return sorted([d for d, c in sorted_dates if c > min_count and d >= '2026-03-01'])

def get_stock_on_date(info, date):
    """获取某日行情数据"""
    klines = info.get('klines', [])
    for k in klines:
        if isinstance(k, list) and len(k) >= 6 and k[0] == date:
            return {
                'open': float(k[1]), 'close': float(k[2]),
                'high': float(k[3]), 'low': float(k[4]),
                'volume': float(k[5]),
            }
    return None

def get_stock_history(info, end_date):
    """获取到指定日期为止的完整历史（不含当天）"""
    result = []
    for k in info.get('klines', []):
        if isinstance(k, list) and len(k) >= 6:
            if k[0] >= end_date:
                break
            result.append({
                'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5]),
            })
    return result

# ============================================================
# 2. 因子库 — 全部30个因子
# ============================================================
def compute_all_factors(hist):
    """
    从历史K线计算所有因子值
    返回: dict of factor_name -> value, 或 None 如果数据不足
    """
    if len(hist) < 25:
        return None
    
    n = len(hist)
    # 基础数据
    C = np.array([h['close'] for h in hist])
    O = np.array([h['open'] for h in hist])
    H = np.array([h['high'] for h in hist])
    L = np.array([h['low'] for h in hist])
    V = np.array([h['volume'] for h in hist])
    
    # 日收益率
    R = np.diff(C) / C[:-1]
    if len(R) < 20:
        return None
    
    # 当前价格
    cp = C[-1]
    
    # ===== A. 动量类 =====
    factors = {}
    
    # 1. 短期动量 (5日)
    if n >= 6:
        factors['mom_5d'] = cp / C[-6] - 1
    # 2. 中期动量 (20日)
    if n >= 21:
        factors['mom_20d'] = cp / C[-21] - 1
    # 3. 动量反转 (5日-20日, 短期超长期)
    if 'mom_5d' in factors and 'mom_20d' in factors:
        factors['mom_reversal'] = factors['mom_5d'] - factors['mom_20d']
    # 4. 加权动量
    if n >= 21:
        weights = np.array([(20-i)/210.0 for i in range(20)])
        factors['w_momentum'] = sum(w * (C[-i]/C[-i-1]-1) for i, w in enumerate(range(20,0,-1)) for _ in [0])  # 简化
        wm = 0
        for i in range(20):
            w = (20-i) / 210.0
            wm += w * (C[-20+i] / C[-21+i] - 1) if C[-21+i] > 0 else 0
        factors['w_momentum'] = wm
    
    # 5. 路径依赖动量 (趋势效率)
    if n >= 21:
        total_path = sum(abs(C[-i] - C[-i-1]) for i in range(1, 21)) / C[-21] if C[-21] > 0 else 1
        net_change = (cp - C[-21]) / C[-21]
        factors['path_momentum'] = net_change / total_path if total_path > 0 else 0
    
    # 6. 动量加速
    if n >= 11:
        mom5_now = cp / C[-6] - 1
        mom5_before = C[-6] / C[-11] - 1
        factors['mom_accel'] = mom5_now - mom5_before
    
    # ===== B. 波动类 =====
    # 7. 历史波动率 (20日)
    if len(R) >= 20:
        factors['volatility_20'] = np.std(R[-20:])
    
    # 8. 波动率变化率
    if len(R) >= 20:
        vol_short = np.std(R[-5:]) if len(R) >= 5 else 0
        vol_long = np.std(R[-20:])
        factors['vol_ratio'] = vol_short / vol_long if vol_long > 0 else 0
    
    # 9. 振幅因子
    if n >= 20:
        amp = [(H[-i] - L[-i]) / C[-i] for i in range(1, 21)]
        factors['avg_amplitude'] = np.mean(amp)
    
    # 10. 偏度 (特质波动率代理)
    if len(R) >= 20:
        factors['skewness_20'] = np.mean((R[-20:] - np.mean(R[-20:]))**3) / (np.std(R[-20:])**3 + 1e-10)
    
    # ===== C. 量价关系类 =====
    # 11. 量价相关性
    if n >= 21 and np.std(V[-21:-1]) > 0 and np.std(C[-21:-1]) > 0:
        p_corr = np.corrcoef(C[-21:-1], V[-21:-1])[0, 1]
        factors['vp_corr'] = p_corr  # 负值 = 量价背离 = A股最强因子
    
    # 12. 放量上涨因子 (上涨日量/下跌日量)
    if n >= 21:
        up_vols = [V[-i] for i in range(1, 21) if C[-i] > C[-i-1]]
        down_vols = [V[-i] for i in range(1, 21) if C[-i] < C[-i-1]]
        factors['vol_asymmetry'] = (np.mean(up_vols) if up_vols else 0) / (np.mean(down_vols) if down_vols else 1)
    
    # 13. 成交量动量
    if n >= 25:
        vol_5 = np.mean(V[-5:])
        vol_20 = np.mean(V[-20:])
        factors['vol_momentum'] = vol_5 / vol_20 if vol_20 > 0 else 0
    
    # 14. 换手率变化
    if n >= 6:
        vol_ratio = V[-1] / np.mean(V[-6:-1]) if np.mean(V[-6:-1]) > 0 else 1
        factors['volume_change'] = vol_ratio
    
    # 15. OBV方向
    if n >= 11:
        obv = 0
        for i in range(1, 11):
            if C[-i] > C[-i-1]:
                obv += V[-i] / V[-i-1] if V[-i-1] > 0 else 1
            elif C[-i] < C[-i-1]:
                obv -= V[-i] / V[-i-1] if V[-i-1] > 0 else 1
        factors['obv_direction'] = obv / 10
    
    # 16. 量缩价涨
    if n >= 6 and np.mean(V[-6:-1]) > 0:
        price_up = cp / C[-6] - 1
        vol_down = 1 - V[-1] / np.mean(V[-6:-1])
        factors['vol_price_diverge'] = price_up * vol_down
    
    # ===== D. 技术形态类 =====
    # 17. 价格位置
    if n >= 22:
        high_20 = np.max(H[-21:-1])
        low_20 = np.min(L[-21:-1])
        factors['price_position'] = (cp - low_20) / (high_20 - low_20) if high_20 > low_20 else 0.5
    
    # 18. 均线偏离度
    if n >= 21:
        ma20 = np.mean(C[-21:-1])
        factors['ma_deviation'] = (cp - ma20) / ma20 if ma20 > 0 else 0
    
    # 19. 均线斜率
    if n >= 26:
        ma20_recent = np.mean(C[-5:])
        ma20_before = np.mean(C[-25:-5])
        factors['ma_slope'] = (ma20_recent - ma20_before) / ma20_before if ma20_before > 0 else 0
    
    # 20. 缺口因子
    factors['gap'] = O[-1] / C[-2] - 1 if C[-2] > 0 else 0
    
    # 21. 影线比例
    if H[-1] > L[-1]:
        upper_shadow = (H[-1] - max(O[-1], C[-1])) / (H[-1] - L[-1])
        lower_shadow = (min(O[-1], C[-1]) - L[-1]) / (H[-1] - L[-1])
        factors['shadow_ratio'] = upper_shadow - lower_shadow  # 正=上影线长=卖压
    
    # ===== E. 统计类 =====
    # 22. 最大单日收益
    if len(R) >= 20:
        factors['max_daily_return'] = np.max(R[-20:])
        factors['min_daily_return'] = np.min(R[-20:])
    
    # 23. 涨停因子 (近似：涨>9.5%)
    if len(R) >= 20:
        factors['limit_up_count'] = sum(1 for r in R[-20:] if r >= 0.095)
    
    # 24. 信息比率
    if len(R) >= 20 and np.std(R[-20:]) > 0:
        factors['info_ratio_20'] = np.mean(R[-20:]) / np.std(R[-20:])
    
    # 25. Kaufman效率比
    if n >= 22:
        net = abs(cp - C[-21])
        path = sum(abs(C[-i] - C[-i-1]) for i in range(1, 21))
        factors['efficiency_ratio'] = net / path if path > 0 else 0
    
    # 26. 新高新低非对称
    if n >= 22:
        high_20 = np.max(H[-21:-1])
        low_20 = np.min(L[-21:-1])
        nh = sum(1 for i in range(1, 21) if H[-i] >= high_20)
        nl = sum(1 for i in range(1, 21) if L[-i] <= low_20)
        factors['hl_asymmetry'] = (nh - nl) / 20
    
    # ===== F. 复合因子 =====
    # 27. 量价动量复合
    if 'mom_5d' in factors and 'vol_momentum' in factors:
        factors['vp_compound'] = factors['mom_5d'] * factors['vol_momentum']
    
    # 28. 低波反转
    if 'volatility_20' in factors and 'mom_5d' in factors:
        factors['low_vol_reversal'] = factors['volatility_20'] * factors['mom_5d'] * (-1)
    
    # 29. 趋势质量
    if 'mom_20d' in factors and 'efficiency_ratio' in factors:
        factors['trend_quality'] = (1 if factors['mom_20d'] > 0 else -1) * factors['efficiency_ratio'] * abs(factors['mom_20d'])
    
    # 30. 异常量价
    if n >= 21:
        price_anom = cp / np.mean(C[-21:-1]) - 1 if np.mean(C[-21:-1]) > 0 else 0
        vol_anom = V[-1] / np.mean(V[-21:-1]) - 1 if np.mean(V[-21:-1]) > 0 else 0
        factors['abnormal_vp'] = price_anom * vol_anom
    
    # ===== G. 额外实用因子（参考现有系统） =====
    # 31. RSI
    if len(R) >= 14:
        gains = [r for r in R[-14:] if r > 0]
        losses = [-r for r in R[-14:] if r < 0]
        ag = np.mean(gains) if gains else 0
        al = np.mean(losses) if losses else 0
        factors['rsi_14'] = 100 - (100 / (1 + ag/al)) if al > 0 else 100
    
    # 32. 布林位置
    if n >= 21:
        ma = np.mean(C[-21:-1])
        std = np.std(C[-21:-1])
        factors['boll_pos'] = (cp - (ma - 2*std)) / (4*std) if std > 0 else 0.5
    
    # 33. 均线收敛度
    if n >= 24:
        ma5 = np.mean(C[-5:])
        ma10 = np.mean(C[-10:])
        ma20 = np.mean(C[-20:])
        ma_all = [v for v in [ma5, ma10, ma20] if v > 0]
        if len(ma_all) >= 2:
            factors['ma_convergence'] = np.std(ma_all) / np.mean(ma_all)
    
    # 34. VWAP偏离
    if V[-1] > 0 and cp > 0:
        vwap_5 = sum(C[-i] * V[-i] for i in range(1, 6)) / sum(V[-i] for i in range(1, 6)) if sum(V[-i] for i in range(1, 6)) > 0 else cp
        factors['vwap_deviation'] = (cp - vwap_5) / vwap_5 if vwap_5 > 0 else 0
    
    # 35. 量比加速度
    if n >= 11:
        vol_5_curr = np.mean(V[-5:])
        vol_5_prev = np.mean(V[-10:-5])
        factors['vol_accel'] = vol_5_curr / vol_5_prev if vol_5_prev > 0 else 1
    
    return factors

# ============================================================
# 3. 因子有效性检验
# ============================================================
def test_factor(all_klines_dict, trading_dates, factor_name, compute_fn=None):
    """
    测试单个因子的T+1预测能力
    返回: {ic, win_rate, samples, etc}
    """
    date_factors = {}      # date -> {code -> factor_value}
    date_returns = {}      # date -> {code -> t+1_return}
    
    total_samples = 0
    
    for di in range(len(trading_dates) - 1):
        today = trading_dates[di]
        tomorrow = trading_dates[di + 1]
        
        today_factors = {}
        today_returns = {}
        
        for code, info in all_klines_dict.items():
            name = info.get('name', '')
            if 'ST' in name or '*ST' in name:
                continue
            
            sd = get_stock_on_date(info, today)
            if not sd:
                continue
            if sd['close'] <= 0 or sd['close'] > 500:
                continue
            
            # 获取T+1收益
            sd_next = get_stock_on_date(info, tomorrow)
            if not sd_next:
                continue
            t1_return = (sd_next['close'] - sd['close']) / sd['close']
            
            # 计算因子
            hist_raw = get_stock_history(info, today)
            if len(hist_raw) < 25:
                continue
            # 加上今天的数据
            today_hist = hist_raw + [sd]
            
            factors = compute_all_factors(today_hist)
            if not factors or factor_name not in factors:
                continue
            
            fv = factors[factor_name]
            if not np.isfinite(fv) or abs(fv) > 1000:
                continue
            
            today_factors[code] = fv
            today_returns[code] = t1_return
            total_samples += 1
        
        if today_factors:
            date_factors[today] = today_factors
            date_returns[today] = today_returns
    
    # 汇总IC和胜率
    all_f = []
    all_r = []
    for d in date_factors:
        for c in date_factors[d]:
            if c in date_returns.get(d, {}):
                all_f.append(date_factors[d][c])
                all_r.append(date_returns[d][c])
    
    if len(all_f) < 50:
        return None
    
    all_f = np.array(all_f)
    all_r = np.array(all_r)
    
    # Rank IC (Spearman)
    rank_f = np.argsort(np.argsort(all_f))
    rank_r = np.argsort(np.argsort(all_r))
    n = len(all_f)
    rank_ic = np.corrcoef(rank_f, rank_r)[0, 1]
    
    # 胜率：因子高的前20% vs 因子低的后20%
    n_top = max(5, n // 5)
    top_idx = np.argsort(all_f)[-n_top:]
    bot_idx = np.argsort(all_f)[:n_top]
    
    top_win = np.mean(all_r[top_idx] > 0)
    bot_win = np.mean(all_r[bot_idx] > 0)
    top_mean = np.mean(all_r[top_idx])
    bot_mean = np.mean(all_r[bot_idx])
    
    # 整体胜率
    overall_win = np.mean(all_r > 0)
    
    # 最简单的方向：如果因子值高，T+1上涨的概率是否更大？
    # 把因子分成10等分，看每组胜率
    sorted_idx = np.argsort(all_f)
    decile_size = n // 10
    decile_wins = []
    for d in range(10):
        start = d * decile_size
        end = min((d+1) * decile_size, n)
        if end > start:
            decile_wins.append(np.mean(all_r[sorted_idx[start:end]] > 0))
    
    # 单调性检验：胜率是否随因子值递增/递减？
    monotonic = 0
    if len(decile_wins) >= 4:
        up = sum(1 for i in range(1, len(decile_wins)) if decile_wins[i] > decile_wins[i-1])
        down = sum(1 for i in range(1, len(decile_wins)) if decile_wins[i] < decile_wins[i-1])
        monotonic = max(up, down) / (len(decile_wins) - 1)
    
    # 最佳方向：因子大还是小好？
    if top_win > bot_win:
        direction = '正向'  # 因子越大越好
        win_spread = top_win - bot_win
        best_decile_win = max(decile_wins) if decile_wins else 0
        best_is_top = decile_wins.index(max(decile_wins)) >= 5 if decile_wins else True
    else:
        direction = '反向'  # 因子越小越好
        win_spread = bot_win - top_mean
        best_decile_win = max(decile_wins) if decile_wins else 0
    
    return {
        'factor': factor_name,
        'samples': n,
        'days': len(date_factors),
        'rank_ic': round(rank_ic, 4),
        'overall_win': round(overall_win * 100, 1),
        'top_win': round(top_win * 100, 1),
        'bot_win': round(bot_win * 100, 1),
        'top_mean': round(top_mean * 100, 2),
        'bot_mean': round(bot_mean * 100, 2),
        'win_spread': round(abs(top_win - bot_win) * 100, 1),
        'direction': direction,
        'monotonic': round(monotonic, 2),
        'decile_wins': [round(w*100, 1) for w in decile_wins] if decile_wins else [],
    }

# ============================================================
# 4. 主流程
# ============================================================
def main():
    print("=" * 70)
    print("  JH 因子挖掘引擎 v1.0")
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  目标: 从3024只A股60天数据中发现T+1预测因子（最大化胜率）")
    print("=" * 70)
    
    print("\n加载数据...")
    all_klines = load_all_klines()
    print(f"  {len(all_klines)}只股票")
    
    trading_dates = get_trading_dates(all_klines)
    print(f"  {len(trading_dates)}个交易日: {trading_dates[0]} ~ {trading_dates[-1]}")
    
    # 所有要测试的因子
    test_factor_names = [
        # 动量
        'mom_5d', 'mom_20d', 'mom_reversal', 'w_momentum',
        'path_momentum', 'mom_accel',
        # 波动
        'volatility_20', 'vol_ratio', 'avg_amplitude',
        # 量价
        'vp_corr', 'vol_asymmetry', 'vol_momentum',
        'volume_change', 'obv_direction', 'vol_price_diverge',
        # 技术形态
        'price_position', 'ma_deviation', 'ma_slope',
        'gap', 'shadow_ratio',
        # 统计
        'max_daily_return', 'min_daily_return',
        'limit_up_count', 'info_ratio_20', 'efficiency_ratio',
        # 复合
        'vp_compound', 'trend_quality', 'abnormal_vp',
        # 现有系统相关的
        'rsi_14', 'boll_pos', 'ma_convergence', 'vwap_deviation', 'vol_accel',
    ]
    
    print(f"\n将测试 {len(test_factor_names)} 个因子...\n")
    
    results = []
    
    for fi, fname in enumerate(test_factor_names):
        t0 = time.time()
        r = test_factor(all_klines, trading_dates, fname)
        elapsed = time.time() - t0
        
        if r:
            results.append(r)
            print(f"  [{fi+1:>2}/{len(test_factor_names)}] {fname:<22s} IC:{r['rank_ic']:>+7.4f} "
                  f"胜差:{r['win_spread']:>5.1f}% 顶:{r['top_win']:>5.1f}% 底:{r['bot_win']:>5.1f}% "
                  f"方向:{r['direction']} 样本:{r['samples']:>5d} {elapsed:.0f}s")
        else:
            print(f"  [{fi+1:>2}/{len(test_factor_names)}] {fname:<22s} ❌ 数据不足")
    
    # 按胜差排序
    results.sort(key=lambda x: x['win_spread'], reverse=True)
    
    print(f"\n{'='*70}")
    print(f"  🏆 因子有效性排名（按胜率差降序）")
    print(f"{'='*70}")
    print(f"  {'排名':>4} {'因子名':<22s} {'IC':>8} {'胜差%':>6} {'顶胜率':>7} {'底胜率':>7} {'方向':>4} {'样本':>6} {'单调性':>6}")
    print(f"  {'-'*4} {'-'*22} {'-'*8} {'-'*6} {'-'*7} {'-'*7} {'-'*4} {'-'*6} {'-'*6}")
    
    for i, r in enumerate(results[:20]):
        print(f"  {i+1:>4} {r['factor']:<22s} {r['rank_ic']:>+8.4f} {r['win_spread']:>6.1f} "
              f"{r['top_win']:>7.1f} {r['bot_win']:>7.1f} {r['direction']:>4} {r['samples']:>6d} {r['monotonic']:>6.2f}")
    
    # 最佳因子
    best = results[:5] if len(results) >= 5 else results
    print(f"\n{'='*70}")
    print(f"  📊 最佳因子详细数据")
    print(f"{'='*70}")
    
    # 保存结果
    out_path = os.path.join(RESULT_DIR, f'factor_ranking_{datetime.now().strftime("%Y%m%d_%H%M")}.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_path}")
    
    # 构建新评分系统要用的因子列表
    top_factors = []
    for r in results[:10]:
        direction = 1 if r['direction'] == '正向' else -1
        top_factors.append({
            'name': r['factor'],
            'win_spread': r['win_spread'],
            'rank_ic': r['rank_ic'],
            'direction': direction,
        })
    
    print(f"\n建议纳入评分系统的最优10因子:")
    for tf in top_factors:
        print(f"  {tf['name']:<22s} win_spread={tf['win_spread']:>5.1f}% IC={tf['rank_ic']:>+7.4f} dir={'正' if tf['direction']>0 else '反'}")

if __name__ == '__main__':
    main()
