#!/usr/bin/env python3
"""
JH 真实5分钟条件单回测 v2.0 — Baostock数据源
====================================================
三大改进：
  ① 尾盘买入（D日评分→D+1日14:55买入）
  ② 开盘跳空分析（隔夜风险量化）
  ③ 更长回测期（253个交易日，2025-05~2026-06）
"""

import json, os, sys, time, math, re
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

# ========== Baostock兼容 ==========
def _append_compat(self, other, ignore_index=False, **kwargs):
    return pd.concat([self, pd.DataFrame(other) if not isinstance(other, pd.DataFrame) else other], ignore_index=ignore_index)
if not hasattr(pd.DataFrame, 'append'): pd.DataFrame.append = _append_compat
import baostock as bs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze
from cross_sectional_score import score_early_entry
from backtest_full import build_hist_klines

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# ──────────────────────────────────────────────
# 1. 工具函数
# ──────────────────────────────────────────────

def code_to_bs(code):
    """转Baostock格式: 600519 → sh.600519, 000001 → sz.000001"""
    code = str(code).strip().zfill(6)
    if code.startswith('6'): return f'sh.{code}'
    elif code.startswith(('0','3')): return f'sz.{code}'
    elif code.startswith(('4','8')): return f'bj.{code}'
    return f'sh.{code}'  # 保底

def bs_time_to_str(t):
    """20260601093500000 → '2026-06-01 09:35'"""
    d = t[:8]
    h = t[8:12]
    return f'{d[:4]}-{d[4:6]}-{d[6:8]} {h[:2]}:{h[2:]}'

def is_535(time_str):
    """判断是否是09:35的第一根K线（开盘第一根）"""
    return time_str[8:12] == '0935'

def is_1455(time_str):
    """判断是否是14:55的尾盘K线"""
    return time_str[8:12] == '1455'

# ──────────────────────────────────────────────
# 2. 5分钟K线获取（Baostock）
# ──────────────────────────────────────────────

_cache_5min = {}

def get_5min_bars(code, start_date, end_date):
    """从Baostock获取5分钟K线，带缓存"""
    cache_key = f'{code}|{start_date}|{end_date}'
    if cache_key in _cache_5min:
        return _cache_5min[cache_key]

    bs_code = code_to_bs(code)
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,time,open,high,low,close,volume,amount",
            start_date=start_date, end_date=end_date,
            frequency='5', adjustflag='2'
        )
        if rs.error_code != '0':
            return None
        dl = []
        while rs.next():
            dl.append(rs.get_row_data())
        if not dl:
            return None
        df = pd.DataFrame(dl, columns=rs.fields)
        # 转数值
        for col in ['open','high','low','close','volume','amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        _cache_5min[cache_key] = df
        return df
    except Exception as e:
        return None

# ──────────────────────────────────────────────
# 3. 买点和条件单模拟
# ──────────────────────────────────────────────

def get_entry_at_1455(bars_5min, entry_date):
    """获取指定日期14:55的收盘价作为买入价"""
    day_bars = bars_5min[bars_5min['date'] == entry_date]
    if len(day_bars) == 0:
        return None
    # 找14:55的bar
    pm_bars = day_bars[day_bars['time'].str[8:12] == '1455']
    if len(pm_bars) > 0:
        return float(pm_bars.iloc[0]['close'])
    # 没找到14:55，用最后一根bar
    return float(day_bars.iloc[-1]['close'])

def get_gap_info(bars_5min, buy_date, entry_price):
    """计算隔夜跳空: 买入日后第一根K线的开盘价"""
    dates = sorted(bars_5min['date'].unique())
    buy_idx = -1
    for i, d in enumerate(dates):
        if d >= buy_date:
            buy_idx = i
            break
    if buy_idx < 0 or buy_idx >= len(dates) - 1:
        return None, None
    next_date = dates[buy_idx + 1]
    next_bars = bars_5min[bars_5min['date'] == next_date]
    if len(next_bars) == 0:
        return None, None
    # 取第一根（09:35）的开盘价
    first_bar = next_bars.iloc[0]
    open_next = float(first_bar['open'])
    gap_pct = round((open_next / entry_price - 1) * 100, 2)
    return gap_pct, next_date

def simulate_tp_sl(bars_5min, buy_date, entry_price, tp_pct=5.0, sl_pct=4.0, max_hold_days=3):
    """
    条件单模拟 — 买入后监控每根5分钟K线
    
    返回: dict 或 None
    """
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    
    # 找到买入日期之后的所有bar
    all_bars = bars_5min.sort_values(['date', 'time'])
    dates = sorted(all_bars['date'].unique())
    
    buy_idx = -1
    for i, d in enumerate(dates):
        if d >= buy_date:
            buy_idx = i
            break
    if buy_idx < 0:
        return None
    
    end_idx = min(buy_idx + max_hold_days + 1, len(dates))  # +1 包含买入日尾盘监控
    monitor_dates = dates[buy_idx:end_idx]
    
    if len(monitor_dates) == 0:
        return None
    
    # 逐K线监控（从买入日14:55之后开始）
    monitor_bars = all_bars[all_bars['date'].isin(monitor_dates)]
    
    entry_passed = False
    for _, bar in monitor_bars.iterrows():
        bar_date = bar['date']
        bar_time = bar['time']
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low = float(bar['low'])
        bar_close = float(bar['close'])
        
        # 买入日：只监控14:55之后
        if bar_date == buy_date and not is_1455(bar_time):
            continue
        
        # 从买入日开始监控（但如果买入日买在14:55，盘中已经结束）
        # 实际上买入价是14:55收盘价，条件单次日开盘生效
        # 所以我们真正从买入日的下一根K线开始监控
        if bar_date == buy_date and is_1455(bar_time):
            # 这是买入K线，用买入价检查
            # 如果14:55这根K线的价格已经触发，先检查
            pass
        elif bar_date == buy_date:
            continue
        
        entry_passed = True
        
        # 检查是否触发止盈（先检查最高价）
        if bar_high >= tp_price:
            return {
                'hit_type': 'TP',
                'entry_price': entry_price,
                'exit_price': tp_price,
                'return_pct': tp_pct,
                'exit_date': bar_date,
                'exit_time': bs_time_to_str(bar_time),
            }
        
        # 检查是否触发止损（最低价）
        if bar_low <= sl_price:
            return {
                'hit_type': 'SL',
                'entry_price': entry_price,
                'exit_price': sl_price,
                'return_pct': -sl_pct,
                'exit_date': bar_date,
                'exit_time': bs_time_to_str(bar_time),
            }
    
    # 没触发：用最后一日最后一根K线的收盘价平仓
    last_date = monitor_dates[-1]
    last_bars = all_bars[all_bars['date'] == last_date]
    if len(last_bars) > 0:
        exit_price = float(last_bars.iloc[-1]['close'])
        ret = round((exit_price / entry_price - 1) * 100, 2)
        return {
            'hit_type': 'HOLD',
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return_pct': ret,
            'exit_date': last_date,
            'exit_time': bs_time_to_str(last_bars.iloc[-1]['time']),
        }
    
    return None

# ──────────────────────────────────────────────
# 4. 日线评分（复用现有逻辑）
# ──────────────────────────────────────────────

def load_daily_data():
    """加载全A股日线数据"""
    fpath = os.path.join(DATA_DIR, 'all_klines_60d.json')
    with open(fpath) as f:
        return json.load(f)

def get_trading_dates(all_klines):
    """获取所有交易日（按日期排序）"""
    from collections import Counter
    dc = Counter()
    for code, info in all_klines.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                dc[k[0]] += 1
    # 只取有2000只以上的日（正常交易日）
    trade_dates = sorted([d for d, c in dc.items() if c >= 2000])
    return trade_dates[-253:]  # 取最近253个交易日

def get_snapshot(all_klines, date):
    """获取指定日期的全市场快照"""
    stocks = []
    for code, info in all_klines.items():
        name = info.get('name', '')
        klines = info.get('klines', [])
        for k in klines:
            if isinstance(k, list) and len(k) >= 6 and k[0] == date:
                open_p = float(k[1])
                close = float(k[2])
                high = float(k[3])
                low = float(k[4])
                volume = float(k[5])
                amount = close * volume / 10000  # 万元
                change = (close / open_p - 1) * 100 if open_p > 0 else 0
                stocks.append({
                    'code': code, 'name': name,
                    'price': close, 'change': change,
                    'amount': amount, 'volume': volume,
                    'open': open_p, 'high': high, 'low': low,
                })
                break
    return stocks

def check_market_state(all_klines, date):
    """检查当日大盘状态"""
    snapshot = get_snapshot(all_klines, date)
    if not snapshot:
        return 0
    changes = [s['change'] for s in snapshot if not np.isnan(s['change'])]
    if changes:
        return round(np.median(changes), 2)
    return 0

def score_stocks_on_date(all_klines, date, max_stocks=80, top_n=5):
    """
    对某交易日所有股票评分，返回TOP N
    跳过科创板(688)、创业板(300/301)、北交所(920/4xx/8xx)
    """
    snapshot = get_snapshot(all_klines, date)
    if not snapshot:
        return []
    
    # 成交额排序取TOP200
    snapshot.sort(key=lambda x: -x['amount'])
    top_volume = snapshot[:max_stocks]
    
    # 屏蔽历史选过的（空集合，不屏蔽）
    candidates = []
    for s in top_volume:
        code = s['code']
        # 排除科创板/创业板/北交所
        if code.startswith(('688', '300', '301', '4', '8')) or code.startswith('920'):
            continue
        
        # 构建历史K线（使用已有的build_hist_klines）
        hist = build_hist_klines(all_klines, code, date)
        if len(hist) < 30:
            continue
        
        # 计算指标
        ind = quick_analyze(hist)
        if not ind:
            continue
        
        # 评分
        score, factors = score_early_entry(ind, s['change'], 0)
        if score < 6:
            continue
        
        candidates.append({
            'code': code,
            'name': s['name'],
            'price': s['price'],
            'change': s['change'],
            'amount': s['amount'],
            'score': score,
            'factors': factors,
        })
    
    # 排名取TOP N
    candidates.sort(key=lambda x: -x['score'])
    return candidates[:top_n]

# ──────────────────────────────────────────────
# 5. 主回测流程
# ──────────────────────────────────────────────

def run_backtest():
    # 输出重定向到文件
    log_file = '/tmp/bt20_out.log'
    sys.stdout = open(log_file, 'w', buffering=1)
    sys.stderr = sys.stdout
    
    print("=" * 72)
    print("  JH 真实5分钟条件单回测 v2.0")
    print("  Baostock数据源 | 尾盘14:55买入 | 隔夜跳空分析")
    print("  TP+5% / SL-4% / T+3持有")
    print("=" * 72)
    
    # ===== 加载数据 =====
    print("\n📦 加载日线数据...")
    all_klines = load_daily_data()
    print(f"  ✓ {len(all_klines)}只股票")
    
    trading_dates = get_trading_dates(all_klines)
    print(f"  ✓ {len(trading_dates)}个交易日: {trading_dates[-1]} ~ {trading_dates[0]}")
    
    # ===== 登录Baostock =====
    print("\n🔌 连接Baostock...")
    lg = bs.login()
    if lg.error_code != '0':
        print(f"  ❌ Baostock登录失败: {lg.error_msg}")
        return
    print(f"  ✓ 登录成功")
    
    # ===== 回测参数 =====
    # 缺少前5天（无法评分）和后5天（无法获取分钟数据），所以跳过两端
    backtest_dates = trading_dates[5:-5][-120:]  # 最近120天
    print(f"\n📅 回测区间: {backtest_dates[-1]} ~ {backtest_dates[0]}")
    print(f"   共 {len(backtest_dates)} 个交易日")
    
    all_trades = []
    gap_records = []  # 隔夜跳空记录
    
    # ===== 逐日回测 =====
    t_start = time.time()
    for di, date in enumerate(backtest_dates):
        progress = f"[{di+1}/{len(backtest_dates)}]"
        elapsed = time.time() - t_start
        
        # 进度显示
        if di < 3 or di % 10 == 9 or di >= len(backtest_dates) - 2:
            print(f"\n📅 {progress} {date} ...")
        
        # 评分选股
        picks = score_stocks_on_date(all_klines, date, top_n=5)
        if not picks:
            if di == len(backtest_dates) - 1 or di % 20 == 0:
                print(f"  {progress} 无合格股票")
            continue
        
        # 下一交易日 = 买入日
        buy_date_idx = -1
        for i, d in enumerate(trading_dates):
            if d == date:
                buy_date_idx = i
                break
        if buy_date_idx < 0 or buy_date_idx >= len(trading_dates) - 1:
            continue
        buy_date = trading_dates[buy_date_idx + 1]
        
        # 最后监控日
        hold_end_idx = min(buy_date_idx + 1 + 3, len(trading_dates) - 1)
        hold_end_date = trading_dates[hold_end_idx]
        
        # 对每只选股做条件单模拟
        for pick in picks:
            code = pick['code']
            name = pick['name']
            score = pick['score']
            
            # 获取5分钟数据（买入日~持有结束）
            bars = get_5min_bars(code, buy_date, hold_end_date)
            if bars is None or len(bars) < 5:
                continue
            
            # 获取14:55买入价
            entry_price = get_entry_at_1455(bars, buy_date)
            if entry_price is None or entry_price <= 0:
                continue
            
            # 模拟条件单
            result = simulate_tp_sl(bars, buy_date, entry_price, tp_pct=5.0, sl_pct=4.0, max_hold_days=3)
            if result is None:
                continue
            
            # 隔夜跳空
            gap_pct, gap_date = get_gap_info(bars, buy_date, entry_price)
            
            trade = {
                'date': date,          # 评分日
                'buy_date': buy_date,   # 买入日
                'code': code,
                'name': name,
                'score': score,
                'entry_price': entry_price,
                'hit_type': result['hit_type'],
                'return_pct': result['return_pct'],
                'exit_date': result['exit_date'],
                'gap_pct': gap_pct if gap_pct is not None else 0,
            }
            all_trades.append(trade)
            
            if gap_pct is not None:
                gap_records.append(gap_pct)
            
            # 显示（每只都显示）
            if di < 5 or di % 20 == 0 or di >= len(backtest_dates) - 3:
                emoji = '🟢' if trade['hit_type'] == 'TP' else ('🔴' if trade['hit_type'] == 'SL' else '⚪')
                gap_str = f' 跳空{gap_pct:+.2f}%' if gap_pct is not None else ''
                print(f"  {emoji} {name:6s}({code}) 分{score:.0f} 买{entry_price:.2f}→{result['hit_type']} {result['return_pct']:+.2f}%{gap_str}")
        
        # 清理缓存
        if len(_cache_5min) > 50:
            _cache_5min.clear()
    
    bs.logout()
    
    # ===== 回测报告 =====
    total_elapsed = time.time() - t_start
    print(f"\n{'=' * 72}")
    print(f"  📊 5分钟条件单回测报告 (TP+5% / SL-4%)")
    print(f"  Baostock | 尾盘14:55买入 | {len(backtest_dates)}个交易日")
    print(f"{'=' * 72}")
    
    if not all_trades:
        print("\n  无交易记录")
        return
    
    total = len(all_trades)
    tp_hits = sum(1 for t in all_trades if t['hit_type'] == 'TP')
    sl_hits = sum(1 for t in all_trades if t['hit_type'] == 'SL')
    hold_flat = sum(1 for t in all_trades if t['hit_type'] == 'HOLD')
    
    total_return = sum(t['return_pct'] for t in all_trades)
    avg_return = total_return / total if total > 0 else 0
    wins = sum(1 for t in all_trades if t['return_pct'] > 0)
    win_rate = wins / total * 100 if total > 0 else 0
    
    print(f"\n  📈 交易统计")
    print(f"    总交易: {total}笔")
    print(f"    🟢 止盈(TP): {tp_hits}笔 = {tp_hits/total*100:.1f}%")
    print(f"    🔴 止损(SL): {sl_hits}笔 = {sl_hits/total*100:.1f}%")
    print(f"    ⚪ 持仓到期: {hold_flat}笔 = {hold_flat/total*100:.1f}%")
    print(f"    🏆 胜率: {wins}/{total} = {win_rate:.1f}%")
    print(f"    💰 总收益: {total_return:+.2f}%")
    print(f"       均每笔: {avg_return:+.2f}%")
    print(f"       耗时: {total_elapsed:.0f}s")
    
    # 隔夜跳空分析
    if gap_records:
        gaps_arr = np.array(gap_records)
        print(f"\n  📉 隔夜跳空分析")
        print(f"    平均跳空: {np.mean(gaps_arr):+.2f}%")
        print(f"    中位跳空: {np.median(gaps_arr):+.2f}%")
        print(f"    最大低开: {np.min(gaps_arr):.2f}%")
        print(f"    最大高开: {np.max(gaps_arr):+.2f}%")
        neg_gaps = sum(1 for g in gap_records if g < 0)
        print(f"    低开次数: {neg_gaps}/{len(gap_records)} = {neg_gaps/len(gap_records)*100:.1f}%")
        bad_gaps = sum(1 for g in gap_records if g < -2)
        print(f"    大幅低开(>-2%): {bad_gaps}/{len(gap_records)} = {bad_gaps/len(gap_records)*100:.1f}%")
        good_gaps = sum(1 for g in gap_records if g > 2)
        print(f"    大幅高开(>+2%): {good_gaps}/{len(gap_records)} = {good_gaps/len(gap_records)*100:.1f}%")
    
    # TP/SL详情
    if tp_hits > 0:
        avg_tp_day = np.mean([int(t['exit_date'][8:10]) for t in all_trades if t['hit_type'] == 'TP'])
        print(f"\n  🟢 止盈平均触发日: ...")
    if sl_hits > 0:
        sl_trades = [t for t in all_trades if t['hit_type'] == 'SL']
        sl_with_gap = [t for t in sl_trades if 'gap_pct' in t and t['gap_pct'] < 0]
        sl_gap_avg = np.mean([t['gap_pct'] for t in sl_with_gap]) if sl_with_gap else 0
        print(f"    止损票平均跳空: {sl_gap_avg:.2f}% (低开导致的止损)")
    
    # 散点分析：跳空 vs 收益
    if gap_records:
        print(f"\n  🔍 跳空vs收益相关性")
        gap_return_pairs = [(t['gap_pct'], t['return_pct']) for t in all_trades if 'gap_pct' in t]
        if gap_return_pairs:
            corr = np.corrcoef([p[0] for p in gap_return_pairs], [p[1] for p in gap_return_pairs])[0, 1]
            print(f"    相关系数: {corr:.3f}")
            # 条件：如果跳空< -1.5% 则跳过的效果
            bad_gap_trades = [p for p in gap_return_pairs if p[0] < -1.5]
            if bad_gap_trades:
                bad_avg = np.mean([p[1] for p in bad_gap_trades])
                print(f"    跳空<-1.5%的{len(bad_gap_trades)}笔均收益: {bad_avg:.2f}%")
    
    # 总体判定
    if total_return > 0:
        print(f"\n  ✅ 策略判定: 盈利 (+{total_return:.2f}%)")
    else:
        print(f"\n  ❌ 策略判定: 亏损 ({total_return:.2f}%)")
    
    # 保存结果
    result = {
        'total_trades': total,
        'tp_hits': tp_hits,
        'sl_hits': sl_hits,
        'hold_flat': hold_flat,
        'total_return_pct': round(total_return, 2),
        'avg_return_pct': round(avg_return, 2),
        'win_rate': round(win_rate, 1),
        'total_elapsed_s': round(total_elapsed, 1),
        'backtest_dates': len(backtest_dates),
        'gap_stats': {
            'mean': round(float(np.mean(gap_records)), 2) if gap_records else 0,
            'median': round(float(np.median(gap_records)), 2) if gap_records else 0,
            'min': round(float(np.min(gap_records)), 2) if gap_records else 0,
            'max': round(float(np.max(gap_records)), 2) if gap_records else 0,
            'neg_rate': round(neg_gaps/len(gap_records)*100, 1) if gap_records else 0,
        } if gap_records else {},
        'trades': all_trades,
    }
    
    result_file = os.path.join(DATA_DIR, 'backtest_v20_5min_bs.json')
    with open(result_file, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 详细结果已保存: {result_file}")
    print(f"\n  ⏰ 回测完成: {datetime.now().strftime('%H:%M:%S')}")
    
    sys.stdout.flush()
    return all_trades

if __name__ == '__main__':
    run_backtest()
