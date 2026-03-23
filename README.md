# Python 上位机软件详细设计说明书

## 1. 文档目的

本文档用于指导扫码视觉门 Python 上位机的软件开发、联调、测试与交付。目标是将上一份功能设计中的软件章节展开为可直接落地实现的详细设计，覆盖：

- 软件总体架构
- 模块划分与目录结构
- 核心类与接口设计
- 线程/协程模型
- 数据流与状态机
- 配置、日志、数据库和对外接口
- 异常处理、部署和测试策略

本文档面向对象：

- Python 上位机开发工程师
- 联调工程师
- 测试工程师
- 现场实施工程师

## 2. 建设目标与边界

### 2.1 软件目标

上位机软件需要完成以下职责：

- 管理两台智能读码相机的连接、心跳和扫码会话
- 采集双光电 DI 状态，建立鞋盒轨迹
- 在存在活动鞋盒时控制相机进入持续扫码会话
- 将相机回传结果与鞋盒轨迹正确绑定
- 输出 OK/NG/AMBIGUOUS/FAULT 结果
- 提供现场调试界面、参数配置界面和报警日志界面
- 保存运行记录、报警记录和配置快照
- 预留 PLC/MES/WMS 对接能力

### 2.2 软件边界

本软件不负责：

- 图像算法和条码识别算法本体
- 输送线速度闭环控制
- 机械导向逻辑
- 电气安全联锁本体

本软件默认相机已具备独立读码能力，并通过网络返回码值。

## 3. 架构路线选择

### 3.1 方案对比

#### 方案 A：PySide6 本地桌面程序 + asyncio 后台任务

特点：

- 现场调试直观
- 参数修改方便
- 适合设备侧独立运行
- UI 与业务可在一个工程内统一维护

优点：

- 最适合项目初期交付和现场维护
- 开发效率高
- 故障定位方便

缺点：

- 需要处理 UI 线程与后台任务协同

#### 方案 B：纯后台服务 + Web 页面

特点：

- 后台服务稳定，前端可远程访问
- 适合多终端查看

优点：

- 远程运维能力强

缺点：

- 前后端开发成本更高
- 现场无网或限制浏览器环境时不够方便

#### 方案 C：纯后台守护进程 + 配置文件

特点：

- 架构最简单
- 资源占用最小

优点：

- 运行稳定

缺点：

- 现场调试困难
- 非开发人员维护成本高

### 3.2 推荐方案

本项目推荐采用 `方案 A：PySide6 本地桌面程序 + asyncio 后台任务`。

选择理由：

- 当前项目部署在单机 RK3588 工控机上，现场维护和调试体验优先级高
- 双光电、相机、日志、报警、参数标定均需要本地可视化页面支持
- 项目阶段更强调快速落地、可维护和易调试，而不是分布式部署

## 4. 运行环境

### 4.1 硬件环境

- 工控机：RK3588 / RK3588J
- 内存：建议 `8 GB` 及以上
- 存储：建议 `64 GB` 及以上
- 网口：至少 `2 x Gigabit Ethernet`
- 工业 DI：至少 `2 路`
- 工业 DO：至少 `3 路`

### 4.2 软件环境

- OS：`Ubuntu 22.04 ARM64` 或同等级 Linux 发行版
- Python：`3.10+`
- GUI：`PySide6`
- 异步：`asyncio`
- 数据库：`SQLite`
- 配置：`YAML`
- 打包：`PyInstaller` 或 `Nuitka`
- 服务管理：`systemd`

### 4.3 网络拓扑约束

本项目固定采用工控机双网口结构：

- `ETH0`：设备侧网口
  - 连接 `千兆工业交换机`
  - 交换机下挂 `Camera A`、`Camera B`
  - 仅用于相机通信
- `ETH1`：调度侧网口
  - 连接调度上位机
  - 用于结果上报、参数下发、远程联调和后续调度指令

建议网络规划：

| 网口 | 角色 | 推荐网段 |
| --- | --- | --- |
| ETH0 | 相机设备网 | `192.168.10.0/24` |
| ETH1 | 调度上位机网 | `192.168.20.0/24` |

设计约束：

- 相机通信流量不得与调度上位机通信共网
- 软件应允许分别配置 `ETH0` 和 `ETH1` 的绑定地址
- 设备发现、心跳和结果接收默认走 `ETH0`
- 调度接口、远程维护和未来对接扩展默认走 `ETH1`

## 5. 总体架构

### 5.1 分层架构

![分层架构图（中文）](generated_diagrams/layered_arch_cn.png)

![Layered Architecture (English)](generated_diagrams/layered_arch_en.png)

### 5.2 程序架构框图

下面这张框图用于帮助开发快速理解程序主干、事件流和外部连接关系：


![程序架构框图（中文）](generated_diagrams/program_arch_cn.png)

![Program Architecture Diagram (English)](generated_diagrams/program_arch_en.png)

数据流概括：

- `PE1/PE2`、相机结果、用户操作都先进入 `EventBus`
- `RuntimeService` 串行消费主业务事件
- `TrackManager`、`TriggerScheduler`、`ScanSessionController` 共同维护鞋盒时间窗和持续扫码会话
- `ResultBinder` 与 `DecisionEngine` 负责码值归属和最终结果判定
- `Repository`、`SchedulerClient`、`DiDoService` 分别负责落库、对外上报和现场输出

### 5.3 核心设计原则

- UI 与业务解耦，UI 不直接操作底层设备
- 轨迹对象 `BoxTrack` 作为核心领域模型
- 所有设备事件进入统一事件通道，再由业务层消费
- 相机、DI/O、上报接口均使用适配器模式封装
- 配置可热加载，但关键运行参数变更需受控

## 6. 推荐目录结构

```text
vision_gate_app/
├─ app/
│  ├─ main.py
│  ├─ bootstrap.py
│  └─ lifecycle.py
├─ config/
│  ├─ default.yaml
│  ├─ schema.py
│  └─ manager.py
├─ domain/
│  ├─ models.py
│  ├─ enums.py
│  ├─ events.py
│  ├─ track_manager.py
│  ├─ scheduler.py
│  ├─ scan_session.py
│  ├─ binder.py
│  ├─ decision_engine.py
│  └─ alarm_service.py
├─ devices/
│  ├─ dio/
│  │  ├─ base.py
│  │  ├─ rk3588_dio.py
│  │  └─ simulator.py
│  ├─ camera/
│  │  ├─ base.py
│  │  ├─ opt_client.py
│  │  └─ simulator.py
│  └─ report/
│     ├─ plc_client.py
│     ├─ mes_client.py
│     └─ noop_client.py
├─ infra/
│  ├─ db/
│  │  ├─ models.py
│  │  ├─ repository.py
│  │  └─ migrations.py
│  ├─ logging/
│  │  └─ setup.py
│  └─ utils/
│     ├─ time_utils.py
│     ├─ net_utils.py
│     └─ validators.py
├─ services/
│  ├─ runtime_service.py
│  ├─ health_service.py
│  ├─ archive_service.py
│  └─ config_service.py
├─ ui/
│  ├─ main_window.py
│  ├─ viewmodels/
│  ├─ pages/
│  │  ├─ dashboard_page.py
│  │  ├─ device_page.py
│  │  ├─ config_page.py
│  │  ├─ alarm_page.py
│  │  └─ history_page.py
│  └─ widgets/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ e2e/
└─ scripts/
   ├─ run_local.py
   └─ seed_demo_data.py
```

### 6.1 最小版本目标

为了尽快落地并降低联调风险，建议先实现一个最小可运行版本 `MVP`。该版本只解决主链路：

- 读取配置
- 初始化 DI/O 与双相机
- 建立事件队列
- 基于 `PE1/PE2` 创建 `BoxTrack`
- 打开单箱时间窗
- 控制相机持续扫码会话
- 绑定相机结果
- 输出 OK/NG
- 保存 SQLite 记录
- 在单个主界面显示实时状态

`MVP` 不要求一开始就具备：

- 完整的历史查询页
- 完整的报警确认流
- PLC/MES/WMS 完整协议栈
- 参数热更新
- 导出报表

### 6.2 最小可运行骨架目录

建议在正式目录结构之外，先以如下最小骨架启动项目：

```text
vision_gate_mvp/
├─ app/
│  ├─ main.py
│  └─ bootstrap.py
├─ config/
│  └─ default.yaml
├─ domain/
│  ├─ enums.py
│  ├─ models.py
│  ├─ track_manager.py
│  ├─ scheduler.py
│  ├─ scan_session.py
│  ├─ binder.py
│  └─ decision_engine.py
├─ devices/
│  ├─ dio.py
│  ├─ camera.py
│  └─ scheduler_client.py
├─ services/
│  ├─ runtime_service.py
│  ├─ event_bus.py
│  └─ repository.py
├─ ui/
│  └─ main_window.py
├─ data/
└─ logs/
```

### 6.3 最小骨架文件职责

| 文件 | 职责 |
| --- | --- |
| `app/main.py` | 程序入口，初始化 Qt 和 asyncio 事件循环 |
| `app/bootstrap.py` | 组装配置、设备实例、服务实例和主窗口 |
| `config/default.yaml` | 最小运行参数和网络配置 |
| `domain/enums.py` | 枚举定义 |
| `domain/models.py` | `BoxTrack`、`CameraResult` 等模型 |
| `domain/track_manager.py` | 活动轨迹管理 |
| `domain/scheduler.py` | 时间窗计算和关闭策略 |
| `domain/scan_session.py` | 持续扫码会话启停控制 |
| `domain/binder.py` | 码值与轨迹绑定 |
| `domain/decision_engine.py` | 最终判定逻辑 |
| `devices/dio.py` | DI/O 读取与脉冲输出 |
| `devices/camera.py` | 双相机连接、持续扫码与结果接收 |
| `devices/scheduler_client.py` | 调度上位机接口客户端 |
| `services/event_bus.py` | 内存事件队列封装 |
| `services/runtime_service.py` | 主业务编排入口 |
| `services/repository.py` | SQLite 最小写入封装 |
| `ui/main_window.py` | 最小主界面，仅显示设备状态与最近结果 |

### 6.4 最小版本启动链路

```text
main.py
  -> 加载 default.yaml
  -> bootstrap.py 创建 EventBus
  -> 初始化 DiDoService / CameraClient / SchedulerClient / Repository
  -> 初始化 TrackManager / TriggerScheduler / ScanSessionController / ResultBinder / DecisionEngine
  -> 初始化 MainWindow
  -> 启动 RuntimeService
  -> RuntimeService 启动 dio_watch_loop / camera_fetch_loop / event_loop
  -> UI 进入主循环
```

### 6.5 最小版本主界面建议

`MVP` 主界面只保留最关键的信息：

- 相机 A/B 在线状态
- PE1/PE2 实时状态
- 持续扫码会话状态
- 当前活动 `BoxTrack` 数量
- 最近 10 条扫码结果
- 当前 OK/NG 计数
- 当前报警提示

### 6.6 最小版本与完整版的升级关系

建议先用 `MVP` 打通主链路，再平滑升级到完整版：

- `devices/scheduler_client.py` 后续可扩展为完整调度协议适配器
- `ui/main_window.py` 后续拆分为 Dashboard、Device、Config、Alarm、History 页面
- `services/repository.py` 后续拆分为多 Repository 和 migration 机制
- `devices/dio.py`、`devices/camera.py` 后续拆分为 `base + 实现 + simulator`

### 6.7 最小骨架代码示意

以下代码不是完整实现，而是建议开发起步时直接照着搭的骨架形态。

`app/main.py`

```python
import asyncio
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from app.bootstrap import build_app


def main() -> None:
    qt_app = QApplication([])
    loop = QEventLoop(qt_app)
    asyncio.set_event_loop(loop)

    container = build_app()
    container.window.show()

    with loop:
        loop.run_until_complete(container.runtime.start())
        loop.run_forever()


if __name__ == "__main__":
    main()
```

`app/bootstrap.py`

```python
from dataclasses import dataclass

from devices.camera import CameraService
from devices.photoelectric import DiDoService
from devices.scheduler_client import SchedulerClient
from services.event_bus import EventBus
from services.repository import Repository
from services.runtime_service import RuntimeService
from ui.main_window import MainWindow


@dataclass
class AppContainer:
  runtime: RuntimeService
  window: MainWindow


def build_app() -> AppContainer:
  event_bus = EventBus()
  dio = DiDoService(event_bus)
  camera = CameraService(event_bus)
  scheduler_client = SchedulerClient()
  repo = Repository("data/app.db")
  runtime = RuntimeService(event_bus, dio, camera, scheduler_client, repo)
  window = MainWindow(runtime)
  return AppContainer(runtime=runtime, window=window)
```

`services/runtime_service.py`

```python
class RuntimeService:
    def __init__(self, event_bus, dio, camera, scheduler_client, repo):
        self.event_bus = event_bus
        self.dio = dio
        self.camera = camera
        self.scheduler_client = scheduler_client
        self.repo = repo

    async def start(self) -> None:
        await self.dio.start()
        await self.camera.start()
        asyncio.create_task(self.event_loop())

    async def event_loop(self) -> None:
        while True:
            event = await self.event_bus.get()
            await self.handle_event(event)
```

### 6.8 最小版本推荐依赖

`MVP` 建议先控制依赖数量，优先使用以下最小集合：

- `PySide6`
- `qasync`
- `PyYAML`
- `pydantic` 或 `dataclasses + 手工校验`

数据库层在 `MVP` 阶段可以直接使用 Python 内置 `sqlite3`，不强制引入 ORM。

## 7. 关键领域模型设计

### 7.1 枚举定义

```python
class RunMode(Enum):
    LR = "LR"
    FB = "FB"

class TrackStatus(Enum):
    CREATED = "CREATED"
    TRACKING = "TRACKING"
    WINDOW_OPEN = "WINDOW_OPEN"
    WAITING_RESULT = "WAITING_RESULT"
    FINALIZED = "FINALIZED"
    EXPIRED = "EXPIRED"

class DecisionStatus(Enum):
    OK = "OK"
    NO_READ = "NO_READ"
    AMBIGUOUS = "AMBIGUOUS"
    TIMEOUT = "TIMEOUT"
    FAULT = "FAULT"

class DeviceStatus(Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"
```

### 7.2 BoxTrack 模型

`BoxTrack` 是系统核心对象，每个经过视觉门的鞋盒都对应一个 `BoxTrack`。

```python
@dataclass
class CameraTriggerPlan:
    camera_id: str
    trigger_ts: float
    trigger_offset_mm: float
    trigger_sent: bool = False


@dataclass
class CameraResult:
    camera_id: str
    code: str | None
    raw_payload: dict
    result_ts: float
    success: bool


@dataclass
class BoxTrack:
    track_id: str
    mode: RunMode
    created_ts: float
    pe1_on_ts: float | None = None
    pe1_off_ts: float | None = None
    pe2_on_ts: float | None = None
    pe2_off_ts: float | None = None
    speed_mm_s: float | None = None
    length_mm: float | None = None
    scan_window_start_ts: float | None = None
    scan_window_end_ts: float | None = None
    first_ok_ts: float | None = None
    scan_close_reason: str | None = None
    status: TrackStatus = TrackStatus.CREATED
    trigger_plans: list[CameraTriggerPlan] = field(default_factory=list)
    camera_results: list[CameraResult] = field(default_factory=list)
    final_code: str | None = None
    final_status: DecisionStatus | None = None
    alarm_codes: list[str] = field(default_factory=list)
```

### 7.3 设备快照模型

```python
@dataclass
class DeviceHealth:
    device_id: str
    device_type: str
    status: DeviceStatus
    last_heartbeat_ts: float | None
    message: str = ""
```

## 8. 事件模型设计

### 8.1 事件类型

系统内部推荐统一为事件驱动：

```python
class EventType(Enum):
    PE_RISE = "PE_RISE"
    PE_FALL = "PE_FALL"
    CAMERA_RESULT = "CAMERA_RESULT"
    CAMERA_HEARTBEAT = "CAMERA_HEARTBEAT"
    TIMER_TRIGGER = "TIMER_TRIGGER"
    TRACK_TIMEOUT = "TRACK_TIMEOUT"
    DEVICE_FAULT = "DEVICE_FAULT"
    OPERATOR_CMD = "OPERATOR_CMD"
```

### 8.2 事件结构

```python
@dataclass
class AppEvent:
    event_id: str
    event_type: EventType
    source: str
    ts: float
    payload: dict
```

### 8.3 事件总线建议

不建议一开始引入复杂消息中间件，推荐用轻量级内存事件总线：

- 底层使用 `asyncio.Queue`
- 设备服务将事件放入统一队列
- 业务协调器串行消费关键业务事件
- UI 通过信号槽或只读状态缓存订阅结果
- 持续扫码会话状态由领域层统一维护，避免 UI 或设备层各自判断

## 9. 核心模块设计

### 9.1 AppController

职责：

- 应用启动与关闭
- 初始化配置、数据库、日志、设备服务
- 启动后台任务
- 驱动主窗口和运行服务

建议接口：

```python
class AppController:
    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def restart_runtime(self) -> None: ...
```

### 9.2 RuntimeService

职责：

- 统一协调 DI、相机、调度器、绑定器和判定引擎
- 消费系统事件
- 推进 `BoxTrack` 生命周期

建议接口：

```python
class RuntimeService:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def handle_event(self, event: AppEvent) -> None: ...
```

### 9.3 TrackManager

职责：

- 创建、查询、更新和释放 `BoxTrack`
- 管理活动轨迹队列
- 负责轨迹超时回收

建议接口：

```python
class TrackManager:
    def create_track(self, ts: float, mode: RunMode) -> BoxTrack: ...
    def get_active_tracks(self) -> list[BoxTrack]: ...
    def match_track_for_pe2(self, ts: float) -> BoxTrack: ...
    def match_last_open_track(self) -> BoxTrack | None: ...
    def finalize_track(self, track_id: str, status: DecisionStatus) -> BoxTrack: ...
    def cleanup_expired(self, now_ts: float) -> list[BoxTrack]: ...
```

关键规则：

- 所有活动 `BoxTrack` 按创建时间升序维护
- 默认 `PE2` 匹配最早进入且尚未触发的轨迹
- 如果存在重叠歧义，立即报警并标记 `TRACK_OVERLAP`

### 9.4 TriggerScheduler

职责：

- 根据速度、配置和模式计算每个鞋盒的读码时间窗
- 管理单箱时间窗打开与关闭
- 向 `ScanSessionController` 申请开启或关闭持续扫码会话

建议接口：

```python
class TriggerScheduler:
    def open_scan_window(self, track: BoxTrack, mode: RunMode) -> BoxTrack: ...
    def prepare_window_close(self, track: BoxTrack, pe1_fall_ts: float) -> BoxTrack: ...
    def close_expired_windows(self, now_ts: float) -> list[BoxTrack]: ...
```

计算逻辑：

- 窗口开始时间通常取 `pe2_on_ts`
- 窗口结束时间由以下条件联合约束：
  - `PE1` 下降沿后 `tail_delay_ms`
  - 预测离开读码区域时间
  - `max_track_window_ms`
  - 读到首个有效码后 `track_hold_after_ok_ms`
- 速度异常或为空时使用 `line_speed_mm_s` 默认值

### 9.5 ScanSessionController

职责：

- 管理相机持续扫码会话的开关
- 当活动 `BoxTrack` 数量从 `0` 变为 `1` 时启动扫码会话
- 当活动 `BoxTrack` 数量回到 `0` 且超过 `idle_off_delay_ms` 时关闭扫码会话

建议接口：

```python
class ScanSessionController:
    async def ensure_running(self) -> None: ...
    async def stop_if_idle(self) -> None: ...
    def is_running(self) -> bool: ...
```

实现要求：

- 连续来料时不能因为单箱关闭窗口而频繁启停相机
- 持续扫码状态应对 UI 可见
- 若相机仅支持单次触发，则由该控制器退化为“窗口内连续触发”策略

### 9.6 CameraClient

职责：

- 管理单台相机 TCP 连接
- 启动和停止持续扫码
- 接收结果报文
- 上报心跳状态

建议基类：

```python
class BaseCameraClient(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def start_scan_session(self) -> None: ...
    async def stop_scan_session(self) -> None: ...
    async def trigger_once(self, track_id: str) -> None: ...
    async def fetch_loop(self) -> None: ...
    def get_health(self) -> DeviceHealth: ...
```

`OPT` 相机适配类：

```python
class OptCameraClient(BaseCameraClient):
    async def start_scan_session(self) -> None: ...
    async def stop_scan_session(self) -> None: ...
    async def trigger_once(self, track_id: str) -> None: ...
    def parse_payload(self, payload: bytes) -> CameraResult: ...
```

设计要求：

- 每台相机独立一个 client 实例
- 每台相机独立连接状态
- 结果回调时必须带 `camera_id`
- 原始报文建议保留到 `raw_payload`
- 优先使用相机原生“持续读码/持续解码”模式
- 若相机不支持持续扫码，则在每个活动窗口内连续触发 `3~5` 次，直到首个有效码或窗口结束

### 9.7 DiDoService

职责：

- 轮询或订阅 DI 状态变化
- 输出 DO 状态
- 屏蔽不同工控机 IO 驱动差异

建议接口：

```python
class BaseDiDoService(ABC):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def read_di(self, channel: int) -> bool: ...
    async def write_do(self, channel: int, value: bool) -> None: ...
    async def watch_inputs(self) -> None: ...
```

设计要求：

- 输入边沿检测必须去抖
- 输出动作需支持脉冲输出模式
- DI/DO 映射通过配置驱动，不写死在代码中

### 9.8 ResultBinder

职责：

- 将相机结果绑定到正确的 `BoxTrack`
- 检查是否存在冲突或超时

建议接口：

```python
class ResultBinder:
    def bind(self, result: CameraResult, active_tracks: list[BoxTrack]) -> BoxTrack | None: ...
    def resolve_final_code(self, track: BoxTrack) -> tuple[str | None, DecisionStatus]: ...
```

绑定规则：

- 优先按 `camera_id + result_ts` 命中活动时间窗
- 若只命中一个轨迹，直接绑定
- 若命中多个轨迹，优先选择距离窗口中心最近者
- 若仍不能唯一确定，则判为歧义
- 未命中任何活动轨迹时，记 `UNBOUND_RESULT`

### 9.9 DecisionEngine

职责：

- 根据 `camera_results` 判定最终状态
- 输出 `OK / NO_READ / AMBIGUOUS / TIMEOUT / FAULT`

建议接口：

```python
class DecisionEngine:
    def evaluate(self, track: BoxTrack) -> DecisionStatus: ...
```

基础规则：

- 两相机结果一致：`OK`
- 仅一相机成功：`OK`
- 均失败：`NO_READ`
- 两结果冲突：`AMBIGUOUS`
- 超时未返回：`TIMEOUT`
- 设备异常导致不可判：`FAULT`

### 9.10 AlarmService

职责：

- 统一定义报警码、报警级别和恢复机制
- 向 UI、数据库和外部接口广播报警

建议接口：

```python
class AlarmService:
    def raise_alarm(self, code: str, message: str, level: str = "ERROR") -> None: ...
    def clear_alarm(self, code: str) -> None: ...
    def list_active_alarms(self) -> list[dict]: ...
```

## 10. 状态机与时序设计

### 10.1 系统主状态机

```text
BOOT
  -> LOAD_CONFIG
  -> INIT_DEVICES
  -> SELF_CHECK
  -> READY
  -> RUNNING
  -> FAULT
  -> STOPPING
```

### 10.2 单箱生命周期状态机

```text
CREATED
  -> TRACKING
  -> WINDOW_OPEN
  -> WAITING_RESULT
  -> FINALIZED
        ├─ OK
        ├─ NO_READ
        ├─ AMBIGUOUS
        ├─ TIMEOUT
        └─ FAULT
```

### 10.3 连续来料时序泳道图

下图用于说明两个鞋盒连续经过视觉门时，系统如何在同一段持续扫码会话中维护两个独立时间窗，并按结果时间戳完成绑定。

![连续来料时序泳道图（中文）](generated_diagrams/continuous_swimlane_cn.png)

![Continuous Arrival Swimlane (English)](generated_diagrams/continuous_swimlane_en.png)

为保证 Word 和 Markdown 中都方便查阅，图片后保留表格化说明。

#### 表 1：连续来料分阶段时序

| 阶段 | 现场状态 | PE1 / PE2 | Track #1 | Track #2 | 扫码会话 | 结果绑定与输出 |
| --- | --- | --- | --- | --- | --- | --- |
| `t0` | 无鞋盒 | 均为空闲 | 无 | 无 | 未启动 | 无 |
| `t1` | `Box#1` 前沿到达视觉门 | `PE1` 上升沿 | 创建 `Track #1`，状态 `CREATED -> TRACKING` | 无 | 未启动 | 无 |
| `t2` | `Box#1` 到达读码基准位置 | `PE2` 对应 `Box#1` 上升沿 | 打开 `Track #1` 时间窗 | 无 | 启动持续扫码会话 | 相机开始连续返回结果 |
| `t3` | `Box#2` 紧跟进入视觉门 | `PE1` 对应 `Box#2` 上升沿 | `Track #1` 仍处于活动窗口 | 创建 `Track #2` | 保持启动 | 不停止扫码，等待后续结果 |
| `t4` | `Box#2` 到达读码基准位置 | `PE2` 对应 `Box#2` 上升沿 | `Track #1` 仍可能未关闭 | 打开 `Track #2` 时间窗 | 继续保持启动 | 此时系统内允许 `Track #1` 与 `Track #2` 同时存在 |
| `t5` | 相机返回第一个鞋盒结果 | 无新增光电动作 | `Track #1` 命中结果并完成判定 | `Track #2` 仍等待结果 | 保持启动 | `ResultBinder` 按时间窗将结果绑定到 `Track #1`，随后输出 `OK/NG(T1)` |
| `t6` | 相机返回第二个鞋盒结果 | 无新增光电动作 | 已完成 | `Track #2` 命中结果并完成判定 | 若无活动轨迹则进入空闲关闭延时 | `ResultBinder` 将结果绑定到 `Track #2`，随后输出 `OK/NG(T2)` |
| `t7` | 两个鞋盒均处理完成 | 光电恢复空闲 | 已释放 | 已释放 | 超过 `idle_off_delay_ms` 后关闭 | 本轮持续扫码会话结束 |

#### 表 2：关键控制规则

| 项目 | 规则 | 说明 |
| --- | --- | --- |
| 时间窗打开 | `PE2` 上升沿后为当前鞋盒打开时间窗 | `PE2` 是主要读码基准 |
| 持续扫码启动 | 只要存在活动 `BoxTrack` 就启动或保持扫码会话 | 不因单箱结束而立刻停扫 |
| 时间窗并存 | 允许 `Track #1` 和 `Track #2` 同时活动 | 连续来料时是正常状态 |
| 结果绑定 | 按 `result_ts` 落入的时间窗绑定 | 不是按“当前最近鞋盒”绑定 |
| 单箱关窗 | 由 `first_ok + hold`、`PE1 fall + tail_delay`、预测离站时间、`max_track_window_ms` 联合决定 | 防止过早关窗或无限等待 |
| 会话关闭 | 当活动 `BoxTrack = 0` 且超过 `idle_off_delay_ms` | 关闭的是共享扫码会话，不是单箱窗口 |

理解要点：

- `Track #1` 与 `Track #2` 的时间窗可以部分重叠
- 相机扫码会话是“共享资源”，单箱时间窗是“独立资源”
- `ResultBinder` 的职责就是在共享扫码结果中找出每条结果属于哪个时间窗
- 这套机制的目标是让连续来料时不漏扫、不频繁启停，同时避免串箱

### 10.4 标准处理时序

![标准处理时序图（中文）](generated_diagrams/standard_timing_cn.png)

![Standard Processing Sequence (English)](generated_diagrams/standard_timing_en.png)

标准步骤如下：

1. PE1 上升沿
2. TrackManager.create_track()
3. PE2 上升沿
4. TrackManager.match_track_for_pe2()
5. TriggerScheduler.open_scan_window()
6. ScanSessionController.ensure_running()
7. CameraClient 持续返回结果
8. ResultBinder.bind()
9. DecisionEngine.evaluate()
10. TriggerScheduler.prepare_window_close()
11. DiDoService.write_do()
12. DB Repository.save_scan_record()
13. ScanSessionController.stop_if_idle()
14. UI 刷新

### 10.5 超时策略

建议定义三个超时：

- `trigger_timeout_ms`：持续扫码会话未成功启动或写入/确认
- `camera_result_timeout_ms`：窗口打开后等待结果超时
- `track_ttl_ms`：轨迹总生存时间

超时触发后：

- 记录报警
- 将轨迹标记为 `TIMEOUT`
- 输出 `NG`
- 进入归档流程

## 11. 并发模型设计

### 11.1 总体并发策略

推荐采用：

- `Qt 主线程` 承载 UI
- `asyncio` 承载设备通信和业务逻辑
- 必要时通过 `QTimer + qasync` 或等效桥接方案整合事件循环

### 11.2 后台任务建议

```text
Task 1: camera_a_fetch_loop
Task 2: camera_b_fetch_loop
Task 3: dio_watch_loop
Task 4: runtime_event_loop
Task 5: health_check_loop
Task 6: archive_flush_loop
Task 7: report_retry_loop
```

### 11.3 并发原则

- 轨迹相关业务处理尽量串行，降低竞态风险
- 相机接收和 DI 监听可并行
- 对共享状态的修改统一由 `RuntimeService` 完成
- 避免多个协程同时直接修改 `active_tracks`

## 12. UI 详细设计

### 12.1 页面组成

#### 1. 主监控页 Dashboard

显示：

- 当前模式
- 相机在线状态
- PE1/PE2 实时状态
- 当前速度估计
- 最近扫描结果
- OK/NG 统计
- 当前报警

#### 2. 设备页 Device

显示：

- 相机连接状态
- 持续扫码状态
- DI/DO 状态
- 网络延迟
- 会话启动次数、读码成功率、超时次数

#### 3. 参数页 Config

配置：

- 运行模式
- 相机 IP、端口
- 光电映射
- 触发距离参数
- 时间窗参数
- 超时时间
- 输出脉冲时长

#### 4. 历史页 History

显示：

- 扫码历史
- 条件查询
- 导出 CSV

#### 5. 报警页 Alarm

显示：

- 当前报警
- 历史报警
- 恢复时间
- 确认人

### 12.2 UI 与业务交互原则

- UI 不直接访问数据库和设备驱动
- UI 通过 `ViewModel` 或只读服务接口获取状态
- 所有参数修改均走 `ConfigService`
- 涉及运行时生效的参数需二次确认

## 13. 配置设计

### 13.1 配置文件建议

建议使用单一主配置文件：`config/default.yaml`

```yaml
app:
  site_name: "WM QR Gate"
  debug: false

runtime:
  mode: "LR"
  line_speed_mm_s: 800
  track_ttl_ms: 1500

trigger:
  scan_mode: "continuous_window"
  sensor_distance_mm: 120
  pe2_to_cam_a_mm: 100
  pe2_to_cam_b_mm: 100
  trigger_timeout_ms: 300
  camera_result_timeout_ms: 500
  min_box_gap_mm: 50
  tail_delay_ms: 80
  idle_off_delay_ms: 150
  track_hold_after_ok_ms: 50
  max_track_window_ms: 400

network:
  eth0_bind: "192.168.10.10"
  eth1_bind: "192.168.20.10"
  scheduler_host: "192.168.20.100"
  scheduler_port: 9100

camera:
  camera_a:
    id: "CAM_A"
    ip: "192.168.10.101"
    port: 3000
    enabled: true
  camera_b:
    id: "CAM_B"
    ip: "192.168.10.102"
    port: 3000
    enabled: true

dio:
  di_map:
    pe1: 0
    pe2: 1
    reject_fb: 2
  do_map:
    ok: 0
    ng: 1
    reject: 2
    cam_trigger_backup: 3
  pulse_ms:
    ok: 80
    ng: 120
    reject: 200

storage:
  db_path: "./data/app.db"
  log_dir: "./logs"
  export_dir: "./exports"

report:
  plc_enabled: false
  mes_enabled: false
```

### 13.2 配置生效规则

- 网络地址、通道映射修改后需重启运行服务
- 日志级别可热切换
- 模式切换建议仅在待机状态允许
- 时间窗参数支持调试页在线标定，但建议仅在空闲状态保存

## 14. 数据库设计

### 14.1 表结构

建议至少包含以下表：

- `scan_record`
- `camera_result_record`
- `alarm_record`
- `config_snapshot`
- `system_event_log`

### 14.2 camera_result_record

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INTEGER | 主键 |
| track_id | TEXT | 轨迹号 |
| camera_id | TEXT | 相机编号 |
| code_value | TEXT | 码值 |
| success | INTEGER | 0/1 |
| result_ts | TEXT | 结果时间 |
| raw_payload | TEXT | 原始报文 |

### 14.3 system_event_log

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INTEGER | 主键 |
| event_type | TEXT | 事件类型 |
| source | TEXT | 来源 |
| payload | TEXT | 原始数据 |
| created_at | TEXT | 记录时间 |

### 14.4 Repository 接口

```python
class ScanRepository:
    async def save_scan_record(self, track: BoxTrack) -> None: ...
    async def save_camera_result(self, result: CameraResult, track_id: str) -> None: ...
    async def save_alarm(self, alarm: dict) -> None: ...
    async def save_event(self, event: AppEvent) -> None: ...
    async def query_scan_records(self, filters: dict) -> list[dict]: ...
```

## 15. 日志设计

### 15.1 日志文件建议

- `app.log`：应用主日志
- `runtime.log`：业务运行日志
- `device.log`：设备通信日志
- `alarm.log`：报警日志

### 15.2 日志格式

建议包含：

- 时间戳
- 日志级别
- 模块名
- track_id
- camera_id
- message

示例：

```text
2026-03-18 15:12:01.233 | INFO  | runtime | track_id=T20260318151201231 | created track
2026-03-18 15:12:01.411 | INFO  | camera  | camera_id=CAM_A | trigger sent
2026-03-18 15:12:01.563 | WARN  | binder  | track_id=T20260318151201231 | single camera result only
```

## 16. 对外接口设计

### 16.1 PLC 适配接口

```python
class PlcClient(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def notify_ready(self, value: bool) -> None: ...
    async def notify_scan_result(self, result: DecisionStatus, track_id: str) -> None: ...
    async def notify_reject(self, track_id: str) -> None: ...
```

### 16.2 调度上位机接口

工控机 `ETH1` 连接调度上位机后，建议先定义一个最小接口集，满足最小版本联调：

```python
class SchedulerClient(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def report_result(self, payload: dict) -> bool: ...
    async def report_heartbeat(self, payload: dict) -> bool: ...
```

最小上报字段建议：

- `device_id`
- `track_id`
- `mode`
- `final_code`
- `status`
- `created_at`

`MVP` 阶段建议只做：

- 结果上报
- 心跳上报

参数下发、任务编排和远程控制可在二期扩展。

### 16.3 MES 适配接口

```python
class MesClient(ABC):
    async def report_scan_record(self, payload: dict) -> bool: ...
```

建议上报载荷：

```json
{
  "device_id": "VG-01",
  "line_id": "LINE-01",
  "track_id": "T20260318151201231",
  "mode": "LR",
  "final_code": "6901234567890",
  "status": "OK",
  "process_time_ms": 242,
  "created_at": "2026-03-18T15:12:01.233"
}
```

### 16.4 调度接口与其它上层接口的关系

建议接口职责拆分如下：

- 调度上位机：设备联机、结果接收、参数同步
- PLC：现场动作联动、就地信号交互
- MES/WMS：业务数据归档和追溯

这样可以避免把所有上层交互都压在一个协议里，降低联调复杂度。

## 17. 异常处理设计

### 17.1 异常分类

| 分类 | 示例 | 处理策略 |
| --- | --- | --- |
| 设备异常 | 相机断线、DI 驱动异常 | 报警，必要时停机 |
| 业务异常 | 串箱、冲突码、超时 | 判 NG，落库并提示 |
| 配置异常 | IP 无效、通道冲突 | 阻止启动 |
| 存储异常 | 数据库写失败 | 重试并告警 |
| 网络异常 | PLC/MES 超时 | 重试并缓存待补报 |

### 17.2 恢复策略

- 相机掉线：自动重连，超过阈值进入故障态
- MES 上报失败：本地缓存，后台重试
- 数据库偶发写失败：短时重试
- 配置解析失败：回退到最近一次有效配置

## 18. 启动、停止与恢复流程

### 18.1 启动流程

```text
读取配置
-> 初始化日志
-> 初始化数据库
-> 初始化 DI/O
-> 初始化相机
-> 建立事件队列
-> 启动后台任务
-> 自检完成
-> 进入 READY
```

### 18.2 停止流程

```text
停止接收新事件
-> 结束后台任务
-> 刷新待写数据库
-> 关闭设备连接
-> 保存运行快照
-> 退出
```

### 18.3 故障恢复

建议在应用中保留：

- 最近活动轨迹快照
- 当前活动报警
- 最近配置版本

系统重启后：

- 不恢复旧轨迹参与判定
- 仅恢复报警历史和配置

## 19. 测试设计

### 19.1 单元测试

覆盖以下模块：

- `TrackManager`
- `TriggerScheduler`
- `ScanSessionController`
- `ResultBinder`
- `DecisionEngine`
- `ConfigManager`

### 19.2 集成测试

覆盖以下流程：

- DI 事件 -> 建轨 -> 开窗 -> 持续扫码 -> 结果回传 -> 判定
- 连续来料 -> 会话保持 -> 多箱时间窗绑定
- 单相机成功
- 双相机冲突
- 超时无结果
- 相机掉线恢复
- 调度上位机心跳与结果上报

### 19.3 仿真测试

建议实现 `Simulator`：

- 模拟 DI 边沿输入
- 模拟相机返回码值
- 模拟速度波动
- 模拟相邻箱距过小

这会显著提升本地开发效率。

### 19.4 端到端测试

建议在现场联调前，用录制脚本回放以下场景：

- 正常连续来料
- 单边有码
- 双边同码
- 双边异码
- 码值延迟返回
- 设备掉线后恢复

## 20. 关键伪代码

### 20.1 运行主循环

```python
async def runtime_loop():
    while running:
        event = await event_queue.get()
        await runtime_service.handle_event(event)
```

### 20.2 PE 事件处理

```python
async def handle_pe_rise(sensor_name: str, ts: float):
    if sensor_name == "PE1":
        track = track_manager.create_track(ts, mode=current_mode)
        publish_ui_track_created(track)
        return

    if sensor_name == "PE2":
        track = track_manager.match_track_for_pe2(ts)
        track.pe2_on_ts = ts
        track.speed_mm_s = speed_estimator.calc(track.pe1_on_ts, ts)
        scheduler.open_scan_window(track, current_mode)
        await scan_session.ensure_running()

async def handle_pe_fall(sensor_name: str, ts: float):
    if sensor_name == "PE1":
        track = track_manager.match_last_open_track()
        if track is not None:
            track.pe1_off_ts = ts
            scheduler.prepare_window_close(track, ts)
```

### 20.3 相机结果处理

```python
async def handle_camera_result(result: CameraResult):
    track = binder.bind(result, track_manager.get_active_tracks())
    if track is None:
        alarm_service.raise_alarm("UNBOUND_RESULT", "camera result cannot bind")
        return

    track.camera_results.append(result)
    if result.success and track.first_ok_ts is None:
        track.first_ok_ts = result.result_ts
    repo.save_camera_result(result, track.track_id)

    final_code, final_status = binder.resolve_final_code(track)
    if final_status is not None:
        track.final_code = final_code
        track.final_status = decision_engine.evaluate(track)
        await output_result(track)
```

### 20.4 输出结果

```python
async def output_result(track: BoxTrack):
    if track.final_status == DecisionStatus.OK:
        await dio.write_pulse("ok")
    else:
        await dio.write_pulse("ng")

    if track.final_status in {DecisionStatus.NO_READ, DecisionStatus.AMBIGUOUS, DecisionStatus.TIMEOUT}:
        await dio.write_pulse("reject")

    await repo.save_scan_record(track)
    await reporter.report_track(track)
    track_manager.finalize_track(track.track_id, track.final_status)
    await scan_session.stop_if_idle()
```

## 21. 打包与部署建议

### 21.1 部署目录建议

```text
/opt/vision-gate/
├─ app/
├─ config/
├─ data/
├─ logs/
├─ exports/
└─ run.sh
```

建议双网口在部署阶段做静态配置并写入部署手册：

- `ETH0`：设备网静态 IP
- `ETH1`：调度网静态 IP

### 21.2 systemd 服务建议

服务名称：

- `vision-gate.service`

启动策略：

- 开机自启
- 崩溃自动重启
- 写日志到文件和 journal

### 21.3 发布物建议

- 可执行程序
- 默认配置文件
- 版本说明
- 数据库初始化脚本
- 设备联调手册

## 22. 开发顺序建议

建议按以下顺序落地：

1. 先按 `MVP` 目录搭最小骨架
2. 完成 `domain` 和 `devices` 层的无 UI 版本
3. 完成 `DI 仿真器` 和 `相机仿真器`
4. 先打通完整闭环：建轨、开窗、持续扫码、绑定、判定、落库
5. 接入调度上位机最小接口
6. 再接入 UI 完整页面
7. 最后接入 PLC/MES 和导出功能

这样可以降低现场才能调通的风险。

## 23. 本版实现约束与建议

### 23.1 本版约束

- 默认只支持双相机模式
- 默认只支持单列单通道鞋盒
- 默认相机结果通过网络回传
- 默认不做图像保存

### 23.2 强烈建议优先实现的能力

- 仿真模式
- 参数导入导出
- 日志一键打包
- 相机原始报文归档
- 最近 100 条结果快速检索

## 24. 交付物建议

软件开发交付建议至少包含：

- 源代码
- 最小版本骨架工程
- 配置模板
- SQLite 初始化脚本
- systemd 服务文件
- 上位机操作手册
- 联调说明
- 版本变更记录

## 25. 总结

本设计选择 `PySide6 本地桌面程序 + asyncio 后台任务`，核心目标是让扫码视觉门的 Python 上位机具备：

- 稳定的设备通信能力
- 清晰可维护的轨迹绑定逻辑
- 良好的现场调试体验
- 可追溯的日志与数据记录能力

开发实现时应优先保证：

- `BoxTrack` 生命周期清晰
- 设备层与业务层解耦
- 轨迹绑定规则可测试
- 配置项和时间窗可标定
- 持续扫码会话的启停逻辑稳定

只要先把“事件流 -> 建轨 -> 触发 -> 绑定 -> 判定 -> 输出 -> 落库”这条主链路打透，后续再增加 PLC/MES、导出、报警增强都会比较顺。
