#!/usr/bin/env python3
"""
JH 反T时机分析引擎 v2.0
专为工业富联(601138)设计的日内反T（先卖后买）分析工具

v2.0 优化：
- VWAP偏离>1.5%生成明确买卖信号（不再"中性"）
- 支撑/阻力位：结合MA5/10/20 + 近期高低点 + 整数关口
- 量价背离：真正的价格新高/新低 + 量能萎缩检测
- 大盘情绪过滤：上证指数跌>1%时信号更保守
- RSI超买/超卖辅助判断
"""
import json, os, sys, math
from datetime import datetime
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)
T_RESULT_FILE = os.path.join(DATA_DIR, 't_analysis.json')

STOCK_CODE = '601138'
STOCK_NAME = '工业富联'

# ============================================================
# 1. 数据采集
# ============================================================
def fetch_minute_data(code=STOCK_CODE):
    """腾讯分时数据（1分钟级别）"""
    import urllib.request
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?_var=min_data&code={prefix}{code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}') + 1]
        data = json.loads(json_str)
        minutes = data.get('data', {}).get(f'{prefix}{code}', {}).get('data', {}).get('data', [])
        result = []
        for m in minutes:
            parts = m.split()
            if len(parts) >= 3:
                mins = int(parts[0])
                h = 9 + (30 + mins) // 60
                mi = (30 + mins) % 60
                result.append({
                    'time': f"{h:02d}:{mi:02d}",
                    'minutes': mins,
                    'price': float(parts[1]),
                    'volume': int(parts[2]),
                })
        return result
    except Exception as e:
        print(f"[ERROR] fetch_minute_data: {e}", file=sys.stderr)
        return []


def fetch_realtime(code=STOCK_CODE):
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
            'open': float(vals[5]), 'high': float(vals[33]), 'low': float(vals[34]),
            'change': float(vals[32]), 'volume': float(vals[36]),
            'amount': float(vals[37]), 'turnover': float(vals[38]),
        }
    except:
        return None


def fetch_klines_5min(code=STOCK_CODE):
    """腾讯5分钟K线（用于更长周期分析）"""
    import urllib.request
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},m5,,48"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}') + 1]
        data = json.loads(json_str)
        m5data = data.get('data', {}).get(f'{prefix}{code}', {}).get('m5', [])
        return [{
            'time': k[0], 'open': float(k[1]), 'close': float(k[2]),
            'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])
        } for k in m5data]
    except:
        return []


def fetch_daily_klines(code=STOCK_CODE, days=20):
    """日K线用于计算支撑阻力"""
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


def fetch_index_realtime(index_code='000001'):
    """获取大盘指数实时数据（上证指数）"""
    import urllib.request
    url = f"https://qt.gtimg.cn/q=sh{index_code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
        vals = data.split('"')[1].split('~')
        return {
            'name': vals[1], 'price': float(vals[3]), 'prev_close': float(vals[4]),
            'change': float(vals[32]),
        }
    except:
        return None


# ============================================================
# 2. 反T分析引擎
# ============================================================
class ReverseTAnalyzer:
    """反T（先卖后买）时机分析器 v2.0"""

    def __init__(self, minutes, realtime, daily_klines, index_data=None):
        self.minutes = minutes
        self.rt = realtime
        self.daily = daily_klines
        self.index = index_data  # 大盘数据

    def analyze(self):
        """综合分析，返回反T信号"""
        if not self.minutes or len(self.minutes) < 10:
            return {'error': '分时数据不足', 'signals': []}

        prices = [m['price'] for m in self.minutes]
        volumes = [m['volume'] for m in self.minutes]
        times = [m['time'] for m in self.minutes]

        current = prices[-1]
        prev_close = self.rt['prev_close'] if self.rt else prices[0]

        # 1. 分时均线（VWAP）
        vwap = self._calc_vwap()

        # 2. 均线偏离度
        deviation = (current - vwap) / vwap * 100

        # 3. 支撑阻力位（增强版：MA + 高低点 + 整数关口）
        levels = self._calc_levels(current, prev_close)

        # 4. 量价背离检测（增强版）
        divergence = self._detect_divergence(prices, volumes)

        # 5. 波段高低点
        swing = self._detect_swing(prices, times)

        # 6. RSI计算
        rsi = self._calc_rsi()

        # 7. 大盘情绪
        market_sentiment = self._assess_market()

        # 8. 反T信号生成（增强版）
        signals = self._generate_signals(current, vwap, deviation, levels, divergence, swing, prices, volumes, rsi, market_sentiment)

        # 9. 当前状态评估（增强版）
        status = self._assess_status(current, vwap, deviation, levels, divergence, rsi, market_sentiment)

        return {
            'stock': STOCK_NAME,
            'code': STOCK_CODE,
            'time': datetime.now().strftime('%H:%M:%S'),
            'current': current,
            'prev_close': prev_close,
            'change': round((current / prev_close - 1) * 100, 2),
            'vwap': round(vwap, 2),
            'deviation': round(deviation, 2),
            'rsi': rsi,
            'levels': levels,
            'divergence': divergence,
            'swing': swing,
            'market_sentiment': market_sentiment,
            'status': status,
            'signals': signals,
            'minute_count': len(self.minutes),
        }

    def _calc_vwap(self):
        """计算分时VWAP（成交量加权平均价）"""
        total_vol = 0
        total_pv = 0
        for m in self.minutes:
            if m['volume'] > 0:
                total_pv += m['price'] * m['volume']
                total_vol += m['volume']
        return total_pv / total_vol if total_vol > 0 else self.minutes[-1]['price']

    def _calc_rsi(self, period=14):
        """计算RSI（基于分时数据的近似）"""
        prices = [m['price'] for m in self.minutes]
        if len(prices) < period + 1:
            return None
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        # 用最近 period*5 个数据点计算（分时1分钟太密，取5倍窗口）
        window = min(period * 5, len(deltas))
        recent = deltas[-window:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / window if gains else 0
        avg_loss = sum(losses) / window if losses else 0.001
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 1)

    def _calc_levels(self, current, prev_close):
        """计算支撑/阻力位（增强版：MA + 10日高低点 + 整数关口）"""
        levels = {'support': [], 'resistance': []}

        if self.daily and len(self.daily) >= 5:
            # MA5, MA10, MA20
            closes = [k['close'] for k in self.daily]
            for ma_period, label in [(5, 'MA5'), (10, 'MA10'), (20, 'MA20')]:
                if len(closes) >= ma_period:
                    ma_val = round(np.mean(closes[-ma_period:]), 2)
                    if ma_val < current:
                        levels['support'].append({'price': ma_val, 'type': label, 'strength': '强' if ma_period >= 10 else '中'})
                    else:
                        levels['resistance'].append({'price': ma_val, 'type': label, 'strength': '强' if ma_period >= 10 else '中'})

            # 近5日高低点
            recent5 = self.daily[-5:]
            recent_high = max(k['high'] for k in recent5)
            recent_low = min(k['low'] for k in recent5)
            levels['support'].append({'price': round(recent_low, 2), 'type': '近5日最低', 'strength': '强'})
            levels['resistance'].append({'price': round(recent_high, 2), 'type': '近5日最高', 'strength': '强'})

            # 近10日高低点（如有数据）
            if len(self.daily) >= 10:
                recent10 = self.daily[-10:]
                high10 = max(k['high'] for k in recent10)
                low10 = min(k['low'] for k in recent10)
                if round(low10, 2) != round(recent_low, 2):
                    levels['support'].append({'price': round(low10, 2), 'type': '近10日最低', 'strength': '强'})
                if round(high10, 2) != round(recent_high, 2):
                    levels['resistance'].append({'price': round(high10, 2), 'type': '近10日最高', 'strength': '强'})

            # 昨收价作为心理关口
            levels['support'].append({'price': round(prev_close, 2), 'type': '昨收价', 'strength': '中'})

        # 基于分时的关键价位
        prices = [m['price'] for m in self.minutes]
        today_high = max(prices)
        today_low = min(prices)
        mid = (today_high + today_low) / 2

        levels['support'].append({'price': round(today_low, 2), 'type': '今日最低', 'strength': '强'})
        if mid > today_low:
            levels['support'].append({'price': round(mid - (today_high - today_low) * 0.3, 2), 'type': '分时中位', 'strength': '中'})

        levels['resistance'].append({'price': round(today_high, 2), 'type': '今日最高', 'strength': '强'})
        levels['resistance'].append({'price': round(mid + (today_high - today_low) * 0.3, 2), 'type': '分时中位', 'strength': '中'})

        # 整数关口（5元和10元间隔都检查）
        for gap in [5, 10]:
            round_up = math.ceil(current / gap) * gap
            round_down = math.floor(current / gap) * gap
            if round_up != round(current, 0) and abs(round_up - current) / current < 0.03:
                levels['resistance'].append({'price': float(round_up), 'type': f'{gap}元关口', 'strength': '强'})
            if round_down != round(current, 0) and abs(current - round_down) / current < 0.03:
                levels['support'].append({'price': float(round_down), 'type': f'{gap}元关口', 'strength': '强'})

        # 去重并排序
        levels['support'] = self._dedup_levels(levels['support'])
        levels['resistance'] = self._dedup_levels(levels['resistance'])
        levels['support'].sort(key=lambda x: x['price'], reverse=True)
        levels['resistance'].sort(key=lambda x: x['price'])

        # 只保留当前价上方的支撑（最近的）和下方的阻力（最近的）
        levels['support'] = [s for s in levels['support'] if s['price'] < current][:5]
        levels['resistance'] = [r for r in levels['resistance'] if r['price'] > current][:5]

        return levels

    def _dedup_levels(self, levels):
        """去重：价格差<0.3%的合并"""
        result = []
        seen = []
        for lv in levels:
            dup = False
            for s in seen:
                if abs(lv['price'] - s) / max(s, 0.01) < 0.003:
                    dup = True
                    break
            if not dup:
                result.append(lv)
                seen.append(lv['price'])
        return result

    def _detect_divergence(self, prices, volumes):
        """量价背离检测（增强版：真正的价格新高/新低 + 量能萎缩）"""
        if len(prices) < 20:
            return {'detected': False, 'type': None, 'desc': ''}

        # 分3段比较
        n = len(prices)
        seg_size = n // 3
        seg1_p, seg1_v = prices[:seg_size], volumes[:seg_size]
        seg2_p, seg2_v = prices[seg_size:seg_size*2], volumes[seg_size:seg_size*2]
        seg3_p, seg3_v = prices[seg_size*2:], volumes[seg_size*2:]

        # 顶背离：第3段价格新高，但量能比第2段萎缩
        seg3_high = max(seg3_p)
        seg2_high = max(seg2_p)
        seg1_high = max(seg1_p)

        if seg3_high > seg2_high and seg3_high > seg1_high:
            vol_ratio = np.mean(seg3_v) / max(np.mean(seg2_v), 1)
            if vol_ratio < 0.75:
                return {
                    'detected': True,
                    'type': '顶背离',
                    'desc': f'价格创日内新高{seg3_high:.2f}，但成交量仅前段的{vol_ratio:.0%}，上涨乏力',
                    'strength': '强' if vol_ratio < 0.5 else '中',
                    'vol_ratio': round(vol_ratio, 2),
                }

        # 底背离：第3段价格新低，但量能比第2段萎缩（抛压减弱）
        seg3_low = min(seg3_p)
        seg2_low = min(seg2_p)
        seg1_low = min(seg1_p)

        if seg3_low < seg2_low and seg3_low < seg1_low:
            vol_ratio = np.mean(seg3_v) / max(np.mean(seg2_v), 1)
            if vol_ratio < 0.75:
                return {
                    'detected': True,
                    'type': '底背离',
                    'desc': f'价格创日内新低{seg3_low:.2f}，但成交量仅前段的{vol_ratio:.0%}，抛压减弱',
                    'strength': '强' if vol_ratio < 0.5 else '中',
                    'vol_ratio': round(vol_ratio, 2),
                }

        # 额外检测：放量滞涨
        if len(seg3_p) >= 5:
            recent_price_change = abs(seg3_p[-1] - seg3_p[-5]) / seg3_p[-5] * 100
            vol_surge = np.mean(seg3_v[-5:]) / max(np.mean(seg2_v[-5:]), 1)
            if recent_price_change < 0.3 and vol_surge > 1.5:
                return {
                    'detected': True,
                    'type': '放量滞涨',
                    'desc': f'成交量放大{vol_surge:.1f}倍但价格几乎不动，可能变盘',
                    'strength': '中',
                    'vol_ratio': round(vol_surge, 2),
                }

        return {'detected': False, 'type': None, 'desc': '暂无明显量价背离'}

    def _detect_swing(self, prices, times):
        """检测波段高低点"""
        if len(prices) < 20:
            return {'high': None, 'low': None, 'range': 0}

        # 找局部高点和低点（用5分钟窗口）
        window = 5
        highs = []
        lows = []

        for i in range(window, len(prices) - window):
            if prices[i] == max(prices[i-window:i+window+1]):
                highs.append({'price': prices[i], 'time': times[i], 'index': i})
            if prices[i] == min(prices[i-window:i+window+1]):
                lows.append({'price': prices[i], 'time': times[i], 'index': i})

        # 最近的高点和低点
        last_high = highs[-1] if highs else None
        last_low = lows[-1] if lows else None

        # 当前波段幅度
        if last_high and last_low:
            range_pct = (last_high['price'] - last_low['price']) / last_low['price'] * 100
        else:
            range_pct = (max(prices) - min(prices)) / min(prices) * 100

        return {
            'high': last_high,
            'low': last_low,
            'range': round(range_pct, 2),
            'all_highs': highs[-3:] if highs else [],
            'all_lows': lows[-3:] if lows else [],
        }

    def _assess_market(self):
        """评估大盘情绪"""
        idx = self.index
        if not idx:
            return {'change': None, 'sentiment': '未知', 'impact': '无数据'}

        change = idx.get('change', 0)
        if change <= -2:
            return {'change': change, 'sentiment': '恐慌', 'impact': '大盘暴跌，接回信号需极度谨慎，建议再等一天', 'filter': 'very_conservative'}
        elif change <= -1:
            return {'change': change, 'sentiment': '偏弱', 'impact': '大盘下跌，接回信号需更保守，加严触发条件', 'filter': 'conservative'}
        elif change <= 0:
            return {'change': change, 'sentiment': '中性偏弱', 'impact': '大盘微跌，正常判断', 'filter': 'normal'}
        elif change <= 1:
            return {'change': change, 'sentiment': '中性偏强', 'impact': '大盘微涨，正常判断', 'filter': 'normal'}
        elif change <= 2:
            return {'change': change, 'sentiment': '偏强', 'impact': '大盘上涨，可适当积极', 'filter': 'aggressive'}
        else:
            return {'change': change, 'sentiment': '强势', 'impact': '大盘大涨，追高风险大，卖信号更可信', 'filter': 'aggressive'}

    def _generate_signals(self, current, vwap, deviation, levels, divergence, swing, prices, volumes, rsi, market_sentiment):
        """生成反T信号（增强版）"""
        signals = []
        now = datetime.now()
        hour, minute = now.hour, now.minute

        # 判断是否在交易时间
        is_trading = (9 <= hour < 11) or (hour == 11 and minute <= 30) or (13 <= hour < 15)
        if not is_trading:
            signals.append({
                'type': 'info',
                'action': '等待开盘',
                'desc': '当前非交易时间，以下为上一交易日分析参考',
                'target_price': None,
                'expected_profit': None,
                'urgency': 'low',
            })

        m_filter = market_sentiment.get('filter', 'normal')

        # ---- 卖出信号 ----

        # 信号1: 偏离VWAP过高 → 卖出信号（降低阈值到1.0%）
        if deviation > 1.0:
            target = round(vwap * 1.005, 2)
            profit = round(deviation - 0.3, 1)
            urgency = 'high' if deviation > 2.5 else ('medium' if deviation > 1.5 else 'low')
            signals.append({
                'type': 'sell',
                'action': f'高位偏离VWAP {deviation:.1f}%',
                'desc': f'当前价偏离分时均线{deviation:.1f}%，VWAP={vwap:.2f}，回归概率大',
                'target_price': target,
                'expected_profit': f'{profit}%',
                'urgency': urgency,
                'reason': '均线偏离回归',
                'detail': f'当前{current:.2f} vs VWAP{vwap:.2f}，偏离{deviation:.1f}%',
            })

        # 信号2: RSI超买 → 卖出
        if rsi and rsi > 70:
            signals.append({
                'type': 'sell',
                'action': f'RSI超买({rsi})',
                'desc': f'RSI={rsi}进入超买区，短线回调概率大',
                'target_price': round(vwap, 2),
                'expected_profit': '1-2%',
                'urgency': 'high' if rsi > 80 else 'medium',
                'reason': 'RSI超买',
            })

        # 信号3: 量价顶背离 → 卖出信号
        if divergence.get('detected') and divergence.get('type') == '顶背离':
            signals.append({
                'type': 'sell',
                'action': '顶背离卖出',
                'desc': divergence['desc'],
                'target_price': round(vwap, 2),
                'expected_profit': f'{round(deviation, 1)}%',
                'urgency': 'high' if divergence.get('strength') == '强' else 'medium',
                'reason': '量价背离',
            })

        # 信号4: 放量滞涨 → 卖出信号
        if divergence.get('detected') and divergence.get('type') == '放量滞涨':
            signals.append({
                'type': 'sell',
                'action': '放量滞涨',
                'desc': divergence['desc'],
                'target_price': round(vwap, 2),
                'expected_profit': '1-2%',
                'urgency': 'medium',
                'reason': '放量滞涨',
            })

        # 信号5: 到达阻力位 → 卖出信号
        for res in levels.get('resistance', []):
            dist_pct = (res['price'] - current) / current * 100
            if 0 < dist_pct < 0.8:
                signals.append({
                    'type': 'sell',
                    'action': f'接近阻力{res["price"]}({res["type"]})',
                    'desc': f'{res["type"]}({res["strength"]})，距当前仅{dist_pct:.1f}%，冲高回落概率大',
                    'target_price': res['price'],
                    'expected_profit': '1-3%',
                    'urgency': 'high' if res['strength'] == '强' and dist_pct < 0.5 else 'medium',
                    'reason': '阻力位',
                })

        # ---- 买回信号 ----

        # 信号6: 偏离VWAP过低 → 买回信号
        if deviation < -1.0:
            target = round(vwap * 0.995, 2)
            profit = round(abs(deviation) - 0.3, 1)
            urgency = 'high' if deviation < -2.5 else ('medium' if deviation < -1.5 else 'low')
            # 大盘保守时降低urgency
            if m_filter == 'conservative' and urgency == 'medium':
                urgency = 'low'
            if m_filter == 'very_conservative':
                urgency = 'low'
            signals.append({
                'type': 'buy_back',
                'action': f'低位偏离VWAP {deviation:.1f}%',
                'desc': f'当前价偏离分时均线{deviation:.1f}%，VWAP={vwap:.2f}，超跌反弹概率大',
                'target_price': target,
                'expected_profit': f'{profit}%',
                'urgency': urgency,
                'reason': '均线偏离回归',
            })

        # 信号7: RSI超卖 → 买回
        if rsi and rsi < 30:
            urgency = 'high' if rsi < 20 else 'medium'
            if m_filter in ('conservative', 'very_conservative'):
                urgency = 'low' if urgency == 'medium' else 'medium'
            signals.append({
                'type': 'buy_back',
                'action': f'RSI超卖({rsi})',
                'desc': f'RSI={rsi}进入超卖区，短线反弹概率大',
                'target_price': round(vwap * 0.998, 2),
                'expected_profit': '1-3%',
                'urgency': urgency,
                'reason': 'RSI超卖',
            })

        # 信号8: 到达支撑位 → 买回信号
        for sup in levels.get('support', []):
            dist_pct = (current - sup['price']) / current * 100
            if 0 < dist_pct < 0.8:
                urgency = 'high' if sup['strength'] == '强' and dist_pct < 0.5 else 'medium'
                if m_filter in ('conservative', 'very_conservative'):
                    urgency = 'low' if urgency == 'medium' else 'medium'
                signals.append({
                    'type': 'buy_back',
                    'action': f'接近支撑{sup["price"]}({sup["type"]})',
                    'desc': f'{sup["type"]}({sup["strength"]})，距当前仅{dist_pct:.1f}%，反弹概率大',
                    'target_price': sup['price'],
                    'expected_profit': '1-3%',
                    'urgency': urgency,
                    'reason': '支撑位',
                })

        # 信号9: 底背离 → 买回信号
        if divergence.get('detected') and divergence.get('type') == '底背离':
            urgency = 'high' if divergence.get('strength') == '强' else 'medium'
            if m_filter in ('conservative', 'very_conservative'):
                urgency = 'low' if urgency == 'medium' else 'medium'
            signals.append({
                'type': 'buy_back',
                'action': '底背离买回',
                'desc': divergence['desc'],
                'target_price': round(vwap, 2),
                'expected_profit': '2-3%',
                'urgency': urgency,
                'reason': '量价背离',
            })

        # 信号10: 波段空间充足
        if swing['range'] >= 3:
            signals.append({
                'type': 'info',
                'action': '波段空间充足',
                'desc': f'今日振幅{swing["range"]:.1f}%，有做T空间',
                'target_price': None,
                'expected_profit': None,
                'urgency': 'low',
            })

        # 大盘情绪提示
        if m_filter in ('conservative', 'very_conservative'):
            signals.append({
                'type': 'info',
                'action': f'大盘偏弱({market_sentiment["change"]}%)',
                'desc': market_sentiment['impact'],
                'target_price': None,
                'expected_profit': None,
                'urgency': 'low',
            })

        if not signals:
            # 给出"距离最近触发条件"的提示
            nearest = self._nearest_trigger(current, vwap, deviation, levels, rsi)
            signals.append({
                'type': 'wait',
                'action': '观望等待',
                'desc': f'当前无明显T点。{nearest}',
                'target_price': None,
                'expected_profit': None,
                'urgency': 'low',
            })

        return signals

    def _nearest_trigger(self, current, vwap, deviation, levels, rsi):
        """计算距离最近触发条件的提示"""
        hints = []

        # 距离VWAP偏离触发
        if deviation >= 0:
            sell_gap = 1.0 - deviation
            if sell_gap > 0:
                sell_target = round(vwap * (1 + sell_gap / 100), 2)
                hints.append(f'再涨{sell_gap:.1f}%至{sell_target}触发卖出信号')
        else:
            buy_gap = 1.0 + deviation
            if buy_gap > 0:
                buy_target = round(vwap * (1 - buy_gap / 100), 2)
                hints.append(f'再跌{buy_gap:.1f}%至{buy_target}触发买回信号')

        # 距离最近支撑/阻力
        supports = levels.get('support', [])
        resistances = levels.get('resistance', [])
        if supports:
            near_sup = supports[0]
            gap = (current - near_sup['price']) / current * 100
            hints.append(f'最近支撑{near_sup["price"]}({near_sup["type"]})，距{gap:.1f}%')
        if resistances:
            near_res = resistances[0]
            gap = (near_res['price'] - current) / current * 100
            hints.append(f'最近阻力{near_res["price"]}({near_res["type"]})，距{gap:.1f}%')

        # RSI提示
        if rsi:
            if rsi > 55:
                hints.append(f'RSI={rsi}偏高，>70触发卖出')
            elif rsi < 45:
                hints.append(f'RSI={rsi}偏低，<30触发买回')

        return '；'.join(hints[:3]) if hints else ''

    def _assess_status(self, current, vwap, deviation, levels, divergence, rsi, market_sentiment):
        """评估当前状态（增强版：综合VWAP偏离 + RSI + 大盘）"""
        score = 0  # 正=偏高，负=偏低

        # VWAP偏离打分
        if deviation > 2:
            score += 3
        elif deviation > 1:
            score += 2
        elif deviation > 0.5:
            score += 1
        elif deviation > -0.5:
            score += 0
        elif deviation > -1:
            score -= 1
        elif deviation > -2:
            score -= 2
        else:
            score -= 3

        # RSI打分
        if rsi:
            if rsi > 70:
                score += 2
            elif rsi > 60:
                score += 1
            elif rsi < 30:
                score -= 2
            elif rsi < 40:
                score -= 1

        # 背离打分
        if divergence.get('detected'):
            if divergence['type'] == '顶背离':
                score += 2
            elif divergence['type'] == '底背离':
                score -= 2

        # 状态判定
        if score >= 3:
            return {'level': 'high', 'text': '高位偏高', 'advice': '适合卖出，不适合追高', 'color': 'red', 'score': score}
        elif score >= 1:
            return {'level': 'elevated', 'text': '偏高', 'advice': '可以考虑分批卖出', 'color': 'orange', 'score': score}
        elif score <= -3:
            return {'level': 'bottom', 'text': '低位偏低', 'advice': '适合买回，不建议卖出', 'color': 'green', 'score': score}
        elif score <= -1:
            return {'level': 'low', 'text': '偏低', 'advice': '可以考虑分批买回', 'color': 'green', 'score': score}
        else:
            return {'level': 'neutral', 'text': '中性', 'advice': '暂无明显T点，等待信号触发', 'color': 'gray', 'score': score}


# ============================================================
# 3. 主入口
# ============================================================
def run_t_analysis():
    """运行反T分析"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 反T分析 - {STOCK_NAME}({STOCK_CODE})")

    # 采集数据
    minutes = fetch_minute_data()
    realtime = fetch_realtime()
    daily = fetch_daily_klines()
    index_data = fetch_index_realtime()  # 新增：大盘数据

    if not minutes:
        return {'error': '分时数据获取失败', 'signals': []}

    print(f"  分时数据: {len(minutes)}条")
    print(f"  当前价: {realtime['price'] if realtime else 'N/A'}")
    if index_data:
        print(f"  大盘: {index_data['name']} {index_data['price']} ({index_data['change']}%)")

    # 分析（传入大盘数据）
    analyzer = ReverseTAnalyzer(minutes, realtime, daily, index_data)
    result = analyzer.analyze()

    # 保存
    with open(T_RESULT_FILE, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # 打印结果
    print(f"\n{'='*50}")
    print(f"  {STOCK_NAME} 反T分析结果 v2.0")
    print(f"{'='*50}")
    print(f"  当前价: {result['current']}")
    print(f"  VWAP: {result['vwap']}")
    print(f"  偏离度: {result['deviation']}%")
    print(f"  RSI: {result.get('rsi', 'N/A')}")
    print(f"  大盘: {result.get('market_sentiment', {}).get('sentiment', 'N/A')} ({result.get('market_sentiment', {}).get('change', 'N/A')}%)")
    print(f"  状态: {result['status']['text']} (评分{result['status'].get('score', 0)}) - {result['status']['advice']}")
    print(f"\n  反T信号 ({len(result['signals'])}条):")
    for s in result['signals']:
        emoji = '🔴' if s['type'] == 'sell' else '🟢' if s['type'] == 'buy_back' else '⚪'
        tp = f"目标{s['target_price']}" if s.get('target_price') else ''
        ep = f"预期{s['expected_profit']}" if s.get('expected_profit') else ''
        urgency_tag = f"[{s['urgency']}]" if s.get('urgency') else ''
        print(f"  {emoji} {urgency_tag} {s['action']} | {s['desc']} {tp} {ep}")

    return result


if __name__ == '__main__':
    result = run_t_analysis()
