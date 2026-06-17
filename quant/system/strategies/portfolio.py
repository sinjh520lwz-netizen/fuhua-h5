# -*- coding: utf-8 -*-
"""
组合管理 - A股多因子量化系统
=================================

功能：
- 资金分配：等权、市值加权、风险平价
- 止损止盈管理
- 最大持仓限制
- 仓位再平衡
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class PositionInfo:
    """持仓信息"""
    code: str               # 股票代码
    shares: int             # 持有股数
    buy_price: float        # 买入均价
    current_price: float    # 当前价格
    weight: float           # 目标权重
    pnl_pct: float = 0.0   # 浮动盈亏百分比


class WeightingMethod:
    """权重分配方法"""

    @staticmethod
    def equal_weight(codes: List[str]) -> Dict[str, float]:
        """
        等权分配

        Args:
            codes: 股票代码列表

        Returns:
            {代码: 权重}
        """
        n = len(codes)
        if n == 0:
            return {}
        w = 1.0 / n
        return {code: w for code in codes}

    @staticmethod
    def market_cap_weight(codes: List[str], market_caps: Dict[str, float]) -> Dict[str, float]:
        """
        市值加权

        大市值获得更高权重，类似于指数编制中的加权方式。

        Args:
            codes: 股票代码列表
            market_caps: {代码: 总市值}

        Returns:
            {代码: 权重}
        """
        total_cap = sum(market_caps.get(c, 0) for c in codes)
        if total_cap <= 0:
            return WeightingMethod.equal_weight(codes)
        return {code: market_caps.get(code, 0) / total_cap for code in codes}

    @staticmethod
    def risk_parity(
        codes: List[str],
        volatilities: Dict[str, float],
    ) -> Dict[str, float]:
        """
        风险平价

        每只股票对组合风险的贡献相等。
        权重与波动率成反比：w_i ∝ 1/sigma_i

        Args:
            codes: 股票代码列表
            volatilities: {代码: 年化波动率}

        Returns:
            {代码: 权重}
        """
        inv_vols = {}
        for code in codes:
            vol = volatilities.get(code, 0.3)
            inv_vols[code] = 1.0 / max(vol, 0.01)

        total = sum(inv_vols.values())
        if total <= 0:
            return WeightingMethod.equal_weight(codes)
        return {code: iv / total for code, iv in inv_vols.items()}


class PortfolioManager:
    """
    组合管理器

    管理持仓、分配资金、执行止损止盈、控制持仓上限。

    用法：
        pm = PortfolioManager(
            total_capital=100000,
            max_positions=10,
            take_profit=0.05,
            stop_loss=-0.06,
        )
        # 分配目标权重
        targets = pm.allocate(selected_codes, method='equal')
        # 检查止损止盈
        exits = pm.check_exits(current_positions, current_prices)
    """

    def __init__(
        self,
        total_capital: float = 100000.0,
        max_positions: int = 10,
        take_profit: float = 0.05,
        stop_loss: float = -0.06,
        single_position_limit: float = 0.20,
    ):
        """
        初始化组合管理器

        Args:
            total_capital: 总资金
            max_positions: 最大持仓数
            take_profit: 止盈比例
            stop_loss: 止损比例
            single_position_limit: 单只股票最大仓位占比
        """
        self.total_capital = total_capital
        self.max_positions = max_positions
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.single_position_limit = single_position_limit

    def allocate(
        self,
        codes: List[str],
        method: str = 'equal',
        market_caps: Optional[Dict[str, float]] = None,
        volatilities: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Dict]:
        """
        计算目标仓位

        Args:
            codes: 选中的股票列表
            method: 权重方法 'equal'/'cap'/'risk_parity'
            market_caps: 市值数据（市值加权时需要）
            volatilities: 波动率数据（风险平价时需要）

        Returns:
            {代码: {'weight': 权重, 'target_amount': 目标金额, 'target_shares': 目标股数}}
        """
        # 限制最大持仓数
        codes = codes[:self.max_positions]

        # 计算权重
        if method == 'cap' and market_caps:
            weights = WeightingMethod.market_cap_weight(codes, market_caps)
        elif method == 'risk_parity' and volatilities:
            weights = WeightingMethod.risk_parity(codes, volatilities)
        else:
            weights = WeightingMethod.equal_weight(codes)

        # 限制单只最大仓位
        for code in weights:
            weights[code] = min(weights[code], self.single_position_limit)
        # 重新归一化
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {c: w / total_w for c, w in weights.items()}

        # 计算目标金额
        result = {}
        for code, w in weights.items():
            target_amount = self.total_capital * w
            result[code] = {
                'weight': round(w, 4),
                'target_amount': round(target_amount, 2),
            }

        return result

    def check_exits(
        self,
        positions: Dict[str, Dict],
        current_prices: Dict[str, float],
    ) -> List[Dict]:
        """
        检查止损止盈条件

        Args:
            positions: {代码: {'buy_price': 买入价, 'shares': 股数, ...}}
            current_prices: {代码: 当前价格}

        Returns:
            需要平仓的信号列表 [{'code': 代码, 'reason': TP/SL, 'pnl_pct': 盈亏%}]
        """
        exits = []
        for code, pos in positions.items():
            buy_price = pos.get('buy_price', 0)
            if buy_price <= 0:
                continue
            current = current_prices.get(code, 0)
            if current <= 0:
                continue

            pnl_pct = (current - buy_price) / buy_price

            if pnl_pct >= self.take_profit:
                exits.append({
                    'code': code,
                    'reason': 'TP',
                    'pnl_pct': round(pnl_pct * 100, 2),
                })
            elif pnl_pct <= self.stop_loss:
                exits.append({
                    'code': code,
                    'reason': 'SL',
                    'pnl_pct': round(pnl_pct * 100, 2),
                })

        return exits

    def rebalance(
        self,
        current_positions: Dict[str, Dict],
        target_weights: Dict[str, float],
        current_prices: Dict[str, float],
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        计算再平衡交易

        Args:
            current_positions: 当前持仓
            target_weights: {代码: 目标权重}
            current_prices: 当前价格

        Returns:
            (买入列表, 卖出列表)，每个元素: {'code': 代码, 'shares': 股数, 'amount': 金额}
        """
        buys = []
        sells = []

        # 当前总市值
        total_value = self.total_capital
        for code, pos in current_positions.items():
            price = current_prices.get(code, pos.get('buy_price', 0))
            total_value += pos.get('shares', 0) * (price - pos.get('buy_price', 0))

        # 需要卖出的（不在目标中或权重降低的）
        for code, pos in current_positions.items():
            if code not in target_weights:
                sells.append({
                    'code': code,
                    'shares': pos['shares'],
                    'action': 'sell_all',
                })
            else:
                target_value = total_value * target_weights[code]
                current_value = pos['shares'] * current_prices.get(code, pos['buy_price'])
                if current_value > target_value * 1.05:  # 偏差>5%才调
                    excess = current_value - target_value
                    price = current_prices.get(code, 0)
                    sell_shares = int(excess / price / 100) * 100
                    if sell_shares >= 100:
                        sells.append({
                            'code': code,
                            'shares': sell_shares,
                            'action': 'reduce',
                        })

        # 需要买入的
        for code, target_w in target_weights.items():
            if code in current_positions:
                continue
            target_value = total_value * target_w
            price = current_prices.get(code, 0)
            if price > 0:
                shares = int(target_value / price / 100) * 100
                if shares >= 100:
                    buys.append({
                        'code': code,
                        'shares': shares,
                        'action': 'buy_new',
                    })

        return buys, sells

    def to_dict(self) -> Dict:
        """转为字典"""
        return {
            'total_capital': self.total_capital,
            'max_positions': self.max_positions,
            'take_profit': f"{self.take_profit*100:.1f}%",
            'stop_loss': f"{self.stop_loss*100:.1f}%",
            'single_position_limit': f"{self.single_position_limit*100:.1f}%",
        }
