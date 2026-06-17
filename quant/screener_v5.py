#!/usr/bin/env python3
"""
JH选股引擎 v5.0 — 优化权重 + 情绪过滤
14:30盘中运行，输出jh_summary.json
"""
import os, sys, time, json, warnings
import numpy as np
warnings.filterwarnings('ignore')

SCRIPT_DIR = "/var/www/html/h5/quant"
sys.path.insert(0, SCRIPT_DIR)

from datetime import datetime
import urllib.request

# 优化权重
OPT_W = [2.28, 0.48, 1.75, 0.50, 0.26, 1.53, 1.70, 0.52]
BASE_SCORE = 15
PANIC_DROP = -1.5; CAUTION_DROP = -0.5; BULL_RISE = 2.0
CAUTION_SCORE = 25; BULL_SCORE = 12

DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def fetch_market():
    """获取大盘涨跌幅"""
    try:
        url = "https://qt.gtimg.cn/q=sh000001"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=8).read().decode('gbk')
        vals = data.split('"')[1].split('~')
        return {'name': vals[1], 'price': float(vals[3]), 'change': float(vals[32])}
    except:
        return {'name': '上证指数', 'price': 0, 'change': 0}

def get_threshold(mkt_change):
    if mkt_change <= PANIC_DROP: return None
    elif mkt_change <= CAUTION_DROP: return CAUTION_SCORE
    elif mkt_change >= BULL_RISE: return BULL_SCORE
    return BASE_SCORE

def fetch_klines(code, days=60):
    """腾讯日K线"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,{days},qfq"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        text = urllib.request.urlopen(req, timeout=8).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}') + 1]
        data = json.loads(json_str)
        raw = data.get('data', {}).get(f'{prefix}{code}', {})
        kdata = raw.get('qfqday', []) or raw.get('day', [])
        return [{'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                 'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])} for k in kdata]
    except:
        return []

def fetch_1430_price(code):
    """从腾讯分时API获取14:30价格"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/minute/query?_var=min_data&code={prefix}{code}'
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        text = urllib.request.urlopen(req, timeout=8).read().decode('utf-8')
        ji = text.index('{'); je = text.rindex('}')
        data = json.loads(text[ji:je+1])
        mins = data.get('data',{}).get(f'{prefix}{code}',{}).get('data',{}).get('data',[])
        for m in mins:
            parts = str(m).split()
            if len(parts) >= 2 and parts[0] == '1430':
                return float(parts[1])
        for m in mins:
            parts = str(m).split()
            if len(parts) >= 2 and parts[0] == '1431':
                return float(parts[1])
        return None
    except:
        return None

def fetch_realtime(code):
    """腾讯实时行情"""
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

def calc_indicators(klines):
    """计算技术指标"""
    if len(klines) < 30: return None
    C = np.array([k['close'] for k in klines])
    H = np.array([k['high'] for k in klines])
    L = np.array([k['low'] for k in klines])
    V = np.array([k['volume'] for k in klines])
    O = np.array([k['open'] for k in klines])
    n = len(C); i = n - 1

    def ma(arr, period):
        return np.mean(arr[-period:]) if len(arr) >= period else np.nan

    ma5, ma10, ma20 = ma(C, 5), ma(C, 10), ma(C, 20)
    ma60 = ma(C, 60) if n >= 60 else np.nan

    def ema(arr, period):
        result = np.zeros(len(arr)); k = 2 / (period + 1)
        result[0] = arr[0]
        for j in range(1, len(arr)):
            result[j] = arr[j] * k + result[j-1] * (1-k)
        return result

    ema12, ema26 = ema(C, 12), ema(C, 26)
    dif = ema12 - ema26
    dea = np.zeros(n); dea[0] = dif[0]
    for j in range(1, n): dea[j] = dif[j] * 0.2 + dea[j-1] * 0.8

    def rsi(arr, period):
        if len(arr) < period + 1: return np.nan
        gains, losses = 0, 0
        for j in range(len(arr) - period, len(arr)):
            chg = arr[j] - arr[j-1]
            if chg > 0: gains += chg
            else: losses -= chg
        ag, al = gains / period, losses / period
        return 100 - (100 / (1 + ag / al)) if al else 100

    rsi14 = rsi(C, 14)
    vol_ma5 = ma(V, 5)
    vol_ratio = V[i] / vol_ma5 if vol_ma5 > 0 else 1
    mom_5d = (C[i] / C[i-5] - 1) * 100 if i >= 5 else 0
    mom_10d = (C[i] / C[i-10] - 1) * 100 if i >= 10 else 0

    up_days = sum(1 for j in range(max(0, i-19), i+1) if C[j] >= O[j])
    trend_score = up_days / min(20, i+1) * 100

    h20 = np.max(H[i-19:i+1]) if n >= 20 else H[i]
    breakout = (C[i] - np.min(L[i-19:i+1])) / (h20 - np.min(L[i-19:i+1])) * 100 if h20 != np.min(L[i-19:i+1]) else 50

    prev_dif = dif[i-1] if i > 0 else np.nan
    prev_dea = dea[i-1] if i > 0 else np.nan

    return {
        'close': C[i], 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'dif': dif[i], 'dea': dea[i], 'prev_dif': prev_dif, 'prev_dea': prev_dea,
        'rsi14': rsi14, 'vol_ratio': vol_ratio,
        'trend_score': trend_score, 'breakout': breakout,
        'mom_5d': mom_5d, 'mom_10d': mom_10d,
    }

def score_weighted(ind, rt_change):
    """优化权重评分"""
    w = OPT_W
    close = ind['close']; ma5 = ind['ma5']
    dif = ind['dif']; dea = ind['dea']
    prev_dif = ind['prev_dif']; prev_dea = ind['prev_dea']
    rsi14 = ind['rsi14']; vr = ind['vol_ratio']
    ts = ind['trend_score']; bo = ind['breakout']
    mom5 = ind['mom_5d']; mom10 = ind['mom_10d']

    if np.isnan(ma5) or close <= ma5: return 0.0, {'硬过滤': '未站MA5'}
    if mom5 > 8: return 0.0, {'硬过滤': '5日涨{:.0f}%过热'.format(mom5)}

    score = 10.0; factors = {}

    # 动量反转
    if mom5 > 0 and mom10 < -1: s = 6*w[0]; factors['刚转头'] = round(s,1)
    elif mom5 > 0 and -1 <= mom10 <= 1.5: s = 4*w[0]; factors['恢复中'] = round(s,1)
    elif mom5 > 0 and mom10 > 1.5 and mom5 <= 4: s = 2*w[0]; factors['温和涨'] = round(s,1)
    else: s = 0
    score += s

    # 量价
    if 1.3 <= vr <= 2.5: s = 8*w[1]; factors['放量'] = round(s,1)
    elif 2.5 < vr <= 4.0: s = 5*w[1]; factors['放量'] = round(s,1)
    elif 1.0 <= vr < 1.3: s = 3*w[1]
    elif vr > 5: s = -6*w[1]; factors['天量'] = round(s,1)
    elif vr > 4: s = -3*w[1]
    else: s = 0
    score += s

    # MACD
    if not (np.isnan(dif) or np.isnan(dea) or np.isnan(prev_dif) or np.isnan(prev_dea)):
        if dif > dea and prev_dif <= prev_dea: s = (10+(3 if dif<0 else 0))*w[2]; factors['MACD金叉'] = round(s,1)
        elif dif > dea: s = 4*w[2]
        elif dif < dea and abs(dif-dea) < 0.03 and mom5 > 0.5: s = 6*w[2]; factors['即将金叉'] = round(s,1)
        else: s = 0
        score += s

    # RSI
    if not np.isnan(rsi14):
        if 45 <= rsi14 <= 55: s = 8*w[3]; factors['RSI最佳'] = round(s,1)
        elif 55 < rsi14 <= 62: s = 6*w[3]
        elif 38 <= rsi14 < 45: s = 5*w[3]
        elif 62 < rsi14 <= 70: s = 3*w[3]
        elif rsi14 > 75: s = -10*w[3]; factors['RSI超买'] = round(s,1)
        elif rsi14 > 70: s = -6*w[3]
        elif rsi14 < 30: s = -6*w[3]
        else: s = 0
        score += s

    # 突破位
    if 30 <= bo <= 50: s = 8*w[4]
    elif 50 < bo <= 65: s = 6*w[4]
    elif 20 <= bo < 30: s = 4*w[4]
    elif 65 < bo <= 80: s = 2*w[4]
    elif bo > 90: s = -8*w[4]
    elif bo > 80: s = -4*w[4]
    else: s = 0
    score += s

    # 涨幅
    if 0.2 <= rt_change <= 1.0: s = 8*w[5]; factors['小涨'] = round(s,1)
    elif 1.0 < rt_change <= 1.8: s = 5*w[5]
    elif -0.5 <= rt_change < 0.2: s = 4*w[5]
    elif rt_change > 3.0: s = -10*w[5]; factors['涨幅过大'] = round(s,1)
    elif rt_change > 1.8: s = -5*w[5]
    elif rt_change < -1.5: s = -4*w[5]
    else: s = 0
    score += s

    # 趋势
    if 48 <= ts <= 60: s = 6*w[7]; factors['趋势好'] = round(s,1)
    elif 60 < ts <= 70: s = 4*w[7]
    elif 40 <= ts < 48: s = 2*w[7]
    elif ts > 78: s = -5*w[7]
    elif ts < 35: s = -4*w[7]
    else: s = 0
    score += s

    final = round(min(max(score, 0), 100), 1)
    if final > 75: final = round(75 + (final - 75) / 2, 1)
    return final, factors


def run_screener():
    """主选股流程"""
    start_time = time.time()
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] JH选股引擎 v5.0 启动...")

    # 大盘情绪
    mkt = fetch_market()
    mkt_change = mkt['change']
    threshold = get_threshold(mkt_change)
    print(f"  大盘: {mkt['name']} {mkt_change:+.2f}%")
    print(f"  门槛: {'不买入(暴跌)' if threshold is None else threshold}")

    if threshold is None:
        print("  ⚠️ 大盘暴跌，不选股")
        return save_results([], mkt_change, threshold)

    # 热门股池（从同花顺或东方财富获取）
    # 这里用腾讯涨幅榜
    print("  获取涨幅榜...")
    candidates = scan_top_stocks()
    print(f"  候选: {len(candidates)}只")

    # 分析评分
    results = []
    for idx, stock in enumerate(candidates):
        code = stock['code']
        if code.startswith('688') or code.startswith('8'): continue
        if 'ST' in stock.get('name', ''): continue

        klines = fetch_klines(code, 60)
        if len(klines) < 30: continue

        rt = fetch_realtime(code)
        if not rt: continue
        if rt['amount'] < 50000: continue  # 成交额>5万

        ind = calc_indicators(klines)
        if not ind: continue

        score, factors = score_weighted(ind, rt['change'])
        if score >= threshold:
            # 获取14:30价格作为评分价
            price_1430 = fetch_1430_price(code)
            score_price = price_1430 if price_1430 else rt['price']

            results.append({
                'code': code,
                'name': rt['name'],
                'score': score,
                'price': rt['price'],           # 收盘/实时价
                'score_price': round(score_price, 2),  # 14:30评分价
                'change': round((rt['price'] - score_price) / score_price * 100, 2) if score_price else rt['change'],
                'factors': factors,
                'amount': rt['amount']
            })

        if (idx + 1) % 50 == 0:
            print(f"  [{idx+1}/{len(candidates)}] 已找到{len(results)}只...")

    # 排序取Top5
    results.sort(key=lambda x: -x['score'])
    top = results[:5]

    print(f"\n  最终推荐 {len(top)} 只:")
    for r in top:
        fs = ', '.join([f"{k}{v:+.0f}" for k,v in r['factors'].items() if isinstance(v,(int,float))])
        print(f"  {r['code']} {r['name']} 分:{r['score']} 价:{r['price']} 涨:{r['change']:+.1f}% [{fs}]")

    elapsed = time.time() - start_time
    print(f"\n  耗时: {elapsed:.0f}秒")

    return save_results(top, mkt_change, threshold)


def scan_top_stocks():
    """扫描A股涨幅榜（腾讯API）"""
    candidates = []
    # 涨幅榜
    for market in ['sh', 'sz']:
        try:
            url = f"https://qt.gtimg.cn/q={market}Rank1"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
            # 解析涨幅榜
            lines = data.split(';')
            for line in lines:
                if '~' not in line: continue
                parts = line.split('~')
                if len(parts) < 5: continue
                code = parts[2] if len(parts) > 2 else ''
                name = parts[1] if len(parts) > 1 else ''
                change = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                if code and 0.3 <= change <= 9.5:
                    candidates.append({'code': code, 'name': name, 'change': change})
        except:
            pass

    # 如果涨幅榜不够，补充全市场扫描
    if len(candidates) < 100:
        print("  补充全市场扫描...")
        codes = []
        # 主板代码范围
        for i in range(600000, 604000): codes.append(str(i))
        for i in range(1, 4000): codes.append(f"{i:06d}")

        batch_size = 50
        for bi in range(0, len(codes), batch_size):
            batch = codes[bi:bi+batch_size]
            query = ','.join([f"{'sh' if c.startswith('6') else 'sz'}{c}" for c in batch])
            try:
                url = f"https://qt.gtimg.cn/q={query}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
                for line in data.split(';'):
                    if '~' not in line: continue
                    m = __import__('re').search(r'v_(\w+)="(.*)"', line)
                    if not m: continue
                    vals = m.group(2).split('~')
                    if len(vals) < 38: continue
                    code = vals[2]; name = vals[1]
                    price = float(vals[3]) if vals[3] else 0
                    change = float(vals[32]) if vals[32] else 0
                    if price <= 0 or 'ST' in name: continue
                    if code.startswith('688') or code.startswith('8') or code.startswith('92'): continue
                    if 0.3 <= change <= 9.5:
                        candidates.append({'code': code, 'name': name, 'change': change})
            except:
                pass
            if len(candidates) >= 300: break

    return candidates


def save_results(top, mkt_change, threshold):
    """保存结果到jh_summary.json"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 读取已有backtest数据
    bt_file = os.path.join(DATA_DIR, 'backtest_1430_v3_real.json')
    bt_data = {}
    if os.path.exists(bt_file):
        with open(bt_file) as f:
            bt_data = json.load(f)

    summary = bt_data.get('summary', {})

    jh = {
        'version': 'JH Screener v5.0',
        'full_name': 'JH Screener v5.0 (优化权重+情绪过滤)',
        'strategy': '埋伏策略v5.0（8因子优化权重，情绪过滤）',
        'updated_at': now,
        'backtest': {
            'params': {
                'tp': 6.0, 'sl': 4.0, 'threshold': threshold or BASE_SCORE,
                'hold_days': 7, 'buy_time': '14:30',
                'optimized_weights': OPT_W,
                'sentiment': {'panic': PANIC_DROP, 'caution': CAUTION_DROP, 'bull': BULL_RISE}
            },
            'total_trades': summary.get('total_trades', 0),
            'tp': summary.get('tp', 0), 'sl': summary.get('sl', 0), 'hold': summary.get('hold', 0),
            'win_rate': summary.get('win_rate', 0),
            'total_return': summary.get('total_return', 0),
            'avg_return': summary.get('avg_return', 0),
            'tp_pct': summary.get('tp_pct', 0),
            'sl_pct': summary.get('sl_pct', 0),
            'hold_pct': summary.get('hold_pct', 0),
            'days': summary.get('days', 0),
            'init_capital': summary.get('init_capital', 15000),
            'final_value': summary.get('final_value', 0),
            'total_profit': summary.get('total_profit', 0),
            'max_drawdown': summary.get('max_drawdown', 0),
            'avg_deviation': summary.get('avg_deviation', 0)
        },
        'recommendations': top,
        'cumulative': bt_data.get('capital_history', []),
        'stats_detail': {
            'max_drawdown': summary.get('max_drawdown', 0),
            'avg_trade_per_day': round(summary.get('total_trades', 0) / max(summary.get('days', 1), 1), 1),
            'largest_win': 6.0,
            'largest_loss': -4.0
        }
    }

    out_file = os.path.join(DATA_DIR, 'jh_summary.json')
    with open(out_file, 'w') as f:
        json.dump(jh, f, ensure_ascii=False, indent=2, default=str)
    print(f"  已保存: {out_file}")

    # 也保存一份带日期的备份
    date_str = datetime.now().strftime('%Y%m%d_%H%M')
    backup = os.path.join(DATA_DIR, f'recommendations_{date_str}.json')
    with open(backup, 'w') as f:
        json.dump({'date': date_str, 'mkt_change': mkt_change, 'threshold': threshold,
                   'recommendations': top}, f, ensure_ascii=False, indent=2)

    return jh


if __name__ == '__main__':
    run_screener()
