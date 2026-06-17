# -*- coding: utf-8 -*-
"""
回测引擎 - A股多因子量化回测系统
=================================

核心功能：
- 接收策略信号 + 股票历史数据，模拟交易
- 支持止盈(TP)、止损(SL)、最大持仓天数限制
- 计算完整绩效指标：总收益、年化收益、最大回撤、夏普比率、胜率、盈亏比

数学公式：
    Sharpe = (R_p - R_f) / sigma_p * sqrt(252)
    MaxDD  = max((peak - trough) / peak)
    Calmar = CAGR / MaxDD
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Any
from datetime import datetime


@dataclass
class TradeRecord:
    """单笔交易记录"""
    code: str               # 股票代码
    buy_date: str           # 买入日期
    buy_price: float        # 买入价格
    sell_date: str          # 卖出日期
    sell_price: float       # 卖出价格
    shares: int             # 持有股数
    pnl: float              # 盈亏金额
    pnl_pct: float          # 盈亏百分比
    hold_days: int          # 持仓天数
    exit_reason: str        # 退出原因: TP/SL/EXPIRE/SIGNAL


@dataclass
class BacktestResult:
    """回测结果汇总"""
    # 基本信息
    strategy_name: str      # 策略名称
    start_date: str         # 回测起始日
    end_date: str           # 回测结束日
    total_trades: int       # 总交易次数

    # 收益指标
    total_return: float     # 总收益率 (百分比)
    annual_return: float    # 年化收益率 (百分比)
    max_drawdown: float     # 最大回撤 (百分比)

    # 风险指标
    sharpe_ratio: float     # 夏普比率
    calmar_ratio: float     # 卡尔玛比率
    volatility: float       # 年化波动率

    # 交易指标
    win_rate: float         # 胜率 (百分比)
    profit_loss_ratio: float  # 盈亏比
    avg_hold_days: float    # 平均持仓天数

    # 详细数据
    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    equity_dates: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转为字典，方便JSON序列化"""
        return {
            'strategy_name': self.strategy_name,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'total_trades': self.total_trades,
            'total_return': round(self.total_return, 2),
            'annual_return': round(self.annual_return, 2),
            'max_drawdown': round(self.max_drawdown, 2),
            'sharpe_ratio': round(self.sharpe_ratio, 3),
            'calmar_ratio': round(self.calmar_ratio, 3),
            'volatility': round(self.volatility, 2),
            'win_rate': round(self.win_rate, 2),
            'profit_loss_ratio': round(self.profit_loss_ratio, 2),
            'avg_hold_days': round(self.avg_hold_days, 1),
            'trades_count': len(self.trades),
            'equity_curve_length': len(self.equity_curve),
        }


class BacktestEngine:
    """
    回测引擎

    用法：
        engine = BacktestEngine(
            initial_capital=100000,
            take_profit=0.05,    # 止盈5%
            stop_loss=-0.06,     # 止损6%
            max_hold_days=10,    # 最多持有10天
            commission=0.001,    # 手续费千一
            slippage=0.001,      # 滑点千一
        )
        result = engine.run(strategy_func, stock_data_dict)
    """

    def __init__(
        self,
        initial_capital: float = 100000.0,
        take_profit: float = 0.05,
        stop_loss: float = -0.06,
        max_hold_days: int = 10,
        commission: float = 0.001,
        slippage: float = 0.001,
        risk_free_rate: float = 0.025,
        max_positions: int = 5,
    ):
        """
        初始化回测引擎

        Args:
            initial_capital: 初始资金
            take_profit: 止盈比例 (如0.05表示5%)
            stop_loss: 止损比例 (如-0.06表示-6%)
            max_hold_days: 最大持仓天数
            commission: 单边手续费率
            slippage: 滑点率
            risk_free_rate: 无风险利率(年化)
            max_positions: 最大同时持仓数
        """
        self.initial_capital = initial_capital
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.max_hold_days = max_hold_days
        self.commission = commission
        self.slippage = slippage
        self.risk_free_rate = risk_free_rate
        self.max_positions = max_positions

    def run(
        self,
        strategy_func: Callable,
        stock_data: Dict[str, pd.DataFrame],
        strategy_name: str = "未命名策略",
    ) -> BacktestResult:
        """
        运行回测

        Args:
            strategy_func: 策略函数，签名 strategy_func(date, stock_data, context) -> List[dict]
                返回信号列表，每个信号: {'code': str, 'action': 'buy'/'sell', 'score': float}
            stock_data: {股票代码: DataFrame}，DataFrame需含 date/open/close/high/low/volume 列
            strategy_name: 策略名称

        Returns:
            BacktestResult 回测结果
        """
        # 1. 准备数据：获取所有交易日历
        all_dates = set()
        code_map = {}  # code -> DataFrame，已按日期索引
        for code, df in stock_data.items():
            df = df.copy()
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df = df.set_index('date').sort_index()
            code_map[code] = df
            all_dates.update(df.index.tolist())
        all_dates = sorted(all_dates)

        if not all_dates:
            return self._empty_result(strategy_name)

        # 2. 初始化状态
        cash = self.initial_capital
        positions = {}  # code -> {shares, buy_price, buy_date, buy_cost}
        trades = []
        equity_curve = []
        equity_dates = []

        # 3. 逐日回测
        for date in all_dates:
            # 3a. 更新持仓市值，检查止盈止损和持仓天数
            codes_to_sell = []
            for code, pos in positions.items():
                if code not in code_map or date not in code_map[code].index:
                    continue
                row = code_map[code].loc[date]
                current_price = row['close']
                pnl_pct = (current_price - pos['buy_price']) / pos['buy_price']

                # 计算持仓天数
                buy_idx = all_dates.index(pos['buy_date']) if pos['buy_date'] in all_dates else 0
                cur_idx = all_dates.index(date)
                hold_days = cur_idx - buy_idx

                # 检查退出条件
                exit_reason = None
                if pnl_pct >= self.take_profit:
                    exit_reason = 'TP'
                elif pnl_pct <= self.stop_loss:
                    exit_reason = 'SL'
                elif hold_days >= self.max_hold_days:
                    exit_reason = 'EXPIRE'

                if exit_reason:
                    sell_price = current_price * (1 - self.slippage)  # 卖出有滑点
                    sell_cost = pos['shares'] * sell_price * self.commission
                    pnl = pos['shares'] * (sell_price - pos['buy_price']) - pos['buy_cost'] - sell_cost
                    trades.append(TradeRecord(
                        code=code,
                        buy_date=pos['buy_date'],
                        buy_price=pos['buy_price'],
                        sell_date=date,
                        sell_price=sell_price,
                        shares=pos['shares'],
                        pnl=pnl,
                        pnl_pct=pnl_pct * 100,
                        hold_days=hold_days,
                        exit_reason=exit_reason,
                    ))
                    cash += pos['shares'] * sell_price - sell_cost
                    codes_to_sell.append(code)

            for code in codes_to_sell:
                del positions[code]

            # 3b. 获取策略信号
            context = {
                'cash': cash,
                'positions': positions.copy(),
                'date': date,
                'initial_capital': self.initial_capital,
            }
            try:
                signals = strategy_func(date, code_map, context)
            except Exception:
                signals = []

            # 3c. 处理卖出信号
            if signals:
                for sig in signals:
                    if sig.get('action') == 'sell' and sig['code'] in positions:
                        code = sig['code']
                        pos = positions[code]
                        if code in code_map and date in code_map[code].index:
                            sell_price = code_map[code].loc[date, 'close'] * (1 - self.slippage)
                            sell_cost = pos['shares'] * sell_price * self.commission
                            pnl_pct = (sell_price - pos['buy_price']) / pos['buy_price']
                            buy_idx = all_dates.index(pos['buy_date']) if pos['buy_date'] in all_dates else 0
                            hold_days = all_dates.index(date) - buy_idx
                            pnl = pos['shares'] * (sell_price - pos['buy_price']) - pos['buy_cost'] - sell_cost
                            trades.append(TradeRecord(
                                code=code, buy_date=pos['buy_date'], buy_price=pos['buy_price'],
                                sell_date=date, sell_price=sell_price, shares=pos['shares'],
                                pnl=pnl, pnl_pct=pnl_pct * 100, hold_days=hold_days,
                                exit_reason='SIGNAL',
                            ))
                            cash += pos['shares'] * sell_price - sell_cost
                            del positions[code]

            # 3d. 处理买入信号
            if signals:
                buy_signals = [s for s in signals if s.get('action') == 'buy']
                buy_signals.sort(key=lambda x: x.get('score', 0), reverse=True)
                for sig in buy_signals:
                    if len(positions) >= self.max_positions:
                        break
                    code = sig['code']
                    if code in positions:
                        continue
                    if code not in code_map or date not in code_map[code].index:
                        continue
                    buy_price = code_map[code].loc[date, 'close'] * (1 + self.slippage)
                    # 每只股票分配等额资金
                    alloc = self.initial_capital / self.max_positions
                    shares = int(alloc / buy_price / 100) * 100  # A股100股整数倍
                    if shares < 100:
                        continue
                    buy_cost = shares * buy_price * self.commission
                    total_cost = shares * buy_price + buy_cost
                    if total_cost > cash:
                        continue
                    cash -= total_cost
                    positions[code] = {
                        'shares': shares,
                        'buy_price': buy_price,
                        'buy_date': date,
                        'buy_cost': buy_cost,
                    }

            # 3e. 计算当日总权益
            market_value = 0
            for code, pos in positions.items():
                if code in code_map and date in code_map[code].index:
                    market_value += pos['shares'] * code_map[code].loc[date, 'close']
            total_equity = cash + market_value
            equity_curve.append(total_equity)
            equity_dates.append(date)

        # 4. 强制清仓未平仓持仓
        for code, pos in list(positions.items()):
            last_price = pos['buy_price']  # 保守处理
            if code in code_map and len(code_map[code]) > 0:
                last_price = code_map[code].iloc[-1]['close']
            pnl_pct = (last_price - pos['buy_price']) / pos['buy_price']
            sell_cost = pos['shares'] * last_price * self.commission
            pnl = pos['shares'] * (last_price - pos['buy_price']) - pos['buy_cost'] - sell_cost
            trades.append(TradeRecord(
                code=code, buy_date=pos['buy_date'], buy_price=pos['buy_price'],
                sell_date=all_dates[-1], sell_price=last_price, shares=pos['shares'],
                pnl=pnl, pnl_pct=pnl_pct * 100, hold_days=0, exit_reason='END',
            ))

        # 5. 计算统计指标
        return self._calc_metrics(strategy_name, trades, equity_curve, equity_dates)

    def _calc_metrics(
        self,
        strategy_name: str,
        trades: List[TradeRecord],
        equity_curve: List[float],
        equity_dates: List[str],
    ) -> BacktestResult:
        """计算回测绩效指标"""
        if not equity_curve or len(equity_curve) < 2:
            return self._empty_result(strategy_name)

        equity = np.array(equity_curve, dtype=float)

        # --- 总收益率 ---
        total_return = (equity[-1] - equity[0]) / equity[0] * 100

        # --- 年化收益率 (CAGR) ---
        n_days = len(equity)
        n_years = n_days / 252.0
        if n_years > 0 and equity[0] > 0:
            annual_return = ((equity[-1] / equity[0]) ** (1 / n_years) - 1) * 100
        else:
            annual_return = 0.0

        # --- 最大回撤 ---
        # MaxDD = max((peak - trough) / peak)
        running_max = np.maximum.accumulate(equity)
        drawdowns = (running_max - equity) / running_max
        max_drawdown = float(np.max(drawdowns)) * 100

        # --- 日收益率序列 ---
        daily_returns = np.diff(equity) / equity[:-1]

        # --- 年化波动率 ---
        volatility = float(np.std(daily_returns)) * np.sqrt(252) * 100

        # --- 夏普比率 ---
        # Sharpe = (R_p - R_f) / sigma_p * sqrt(252)
        # 这里 R_p 是日均收益, sigma_p 是日收益标准差
        if len(daily_returns) > 0 and np.std(daily_returns) > 0:
            rf_daily = self.risk_free_rate / 252
            sharpe_ratio = (np.mean(daily_returns) - rf_daily) / np.std(daily_returns) * np.sqrt(252)
        else:
            sharpe_ratio = 0.0

        # --- 卡尔玛比率 ---
        # Calmar = CAGR / MaxDD
        calmar_ratio = annual_return / max_drawdown if max_drawdown > 0 else 0.0

        # --- 胜率、盈亏比 ---
        if trades:
            wins = [t for t in trades if t.pnl > 0]
            losses = [t for t in trades if t.pnl <= 0]
            win_rate = len(wins) / len(trades) * 100

            avg_win = np.mean([t.pnl for t in wins]) if wins else 0
            avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 1
            profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

            avg_hold_days = np.mean([t.hold_days for t in trades])
        else:
            win_rate = 0.0
            profit_loss_ratio = 0.0
            avg_hold_days = 0.0

        return BacktestResult(
            strategy_name=strategy_name,
            start_date=equity_dates[0] if equity_dates else '',
            end_date=equity_dates[-1] if equity_dates else '',
            total_trades=len(trades),
            total_return=total_return,
            annual_return=annual_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            calmar_ratio=calmar_ratio,
            volatility=volatility,
            win_rate=win_rate,
            profit_loss_ratio=profit_loss_ratio,
            avg_hold_days=avg_hold_days,
            trades=trades,
            equity_curve=equity_curve,
            equity_dates=equity_dates,
        )

    def _empty_result(self, strategy_name: str) -> BacktestResult:
        """返回空结果"""
        return BacktestResult(
            strategy_name=strategy_name,
            start_date='', end_date='', total_trades=0,
            total_return=0, annual_return=0, max_drawdown=0,
            sharpe_ratio=0, calmar_ratio=0, volatility=0,
            win_rate=0, profit_loss_ratio=0, avg_hold_days=0,
        )
