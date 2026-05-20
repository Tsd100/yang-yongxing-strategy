"""
杨永兴战法 + SEPA策略 联合扫描引擎
先杨永兴九步技术面筛选，再用SEPA基本面验证（顺序不可颠倒）

流程：
  第一阶段 - 杨永兴九步技术面筛选（快速缩小范围）：
    0. 大盘环境判断（放量大跌则空仓）
    1. 排除ST/非主板（只留主板票）
    2. 今日涨幅 1%-5%（盈利空间充足但不追高）
    3. 近20天有涨停记录（主力活跃，有人气）
    4. 量比 ≥ 1.2（有资金关注）
    5. 流通市值 50-200亿（盘子适中易撬动）
    6. 换手率 5%-10%（健康换手，非出货）
    7. 振幅 ≤ 8%（波动适中）
    8. K线上方无压力（无长上影线）
    9. 分时站均价线上方（买方主导，主力护盘）

  第二阶段 - SEPA基本面验证（对杨永兴候选股做精准财务验证，共9步）：
    1. 剔除ST和上市不满1年的次新股
    2. 营收同比增长 > 25%（超级增长门槛）
    3. 净利润同比增长 > 30%，且近2-3季度逐季提升（EPS加速）
    4. ROE > 17%（盈利效率高，SEPA原文优秀标准）
    5. 近3年净利润CAGR > 20%（业绩持续性强）
    6. 股价在50日/150日均线之上（中长期上升趋势）
    7. 近10日均量 > 120日均量（机构资金入场）
    8. 股价接近52周前期高点（>85%，同花顺创新高数据）
    9. VCP紧凑收盘（10日振幅<8% + 5日收盘价稳定，VCP代理指标）

  数据源：A股实时行情使用腾讯财经接口
"""

import logging
import datetime
import math
import pandas as pd
import numpy as np
from sepa_filter import SEPAFilter
from scanner import Scanner, is_st_stock
from scan_params import ScanParams
import data_fetcher as df_api
from openviking_adapter import OpenVikingAdapter

logger = logging.getLogger(__name__)


class CombinedScanner:
    """SEPA + 杨永兴 联合扫描引擎（集成OpenViking上下文管理）"""

    def __init__(self, openviking: OpenVikingAdapter = None):
        self.filter_log = []
        self.sepa_filter = SEPAFilter()
        self.scanner = Scanner()
        self.ov = openviking or OpenVikingAdapter()

    def log_filter(self, phase, step, action, count_before, count_after, reason=""):
        self.filter_log.append({
            "phase": phase,
            "step": step,
            "action": action,
            "count_before": count_before,
            "count_after": count_after,
            "filtered": count_before - count_after,
            "reason": reason,
        })

    def scan(self, params=None, progress_callback=None):
        """
        执行杨永兴+SEPA联合扫描（顺序：杨永兴先 → SEPA后）

        参数:
          params: ScanParams 对象，为 None 时使用默认值
          progress_callback: 进度回调 fn(phase, step, label, before, after)

        返回: {
            yang_candidates: list,      # 杨永兴技术面通过的候选股
            final_candidates: list,    # 双战法同时通过的最终候选
            filter_log: list,           # 完整筛选日志
            market: dict,               # 大盘环境
            scan_time: str,
            strategy: str,
        }
        """
        p = params if params is not None else ScanParams()
        self.filter_log = []
        yang_candidates = []

        # ============ 第一阶段：杨永兴九步技术面筛选（快速缩小范围）============
        logger.info("=" * 60)
        logger.info("第一阶段：杨永兴九步技术面筛选")
        logger.info("=" * 60)

        def _yang_progress(step, label, before, after, stocks_list=None):
            self.filter_log.append({
                "phase": "杨永兴",
                "step": step,
                "action": label,
                "count_before": before,
                "count_after": after,
                "filtered": before - after,
                "reason": "",
            })
            if progress_callback:
                progress_callback("杨永兴", step, label, before, after, stocks_list)

        yang_result = self.scanner.scan(params=p, progress_callback=_yang_progress)
        yang_candidates = yang_result.get("candidates", [])
        yang_codes = set(c["code"] for c in yang_candidates)

        logger.info(f"杨永兴筛选完成: {len(yang_candidates)} 只候选股")

        if not yang_candidates:
            logger.warning("杨永兴无候选股，联合扫描结束")
            return self._build_result([], [], {}, {}, "杨永兴筛选无候选股")

        # ============ 大盘环境判断（放量大跌则直接结束）============
        market_status = yang_result.get("market", {})
        market_trend = df_api.get_market_trend()

        if market_status.get("is_crash"):
            logger.warning("大盘放量大跌，按杨永兴战法应空仓！")
            return self._build_result([], yang_candidates, market_trend, market_status, "大盘放量大跌，空仓")

        # ============ 第二阶段：SEPA基本面验证（对杨永兴候选股做精准验证）============
        logger.info("=" * 60)
        logger.info("第二阶段：SEPA基本面验证（9步筛选：1剔除ST次新 + 2营收>25% + 3EPS加速+净利>30% + 4ROE>17% + 5三年CAGR>20% + 6均线 + 7放量 + 8接近52周高点 + 9VCP紧凑收盘）")
        logger.info("=" * 60)

        # SEPA只对杨永兴候选股进行财务验证
        def _sepa_progress(step, label, before, after, stocks_list=None):
            self.filter_log.append({
                "phase": "SEPA",
                "step": step,
                "action": label,
                "count_before": before,
                "count_after": after,
                "filtered": before - after,
            })
            if progress_callback:
                progress_callback("SEPA", step, label, before, after, stocks_list)

        # 用杨永兴候选股构建 DataFrame，避免重复调用行情API
        yang_df = pd.DataFrame(yang_candidates)
        sepa_result = self.sepa_filter.scan(
            target_codes=list(yang_codes),
            params=p,
            progress_callback=_sepa_progress,
            target_stocks=yang_df,
        )

        sepa_candidates = sepa_result.get("candidates", [])
        logger.info(f"SEPA验证完成: {len(sepa_candidates)} 只候选股通过")

        # 最终结果：杨永兴 + SEPA 同时通过
        final_codes = set(c["code"] for c in sepa_candidates)
        final_candidates = [c for c in yang_candidates if c["code"] in final_codes]

        # 补充SEPA财务数据到最终候选
        financial_cache = self.sepa_filter._financial_cache
        for c in final_candidates:
            fin = financial_cache.get(c["code"], {})
            c["revenue_growth_yoy"] = fin.get("revenue_growth_yoy")
            c["profit_growth_yoy"] = fin.get("profit_growth_yoy")
            c["roe"] = fin.get("roe")

        logger.info(f"双战法同时通过: {len(final_candidates)} 只")

        result = self._build_result(
            final_candidates, yang_candidates, market_trend, market_status
        )

        # ============ OpenViking 上下文同步 ============
        if self.ov.available:
            self.ov.sync_scan_result(result)
            logger.info("📋 扫描结果已同步到OpenViking上下文数据库")

        return result

    def _build_result(self, final_candidates, yang_candidates,
                      market=None, market_status=None, warning=""):
        """构建最终返回结果"""
        return {
            "yang_candidates": yang_candidates,
            "final_candidates": final_candidates,
            "filter_log": self.filter_log,
            "market": market or {},
            "market_status": market_status or {},
            "scan_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_final": len(final_candidates),
            "total_yang": len(yang_candidates),
            "strategy": "杨永兴+SEPA",
            "warning": warning or "",
        }