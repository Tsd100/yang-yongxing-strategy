"""
杨永兴短线战法 - 核心筛选引擎
九步过滤法：
0. 大盘环境判断（放量大跌则空仓）
1. 排除ST/非主板（只留主板票）
2. 今日涨幅 1%-5%（盈利空间充足但不追高）
3. 近20天有涨停记录（主力活跃，有人气）
4. 量比 ≥ 1.2（有资金关注）
5. 流通市值 50-200亿（盘子适中易撬动）
6. 换手率 5%-10%（健康换手，非出货）
7. 振幅 ≤ 8%（波动适中）
8. K线上方无压力（无长上影线）
9. 分时全天站均价线上方（买方主导，主力护盘）
"""

import logging
import datetime
import pandas as pd
from config import (
    RISE_MIN, RISE_MAX, MARKET_CAP_MIN, MARKET_CAP_MAX,
    TURNOVER_MIN, TURNOVER_MAX, VOLUME_RATIO_MIN, AMPLITUDE_MAX,
    LIMIT_UP_DAYS, MAIN_BOARD_ONLY, STOP_LOSS, FORCE_STOP_LOSS,
)
from scan_params import ScanParams
import data_fetcher as df_api

logger = logging.getLogger(__name__)


def is_main_board(code):
    """判断是否为主板股票"""
    if not MAIN_BOARD_ONLY:
        return True
    # 排除: 科创板688, 北交所8/4/920开头, 创业板300/301
    if code.startswith("688"):  # 科创板
        return False
    if code.startswith(("8", "4", "920")):  # 北交所（82/83/87/88/920）
        return False
    if code.startswith(("300", "301")):  # 创业板
        return False
    return True


def is_st_stock(name):
    """判断是否为ST股票"""
    if not name:
        return False
    return "ST" in name or "*ST" in name


def _stocks_to_list(stocks_df):
    """将股票DataFrame转为简洁的dict列表，用于进度回传"""
    if stocks_df is None or stocks_df.empty:
        return []
    cols = ["code", "name", "price", "change_pct", "volume_ratio",
            "turnover_rate", "circ_mv_billion", "amplitude"]
    available = [c for c in cols if c in stocks_df.columns]
    records = stocks_df[available].head(300).copy()
    # 统一类型，避免JSON序列化问题
    for c in records.columns:
        if c == "code" or c == "name":
            records[c] = records[c].astype(str)
        else:
            records[c] = records[c].apply(
                lambda x: round(float(x), 2) if pd.notna(x) and x is not None else None
            )
    return records.to_dict(orient="records")


class Scanner:
    """杨永兴九步过滤选股引擎"""

    def __init__(self):
        self.filter_log = []  # 记录每步过滤情况
        self.limit_up_cache = {}  # 涨停记录缓存

    def log_filter(self, step, action, count_before, count_after, reason=""):
        self.filter_log.append({
            "step": step,
            "action": action,
            "count_before": count_before,
            "count_after": count_after,
            "filtered": count_before - count_after,
            "reason": reason,
        })

    def scan(self, skip_intraday=False, params=None, progress_callback=None):
        """
        执行完整九步筛选
        参数:
          skip_intraday: 跳过分时数据检查
          params: ScanParams 对象，为 None 时使用 config.py 默认值
          progress_callback: 进度回调 fn(step, label, before, after, stocks_df)
        返回: { candidates: list, market: dict, filter_log: list }
        """
        p = params if params is not None else ScanParams()
        self.filter_log = []

        def _report(step, label, before, after, stocks_df=None):
            self.log_filter(step, label, before, after)
            if progress_callback:
                stock_list = _stocks_to_list(stocks_df)
                progress_callback(step, label, before, after, stock_list)

        # ============ 步骤0：大盘环境判断 ============
        logger.info("===== 步骤0：大盘环境判断 =====")
        market_status = df_api.get_market_status()
        market_trend = df_api.get_market_trend()

        if market_status.get("is_crash"):
            logger.warning("⚠️ 大盘放量大跌，今日不操作！")
            if progress_callback:
                progress_callback(0, "大盘放量大跌，空仓", 0, 0, None)
            return {
                "candidates": [],
                "market": {**market_status, **market_trend},
                "filter_log": self.filter_log,
                "warning": "大盘放量大跌，按规则应空仓",
            }

        # ============ 步骤1：获取全市场行情 ============
        logger.info("===== 步骤1：获取全市场实时行情 =====")
        if progress_callback:
            progress_callback(1, "正在获取全市场实时行情...", 0, 0, None)
        all_stocks = df_api.get_realtime_quotes()
        if all_stocks.empty:
            logger.error("无法获取行情数据")
            if progress_callback:
                progress_callback(1, "行情获取失败", 0, 0, None)
            return {"candidates": [], "market": market_trend, "filter_log": self.filter_log}

        total = len(all_stocks)
        logger.info(f"全市场共 {total} 只股票")
        if progress_callback:
            progress_callback(1, f"获取全市场实时行情", total, total, None)

        if p.main_board_only:
            all_stocks["is_main"] = all_stocks["code"].apply(is_main_board)
            stocks = all_stocks[all_stocks["is_main"]].copy()
        else:
            stocks = all_stocks.copy()
        _report(1, "排除非主板/全板块", total, len(stocks), stocks)

        # ============ 步骤2：涨幅范围 ============
        logger.info(f"===== 步骤2：涨幅范围 {p.rise_min}%-{p.rise_max}% =====")
        before = len(stocks)
        stocks = stocks[
            (stocks["change_pct"] >= p.rise_min) & (stocks["change_pct"] <= p.rise_max)
        ].copy()
        _report(2, f"涨幅{p.rise_min}%-{p.rise_max}%", before, len(stocks), stocks)
        logger.info(f"剩余 {len(stocks)} 只")

        if stocks.empty:
            return self._build_result(stocks, market_trend)

        # ============ 步骤3：涨停基因 ============
        logger.info("===== 步骤3：涨停基因筛选 =====")
        if progress_callback:
            progress_callback(3, f"涨停基因筛选(近{p.limit_up_days}天)...", 0, 0, None)
        before = len(stocks)

        candidate_codes = stocks["code"].tolist() if "code" in stocks.columns else []
        self.limit_up_cache = df_api.get_limit_up_history(days=p.limit_up_days, target_codes=candidate_codes)
        limit_up_codes = set()
        for codes in self.limit_up_cache.values():
            limit_up_codes.update(codes)

        today_limit = df_api.get_limit_up_today()
        limit_up_codes.update(today_limit)

        stocks = stocks[stocks["code"].isin(limit_up_codes)].copy()
        _report(3, f"近{p.limit_up_days}天有涨停", before, len(stocks), stocks)
        logger.info(f"剩余 {len(stocks)} 只")

        if stocks.empty:
            return self._build_result(stocks, market_trend)

        # ============ 步骤4：量比 ============
        logger.info("===== 步骤4：量比筛选 =====")
        before = len(stocks)
        if "volume_ratio" in stocks.columns and stocks["volume_ratio"].notna().any():
            mask = (stocks["volume_ratio"] >= p.volume_ratio_min) | stocks["volume_ratio"].isna()
            stocks = stocks[mask].copy()
            _report(4, f"量比≥{p.volume_ratio_min}", before, len(stocks), stocks)
        else:
            _report(4, "量比数据缺失，跳过", before, len(stocks), stocks)
        logger.info(f"剩余 {len(stocks)} 只")

        if stocks.empty:
            return self._build_result(stocks, market_trend)

        # ============ 步骤5：流通市值 ============
        logger.info("===== 步骤5：流通市值筛选 =====")
        before = len(stocks)
        if "circ_mv_billion" in stocks.columns and stocks["circ_mv_billion"].notna().any():
            mask = (
                (stocks["circ_mv_billion"] >= p.market_cap_min) &
                (stocks["circ_mv_billion"] <= p.market_cap_max)
            ) | stocks["circ_mv_billion"].isna()
            stocks = stocks[mask].copy()
        _report(5, f"流通市值{p.market_cap_min}-{p.market_cap_max}亿", before, len(stocks), stocks)
        logger.info(f"剩余 {len(stocks)} 只")

        if stocks.empty:
            return self._build_result(stocks, market_trend)

        # ============ 步骤6：换手率 ============
        logger.info("===== 步骤6：换手率筛选 =====")
        before = len(stocks)
        if "turnover_rate" in stocks.columns and stocks["turnover_rate"].notna().any():
            mask = (
                (stocks["turnover_rate"] >= p.turnover_min) &
                (stocks["turnover_rate"] <= p.turnover_max)
            ) | stocks["turnover_rate"].isna()
            stocks = stocks[mask].copy()
            _report(6, f"换手率{p.turnover_min}-{p.turnover_max}%", before, len(stocks), stocks)
        else:
            _report(6, "换手率数据缺失，跳过", before, len(stocks), stocks)
        logger.info(f"剩余 {len(stocks)} 只")

        if stocks.empty:
            return self._build_result(stocks, market_trend)

        # ============ 步骤7：振幅 ============
        logger.info("===== 步骤7：成交量稳定放大筛选 =====")
        before = len(stocks)
        if "amplitude" in stocks.columns:
            stocks = stocks[stocks["amplitude"] <= p.amplitude_max].copy()
            _report(7, f"振幅≤{p.amplitude_max}%", before, len(stocks), stocks)
        else:
            _report(7, "振幅数据缺失，跳过", before, len(stocks), stocks)
        logger.info(f"剩余 {len(stocks)} 只")

        if stocks.empty:
            return self._build_result(stocks, market_trend)

        # ============ 步骤8：K线形态 ============
        logger.info("===== 步骤8：K线形态筛选 =====")
        before = len(stocks)
        stocks = self._filter_kline_pressure(stocks, shadow_threshold=p.kline_shadow_max)
        _report(8, f"K线上方无压力(上影线<{p.kline_shadow_max}%)", before, len(stocks), stocks)
        logger.info(f"剩余 {len(stocks)} 只")

        if stocks.empty:
            return self._build_result(stocks, market_trend)

        # ============ 步骤9：分时均价线 ============
        if not skip_intraday and not p.skip_intraday:
            logger.info("===== 步骤9：分时均价线筛选 =====")
            before = len(stocks)
            stocks = self._filter_intraday(stocks)
            _report(9, "分时站均价线上方", before, len(stocks), stocks)
            logger.info(f"剩余 {len(stocks)} 只")

        return self._build_result(stocks, market_trend)

    def _filter_kline_pressure(self, stocks, shadow_threshold=3.0):
        """K线形态过滤：高位长上影线剔除，阈值可配置"""
        result_codes = []
        threshold = shadow_threshold / 100.0  # 百分比转小数

        for _, row in stocks.iterrows():
            code = row["code"]
            try:
                kline = df_api.get_stock_kline(code, days=20)
                if kline.empty or len(kline) < 5:
                    result_codes.append(code)
                    continue

                recent = kline.head(5)
                has_long_shadow = False
                for _, krow in recent.iterrows():
                    if pd.notna(krow.get("high")) and pd.notna(krow.get("close")) and krow["close"] > 0:
                        upper_shadow = (krow["high"] - krow["close"]) / krow["close"]
                        if upper_shadow > threshold:
                            has_long_shadow = True
                            break

                if not has_long_shadow:
                    result_codes.append(code)

            except Exception:
                result_codes.append(code)

        return stocks[stocks["code"].isin(result_codes)].copy()

    def _filter_intraday(self, stocks):
        """分时数据过滤：全天站均价线上方"""
        result_codes = []

        for _, row in stocks.iterrows():
            code = row["code"]
            try:
                intraday = df_api.get_intraday_data(code)
                # 只保留明确站均价线上方的
                if intraday.get("above_avg") is True:
                    result_codes.append(code)
                elif intraday.get("above_avg") is None:
                    # 数据缺失时保留，但标注
                    result_codes.append(code)
                # above_avg=False 的剔除
            except Exception:
                result_codes.append(code)

        return stocks[stocks["code"].isin(result_codes)].copy()

    def _build_result(self, stocks, market_info):
        """构建结果"""
        candidates = []
        for _, row in stocks.iterrows():
            candidate = {
                "code": str(row.get("code", "")),
                "name": str(row.get("name", "")),
                "price": float(row.get("price", 0) or 0),
                "change_pct": float(row.get("change_pct", 0) or 0),
                "volume_ratio": float(row.get("volume_ratio", 0) or 0),
                "turnover_rate": float(row.get("turnover_rate", 0) or 0),
                "circ_mv_billion": float(row.get("circ_mv_billion", 0) or 0),
                "amplitude": float(row.get("amplitude", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
            }
            candidates.append(candidate)

        return {
            "candidates": candidates,
            "market": market_info,
            "filter_log": self.filter_log,
            "scan_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_candidates": len(candidates),
        }
