# -*- coding: utf-8 -*-
"""
10种选股因子 + 5种择时因子
每种因子含：数学推导、Python实现、A股适配

选股因子：
1. 动量因子 Momentum
2. 反转因子 Reversal
3. 波动率因子 Volatility
4. 换手率因子 Turnover
5. 振幅因子 Amplitude
6. 价格位置因子 PricePosition
7. 量价背离因子 VolumePriceDivergence
8. MACD因子
9. RSI因子
10. 布林带因子 BollingerPosition

择时因子：
1. 均线多头排列
2. 成交量突破
3. 市场宽度
4. 波动率指数
5. 资金流向
"""
import numpy as np
import pandas as pd
from factors.base import BaseFactor


# ============================================================
# 选股因子 (10种)
# ============================================================

class MomentumFactor(BaseFactor):
    """
    动量因子
    公式: MOM_n(t) = P(t) / P(t-n) - 1
    含义: 过去n天的收益率，捕捉趋势延续效应
    A股适配: A股短期动量(5-20天)存在正向动量效应
    """
    name = "momentum"
    description = "动量因子"
    math_formula = "MOM_n(t) = P(t)/P(t-n) - 1"

    def __init__(self, period=20):
        self.period = period

    def compute(self, df):
        df = df.copy()
        df[self.name] = df['close'] / df['close'].shift(self.period) - 1
        return df


class ReversalFactor(BaseFactor):
    """
    反转因子
    公式: REV_n(t) = -1 * (P(t) / P(t-n) - 1)
    含义: 短期涨幅过大的股票倾向回调
    A股适配: A股5日反转效应显著（追涨杀跌的反向）
    """
    name = "reversal"
    description = "反转因子"
    math_formula = "REV_n(t) = -MOM_n(t)"

    def __init__(self, period=5):
        self.period = period

    def compute(self, df):
        df = df.copy()
        df[self.name] = -(df['close'] / df['close'].shift(self.period) - 1)
        return df


class VolatilityFactor(BaseFactor):
    """
    波动率因子
    公式: VOL_n(t) = std(r_t, r_{t-1}, ..., r_{t-n+1})
    其中 r_t = (P_t - P_{t-1}) / P_{t-1}
    含义: 收益率标准差，衡量股票波动风险
    A股适配: 低波动异象——低波动股票长期跑赢高波动
    """
    name = "volatility"
    description = "波动率因子"
    math_formula = "VOL_n(t) = std(r_t, ..., r_{t-n+1})"

    def __init__(self, period=20):
        self.period = period

    def compute(self, df):
        df = df.copy()
        returns = df['close'].pct_change()
        df[self.name] = returns.rolling(self.period).std()
        return df


class TurnoverFactor(BaseFactor):
    """
    换手率因子
    公式: TO_n(t) = volume(t) / MA(volume, n)
    含义: 当日成交量相对于n日均量的倍数
    A股适配: 高换手率常伴随短期见顶信号
    """
    name = "turnover"
    description = "换手率因子"
    math_formula = "TO_n(t) = V(t) / MA(V, n)"

    def __init__(self, period=5):
        self.period = period

    def compute(self, df):
        df = df.copy()
        vol_ma = df['volume'].rolling(self.period).mean()
        df[self.name] = df['volume'] / vol_ma.replace(0, np.nan)
        return df


class AmplitudeFactor(BaseFactor):
    """
    振幅因子
    公式: AMP(t) = (High(t) - Low(t)) / Close(t-1)
    含义: 日内价格波动幅度
    A股适配: 高振幅常预示短期方向不确定性增加
    """
    name = "amplitude"
    description = "振幅因子"
    math_formula = "AMP(t) = (H(t) - L(t)) / C(t-1)"

    def compute(self, df):
        df = df.copy()
        preclose = df['close'].shift(1)
        df[self.name] = (df['high'] - df['low']) / preclose.replace(0, np.nan)
        return df


class PricePositionFactor(BaseFactor):
    """
    价格位置因子
    公式: PP_n(t) = (P(t) - MIN(P, n)) / (MAX(P, n) - MIN(P, n))
    含义: 当前价格在n日高低区间中的相对位置 [0, 1]
    A股适配: 接近0=超卖区，接近1=超买区
    """
    name = "price_position"
    description = "价格位置因子"
    math_formula = "PP(t) = (P-MIN_n)/(MAX_n-MIN_n)"

    def __init__(self, period=20):
        self.period = period

    def compute(self, df):
        df = df.copy()
        pmin = df['close'].rolling(self.period).min()
        pmax = df['close'].rolling(self.period).max()
        denom = (pmax - pmin).replace(0, np.nan)
        df[self.name] = (df['close'] - pmin) / denom
        return df


class VolumePriceDivergenceFactor(BaseFactor):
    """
    量价背离因子
    公式: VPD_n(t) = corr(Close, Volume, n)
    含义: 量价相关性，正常为正；负相关=背离（价涨量缩或价跌量增）
    A股适配: 顶部区域常出现量价背离
    """
    name = "vol_price_div"
    description = "量价背离因子"
    math_formula = "VPD(t) = corr(C, V, n)"

    def __init__(self, period=20):
        self.period = period

    def compute(self, df):
        df = df.copy()
        df[self.name] = df['close'].rolling(self.period).corr(df['volume'])
        return df


class MACDFactor(BaseFactor):
    """
    MACD因子
    公式:
        EMA12 = EMA(Close, 12)
        EMA26 = EMA(Close, 26)
        DIF = EMA12 - EMA26
        DEA = EMA(DIF, 9)
        MACD_HIST = 2 * (DIF - DEA)
    含义: 趋势跟踪动量指标
    A股适配: DIF上穿DEA为金叉买入信号
    """
    name = "macd_hist"
    description = "MACD柱状因子"
    math_formula = "HIST = 2*(DIF-DEA), DIF=EMA12-EMA26"

    def compute(self, df):
        df = df.copy()
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        df['macd_dif'] = dif
        df['macd_dea'] = dea
        df[self.name] = 2 * (dif - dea)
        return df


class RSIFactor(BaseFactor):
    """
    RSI因子
    公式:
        RS = MA(gain, n) / MA(loss, n)
        RSI = 100 - 100 / (1 + RS)
    含义: 相对强弱指数，0-100
    A股适配: RSI<30超卖，>70超买
    """
    name = "rsi"
    description = "RSI因子"
    math_formula = "RSI = 100 - 100/(1+RS), RS=avg_gain/avg_loss"

    def __init__(self, period=14):
        self.period = period

    def compute(self, df):
        df = df.copy()
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(self.period).mean()
        avg_loss = loss.rolling(self.period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[self.name] = 100 - 100 / (1 + rs)
        return df


class BollingerPositionFactor(BaseFactor):
    """
    布林带位置因子
    公式:
        BB_mid = MA(Close, n)
        BB_std = std(Close, n)
        BB_upper = BB_mid + k*BB_std
        BB_lower = BB_mid - k*BB_std
        BB_pos = (Close - BB_mid) / (BB_upper - BB_lower) * 2
    含义: 价格在布林带中的标准化位置
    A股适配: 超过1=突破上轨，低于-1=跌破下轨
    """
    name = "bollinger_pos"
    description = "布林带位置因子"
    math_formula = "BB_pos = (C-BB_mid)/(BB_upper-BB_mid)"

    def __init__(self, period=20, k=2):
        self.period = period
        self.k = k

    def compute(self, df):
        df = df.copy()
        mid = df['close'].rolling(self.period).mean()
        std = df['close'].rolling(self.period).std()
        upper = mid + self.k * std
        lower = mid - self.k * std
        width = (upper - lower).replace(0, np.nan)
        df['boll_mid'] = mid
        df['boll_upper'] = upper
        df['boll_lower'] = lower
        df[self.name] = (df['close'] - mid) / (width / 2)
        return df


# ============================================================
# 择时因子 (5种)
# ============================================================

class MAAlignmentFactor(BaseFactor):
    """
    均线多头排列因子
    公式: ALIGN = (MA5>MA10) + (MA10>MA20) + (MA20>MA60)
    取值: 0-3, 3=完全多头排列
    A股适配: 多头排列时趋势向上概率大
    """
    name = "ma_alignment"
    description = "均线多头排列"
    math_formula = "ALIGN = I(MA5>MA10)+I(MA10>MA20)+I(MA20>MA60)"

    def compute(self, df):
        df = df.copy()
        ma5 = df['close'].rolling(5).mean()
        ma10 = df['close'].rolling(10).mean()
        ma20 = df['close'].rolling(20).mean()
        ma60 = df['close'].rolling(60).mean()
        df[self.name] = (
            (ma5 > ma10).astype(int) +
            (ma10 > ma20).astype(int) +
            (ma20 > ma60).astype(int)
        )
        return df


class VolumeBreakoutFactor(BaseFactor):
    """
    成交量突破因子
    公式: VB = Volume(t) / MA(Volume, 20)
    取值: >2=放量突破, <0.5=缩量
    A股适配: 放量突破常伴随趋势启动
    """
    name = "vol_breakout"
    description = "成交量突破"
    math_formula = "VB = V(t)/MA(V,20)"

    def compute(self, df):
        df = df.copy()
        vol_ma20 = df['volume'].rolling(20).mean()
        df[self.name] = df['volume'] / vol_ma20.replace(0, np.nan)
        return df


class MoneyFlowFactor(BaseFactor):
    """
    资金流向因子 (Chaikin Money Flow思路)
    公式: MF = ((C-L) - (H-C)) / (H-L) * V
    归一化: CMF = sum(MF, n) / sum(V, n)
    取值: [-1, 1], 正=资金流入, 负=资金流出
    A股适配: 主力资金流向对短期走势有指示意义
    """
    name = "money_flow"
    description = "资金流向因子"
    math_formula = "CMF = sum(((C-L)-(H-C))/(H-L)*V, n) / sum(V, n)"

    def __init__(self, period=20):
        self.period = period

    def compute(self, df):
        df = df.copy()
        hl = (df['high'] - df['low']).replace(0, np.nan)
        mf = ((df['close'] - df['low']) - (df['high'] - df['close'])) / hl * df['volume']
        df[self.name] = mf.rolling(self.period).sum() / df['volume'].rolling(self.period).sum()
        return df


# 所有因子注册表
ALL_FACTORS = {
    # 选股因子
    'momentum_5': MomentumFactor(5),
    'momentum_20': MomentumFactor(20),
    'reversal_5': ReversalFactor(5),
    'reversal_10': ReversalFactor(10),
    'volatility_20': VolatilityFactor(20),
    'turnover_5': TurnoverFactor(5),
    'turnover_10': TurnoverFactor(10),
    'amplitude': AmplitudeFactor(),
    'price_position_20': PricePositionFactor(20),
    'vol_price_div_20': VolumePriceDivergenceFactor(20),
    'macd_hist': MACDFactor(),
    'rsi_14': RSIFactor(14),
    'bollinger_pos': BollingerPositionFactor(),
    # 择时因子
    'ma_alignment': MAAlignmentFactor(),
    'vol_breakout': VolumeBreakoutFactor(),
    'money_flow_20': MoneyFlowFactor(20),
}

def compute_all_factors(df):
    """对单只股票计算所有因子"""
    for name, factor in ALL_FACTORS.items():
        try:
            df = factor.compute(df)
        except Exception as e:
            pass  # 容错
    return df
