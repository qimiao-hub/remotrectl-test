# 本项目与 remotectl 的 DDS 对接

以 `tcp-dds-config-reference.md` 为协议依据。`remotectl` 保留驾驶舱 TCP 客户端和原业务转换；本项目通过原生 `sim-dds-bridge` 收发同一套 IDL 的 DDS 样本。Python 与子进程之间的 JSON 行只走本机管道，不作为网络协议。

已在 Ubuntu 22.04 主机 `192.168.48.128` 完成构建、部署与协议闭环测试，并集成 test_server HTTP/舱云 TCP，见 [Linux 部署记录](deploy/linux/DEPLOYMENT.md)。

```text
驾驶舱 TCP 服务端 ←→ remotectl TCP 客户端
                         ↕ 原生 Fast DDS / 原生成类型
                    sim-dds-bridge
                         ↕ 本机 JSON 行管道
                    sim_tcp_gui 动力学
```

## 配置

`bridge_config.json` 默认开启 `dds_output`，关闭原 `vehicle_output` HTTP 推送。按现场修改：

- `executable`：原生适配进程路径，相对路径以本项目目录为基准。Windows 默认 `native/build/bin/sim-dds-bridge.exe`；Linux 去掉 `.exe`。
- `cockpit_address`：**驾驶舱 TCP 服务端**的 IPv4:端口，当前 `127.0.0.1:11245` 仅为可修改的联调示例。跨机器运行须填写从 remotectl 所在机器可达的驾驶舱地址。
- `vehicle_type`：1 油车、2 电车、3 混动车，必须与 remotectl 的 YAML 一致。
- `steering_ratio`：方向盘角度/车轮角度，默认 12。DDS 中的车轮角为度。
- `control_source`：`remotectl` 接受 DDS 控车；`hil` 使用原 HIL 控车，同时向 remotectl 提供仿真反馈。默认选择 `remotectl`，避免两个控制源交替覆盖。
- `publish_interval`：DDS 快照周期，默认 0.05 秒。原 TCP 上报周期仍由 remotectl 决定。
- `control_timeout`：收到过控制后超过该秒数无新控制，仿真制动并施加驻车；原遥控端的失联策略仍保留。
- `wire_defaults`、`safe_feedback`、`curb_feedback`：未建模字段的反馈。动态速度、角度、里程、档位及踏板由动力学生成；控制输出的灯光、雨刮等会回显到底盘反馈。
- `front_obstacles`、`back_obstacles`、`round_vehicles`：默认空列表，不虚构环境目标。每路障碍物最多 200 条、周边车辆最多 50 条；还须满足整包 `47+9*O+11*V<=1024`。

两端必须使用一致的 `dds_topics.json`，包括 Domain 和 Topic 名。仓库各有一份内容相同的示例，也可通过路径配置直接共用一份。文档没有确认旧 SDK 的默认 Topic、Domain 和 QoS，因此此版本明确约定 **Domain 0、Reliable、Volatile、Keep Last 10**。这些是新原生适配层的配置，不能当作旧 SDK 的既定行为。

| Topic | 原 IDL 类型 | 相对 remotectl 的方向 |
| --- | --- | --- |
| `CmdtoRemoteCtrl` | `CmdtoRemoteCtrl` | 订阅 |
| `CmdCtrlACK` | `CmdCtrlACK` | 发布 |
| `WirectlFeedbackInfo` | `WirectlFeedbackInfo` | 订阅 |
| `GpsImuInfo` | `GpsImuInfo` | 订阅 |
| `SafeFeedbackInfo` | `SafeFeedbackInfo` | 订阅 |
| `fusion_front_scatter_box_` | `DDS_SensorFuseResults` | 订阅 |
| `fusion_back_scatter_box_` | `DDS_SensorFuseResults` | 订阅 |
| `CloudRoundVehicleList` | `CloudRoundVehicleList` | 订阅 |
| `back_lidar_curb_message` | `DDS_FusionCurbMessage` | 订阅 |
| `ImpleResultInfo` | `ImpleResultInfo` | 发布 |
| `CollectionControlRequest` | `mine_collection::CollectionControlRequest` | 发布 |
| `CollectionControlReply` | `mine_collection::CollectionControlReply` | 订阅 |
| `CollectionStatusReport` | `mine_collection::CollectionStatusReport` | 订阅 |

前七个由配置指定的默认 Topic 可在 `topics` 中修改；文档已明确给出的障碍物、挡墙及采集 Topic 保持原名称。类型注册名直接来自现有生成代码。

## 构建入口和启动

以下为人工构建命令。所需公开依赖为 CMake、C++17、Fast DDS **2.x** 和兼容现有生成代码的 Fast CDR **1.x**；remotectl 本体另需 yaml-cpp 和 Linux/POSIX 环境。生成类型使用 `GEN_API_VER=1`，不能直接换成 Fast DDS 3.x/Fast CDR 2.x 而不重新生成并核对类型。

Windows 上可以只构建本项目原生适配器，不构建 POSIX 遥控业务：

```powershell
cmake -S native -B native/build -DREMOTECTL_SOURCE_DIR=C:/Users/qi/Desktop/remotectl
cmake --build native/build --config Release
python simulator.py
```

若 Fast DDS SDK 未安装到默认搜索位置，需要按本机安装位置给 CMake 设置 `CMAKE_PREFIX_PATH`，运行时保证对应 DLL 可被找到。

Linux 上构建 remotectl（也会生成 `sim-dds-bridge`）：

```bash
cmake -S /path/to/remotectl -B /path/to/remotectl/build
cmake --build /path/to/remotectl/build
export REMOTECTL_DDS_CONFIG=/path/to/remotectl/config/dds_topics.json
export REMOTECTL_VEHICLE_CONFIG=/path/to/remotectl/config/runSetup.example.yaml
export REMOTECTL_REMOTE_CONFIG=/path/to/remotectl/config/remote_config.sim.ini
/path/to/remotectl/build/bin/syzk-remotectl
```

`REMOTECTL_VEHICLE_CONFIG` 未设置时仍读取 `/data/sany/config/runSetup.yaml`；`REMOTECTL_REMOTE_CONFIG` 未设置时仍读取 `../config/remote_config.ini`。示例 YAML 的 VIN/ICCID 需与驾驶舱配置一致。`remote_config.sim.ini` 明确关闭无地图仿真中的倒车地图功能，只有显式指定才生效，原车端默认开关不变。需要验证倒车安全时，使用原 INI、实际地图和反馈配置。

先启动驾驶舱 TCP 服务端，再启动 remotectl、本项目。DDS 发现支持晚加入，地址持续发布用于首次发现和任务断线复位后重新取址。地址 ACK 只说明收到地址，**不能证明 TCP 连接或登录成功**。

## 协议边界与观测

- `GET /api/state` 中的 `dds` 提供进程就绪状态、实际成功发布数、底盘/地址 Topic 匹配数、地址 ACK、最近控制和错误。`ready` 只表示原生端点建立；查看匹配数和 remotectl 日志确认后续状态。子进程启动失败详情在 `dds_bridge.log`。
- `GET /api/vehicle-protocol` 仍是旧 HTTP 模拟器的字段预览，不是 remotectl 实际发出的 TCP 报文。实际报文由 remotectl 的原协议函数生成。
- DDS 使用物理量：速度 km/h，东/北速度 m/s，经纬度/航向/俯仰为度，`total_vehicle_distance` 按 IDL 使用米。保留旧 TCP 实现中的车型换算，包括油车小计里程直接使用差值、电/混车型除 1000；本次没有把历史差异统一改写。
- 原程序始终输出 `on_start_enable=false`，它不是仿真点火状态。仿真保持已有点火设置。`parking_req` 的 IDL 注释与旧实现存在差异：旧实现在驻车按键及急停路径置 true，本适配器按该实际业务输出施加驻车。
- TCP 发送、接收的数据区长度均采用大端（高字节在前），接收端已按本次舱端联调要求由历史小端调整为大端；`0x14` 使用实时上报结构体长度的历史行为保持。时间预览按文档正常末两字节为零，2020 年前按旧固定值回退。
- 采集服务仅模拟 task/request 关联、命令接受、采集状态与上传完成状态，不执行实际文件采集或上传；`status_text` 和应答文本均标明模拟。
- 原仓库的采集状态 IDL/生成类型缺少文档要求的 `collect_duration_ms`。本次按文档在末尾补齐 `uint64` 毫秒字段，同步更新构造、复制/移动、比较、CDR 对齐、长度计算及序列化/反序列化；两端须使用本次更新后的类型。仿真使用单调时钟统计采集时长，暂停后时长保持。
- 删除了对 `zkbs`、`sany_utils`、`FailureReporter`、`daemon_sdk` 私有库的依赖。故障码及其置位/清除状态改记标准错误，不再发送到原私有故障平台或守护服务。地图 SSH/SCP 通道保留原实现。
- DDS 动态序列使用生成类型赋值替代 `memcpy/bzero`，反馈缓存与读取端使用互斥保护；`0x07` 超长快照直接拒绝组包。修复了 Socket 立即连接及 `SO_ERROR` 判定，未改变报文字节布局。

配置障碍物示例（后轴坐标，横向 x、纵向 y，单位米）：

```json
{
  "front_obstacles": [
    {"type": 0, "box": {"object_type": 1, "center": [1.0, 15.0, 0.0], "object_width": 3.0, "object_height": 4.0}},
    {"type": 1, "scatter": {"center": [-2.0, 12.0, 0.0]}}
  ],
  "round_vehicles": [
    {"device_type": 1, "longitude": 116.0, "latitude": 40.0, "heading": 90.0}
  ]
}
```
