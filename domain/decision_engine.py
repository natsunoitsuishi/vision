# domain/decision_engine.py
import time
from typing import Dict, List

from config.manager import get_config
from domain.enums import DecisionStatus
from domain.models import BoxTrack, CameraResult
from infra import get_logger


def _has_device_fault(track_data: BoxTrack) -> bool:
    """检查是否有设备异常"""
    # 检查相机结果中是否有设备异常标记
    for result_data in track_data.camera_results:
        if hasattr(result_data, 'error_msg') and result_data.error_msg and "设备异常" in result_data.error_msg:
            return True
    # 检查是否缺少相机连接
    # 这里可以根据实际情况添加更多检测
    return False


class DecisionEngine:
    """
    决策引擎 - 适配设备模拟程序的联调需求

    根据设备模拟程序接口协议（02_设备模拟接口协议说明.md）：
    - 相机返回格式：{"type":"scan_result","camera_id":"CAM1","result":"OK","code":"QR-001","symbology":"QR","ts_ms":230}
    - DO作为扫码使能，设备侧在预设发码时刻检查使能状态后发结果

    决策规则（根据联调测试用例与预期结果.md）：
    - 单一成功码：OK
    - 无成功码：TIMEOUT（或NO_READ）
    - 多个不同成功码：AMBIGUOUS

    特殊场景处理：
    - 连续来料：需要正确绑定每个鞋盒的结果
    - 双边异码：AMBIGUOUS
    - 超时NG：TIMEOUT
    """

    def __init__(self, config: dict = get_config()):
        self.config = config or {}
        self.logger = get_logger(__name__)

        # 配置参数（根据联调操作手册建议）
        self.timeout_threshold = config.get("timeout_threshold", 5.0)  # 超时阈值（秒）
        self.require_both_cameras = config.get("require_both_cameras", False)  # 是否需要双相机（默认单相机即可）
        self.scan_window_buffer_ms = config.get("scan_window_buffer_ms", 500)  # 扫描窗口缓冲（毫秒）

        # 统计信息
        self.stats = {
            "total_decisions": 0,
            "ok_count": 0,
            "timeout_count": 0,
            "ambiguous_count": 0,
            "no_read_count": 0,
            "fault_count": 0
        }

    def evaluate(self, track_data: BoxTrack) -> DecisionStatus:
        """
        评估轨迹的最终决策状态

        根据设备模拟程序的业务场景：
        1. 单鞋盒正常：双相机返回相同码 -> OK
        2. 连续来料正常：每个鞋盒双相机返回相同码 -> OK
        3. 双边异码：双相机返回不同码 -> AMBIGUOUS
        4. 超时NG：双相机都返回NG -> TIMEOUT/NO_READ

        Args:
            track_data: 箱子轨迹对象

        Returns:
            决策状态
        """

        if not track_data:
            self.logger.error("轨迹为空，无法评估")
            self._update_stats(DecisionStatus.FAULT)
            return DecisionStatus.FAULT

        self.logger.info(f"评估轨迹 {track_data.track_id}: "
                          f"相机结果数={len(track_data.camera_results)}, "
                          f"创建时间={track_data.created_ms:.3f}, ")

        # 步骤1：检查设备异常
        if _has_device_fault(track_data):
            self.logger.warning(f"轨迹 {track_data.track_id} 设备异常")
            self._update_stats(DecisionStatus.FAULT)
            return DecisionStatus.FAULT

        # 步骤2：检查是否超时（根据联调用例4：双边超时NG）
        if self._is_timeout(track_data):
            self.logger.warning(f"轨迹 {track_data.track_id} 超时")
            self._update_stats(DecisionStatus.TIMEOUT)
            return DecisionStatus.TIMEOUT

        # 步骤3：分析相机结果
        status = self._analyze_camera_results(track_data)
        self._update_stats(status)
        return status

    def _analyze_camera_results(self, track_data: BoxTrack) -> DecisionStatus:
        """
        分析相机结果（根据设备模拟程序协议）

        设备模拟程序返回的CameraResult字段：
        - result: "OK" 或 "NG"
        - code: 码值（NG时为None）
        - symbology: 码制
        - ts_ms: 仿真时间戳（毫秒）
        """

        # 分离成功和失败的结果
        success_results: Dict[int, CameraResult] = {}  # camera_id -> result
        failed_cameras: List[int] = []  # 失败的相机ID
        ng_cameras: List[int] = []  # 返回NG的相机

        for result_data in track_data.camera_results:
            # 根据设备模拟程序的result字段判断
            if result_data.result == "TRUE" and result_data.code:
                success_results[result_data.camera_id] = result_data
                self.logger.debug(f"相机{result_data.camera_id} 成功: {result_data.code}")
            elif result_data.result == "FALSE":
                ng_cameras.append(result_data.camera_id)
                self.logger.debug(f"相机{result_data.camera_id} 返回NG")
            else:
                failed_cameras.append(result_data.camera_id)
                self.logger.debug(f"相机{result_data.camera_id} 失败: {result_data.result}")

        success_count = len(success_results)
        ng_count = len(ng_cameras)
        fail_count = len(failed_cameras)
        total_expected = 2  # 两个相机

        self.logger.debug(f"轨迹 {track_data.track_id}: 成功={success_count}, NG={ng_count}, 失败={fail_count}")

        # 场景1：双边超时NG（联调用例4）
        # 两个相机都返回NG，且没有成功结果
        if ng_count == total_expected and success_count == 0:
            self.logger.info(f"轨迹 {track_data.track_id} 判定: TIMEOUT (双边NG)")
            return DecisionStatus.TIMEOUT

        # 场景2：无成功结果（NO_READ）
        if success_count == 0:
            if ng_count > 0 or fail_count > 0:
                self.logger.info(f"轨迹 {track_data.track_id} 判定: NO_READ (无成功码)")
                return DecisionStatus.NO_READ
            else:
                # 既无成功也无失败？不应该发生
                self.logger.warning(f"轨迹 {track_data.track_id} 无任何结果")
                return DecisionStatus.FAULT

        # 场景3：单相机成功（根据联调说明，单相机成功即判OK）
        if success_count == 1:
            camera_id, result_data = next(iter(success_results.items()))
            self.logger.info(f"轨迹 {track_data.track_id} 判定: OK (单相机{camera_id}成功: {result_data.code})")
            return DecisionStatus.OK

        # 场景4：双相机成功
        if success_count >= 2:
            # 检查所有成功的码是否一致
            codes = {result.code for result in success_results.values()}

            if len(codes) == 1:
                # 双相机结果一致（联调用例1、2）
                code = codes.pop()
                self.logger.info(f"轨迹 {track_data.track_id} 判定: OK (双相机一致: {code})")
                return DecisionStatus.OK
            else:
                # 双边异码（联调用例3）
                self.logger.warning(f"轨迹 {track_data.track_id} 判定: AMBIGUOUS (结果冲突: "
                                    f"{ {cam_id: res.code for cam_id, res in success_results.items()} })")
                return DecisionStatus.AMBIGUOUS

        # 默认返回FAULT
        self.logger.error(f"轨迹 {track_data.track_id} 无法判定")
        return DecisionStatus.FAULT

    def _is_timeout(self, track_data: BoxTrack) -> bool:
        """
        检查是否超时

        根据设备模拟程序场景：
        - 超时NG场景：两个相机都返回NG
        - 真实超时：在扫描窗口内未收到任何结果
        """
        # 如果已经有结果了，不算超时（即使结果是NG）
        if track_data.camera_results:
            # 检查是否所有结果都是NG（这是场景4，由_analyze_camera_results处理）
            return False

        # 检查是否超过扫描窗口
        if track_data.scan_window_end_ms:
            current_time = time.time()
            elapsed = current_time - track_data.scan_window_end_ms

            if elapsed > self.timeout_threshold:
                self.logger.debug(f"轨迹 {track_data.track_id} 超时: 已过{elapsed:.2f}秒 > {self.timeout_threshold}秒")
                return True

        return False

    def _update_stats(self, status: DecisionStatus):
        """更新统计信息"""
        self.stats["total_decisions"] += 1

        if status == DecisionStatus.OK:
            self.stats["ok_count"] += 1
        elif status == DecisionStatus.TIMEOUT:
            self.stats["timeout_count"] += 1
        elif status == DecisionStatus.AMBIGUOUS:
            self.stats["ambiguous_count"] += 1
        elif status == DecisionStatus.NO_READ:
            self.stats["no_read_count"] += 1
        elif status == DecisionStatus.FAULT:
            self.stats["fault_count"] += 1

    def evaluate_with_detail(self, track_data: BoxTrack) -> dict:
        """
        评估并返回详细信息（用于调试和日志）

        根据联调测试用例，详细输出决策过程

        Returns:
            {
                "status": DecisionStatus,
                "success_results": dict,  # camera_id -> CameraResult
                "ng_cameras": list,       # 返回NG的相机
                "failed_cameras": list,   # 失败的相机
                "codes": dict,            # camera_id -> code
                "reason": str,            # 决策原因
                "timing_info": dict       # 时序信息
            }
        """
        status = self.evaluate(track_data)

        # 统计信息
        success_results = {}
        ng_cameras = []
        failed_cameras = []
        codes = {}

        for result_data in track_data.camera_results:
            if result_data.result == "TRUE" and result_data.code:
                success_results[result_data.camera_id] = result_data
                codes[result_data.camera_id] = result_data.code
            elif result_data.result == "FALSE":
                ng_cameras.append(result_data.camera_id)
            else:
                failed_cameras.append(result_data.camera_id)

        # 生成原因说明
        reason = self._generate_reason(status, success_results, ng_cameras, failed_cameras, track_data)

        # 时序信息
        timing_info = {
            "created_ms": track_data.created_ms,  # 创建时间
            "pe1_on_ms": track_data.pe1_on_ms,  # PE1触发时间
            "pe2_on_ms": track_data.pe2_on_ms,  # PE2触发时间
            "scan_window_start_ms": track_data.scan_window_start_ms,  # 窗口开始
            "scan_window_end_ms": track_data.scan_window_end_ms,  # 窗口结束
            "first_ok_ms": track_data.first_ok_ms,  # 首次成功时间
            "camera_timestamps": {cam_id: res.ts_ms for cam_id, res in success_results.items()}
        }

        return {
            "status": status,
            "track_id": track_data.track_id,
            "success_results": success_results,
            "ng_cameras": ng_cameras,
            "failed_cameras": failed_cameras,
            "codes": codes,
            "reason": reason,
            "timing_info": timing_info,
            "camera_count": len(track_data.camera_results)
        }

    def _generate_reason(self, status: DecisionStatus,
                         success_results: Dict[int, CameraResult],
                         ng_cameras: List[int],
                         failed_cameras: List[int],
                         track_data: BoxTrack) -> str:
        """生成决策原因（用于调试和日志）"""

        if status == DecisionStatus.OK:
            if len(success_results) == 1:
                camera_id, result = next(iter(success_results.items()))
                return f"单相机{camera_id}成功: {result.code} (时间戳: {result.ts_ms}ms)"
            else:
                codes = [res.code for res in success_results.values()]
                if len(set(codes)) == 1:
                    return f"双相机一致: {codes[0]} (时间戳: {[res.ts_ms for res in success_results.values()]})"
                else:
                    return f"双相机结果一致但码值相同: {codes[0]}"

        elif status == DecisionStatus.TIMEOUT:
            if ng_cameras:
                return f"超时: 相机{ng_cameras}返回NG (扫描窗口结束时间: {track_data.scan_window_end_ms})"
            else:
                return f"超时: 扫描窗口内无结果 (阈值: {self.timeout_threshold}秒)"

        elif status == DecisionStatus.AMBIGUOUS:
            codes_info = {cam_id: res.code for cam_id, res in success_results.items()}
            return f"结果冲突: {codes_info}"

        elif status == DecisionStatus.NO_READ:
            if ng_cameras:
                return f"无成功码: 相机{ng_cameras}返回NG"
            elif failed_cameras:
                return f"无成功码: 相机{failed_cameras}失败"
            else:
                return "无成功码: 无任何相机结果"

        elif status == DecisionStatus.FAULT:
            return "设备异常: 请检查相机连接和配置"

        return "未知原因"

    def get_statistics(self) -> dict:
        """获取决策统计信息"""
        return {
            **self.stats,
            "success_rate": self.stats["ok_count"] / self.stats["total_decisions"] if self.stats[
                                                                                          "total_decisions"] > 0 else 0
        }

    def reset_statistics(self):
        """重置统计信息"""
        self.stats = {
            "total_decisions": 0,
            "ok_count": 0,
            "timeout_count": 0,
            "ambiguous_count": 0,
            "no_read_count": 0,
            "fault_count": 0
        }
