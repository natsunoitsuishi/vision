# domain/path_config.py
"""路径配置和摆轮机位置定义"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum


class PathType(Enum):
    """路径类型"""
    MAIN = "main"  # 主线
    BRANCH_1 = "branch_1"  # 分支1
    BRANCH_2 = "branch_2"  # 分支2
    BRANCH_3 = "branch_3"  # 分支3
    BRANCH_4 = "branch_4"  # 分支4
    REJECT = "reject"  # 合单机/异常处理线


class DivertStatus(Enum):
    """摆轮机状态"""
    STRAIGHT = "straight"  # 直行
    DIVERT = "divert"  # 转向
    RESTORING = "restoring"  # 恢复中


@dataclass
class DivertUnit:
    """摆轮机单元"""
    id: int  # 摆轮机ID (1-10)
    position_mm: float  # 距离扫码点的位置（毫米）
    path_type: PathType  # 对应路径类型
    divert_time_ms: int = 200  # 转向响应时间（毫秒）
    restore_time_ms: int = 200  # 恢复响应时间（毫秒）

    # 控制状态
    status: DivertStatus = DivertStatus.STRAIGHT
    current_box: Optional[str] = None  # 当前正在处理的鞋盒ID
    last_divert_time: float = 0.0  # 上次转向时间


@dataclass
class PathConfig:
    """路径配置"""
    path_id: int  # 路径编号 1-4
    path_type: PathType  # 路径类型
    length_mm: float  # 路径长度（毫米）
    divert_units: List[int]  # 该路径上的摆轮机ID列表
    destination: str  # 目的地

    # 速度参数
    speed_mm_s: float = 500.0  # 该路径上的速度

# 通过光电 1 2026-04-01 09:07:44.68
# 通过 2026-04-01 09:07:43.845

# 2026-04-01 09:10:17.949
# 2026-04-01 09:10:19.50

# 默认配置
DEFAULT_PATHS: Dict[int, PathConfig] = {
    1: PathConfig(
        path_id=1,
        path_type=PathType.BRANCH_1,
        length_mm=10000,  # 10米
        divert_units=[1, 2],
        destination="打包区1"
    ),
    2: PathConfig(
        path_id=2,
        path_type=PathType.BRANCH_2,
        length_mm=12000,  # 12米
        divert_units=[3, 4],
        destination="打包区2"
    ),
    3: PathConfig(
        path_id=3,
        path_type=PathType.BRANCH_3,
        length_mm=15000,  # 15米
        divert_units=[5, 6, 7],
        destination="打包区3"
    ),
    4: PathConfig(
        path_id=4,
        path_type=PathType.BRANCH_4,
        length_mm=18000,  # 18米
        divert_units=[8, 9, 10],
        destination="打包区4"
    ),
}

# 摆轮机位置配置（距离扫码点的距离）
DEFAULT_DIVERT_UNITS: Dict[int, DivertUnit] = {
    1: DivertUnit(id=1, position_mm=2000, path_type=PathType.BRANCH_1),
    2: DivertUnit(id=2, position_mm=4000, path_type=PathType.BRANCH_1),
    3: DivertUnit(id=3, position_mm=2000, path_type=PathType.BRANCH_2),
    4: DivertUnit(id=4, position_mm=4000, path_type=PathType.BRANCH_2),
    5: DivertUnit(id=5, position_mm=1500, path_type=PathType.BRANCH_3),
    6: DivertUnit(id=6, position_mm=3000, path_type=PathType.BRANCH_3),
    7: DivertUnit(id=7, position_mm=4500, path_type=PathType.BRANCH_3),
    8: DivertUnit(id=8, position_mm=1500, path_type=PathType.BRANCH_4),
    9: DivertUnit(id=9, position_mm=3000, path_type=PathType.BRANCH_4),
    10: DivertUnit(id=10, position_mm=4500, path_type=PathType.BRANCH_4),
}