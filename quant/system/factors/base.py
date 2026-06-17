# -*- coding: utf-8 -*-
"""
因子计算基类

所有因子必须继承BaseFactor并实现compute方法。
输入标准的OHLCV DataFrame，输出添加了因子列的DataFrame。

使用示例：
    factor = MomentumFactor(period=20)
    df_with_factor = factor.compute(df)
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class BaseFactor(ABC):
    """
    因子计算基类

    所有因子类必须：
    1. 设置 name 属性 - 因子名称（英文，用作列名）
    2. 设置 description 属性 - 因子中文描述
    3. 设置 math_formula 属性 - 数学公式（LaTeX风格）
    4. 实现 compute(df) 方法 - 计算逻辑

    约定：
    - 输入DataFrame必须包含: open, close, high, low, volume
    - 输出DataFrame在原有列基础上添加因子列
    - 因子列名 = self.name
    - NaN是允许的（窗口期不足时）
    """

    # 子类必须覆盖这三个属性
    name: str = "base_factor"
    description: str = "因子基类"
    math_formula: str = ""

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算因子值

        Args:
            df: 包含OHLCV数据的DataFrame
                必需列: open, close, high, low, volume
                可选列: preclose, amount, vol, pctChg

        Returns:
            添加了因子列的DataFrame（不修改原始数据）
        """
        pass

    def validate_input(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        验证输入数据的合法性

        Args:
            df: 输入DataFrame

        Returns:
            验证后的DataFrame副本

        Raises:
            ValueError: 缺少必需列
        """
        required_cols = ['open', 'close', 'high', 'low', 'volume']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"缺少必需列: {missing}，可用列: {list(df.columns)}")

        df = df.copy()

        # 确保数值类型
        for col in ['open', 'close', 'high', 'low', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        return df

    def __repr__(self) -> str:
        return f"<Factor: {self.name} - {self.description}>"

    def info(self) -> dict:
        """返回因子元信息"""
        return {
            "name": self.name,
            "description": self.description,
            "math_formula": self.math_formula,
        }


class FactorRegistry:
    """
    因子注册表

    自动收集所有继承BaseFactor的子类，支持按名称查找和批量计算。

    使用示例：
        registry = FactorRegistry()
        registry.auto_discover()  # 自动发现所有因子
        result = registry.compute_all(df)
    """

    def __init__(self):
        self._factors = {}

    def register(self, factor: BaseFactor):
        """
        注册一个因子实例

        Args:
            factor: BaseFactor子类实例
        """
        if not isinstance(factor, BaseFactor):
            raise TypeError(f"必须是BaseFactor的子类，得到 {type(factor)}")
        self._factors[factor.name] = factor
        logger.debug(f"注册因子: {factor.name}")

    def get(self, name: str) -> Optional[BaseFactor]:
        """按名称获取因子"""
        return self._factors.get(name)

    def list_factors(self) -> list:
        """列出所有已注册因子"""
        return [
            {"name": f.name, "description": f.description, "formula": f.math_formula}
            for f in self._factors.values()
        ]

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算所有已注册因子

        Args:
            df: 原始OHLCV数据

        Returns:
            添加了所有因子列的DataFrame
        """
        result = df.copy()
        for name, factor in self._factors.items():
            try:
                result = factor.compute(result)
                logger.debug(f"因子 {name} 计算完成")
            except Exception as e:
                logger.error(f"因子 {name} 计算失败: {e}")
                result[name] = np.nan
        return result

    def compute_selected(self, df: pd.DataFrame, factor_names: list) -> pd.DataFrame:
        """
        计算指定的因子

        Args:
            df: 原始OHLCV数据
            factor_names: 要计算的因子名称列表

        Returns:
            添加了指定因子列的DataFrame
        """
        result = df.copy()
        for name in factor_names:
            factor = self._factors.get(name)
            if factor is None:
                logger.warning(f"因子 {name} 未注册，跳过")
                continue
            try:
                result = factor.compute(result)
            except Exception as e:
                logger.error(f"因子 {name} 计算失败: {e}")
                result[name] = np.nan
        return result

    def __len__(self):
        return len(self._factors)

    def __repr__(self):
        names = list(self._factors.keys())
        return f"<FactorRegistry: {len(names)} factors - {names}>"
