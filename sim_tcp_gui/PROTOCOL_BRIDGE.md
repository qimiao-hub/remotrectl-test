# HIL 到车端协议桥

`sim_tcp_gui` 启动时会自动完成下面的数据流：

```text
hil_project TCP :2000
  -> 14 字节 HIL 解码
  -> 仿真控制量与完整 HIL 信号状态
  -> 车辆动力学
  -> 车端 0x02 实时上报字段
  -> test_server HTTP :18081
  -> 驾驶舱车端 TCP 协议
```

## 启动顺序

1. 启动 `hil_project`：

   ```powershell
   cd ..\hil_project
   python run_server.py
   ```

2. 启动 `test_server` 的车端模拟器。该进程提供
   `POST /__vehicle/realtime_report`，并把收到的字段编码成车端 `0x02`
   实时上报：

   ```powershell
   cd ..\test_server
   python vehicle_client.py --config simulator.json --vehicle SYZKRMCAB25090501
   ```

3. 启动本项目：

   ```powershell
   cd ..\sim_tcp_gui
   .\.venv\Scripts\python simulator.py
   ```

HIL 或车端模拟器暂时不可用时，本项目仍会正常启动，并在后台持续重连。

## 配置

配置文件为 `bridge_config.json`。可通过环境变量切换文件：

```powershell
$env:SIM_BRIDGE_CONFIG = "D:\config\bridge_config.json"
.\.venv\Scripts\python simulator.py
```

- `hil.host/port`：HIL TCP 地址，默认 `127.0.0.1:2000`。
- `hil.poll_interval`：读取完整状态帧的周期。
- `hil.steering_endian`：方向盘转角字节序，须与 HIL 配置一致。
- `vehicle_output.url`：车端模拟器实时上报控制接口。
- `vehicle_output.publish_interval`：状态推送周期，默认与车端的 200ms
  上报周期一致。
- `vehicle_output.steering_ratio`：方向盘角度到车轮角度的转换比例。
- `vehicle_output.defaults`：HIL 和动力学模型未提供字段的初始值。

## 状态与调试

- `GET /api/state`：包含原有仿真状态，以及 `hil` 和
  `vehicle_protocol` 两个新增区域。
- `GET /api/hil`：连接状态、最近一帧和所有 HIL 字段。
- `GET /api/vehicle-protocol`：转换后的完整车端字段以及可直接校验的
  65 字节 `0x02` 帧十六进制预览。

所有 HIL 专有开关、按钮和故障字段都会保留在 `hil.signals` 中。动力学
模型只接收油门、刹车、方向盘、档位、点火和驻车制动，因此不依赖任何
HIL 协议字段名。车端字段转换也位于独立模块，三个项目之间没有 Python
源码导入关系。

## 默认映射

- `S2-R/S2-N/S2-D` → 倒挡/空挡/前进挡。
- `Steering/Throttle/Brake` → 仿真控制量。
- `X1` → 点火，`J1` → 驻车制动。
- 灯光、转向灯和雨刮 → `left_switch/right_switch` 位域。
- `A/B/Q` 按钮 → `button_operation1/2` 位域。
- 三个掉线故障及预留位 → `fault_info1`。
- 车速、航向、转速、里程、俯仰 → 动力学状态换算。

位域映射集中在 `vehicle_protocol.VehicleReportMapper`，现场协议定义变化时
只需调整这一层。
