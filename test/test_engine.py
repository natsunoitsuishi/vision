
# domain/decision_engine.py
"""
决策引擎 - 根据相机结果判定最终状态
"""
import logging
from typing import List

from .enums import DecisionStatus
from .models import BoxTrack


class DecisionEngine:
    """
    决策引擎 - 根据相机结果判定最终状态

    判定规则：
    1. OK：至少一个相机读到码且无冲突
    2. NO_READ：所有相机都未读到码
    3. AMBIGUOUS：多个相机读到不同的码
    4. TIMEOUT：超时未返回
    5. FAULT：设备异常导致不可判
    """

    def __init__(self, config: dict = None):
        """
        初始化决策引擎

        Args:
            config: 配置字典
                - require_all_cameras: 是否要求所有相机都成功
                - ok_condition: "any_success" 或 "all_success"
                - conflict_strategy: "first" 或 "ambiguous"
        """
        self.config = config or {}
        self._require_all = self.config.get("require_all_cameras", False)
        self._ok_condition = self.config.get("ok_condition", "any_success")
        self._conflict_strategy = self.config.get("conflict_strategy", "ambiguous")
        self._logger = logging.getLogger(__name__)

        # 统计信息
        self._stats = {
            DecisionStatus.OK: 0,
            DecisionStatus.NO_READ: 0,
            DecisionStatus.AMBIGUOUS: 0,
            DecisionStatus.TIMEOUT: 0,
            DecisionStatus.FAULT: 0
        }

    def evaluate(self, track: BoxTrack) -> DecisionStatus:
        """
        评估轨迹的最终状态

        Args:
            track: 轨迹对象

        Returns:
            最终决策状态
        """
        # 检查是否已经超时
        if track.status.value == "EXPIRED" or track.final_status == DecisionStatus.TIMEOUT:
            self._stats[DecisionStatus.TIMEOUT] += 1
            return DecisionStatus.TIMEOUT

        # 检查是否有设备故障标记
        if "DEVICE_FAULT" in track.alarm_codes:
            self._stats[DecisionStatus.FAULT] += 1
            return DecisionStatus.FAULT

        # 获取所有成功的结果
        successful_results = [r for r in track.camera_results if r.result == "OK"]

        # 情况1：没有任何成功的结果
        if not successful_results:
            self._stats[DecisionStatus.NO_READ] += 1
            return DecisionStatus.NO_READ

        # 收集所有成功结果的码值
        codes = [r.code for r in successful_results if r.code]
        unique_codes = list(set(codes))

        # 情况2：有成功结果，检查是否冲突
        if len(unique_codes) == 1:
            # 只有一个码值，成功
            self._stats[DecisionStatus.OK] += 1
            return DecisionStatus.OK
        else:
            # 多个不同的码值，冲突
            self._logger.warning(f"码值冲突: track={track.track_id}, codes={unique_codes}")

            if self._conflict_strategy == "first":
                # 使用第一个结果
                self._stats[DecisionStatus.OK] += 1
                return DecisionStatus.OK
            else:
                # 标记为歧义
                self._stats[DecisionStatus.AMBIGUOUS] += 1
                return DecisionStatus.AMBIGUOUS

    def evaluate_with_strategy(self, track: BoxTrack, strategy: str) -> DecisionStatus:
        """
        使用指定策略评估轨迹

        Args:
            track: 轨迹对象
            strategy: 策略名称
                - "conservative": 保守策略，有冲突则判 NG
                - "aggressive": 激进策略，有任何一个成功就判 OK
                - "balanced": 平衡策略，多数一致则 OK

        Returns:
            最终决策状态
        """
        successful_results = [r for r in track.camera_results if r.result == "OK"]

        if not successful_results:
            return DecisionStatus.NO_READ

        codes = [r.code for r in successful_results if r.code]
        unique_codes = list(set(codes))

        if strategy == "conservative":
            # 保守策略：必须所有相机结果一致
            if len(unique_codes) == 1:
                return DecisionStatus.OK
            else:
                return DecisionStatus.AMBIGUOUS

        elif strategy == "aggressive":
            # 激进策略：有任何成功就 OK
            return DecisionStatus.OK

        elif strategy == "balanced":
            # 平衡策略：多数一致则 OK
            if len(unique_codes) == 1:
                return DecisionStatus.OK
            elif len(successful_results) >= 2:
                # 有多个成功但码不同，按数量判断
                code_counts = {}
                for r in successful_results:
                    if r.code:
                        code_counts[r.code] = code_counts.get(r.code, 0) + 1

                max_count = max(code_counts.values())
                if max_count >= 2:  # 至少有2个相同
                    return DecisionStatus.OK
                else:
                    return DecisionStatus.AMBIGUOUS
            else:
                return DecisionStatus.AMBIGUOUS
        else:
            # 默认使用基础策略
            return self.evaluate(track)

    def evaluate_batch(self, tracks: List[BoxTrack]) -> List[DecisionStatus]:
        """
        批量评估轨迹

        Args:
            tracks: 轨迹列表

        Returns:
            决策状态列表
        """
        return [self.evaluate(track) for track in tracks]

    def get_recommendation(self, track: BoxTrack) -> dict:
        """
        获取决策建议和原因

        Args:
            track: 轨迹对象

        Returns:
            包含状态和原因的字典
        """
        status = self.evaluate(track)

        reasons = []
        successful_results = [r for r in track.camera_results if r.result == "OK"]

        if not successful_results:
            reasons.append("所有相机均未读到码")
        else:
            codes = [r.code for r in successful_results if r.code]
            unique_codes = list(set(codes))

            if len(unique_codes) == 1:
                reasons.append(f"成功读到码: {unique_codes[0]}")
            else:
                reasons.append(f"码值冲突: {unique_codes}")

        if "DEVICE_FAULT" in track.alarm_codes:
            reasons.append("设备故障")

        return {
            "status": status,
            "status_value": status.value,
            "reasons": reasons,
            "success_count": len(successful_results),
            "total_count": len(track.camera_results),
            "codes": list(set([r.code for r in successful_results if r.code]))
        }

    def get_stats(self) -> dict:
        """获取决策统计信息"""
        total = sum(self._stats.values())
        return {
            "total": total,
            "breakdown": {
                status.value: count
                for status, count in self._stats.items()
            },
            "ok_rate": self._stats[DecisionStatus.OK] / total if total > 0 else 0
        }

    def reset_stats(self) -> None:
        """重置统计信息"""
        for status in self._stats:
            self._stats[status] = 0


# domain/decision_engine.py (高级版本 - 支持自定义规则)
class AdvancedDecisionEngine(DecisionEngine):
    """
    高级决策引擎 - 支持自定义规则和机器学习
    """

    def __init__(self, config: dict = None, rules: List[dict] = None):
        super().__init__(config)
        self.rules = rules or []
        self._load_default_rules()

    def _load_default_rules(self):
        """加载默认规则"""
        if not self.rules:
            self.rules = [
                {
                    "name": "timeout_rule",
                    "condition": "track.status == EXPIRED",
                    "result": DecisionStatus.TIMEOUT
                },
                {
                    "name": "fault_rule",
                    "condition": "'DEVICE_FAULT' in track.alarm_codes",
                    "result": DecisionStatus.FAULT
                },
                {
                    "name": "no_read_rule",
                    "condition": "len(successful_results) == 0",
                    "result": DecisionStatus.NO_READ
                },
                {
                    "name": "single_code_rule",
                    "condition": "len(unique_codes) == 1",
                    "result": DecisionStatus.OK
                },
                {
                    "name": "conflict_rule",
                    "condition": "len(unique_codes) > 1",
                    "result": DecisionStatus.AMBIGUOUS
                }
            ]

    def evaluate_with_rules(self, track: BoxTrack) -> DecisionStatus:
        """
        使用规则引擎评估轨迹

        Args:
            track: 轨迹对象

        Returns:
            最终决策状态
        """
        successful_results = [r for r in track.camera_results if r.result == "TRUE"]
        unique_codes = list(set([r.code for r in successful_results if r.code]))

        # 按顺序评估规则
        for rule in self.rules:
            # 简化版规则评估（实际可用表达式引擎）
            condition = rule.get("condition", "")

            if condition == "track.status == EXPIRED":
                if track.status.value == "EXPIRED":
                    return rule["result"]

            elif condition == "'DEVICE_FAULT' in track.alarm_codes":
                if "DEVICE_FAULT" in track.alarm_codes:
                    return rule["result"]

            elif condition == "len(successful_results) == 0":
                if len(successful_results) == 0:
                    return rule["result"]

            elif condition == "len(unique_codes) == 1":
                if len(unique_codes) == 1:
                    return rule["result"]

            elif condition == "len(unique_codes) > 1":
                if len(unique_codes) > 1:
                    return rule["result"]

        # 默认返回 NO_READ
        return DecisionStatus.NO_READ

    def add_rule(self, rule: dict) -> None:
        """添加自定义规则"""
        self.rules.append(rule)
        self._logger.info(f"添加规则: {rule.get('name', 'unnamed')}")

    def remove_rule(self, rule_name: str) -> bool:
        """移除规则"""
        for i, rule in enumerate(self.rules):
            if rule.get("name") == rule_name:
                self.rules.pop(i)
                self._logger.info(f"移除规则: {rule_name}")
                return True
        return False