# -*- coding: utf-8 -*-
"""
择时策略 - A股多因子量化系统
=================================

根据市场状态信号动态调整仓位比例(0.0~1.0)。

策略列表：
1. 均线择时 - MA20上穿/下穿
2. 波动率择时 - 低波满仓、高波减仓
3. 成交量择时 - 放量加仓、缩量减仓
4. 市场宽度择时 - 涨跌家数比
5. 综合择时 - 多信号加权
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional


class TimingStrategy:
    """
    择时策略基类

    输入市场数据，输出仓位比例(0.0~1.0)
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def get_position_ratio(self, market_data: pd.DataFrame, date: str) -> float:
        """
        获取目标仓位比例

        Args:
            market_data: 市场/指数数据，含 close/volume 等列
            date: 当前日期

        Returns:
            仓位比例 0.0(空仓) ~ 1.0(满仓)
        """
        raise NotImplementedError

    def to_dict(self) -> Dict:
        return {'name': self.name, 'description': self.description}


class MATimingStrategy(TimingStrategy):
    """
    均线择时策略

    - 收盘价 > MA20: 满仓(1.0)
    - 收盘价 < MA20 且 MA20 下行: 空仓(0.0)
    - 收盘价 < MA20 但 MA20 上行: 半仓(0.5)
    """

    def __init__(self, ma_period: int = 20):
        super().__init__(
            name=f"MA{ma_period}均线择时",
            description=f"价格>{'MA'+str(ma_period)}满仓，<{'MA'+str(ma_period)}且均线走平/下行减仓",
        )
        self.ma_period = ma_period

    def get_position_ratio(self, market_data: pd.DataFrame, date: str) -> float:
        if len(market_data) < self.ma_period + 1:
            return 0.5  # 数据不足，半仓

        # 确保数据按日期排序
        df = market_data.sort_index()
        if date not in df.index:
            # 找最近的日期
            before = df.index[df.index <= date]
            if len(before) == 0:
                return 0.5
            date = before[-1]

        close = df.loc[:date, 'close']
        ma = close.rolling(self.ma_period).mean()

        if len(close) < self.ma_period + 1:
            return 0.5

        current = close.iloc[-1]
        ma_current = ma.iloc[-1]
        ma_prev = ma.iloc[-2]

        if current > ma_current:
            return 1.0  # 站上均线，满仓
        elif ma_current > ma_prev:
            return 0.5  # 均线仍在上行，半仓
        else:
            return 0.0  # 均线下行且价格在下方，空仓


class VolatilityTimingStrategy(TimingStrategy):
    """
    波动率择时策略

    - 20日波动率 < 历史25分位: 满仓(1.0)
    - 20日波动率 25~75分位: 半仓~满仓线性映射
    - 20日波动率 > 历史75分位: 减仓至0.3
    """

    def __init__(self, vol_period: int = 20, lookback: int = 252):
        super().__init__(
            name="波动率择时",
            description="低波满仓、高波减仓，基于20日波动率的历史百分位",
        )
        self.vol_period = vol_period
        self.lookback = lookback

    def get_position_ratio(self, market_data: pd.DataFrame, date: str) -> float:
        df = market_data.sort_index()
        if date not in df.index:
            before = df.index[df.index <= date]
            if len(before) == 0:
                return 0.5
            date = before[-1]

        close = df.loc[:date, 'close']
        if len(close) < self.vol_period + 5:
            return 0.5

        daily_ret = close.pct_change().dropna()
        rolling_vol = daily_ret.rolling(self.vol_period).std() * np.sqrt(252)

        # 取历史窗口计算分位数
        vol_history = rolling_vol.dropna().iloc[-self.lookback:]
        if len(vol_history) < 20:
            return 0.5

        current_vol = vol_history.iloc[-1]
        q25 = vol_history.quantile(0.25)
        q75 = vol_history.quantile(0.75)

        if current_vol <= q25:
            return 1.0
        elif current_vol >= q75:
            return 0.3
        else:
            # 线性插值: q25->1.0, q75->0.3
            ratio = 1.0 - 0.7 * (current_vol - q25) / (q75 - q25)
            return float(np.clip(ratio, 0.3, 1.0))


class VolumeTimingStrategy(TimingStrategy):
    """
    成交量择时策略

    - 成交量 > MA5量 * 1.5: 放量，满仓(1.0)
    - 成交量 > MA5量 * 1.0: 正常，0.7仓
    - 成交量 < MA5量 * 0.7: 缩量，0.4仓
    """

    def __init__(self, vol_ma: int = 5):
        super().__init__(
            name="成交量择时",
            description="放量加仓、缩量减仓，以5日均量为基准",
        )
        self.vol_ma = vol_ma

    def get_position_ratio(self, market_data: pd.DataFrame, date: str) -> float:
        df = market_data.sort_index()
        if date not in df.index:
            before = df.index[df.index <= date]
            if len(before) == 0:
                return 0.5
            date = before[-1]

        # 找成交量列
        vol_col = 'volume' if 'volume' in df.columns else 'vol'
        if vol_col not in df.columns:
            return 0.5

        volume = df.loc[:date, vol_col].astype(float)
        if len(volume) < self.vol_ma + 1:
            return 0.5

        ma_vol = volume.rolling(self.vol_ma).mean()
        current_vol = volume.iloc[-1]
        ma_vol_current = ma_vol.iloc[-1]

        if ma_vol_current <= 0:
            return 0.5

        ratio = current_vol / ma_vol_current
        if ratio > 1.5:
            return 1.0
        elif ratio > 1.0:
            return 0.7
        elif ratio > 0.7:
            return 0.5
        else:
            return 0.3


class MarketBreadthTimingStrategy(TimingStrategy):
    """
    市场宽度择时策略

    根据全市场涨跌家数比例判断市场情绪：
    - 上涨占比 > 60%: 满仓(1.0)
    - 上涨占比 40%~60%: 半仓(0.6)
    - 上涨占比 < 40%: 轻仓(0.3)
    """

    def __init__(self):
        super().__init__(
            name="市场宽度择时",
            description="根据全市场涨跌家数比例调整仓位",
        )
        self._breadth_cache = {}  # date -> up_ratio

    def set_breadth(self, breadth_data: Dict[str, float]):
        """
        设置市场宽度数据

        Args:
            breadth_data: {日期: 上涨股票占比}，0.0~1.0
        """
        self._breadth_cache = breadth_data.copy()

    def compute_breadth(self, stock_data: Dict[str, pd.DataFrame], date: str) -> float:
        """实时计算市场宽度（上涨家数/总家数）"""
        up_count = 0
        total_count = 0
        for code, df in stock_data.items():
            if date in df.index:
                if 'pctChg' in df.columns:
                    if df.loc[date, 'pctChg'] > 0:
                        up_count += 1
                    total_count += 1
                elif 'close' in df.columns and 'preclose' in df.columns:
                    pre = df.loc[:date, 'close']
                    if len(pre) >= 2:
                        if pre.iloc[-1] > pre.iloc[-2]:
                            up_count += 1
                        total_count += 1
        return up_count / total_count if total_count > 0 else 0.5

    def get_position_ratio(self, market_data: pd.DataFrame, date: str) -> float:
        up_ratio = self._breadth_cache.get(date, 0.5)
        if up_ratio > 0.6:
            return 1.0
        elif up_ratio > 0.4:
            return 0.6
        else:
            return 0.3


class CompositeTimingStrategy(TimingStrategy):
    """
    综合择时策略

    将多个择时信号加权平均，输出最终仓位比例。
    权重可配置，默认均线40%、波动率25%、成交量20%、市场宽度15%。
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        初始化综合择时

        Args:
            weights: 各子策略权重，如 {'ma': 0.4, 'vol': 0.25, 'volume': 0.2, 'breadth': 0.15}
        """
        super().__init__(
            name="综合择时策略",
            description="均线、波动率、成交量、市场宽度四信号加权",
        )
        self.sub_strategies = {
            'ma': MATimingStrategy(ma_period=20),
            'vol': VolatilityTimingStrategy(),
            'volume': VolumeTimingStrategy(),
            'breadth': MarketBreadthTimingStrategy(),
        }
        self.weights = weights or {
            'ma': 0.40,
            'vol': 0.25,
            'volume': 0.20,
            'breadth': 0.15,
        }

    def get_position_ratio(self, market_data: pd.DataFrame, date: str) -> float:
        total_ratio = 0.0
        total_weight = 0.0
        for key, strategy in self.sub_strategies.items():
            w = self.weights.get(key, 0)
            if w > 0:
                ratio = strategy.get_position_ratio(market_data, date)
                total_ratio += w * ratio
                total_weight += w
        return total_ratio / total_weight if total_weight > 0 else 0.5


# ========== 择时策略汇总 ==========
ALL_TIMING_STRATEGIES = {
    'ma': MATimingStrategy(ma_period=20),
    'volatility': VolatilityTimingStrategy(),
    'volume': VolumeTimingStrategy(),
    'breadth': MarketBreadthTimingStrategy(),
    'composite': CompositeTimingStrategy(),
}
