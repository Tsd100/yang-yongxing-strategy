"""
扫描参数数据类 + 策略预设
支持运行时参数注入，不修改 config.py 的默认值
"""
import os
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("scan_params")

# 自定义策略存储路径
_CUSTOM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_CUSTOM_PATH = os.path.join(_CUSTOM_DIR, "custom_presets.json")


@dataclass
class ScanParams:
    """杨永兴 + SEPA 全部可调参数"""

    # ==== 杨永兴技术面 ====
    rise_min: float = 1.0          # 涨幅下限 %
    rise_max: float = 5.0          # 涨幅上限 %
    market_cap_min: float = 50.0   # 流通市值下限 亿
    market_cap_max: float = 200.0  # 流通市值上限 亿
    turnover_min: float = 5.0      # 换手率下限 %
    turnover_max: float = 10.0     # 换手率上限 %
    volume_ratio_min: float = 1.2  # 量比下限
    amplitude_max: float = 8.0     # 振幅上限 %
    limit_up_days: int = 20        # 涨停回溯天数
    main_board_only: bool = True   # 仅主板
    kline_shadow_max: float = 3.0  # K线上影线阈值 %
    skip_intraday: bool = True     # 跳过分时检查

    # ==== SEPA 基本面 ====
    revenue_growth_min: float = 25.0    # 营收增长下限 %
    profit_growth_min: float = 30.0     # 净利增长下限 %
    roe_min: float = 10.0               # ROE 下限 %
    profit_cagr_min: float = 20.0        # 3年净利润CAGR下限 %
    ma_short: int = 50                   # 短期均线
    ma_long: int = 150                   # 长期均线
    vol_short: int = 10                  # 短期均量天数
    vol_long: int = 120                  # 长期均量天数
    near_52w_high: float = 0.85          # 接近52周高点比例
    vcp_range_max: float = 8.0           # VCP 10日振幅上限 %
    vcp_close_std_max: float = 2.0       # VCP 5日收盘稳定度上限 %
    listing_min_days: int = 365          # 上市最少天数
    skip_ma_check: bool = True           # 跳过均线量能检查

    # ==== 风险控制 ====
    stop_loss: float = 3.0               # 止损线 %
    force_stop_loss: float = 5.0         # 强制止损线 %

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})

    @classmethod
    def from_config(cls):
        """从 config.py 加载默认参数"""
        import config
        return cls(
            rise_min=config.RISE_MIN,
            rise_max=config.RISE_MAX,
            market_cap_min=config.MARKET_CAP_MIN,
            market_cap_max=config.MARKET_CAP_MAX,
            turnover_min=config.TURNOVER_MIN,
            turnover_max=config.TURNOVER_MAX,
            volume_ratio_min=config.VOLUME_RATIO_MIN,
            amplitude_max=config.AMPLITUDE_MAX,
            limit_up_days=config.LIMIT_UP_DAYS,
            main_board_only=config.MAIN_BOARD_ONLY,
            stop_loss=config.STOP_LOSS,
            force_stop_loss=config.FORCE_STOP_LOSS,
            revenue_growth_min=config.SEPA_REVENUE_GROWTH_MIN,
            profit_growth_min=config.SEPA_PROFIT_GROWTH_MIN,
            roe_min=config.SEPA_ROE_MIN,
            profit_cagr_min=config.SEPA_PROFIT_CAGR_MIN,
            ma_short=config.SEPA_MA_SHORT,
            ma_long=config.SEPA_MA_LONG,
            vol_short=config.SEPA_VOL_SHORT,
            vol_long=config.SEPA_VOL_LONG,
        )


# ==== 策略预设 ====

PRESETS = {
    "builtin": {
        "name": "内置默认策略",
        "description": "杨永兴+SEPA原始参数，技术面与基本面均衡",
        "params": ScanParams(),
    },
    "relaxed": {
        "name": "宽松策略",
        "description": "放宽各项阈值，捕捉更多候选，适合震荡市/猴市",
        "params": ScanParams(
            rise_min=0.5,
            rise_max=7.0,
            market_cap_min=30.0,
            market_cap_max=300.0,
            turnover_min=3.0,
            turnover_max=15.0,
            volume_ratio_min=1.0,
            amplitude_max=12.0,
            limit_up_days=30,
            main_board_only=False,
            kline_shadow_max=5.0,
            revenue_growth_min=15.0,
            profit_growth_min=20.0,
            roe_min=5.0,
            profit_cagr_min=10.0,
            near_52w_high=0.70,
            vcp_range_max=12.0,
            vcp_close_std_max=3.0,
            listing_min_days=180,
        ),
    },
    "strict": {
        "name": "严格策略",
        "description": "收紧各项阈值，只选最强标的，适合牛市主升浪",
        "params": ScanParams(
            rise_min=2.0,
            rise_max=4.0,
            market_cap_min=80.0,
            market_cap_max=150.0,
            turnover_min=6.0,
            turnover_max=8.0,
            volume_ratio_min=1.5,
            amplitude_max=5.0,
            limit_up_days=15,
            main_board_only=True,
            kline_shadow_max=2.0,
            revenue_growth_min=35.0,
            profit_growth_min=50.0,
            roe_min=15.0,
            profit_cagr_min=30.0,
            near_52w_high=0.90,
            vcp_range_max=5.0,
            vcp_close_std_max=1.5,
            listing_min_days=365,
        ),
    },
}


# ==== 自定义策略持久化 ====


def _load_custom_presets() -> dict:
    """加载自定义策略文件"""
    if not os.path.exists(_CUSTOM_PATH):
        return {}
    try:
        with open(_CUSTOM_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载自定义策略失败: {e}")
        return {}


def _save_custom_presets(data: dict):
    """保存自定义策略文件"""
    os.makedirs(_CUSTOM_DIR, exist_ok=True)
    try:
        with open(_CUSTOM_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存自定义策略失败: {e}")


def get_preset(name: str) -> Optional[ScanParams]:
    """获取预设策略参数（内置 + 自定义）"""
    preset = PRESETS.get(name)
    if preset:
        return preset["params"]
    custom = _load_custom_presets().get(name)
    if custom:
        return ScanParams.from_dict(custom.get("params", {}))
    return None


def list_presets():
    """列出所有预设策略（内置 + 自定义）"""
    result = [{"id": k, "name": v["name"], "description": v["description"], "source": "builtin"}
              for k, v in PRESETS.items()]
    for k, v in _load_custom_presets().items():
        result.append({
            "id": k,
            "name": v.get("name", k),
            "description": v.get("description", ""),
            "source": "custom",
        })
    return result


def save_custom_preset(preset_id: str, name: str, description: str, params: dict) -> dict:
    """保存自定义策略，返回保存后的摘要"""
    custom = _load_custom_presets()
    is_new = preset_id not in custom
    custom[preset_id] = {
        "name": name,
        "description": description,
        "params": params,
    }
    _save_custom_presets(custom)
    logger.info(f"自定义策略已{'新增' if is_new else '更新'}: {preset_id} ({name})")
    return {"ok": True, "id": preset_id, "name": name, "is_new": is_new}


def delete_custom_preset(preset_id: str) -> dict:
    """删除自定义策略"""
    custom = _load_custom_presets()
    if preset_id not in custom:
        return {"ok": False, "error": f"策略不存在: {preset_id}"}
    name = custom[preset_id].get("name", preset_id)
    del custom[preset_id]
    _save_custom_presets(custom)
    logger.info(f"自定义策略已删除: {preset_id} ({name})")
    return {"ok": True, "id": preset_id, "name": name}


def copy_custom_preset(source_id: str, new_id: str, new_name: str) -> dict:
    """复制策略（内置或自定义均可）"""
    # 先查内置
    preset = PRESETS.get(source_id)
    if preset:
        params = preset["params"].to_dict()
        description = preset.get("description", "")
    else:
        custom = _load_custom_presets().get(source_id)
        if not custom:
            return {"ok": False, "error": f"源策略不存在: {source_id}"}
        params = custom.get("params", {})
        description = custom.get("description", "")

    return save_custom_preset(new_id, new_name, description, params)
