# -*- coding: utf-8 -*-
"""
选股策略组合 - A股多因子量化系统
=================================

每个策略定义为一组因子 + 权重的组合，用于对股票进行横截面打分排序。

策略列表：
1. 动量+量价策略 (momentum_volume)
2. 低波+反转策略 (low_vol_reversal)
3. MACD+RSI趋势策略 (macd_rsi_trend)
4. 多因子等权策略 (multi_factor_equal)
5. 自适应权重策略 (adaptive_weight)
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional


class SelectionStrategy:
    """
    选股策略基类

    每个策略包含：
    - name: 策略名称
    - factors: 使用的因子列表
    - weights: 各因子权重
    - description: 策略描述
    """

    def __init__(self, name: str, factors: List[str], weights: List[float], description: str):
        """
        初始化选股策略

        Args:
            name: 策略名称
            factors: 因子名称列表
            weights: 对应权重列表，与factors等长
            description: 策略描述
        """
        self.name = name
        self.factors = factors
        self.weights = np.array(weights, dtype=float)
        # 归一化权重
        w_sum = np.sum(np.abs(self.weights))
        if w_sum > 0:
            self.weights = self.weights / w_sum
        self.description = description

    def compute_score(self, factor_values: Dict[str, float]) -> float:
        """
        根据因子值计算综合评分

        Args:
            factor_values: {因子名: 因子值}，因子值应已标准化到0~100

        Returns:
            综合评分 (0~100)
        """
        score = 0.0
        for factor, weight in zip(self.factors, self.weights):
            val = factor_values.get(factor, 50.0)  # 缺失值取中位
            score += weight * val
        return float(np.clip(score, 0, 100))

    def rank_stocks(self, stock_factors: pd.DataFrame, top_n: int = 20) -> List[str]:
        """
        对股票进行横截面排名

        Args:
            stock_factors: DataFrame，index=股票代码，columns=因子名
            top_n: 选取前N只股票

        Returns:
            排名靠前的股票代码列表
        """
        scores = pd.Series(0.0, index=stock_factors.index)
        for factor, weight in zip(self.factors, self.weights):
            if factor in stock_factors.columns:
                # 标准化到0~100
                col = stock_factors[factor].copy()
                col_min, col_max = col.min(), col.max()
                if col_max > col_min:
                    col = (col - col_min) / (col_max - col_min) * 100
                else:
                    col = 50.0
                scores += weight * col.fillna(50)
        return scores.nlargest(top_n).index.tolist()

    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            'name': self.name,
            'factors': self.factors,
            'weights': [round(w, 4) for w in self.weights.tolist()],
            'description': self.description,
        }

    def __repr__(self):
        return f"SelectionStrategy({self.name})"


# ========== 策略1: 动量+量价 ==========
momentum_volume_strategy = SelectionStrategy(
    name="动量+量价策略",
    factors=["momentum_5d", "momentum_20d", "vol_ratio", "turnover_rate", "price_position"],
    weights=[0.30, 0.20, 0.25, 0.15, 0.10],
    description=(
        "偏好近期强势且成交量活跃的股票。"
        "5日动量和成交量比率为核心驱动因子，"
        "捕捉短线爆发力强、资金关注度高的标的。"
        "适合趋势行情中追涨。"
    ),
)

# ========== 策略2: 低波+反转 ==========
low_vol_reversal_strategy = SelectionStrategy(
    name="低波+反转策略",
    factors=["volatility_20d", "volatility_60d", "reversal_5d", "reversal_20d", "rsi_14d"],
    weights=[0.30, 0.20, 0.25, 0.15, 0.10],
    description=(
        "偏好低波动率且短期超跌反弹的股票。"
        "低波动因子筛选稳定标的，反转因子捕捉反弹机会。"
        "适合震荡市或下跌后的底部布局。"
    ),
)

# ========== 策略3: MACD+RSI趋势 ==========
macd_rsi_trend_strategy = SelectionStrategy(
    name="MACD+RSI趋势策略",
    factors=["macd_hist", "macd_cross", "rsi_14d", "rsi_trend", "ma_alignment"],
    weights=[0.30, 0.20, 0.20, 0.15, 0.15],
    description=(
        "基于MACD金叉+RSI中性区间的趋势跟踪策略。"
        "MACD柱状线判断趋势方向，RSI过滤超买超卖，"
        "均线排列确认多头趋势。"
        "适合趋势明确的单边行情。"
    ),
)

# ========== 策略4: 多因子等权 ==========
multi_factor_equal_strategy = SelectionStrategy(
    name="多因子等权策略",
    factors=[
        "momentum_5d", "vol_ratio", "volatility_20d", "reversal_5d",
        "macd_hist", "rsi_14d", "turnover_rate", "price_position",
    ],
    weights=[0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125],
    description=(
        "综合动量、量价、波动、反转、趋势等8个因子等权组合。"
        "不偏向任何单一风格，追求稳健的全市场选股能力。"
        "适合不确定市场风格时使用。"
    ),
)


class AdaptiveWeightStrategy(SelectionStrategy):
    """
    自适应权重策略

    根据最近N天各因子的IC(信息系数)动态调整权重。
    IC = corr(factor_rank, return_rank)
    近期IC高的因子获得更高权重。

    每月月初重新计算权重。
    """

    def __init__(
        self,
        factors: List[str],
        lookback_days: int = 30,
        min_weight: float = 0.05,
        max_weight: float = 0.35,
    ):
        """
        初始化自适应策略

        Args:
            factors: 因子列表
            lookback_days: IC计算回看天数
            min_weight: 单因子最小权重
            max_weight: 单因子最大权重
        """
        n = len(factors)
        default_weights = [1.0 / n] * n
        super().__init__(
            name="自适应权重策略",
            factors=factors,
            weights=default_weights,
            description=(
                f"根据最近{lookback_days}天因子IC值动态调整权重。"
                f"IC(信息系数)衡量因子对未来收益的预测能力。"
                f"权重范围限制在[{min_weight}, {max_weight}]，"
                f"每月初重新计算，避免过拟合。"
            ),
        )
        self.lookback_days = lookback_days
        self.min_weight = min_weight
        self.max_weight = max_weight
        self._ic_history = []  # 记录IC历史

    def update_weights(self, factor_ic: Dict[str, float]):
        """
        根据最新IC值更新权重

        Args:
            factor_ic: {因子名: IC值}，IC越高权重越大
        """
        # 只使用正IC的因子（有预测能力的）
        ic_values = {}
        for f in self.factors:
            ic = factor_ic.get(f, 0)
            ic_values[f] = max(ic, 0.01)  # 最小0.01避免零权重

        # 按IC值分配权重
        total_ic = sum(ic_values.values())
        raw_weights = {f: ic / total_ic for f, ic in ic_values.items()}

        # 限制权重范围
        for f in self.factors:
            w = raw_weights.get(f, 1.0 / len(self.factors))
            w = np.clip(w, self.min_weight, self.max_weight)
            raw_weights[f] = w

        # 归一化
        total_w = sum(raw_weights.values())
        self.weights = np.array([raw_weights.get(f, 0) / total_w for f in self.factors])

        self._ic_history.append(factor_ic.copy())

    def compute_factor_ic(
        self,
        factor_df: pd.DataFrame,
        forward_returns: pd.Series,
    ) -> Dict[str, float]:
        """
        计算各因子与未来收益的Rank IC

        Args:
            factor_df: DataFrame，index=股票代码，columns=因子名
            forward_returns: Series，index=股票代码，值=未来收益率

        Returns:
            {因子名: IC值}
        """
        ic_dict = {}
        common_idx = factor_df.index.intersection(forward_returns.index)
        if len(common_idx) < 5:
            return {f: 0 for f in self.factors}

        for factor in self.factors:
            if factor in factor_df.columns:
                f_vals = factor_df.loc[common_idx, factor].rank()
                r_vals = forward_returns.loc[common_idx].rank()
                # Rank IC (Spearman)
                corr = f_vals.corr(r_vals)
                ic_dict[factor] = float(corr) if not np.isnan(corr) else 0.0
            else:
                ic_dict[factor] = 0.0

        return ic_dict


# 实例化自适应策略（使用全部8个因子）
adaptive_weight_strategy = AdaptiveWeightStrategy(
    factors=[
        "momentum_5d", "vol_ratio", "volatility_20d", "reversal_5d",
        "macd_hist", "rsi_14d", "turnover_rate", "price_position",
    ],
    lookback_days=30,
    min_weight=0.05,
    max_weight=0.35,
)


# ========== 所有策略汇总 ==========
ALL_SELECTION_STRATEGIES = [
    momentum_volume_strategy,
    low_vol_reversal_strategy,
    macd_rsi_trend_strategy,
    multi_factor_equal_strategy,
    adaptive_weight_strategy,
]
