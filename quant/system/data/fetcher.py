# -*- coding: utf-8 -*-
"""
数据获取层 - 基于mootdx的通达信数据接口

功能：
1. 从通达信服务器获取股票列表和日线数据
2. 本地pickle缓存复用（/root/data/daily_cache_tdx/）
3. 数据清洗：排除ST、科创板(688)、创业板(300/301)、北交所(920)

缓存文件格式: sh_000001.pkl, sz_000001.pkl
DataFrame字段: open, close, high, low, vol, amount, year, month, day,
               hour, minute, datetime, volume, code, date, preclose, pctChg
"""

import os
import re
import pickle
import logging
from pathlib import Path
from typing import List, Optional, Dict

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# 默认缓存目录
DEFAULT_CACHE_DIR = "/root/data/daily_cache_tdx"

# 需要排除的板块前缀
EXCLUDED_PREFIXES = {
    '688': '科创板',
    '300': '创业板',
    '301': '创业板',
    '920': '北交所',
}


def _is_excluded_code(code: str) -> bool:
    """
    判断股票代码是否属于排除板块

    Args:
        code: 股票代码，如 'sh.600000' 或 'sh_000001'

    Returns:
        True表示应排除
    """
    # 提取纯数字代码部分
    pure_code = re.sub(r'[^0-9]', '', code)
    if len(pure_code) < 6:
        return True

    last6 = pure_code[-6:]

    # 排除科创板(688)、创业板(300/301)、北交所(920)
    for prefix in EXCLUDED_PREFIXES:
        if last6.startswith(prefix):
            return True

    return False


def _is_st(name: str) -> bool:
    """
    判断股票名称是否为ST股

    Args:
        name: 股票名称

    Returns:
        True表示是ST
    """
    if not name:
        return False
    name_upper = name.upper()
    return 'ST' in name_upper or '*ST' in name_upper


class DataFetcher:
    """
    通达信数据获取器

    支持两种模式：
    1. 缓存模式：从本地pickle文件加载（快速，推荐）
    2. 在线模式：通过mootdx从通达信服务器获取（需网络）

    使用示例：
        fetcher = DataFetcher(cache_dir="/root/data/daily_cache_tdx")
        stock_list = fetcher.get_stock_list()
        df = fetcher.get_daily("sh.600000")
    """

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, use_online: bool = False):
        """
        初始化数据获取器

        Args:
            cache_dir: 本地缓存目录路径
            use_online: 是否启用在线模式（需要mootdx）
        """
        self.cache_dir = Path(cache_dir)
        self.use_online = use_online
        self._client = None
        self._stock_list_cache = None

    def _get_client(self):
        """延迟初始化mootdx客户端"""
        if self._client is None:
            try:
                from mootdx.quotes import Quotes
                self._client = Quotes.factory(market='std')
                logger.info("mootdx客户端初始化成功")
            except Exception as e:
                logger.error(f"mootdx客户端初始化失败: {e}")
                raise
        return self._client

    def get_stock_list(self, exclude_st: bool = True,
                       exclude_cyb: bool = True,
                       exclude_kcb: bool = True,
                       exclude_bse: bool = True) -> List[str]:
        """
        获取A股股票列表

        从缓存文件名中提取所有可用股票代码，并按条件过滤。

        Args:
            exclude_st: 是否排除ST股
            exclude_cyb: 是否排除创业板(300/301)
            exclude_kcb: 是否排除科创板(688)
            exclude_bse: 是否排除北交所(920)

        Returns:
            股票代码列表，格式如 ['sh.600000', 'sz.000001', ...]
        """
        if self._stock_list_cache is not None:
            return self._stock_list_cache

        stock_list = []

        # 从缓存目录扫描所有pkl文件
        if self.cache_dir.exists():
            for f in sorted(self.cache_dir.glob("*.pkl")):
                # 文件名格式: sh_000001.pkl -> sh.000001
                stem = f.stem  # sh_000001
                parts = stem.split('_')
                if len(parts) == 2:
                    market, code_num = parts
                    full_code = f"{market}.{code_num}"
                else:
                    continue

                # 板块过滤
                if exclude_kcb or exclude_cyb or exclude_bse:
                    if _is_excluded_code(full_code):
                        continue

                stock_list.append(full_code)

        logger.info(f"从缓存获取到 {len(stock_list)} 只股票")
        self._stock_list_cache = stock_list
        return stock_list

    def _cache_path(self, code: str) -> Optional[Path]:
        """
        根据股票代码获取缓存文件路径

        Args:
            code: 股票代码，支持 'sh.600000' 或 'sh_600000' 格式

        Returns:
            缓存文件路径，不存在则返回None
        """
        # 统一转为 sh_600000 格式
        file_name = code.replace('.', '_') + '.pkl'
        path = self.cache_dir / file_name
        if path.exists():
            return path
        return None

    def get_daily(self, code: str, start_date: str = None,
                  end_date: str = None) -> Optional[pd.DataFrame]:
        """
        获取单只股票的日线数据

        优先从缓存加载，缓存不存在时尝试在线获取。

        Args:
            code: 股票代码，如 'sh.600000'
            start_date: 开始日期，格式 '2024-01-01'
            end_date: 结束日期，格式 '2024-12-31'

        Returns:
            DataFrame，包含OHLCV等字段，按日期升序排列
            列: open, close, high, low, vol, amount, volume, date, preclose, pctChg
        """
        df = None

        # 1. 尝试从缓存加载
        cache_path = self._cache_path(code)
        if cache_path:
            try:
                with open(cache_path, 'rb') as f:
                    df = pickle.load(f)
                logger.debug(f"从缓存加载 {code}, {len(df)} 条记录")
            except Exception as e:
                logger.warning(f"缓存加载失败 {code}: {e}")

        # 2. 缓存不存在时尝试在线获取
        if df is None and self.use_online:
            df = self._fetch_online(code)

        if df is None or df.empty:
            return None

        # 3. 数据清洗
        df = self._clean_data(df, code)

        # 4. 日期过滤
        if start_date:
            df = df[df['date'] >= start_date]
        if end_date:
            df = df[df['date'] <= end_date]

        # 5. 按日期排序并重置索引
        df = df.sort_values('date').reset_index(drop=True)

        return df

    def get_daily_batch(self, codes: List[str], start_date: str = None,
                        end_date: str = None,
                        exclude_st: bool = True) -> Dict[str, pd.DataFrame]:
        """
        批量获取多只股票的日线数据

        Args:
            codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            exclude_st: 是否排除ST股（需stock_list配合）

        Returns:
            {code: DataFrame} 字典
        """
        result = {}
        for code in codes:
            df = self.get_daily(code, start_date, end_date)
            if df is not None and not df.empty:
                # ST过滤（如果有name字段）
                if exclude_st and 'name' in df.columns:
                    if _is_st(df['name'].iloc[0]):
                        continue
                result[code] = df
        logger.info(f"批量获取完成，有效股票 {len(result)}/{len(codes)}")
        return result

    def _clean_data(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """
        数据清洗

        - 确保date列为字符串格式
        - 确保数值列为float类型
        - 处理缺失值
        - 添加code列

        Args:
            df: 原始DataFrame
            code: 股票代码

        Returns:
            清洗后的DataFrame
        """
        df = df.copy()

        # 确保date列存在且为字符串
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        elif 'datetime' in df.columns:
            df['date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')

        # 数值列类型转换
        numeric_cols = ['open', 'close', 'high', 'low', 'vol', 'amount',
                        'volume', 'preclose', 'pctChg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 确保有code列
        if 'code' not in df.columns:
            df['code'] = code

        # 去除全为NaN的行
        essential_cols = [c for c in ['open', 'close', 'high', 'low'] if c in df.columns]
        if essential_cols:
            df = df.dropna(subset=essential_cols, how='all')

        # 去除价格为0的异常数据
        if 'close' in df.columns:
            df = df[df['close'] > 0]

        return df

    def _fetch_online(self, code: str) -> Optional[pd.DataFrame]:
        """
        在线从通达信获取日线数据

        Args:
            code: 股票代码，如 'sh.600000'

        Returns:
            DataFrame或None
        """
        try:
            client = self._get_client()

            # 解析市场和代码
            parts = code.split('.')
            if len(parts) != 2:
                return None

            market_str, code_num = parts
            market = 1 if market_str == 'sh' else 0

            # mootdx获取日线
            df = client.bars(
                symbol=code_num,
                frequency=9,  # 日线
                offset=800    # 最近800个交易日
            )

            if df is not None and not df.empty:
                df['code'] = code
                # 缓存到本地
                self._save_cache(code, df)
                return df

        except Exception as e:
            logger.error(f"在线获取 {code} 失败: {e}")

        return None

    def _save_cache(self, code: str, df: pd.DataFrame):
        """保存数据到本地缓存"""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            file_name = code.replace('.', '_') + '.pkl'
            path = self.cache_dir / file_name
            with open(path, 'wb') as f:
                pickle.dump(df, f)
            logger.debug(f"缓存已保存: {path}")
        except Exception as e:
            logger.warning(f"缓存保存失败: {e}")

    def get_cache_info(self) -> dict:
        """
        获取缓存统计信息

        Returns:
            包含缓存文件数量、大小等信息的字典
        """
        if not self.cache_dir.exists():
            return {"exists": False}

        pkl_files = list(self.cache_dir.glob("*.pkl"))
        total_size = sum(f.stat().st_size for f in pkl_files)

        return {
            "exists": True,
            "path": str(self.cache_dir),
            "file_count": len(pkl_files),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
        }
