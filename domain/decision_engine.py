# # domain/decision_engine.py
# from typing import Optional, Dict, List, Set
# import logging
# import time
# from datetime import datetime
#
# from domain.models import BoxTrack, CameraResult
# from domain.enums import DecisionStatus
#
#
# class DecisionEngine:
#     """
#     决策引擎 - 适配设备模拟程序的联调需求
#
#     根据设备模拟程序接口协议（02_设备模拟接口协议说明.md）：
#     - 相机返回格式：{"type":"scan_result","camera_id":"CAM1","result":"OK","code":"QR-001","symbology":"QR","ts_ms":230}
#     - DO作为扫码使能，设备侧在预设发码时刻检查使能状态后发结果
#
#     决策规则（根据联调测试用例与预期结果.md）：
#     - 单一成功码：OK
#     - 无成功码：TIMEOUT（或NO_READ）
#     - 多个不同成功码：AMBIGUOUS
#
#     特殊场景处理：
#     - 连续来料：需要正确绑定每个鞋盒的结果
#     - 双边异码：AMBIGUOUS
#     - 超时NG：TIMEOUT
#     """
#
#     def __init__(self, config: dict = None):
#         self.config = config or {}
#         self.logger = logging.getLogger("decision_engine")
#
#         # 配置参数（根据联调操作手册建议）
#         self.timeout_threshold = config.get("timeout_threshold", 5.0)  # 超时阈值（秒）
#         self.require_both_cameras = config.get("require_both_cameras", False)  # 是否需要双相机（默认单相机即可）
#         self.scan_window_buffer_ms = config.get("scan_window_buffer_ms", 500)  # 扫描窗口缓冲（毫秒）
#
#         # 统计信息
#         self.stats = {
#             "total_decisions": 0,
#             "ok_count": 0,
#             "timeout_count": 0,
#             "ambiguous_count": 0,
#             "no_read_count": 0,
#             "fault_count": 0
#         }
#
#     def evaluate(self, track: BoxTrack) -> DecisionStatus:
#         """
#         评估轨迹的最终决策状态
#
#         根据设备模拟程序的业务场景：
#         1. 单鞋盒正常：双相机返回相同码 -> OK
#         2. 连续来料正常：每个鞋盒双相机返回相同码 -> OK
#         3. 双边异码：双相机返回不同码 -> AMBIGUOUS
#         4. 超时NG：双相机都返回NG -> TIMEOUT/NO_READ
#
#         Args:
#             track: 箱子轨迹对象
#
#         Returns:
#             决策状态
#         """
#         if not track:
#             self.logger.error("轨迹为空，无法评估")
#             self._update_stats(DecisionStatus.FAULT)
#             return DecisionStatus.FAULT
#
#         self.logger.debug(f"评估轨迹 {track.track_id}: "
#                           f"相机结果数={len(track.camera_results)}, "
#                           f"开始时间={track.start_ts}, "
#                           f"结束时间={track.end_ts}")
#
#         # 步骤1：检查设备异常
#         if self._has_device_fault(track):
#             self.logger.warning(f"轨迹 {track.track_id} 设备异常")
#             self._update_stats(DecisionStatus.FAULT)
#             return DecisionStatus.FAULT
#
#         # 步骤2：检查是否超时（根据联调用例4：双边超时NG）
#         if self._is_timeout(track):
#             self.logger.warning(f"轨迹 {track.track_id} 超时")
#             self._update_stats(DecisionStatus.TIMEOUT)
#             return DecisionStatus.TIMEOUT
#
#         # 步骤3：分析相机结果
#         status = self._analyze_camera_results(track)
#         self._update_stats(status)
#         return status
#
#     def _analyze_camera_results(self, track: BoxTrack) -> DecisionStatus:
#         """
#         分析相机结果（根据设备模拟程序协议）
#
#         设备模拟程序返回的CameraResult字段：
#         - result: "OK" 或 "NG"
#         - code: 码值（NG时为None）
#         - symbology: 码制
#         - ts_ms: 仿真时间戳（毫秒）
#         """
#
#         # 分离成功和失败的结果
#         success_results: Dict[int, CameraResult] = {}  # camera_id -> result
#         failed_cameras: List[int] = []  # 失败的相机ID
#         ng_cameras: List[int] = []  # 返回NG的相机
#
#         for result in track.camera_results:
#             # 根据设备模拟程序的result字段判断
#             if result.result == "OK" and result.code:
#                 success_results[result.camera_id] = result
#                 self.logger.debug(f"相机{result.camera_id} 成功: {result.code}")
#             elif result.result == "NG":
#                 ng_cameras.append(result.camera_id)
#                 self.logger.debug(f"相机{result.camera_id} 返回NG")
#             else:
#                 failed_cameras.append(result.camera_id)
#                 self.logger.debug(f"相机{result.camera_id} 失败: {result.result}")
#
#         success_count = len(success_results)
#         ng_count = len(ng_cameras)
#         fail_count = len(failed_cameras)
#         total_expected = 2  # 两个相机
#
#         self.logger.debug(f"轨迹 {track.track_id}: 成功={success_count}, NG={ng_count}, 失败={fail_count}")
#
#         # 场景1：双边超时NG（联调用例4）
#         # 两个相机都返回NG，且没有成功结果
#         if ng_count == total_expected and success_count == 0:
#             self.logger.info(f"轨迹 {track.track_id} 判定: TIMEOUT (双边NG)")
#             return DecisionStatus.TIMEOUT
#
#         # 场景2：无成功结果（NO_READ）
#         if success_count == 0:
#             if ng_count > 0 or fail_count > 0:
#                 self.logger.info(f"轨迹 {track.track_id} 判定: NO_READ (无成功码)")
#                 return DecisionStatus.NO_READ
#             else:
#                 # 既无成功也无失败？不应该发生
#                 self.logger.warning(f"轨迹 {track.track_id} 无任何结果")
#                 return DecisionStatus.FAULT
#
#         # 场景3：单相机成功（根据联调说明，单相机成功即判OK）
#         if success_count == 1:
#             camera_id, result = next(iter(success_results.items()))
#             self.logger.info(f"轨迹 {track.track_id} 判定: OK (单相机{camera_id}成功: {result.code})")
#             return DecisionStatus.OK
#
#         # 场景4：双相机成功
#         if success_count >= 2:
#             # 检查所有成功的码是否一致
#             codes = {result.code for result in success_results.values()}
#
#             if len(codes) == 1:
#                 # 双相机结果一致（联调用例1、2）
#                 code = codes.pop()
#                 self.logger.info(f"轨迹 {track.track_id} 判定: OK (双相机一致: {code})")
#                 return DecisionStatus.OK
#             else:
#                 # 双边异码（联调用例3）
#                 self.logger.warning(f"轨迹 {track.track_id} 判定: AMBIGUOUS (结果冲突: "
#                                     f"{ {cam_id: res.code for cam_id, res in success_results.items()} })")
#                 return DecisionStatus.AMBIGUOUS
#
#         # 默认返回FAULT
#         self.logger.error(f"轨迹 {track.track_id} 无法判定")
#         return DecisionStatus.FAULT
#
#     def _is_timeout(self, track: BoxTrack) -> bool:
#         """
#         检查是否超时
#
#         根据设备模拟程序场景：
#         - 超时NG场景：两个相机都返回NG
#         - 真实超时：在扫描窗口内未收到任何结果
#         """
#         # 如果已经有结果了，不算超时（即使结果是NG）
#         if track.camera_results:
#             # 检查是否所有结果都是NG（这是场景4，由_analyze_camera_results处理）
#             return False
#
#         # 检查是否超过扫描窗口
#         if track.scan_window_end_ts:
#             current_time = time.time()
#             elapsed = current_time - track.scan_window_end_ts
#
#             if elapsed > self.timeout_threshold:
#                 self.logger.debug(f"轨迹 {track.track_id} 超时: 已过{elapsed:.2f}秒 > {self.timeout_threshold}秒")
#                 return True
#
#         return False
#
#     def _has_device_fault(self, track: BoxTrack) -> bool:
#         """检查是否有设备异常"""
#         # 检查相机结果中是否有设备异常标记
#         for result in track.camera_results:
#             if hasattr(result, 'error_msg') and result.error_msg and "设备异常" in result.error_msg:
#                 return True
#
#         # 检查是否缺少相机连接
#         # 这里可以根据实际情况添加更多检测
#
#         return False
#
#     def _update_stats(self, status: DecisionStatus):
#         """更新统计信息"""
#         self.stats["total_decisions"] += 1
#
#         if status == DecisionStatus.OK:
#             self.stats["ok_count"] += 1
#         elif status == DecisionStatus.TIMEOUT:
#             self.stats["timeout_count"] += 1
#         elif status == DecisionStatus.AMBIGUOUS:
#             self.stats["ambiguous_count"] += 1
#         elif status == DecisionStatus.NO_READ:
#             self.stats["no_read_count"] += 1
#         elif status == DecisionStatus.FAULT:
#             self.stats["fault_count"] += 1
#
#     def evaluate_with_detail(self, track: BoxTrack) -> dict:
#         """
#         评估并返回详细信息（用于调试和日志）
#
#         根据联调测试用例，详细输出决策过程
#
#         Returns:
#             {
#                 "status": DecisionStatus,
#                 "success_results": dict,  # camera_id -> CameraResult
#                 "ng_cameras": list,       # 返回NG的相机
#                 "failed_cameras": list,   # 失败的相机
#                 "codes": dict,            # camera_id -> code
#                 "reason": str,            # 决策原因
#                 "timing_info": dict       # 时序信息
#             }
#         """
#         status = self.evaluate(track)
#
#         # 统计信息
#         success_results = {}
#         ng_cameras = []
#         failed_cameras = []
#         codes = {}
#
#         for result in track.camera_results:
#             if result.result == "OK" and result.code:
#                 success_results[result.camera_id] = result
#                 codes[result.camera_id] = result.code
#             elif result.result == "NG":
#                 ng_cameras.append(result.camera_id)
#             else:
#                 failed_cameras.append(result.camera_id)
#
#         # 生成原因说明
#         reason = self._generate_reason(status, success_results, ng_cameras, failed_cameras, track)
#
#         # 时序信息
#         timing_info = {
#             "start_ts": track.start_ts,
#             "end_ts": track.end_ts,
#             "scan_window_end_ts": track.scan_window_end_ts,
#             "duration_ms": (track.end_ts - track.start_ts) * 1000 if track.end_ts else None,
#             "camera_timestamps": {cam_id: res.ts_ms for cam_id, res in success_results.items()}
#         }
#
#         return {
#             "status": status,
#             "track_id": track.track_id,
#             "success_results": success_results,
#             "ng_cameras": ng_cameras,
#             "failed_cameras": failed_cameras,
#             "codes": codes,
#             "reason": reason,
#             "timing_info": timing_info,
#             "camera_count": len(track.camera_results)
#         }
#
#     def _generate_reason(self, status: DecisionStatus,
#                          success_results: Dict[int, CameraResult],
#                          ng_cameras: List[int],
#                          failed_cameras: List[int],
#                          track: BoxTrack) -> str:
#         """生成决策原因（用于调试和日志）"""
#
#         if status == DecisionStatus.OK:
#             if len(success_results) == 1:
#                 camera_id, result = next(iter(success_results.items()))
#                 return f"单相机{camera_id}成功: {result.code} (时间戳: {result.ts_ms}ms)"
#             else:
#                 codes = [res.code for res in success_results.values()]
#                 if len(set(codes)) == 1:
#                     return f"双相机一致: {codes[0]} (时间戳: {[res.ts_ms for res in success_results.values()]})"
#                 else:
#                     return f"双相机结果一致但码值相同: {codes[0]}"
#
#         elif status == DecisionStatus.TIMEOUT:
#             if ng_cameras:
#                 return f"超时: 相机{ng_cameras}返回NG (扫描窗口结束时间: {track.scan_window_end_ts})"
#             else:
#                 return f"超时: 扫描窗口内无结果 (阈值: {self.timeout_threshold}秒)"
#
#         elif status == DecisionStatus.AMBIGUOUS:
#             codes_info = {cam_id: res.code for cam_id, res in success_results.items()}
#             return f"结果冲突: {codes_info}"
#
#         elif status == DecisionStatus.NO_READ:
#             if ng_cameras:
#                 return f"无成功码: 相机{ng_cameras}返回NG"
#             elif failed_cameras:
#                 return f"无成功码: 相机{failed_cameras}失败"
#             else:
#                 return "无成功码: 无任何相机结果"
#
#         elif status == DecisionStatus.FAULT:
#             return "设备异常: 请检查相机连接和配置"
#
#         return "未知原因"
#
#     def get_statistics(self) -> dict:
#         """获取决策统计信息"""
#         return {
#             **self.stats,
#             "success_rate": self.stats["ok_count"] / self.stats["total_decisions"] if self.stats[
#                                                                                           "total_decisions"] > 0 else 0
#         }
#
#     def reset_statistics(self):
#         """重置统计信息"""
#         self.stats = {
#             "total_decisions": 0,
#             "ok_count": 0,
#             "timeout_count": 0,
#             "ambiguous_count": 0,
#             "no_read_count": 0,
#             "fault_count": 0
#         }
#
#
# # 测试代码
# if __name__ == "__main__":
#     import logging
#
#     logging.basicConfig(level=logging.INFO)
#
#     from domain.models import CameraResult, BoxTrack
#
#
#     # 创建测试数据
#     def create_test_track(track_id: str, results: List[CameraResult]):
#         track = BoxTrack(track_id=track_id)
#         track.start_ts = time.time()
#         track.end_ts = time.time() + 0.5
#         for result in results:
#             track.add_camera_result(result)
#         return track
#
#
#     # 测试用例1：单鞋盒正常
#     print("\n=== 测试用例1：单鞋盒正常 ===")
#     track1 = create_test_track("test_001", [
#         CameraResult(camera_id=1, result="OK", code="QR-001", symbology="QR", ts_ms=230),
#         CameraResult(camera_id=2, result="OK", code="QR-001", symbology="QR", ts_ms=250)
#     ])
#     engine = DecisionEngine()
#     result = engine.evaluate_with_detail(track1)
#     print(f"结果: {result['status']}")
#     print(f"原因: {result['reason']}")
#
#     # 测试用例2：双边异码
#     print("\n=== 测试用例2：双边异码 ===")
#     track2 = create_test_track("test_002", [
#         CameraResult(camera_id=1, result="OK", code="QR-LEFT-001", symbology="QR", ts_ms=230),
#         CameraResult(camera_id=2, result="OK", code="QR-RIGHT-999", symbology="QR", ts_ms=250)
#     ])
#     result = engine.evaluate_with_detail(track2)
#     print(f"结果: {result['status']}")
#     print(f"原因: {result['reason']}")
#
#     # 测试用例3：超时NG
#     print("\n=== 测试用例3：超时NG ===")
#     track3 = create_test_track("test_003", [
#         CameraResult(camera_id=1, result="NG", code=None, symbology=None, ts_ms=230),
#         CameraResult(camera_id=2, result="NG", code=None, symbology=None, ts_ms=250)
#     ])
#     result = engine.evaluate_with_detail(track3)
#     print(f"结果: {result['status']}")
#     print(f"原因: {result['reason']}")
#
#     # 测试用例4：单相机成功
#     print("\n=== 测试用例4：单相机成功 ===")
#     track4 = create_test_track("test_004", [
#         CameraResult(camera_id=1, result="OK", code="QR-001", symbology="QR", ts_ms=230),
#         CameraResult(camera_id=2, result="NG", code=None, symbology=None, ts_ms=250)
#     ])
#     result = engine.evaluate_with_detail(track4)
#     print(f"结果: {result['status']}")
#     print(f"原因: {result['reason']}")
#
#     # 测试用例5：连续来料正常（3个鞋盒）
#     print("\n=== 测试用例5：连续来料正常 ===")
#     for i in range(1, 4):
#         track = create_test_track(f"box_{i:03d}", [
#             CameraResult(camera_id=1, result="OK", code=f"QR-{i:03d}", symbology="QR", ts_ms=230 + i * 100),
#             CameraResult(camera_id=2, result="OK", code=f"QR-{i:03d}", symbology="QR", ts_ms=250 + i * 100)
#         ])
#         result = engine.evaluate_with_detail(track)
#         print(f"  {track.track_id}: {result['status']} - {result['reason']}")
#
#     # 打印统计信息
#     print(f"\n=== 统计信息 ===")
#     print(engine.get_statistics())




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
        successful_results = [r for r in track.camera_results if r.result == "OK"]
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