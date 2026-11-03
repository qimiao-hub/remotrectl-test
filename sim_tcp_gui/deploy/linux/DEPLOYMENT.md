# Linux 部署与联调记录

日期：2026-10-22。目标：`qi@192.168.48.128`，Ubuntu 22.04.5 x86_64。部署目录：`/home/qi/remotectl-integration`。SSH 密码未写入项目或运行配置。

## 访问地址与链路

| 功能 | 地址 |
| --- | --- |
| test_server HTTP | `http://192.168.48.128:18080` |
| 舱云 TCP | `192.168.48.128:25300` |
| 动力学 Web UI | `http://192.168.48.128:50000` |
| 动力学状态 / DDS 状态 | `http://192.168.48.128:50000/api/state` |
| 原 JSON 遥测 TCP | `192.168.48.128:50001` |
| Windows 驾驶舱车端 TCP | `192.168.48.1:60006` |

```text
驾驶舱 HTTP / 舱云 TCP → Linux test_server
  → 0x0B 请求控制所选 VIN
  → 启动真实 syzk-remotectl
  ↔ 原生 Fast DDS ↔ sim-dds-bridge ↔ 动力学
  ↔ 驾驶舱车端 TCP 192.168.48.1:60006
```

云端默认 Python 车端行为保持兼容；本次 Linux 运行配置设置 `vehicle_backend=remotectl-dds`，由 `native_vehicle.py` 管理真实车端进程。收到 `0x12` 的 `ended=1` 时结束对应进程，避免继续连接驾驶舱。`0x12` 的 `0` 表示进行中，不能与结束含义颠倒。

当前只使用一个动力学模型和一套 DDS Topic，同一时刻允许一个 VIN；切换车辆前先结束当前控车。云端按实际选中车辆的 VIN、ICCID 生成 YAML。19 位 ICCID 会在固定 20 字节协议区补零。车辆列表的软件标识从原生源码 `software_version.h` 读取，当前为四位 ASCII `0030`，车端登录上报保留前导零。

本车定位链路：网页 GPS 输入和动力学状态 → `dds_bridge.py` 映射 `gps_lat/gps_lng/gps_heading` → 原生桥发布 `GpsImuInfo` → 车端 DDS 回调更新定位缓存 → 车端组装 `0x07`。经度编码为 `(经度+180)×10⁷`，纬度编码为 `(纬度+90)×10⁷`，航向编码为 `航向×10`，使用大端字节序；模拟器不代替车端组装此 TCP 报文。

## 服务与文件

环境文件配置：`run/bridge_config.json` 的 `dds_output.environment_config_file` 指向 `source/sim_tcp_gui/environment_config.json`。当前包含前后各 2 个车辆障碍物（中心距 10 m，无行人），V2V 前后左右各 1 辆（距离 20 m，绝对航向依次为 30°、100°、200°、320°），静态结构计算 `0x07` 整包为 127 字节。支持 `heading_deg` 绝对航向或旧 `heading_offset_deg` 偏差字段，二选一。首次启用或升级 Python 航向字段支持后重启模拟器，后续每 500 ms 自动检查文件；非法更新保留旧数据并在 `/api/state.environment_config` 报错。三组列表经既有 DDS Topic 交给车端组装 TCP，完整字段和后续验收清单见源码目录 `ENVIRONMENT_CONFIG.md`。本次按要求不运行测试、编译或联调。

部署时必须执行 `sudo loginctl enable-linger qi`（其他部署用户替换为对应用户名），并确认 `loginctl show-user qi -p Linger` 为 `yes`。两项服务使用普通用户身份运行 Fast DDS；默认 `RemoveIPC=yes` 会在该用户完全退出时清理其 POSIX 共享内存。启用 linger 让用户管理器在退出后继续运行，避免常驻 DDS 服务持有的共享内存文件被会话清理。

2026-10-23 已修复现网此问题：启用 `qi` 的 linger 后重启模拟器、舱云服务，恢复 `SYZKRMCAB25090510` 车端。底盘和地址 Topic 匹配数均恢复为 1，地址 ACK=1。此前的异常表现为模拟器持续成功发布、组播收发正常，但匹配数为 0，`/proc/<pid>/maps` 中 `fastrtps_*`、`fast_datasharing_*` 共享内存路径均显示 `(deleted)`。仅重新连接 SSH 不能恢复已删除的文件，必须重建受影响的 DDS 进程。

网页左侧“驾驶模式与制动气压”支持跟随遥控、人工、自动、遥控四个选项，并可分别设置两路制动气压（网页输入范围 0～8 MPa）。点击“保存并应用”后立即更新 DDS 反馈，并保存在 `source/sim_tcp_gui/local_state/vehicle_feedback.json`，服务重启后保留。`SIM_VEHICLE_FEEDBACK_CONFIG` 可指定其他保存路径。网页模式用于未接管或退出遥控时的反馈；收到有效遥控控制后优先反馈 `vcu_state=5、Remote_or_Unman=8`，退出或超时后恢复所选模式。“跟随遥控状态”退出后回到人工。控制超时制动行为保持。自动模式仅模拟反馈，不提供自动驾驶算法。

`GET /api/vehicle-feedback` 读取配置和实际反馈模式；`POST /api/vehicle-feedback` 接受 `mode`（`follow/manual/auto/remote`）、`air_pressure_1`、`air_pressure_2`，非法输入返回 HTTP 400。网页输入气压使用 MPa，原生车端仍按既有协议乘 100 写入单字节 TCP 字段。本次仅放宽网页及保存接口限制；超过 2.55 MPa 时舱端读数可能与网页不一致，车端和舱端协议均未调整。

已安装并启用开机启动：

JSON 控制覆盖：TCP `50001` 或 `POST /api/control` 接受 `mode`、`vcu_state`、`Remote_or_Unman` 和两路 `air_pressure_1/2`，也支持嵌套 `vehicle_feedback`。字段优先级为临时 JSON > 有效 DDS > 网页默认值；仅覆盖提供的字段，JSON 反馈按 `dds_output.control_timeout` 超时释放（默认 1 秒），或由 `release_feedback=true` 立即释放。网页和 ACK 同时返回默认值与有效值。车舱协议不变。

- `remotectl-simulator.service`：Flask 动力学界面与原生 DDS 适配器。
- `remotectl-cloud.service`：test_server HTTP、舱云 TCP、原生车端进程监督器。

```bash
sudo systemctl status remotectl-simulator remotectl-cloud
sudo journalctl --namespace=remotectl -u remotectl-cloud -f
sudo journalctl --namespace=remotectl -u remotectl-simulator -f
sudo systemctl restart remotectl-simulator remotectl-cloud
sudo systemctl stop remotectl-cloud remotectl-simulator
```

主要路径：

- `source/remotectl`、`source/sim_tcp_gui`、`source/test_server`：部署源码。
- `build/bin/syzk-remotectl`、`build/bin/sim-dds-bridge`：Linux 构建产物。
- `run/bridge_config.json`：实际驾驶舱地址与 DDS 适配器路径。
- `run/cloud_config.json`：云端 HTTP/TCP 与原生车端后端配置，权限为 600。
- `run/native-vehicles/vehicle-<VIN>.yaml`：选车后生成的车辆身份。
- `source/remotectl/config/dds_topics.json`：两端共享 Domain 0 的 Topic 配置。
- `logs/cloud`：舱云 HTTP/TCP JSONL 日志；持久运行的车端日志交给 journald 限流。
- `artifacts/remotectl-linux-x86_64.tar.gz`：Linux 可执行文件及示例配置包。
- `artifacts/python-requirements.lock`：本次实际安装的 Python 依赖版本。

本次为无排土场地图的仿真环境，明确选择 `remote_config.sim.ini`；没有用仿真 INI 覆盖原实车配置。

## 验证结果

2026-10-23 日志策略：已安装 `remotectl-log-clear.timer`，每 5 分钟清空 `logs` 下的 `.log/.jsonl` 和模拟器 `dds_bridge.log`，保留文件 inode；两个服务使用独立 journal 命名空间 `remotectl`，同步轮转清空，不清理其他系统服务日志。安装脚本为 `deploy/linux/log_retention.py`，以 root 执行 `--install`。变更前留在默认 journal 命名空间中的历史记录保持原系统保留策略。

`0x02` 的单调时钟 50 ms 调度及 TCP_NODELAY 已在 Linux 编译更新。用户随后取消验证；闭环检查在制动就绪阶段超时，20 Hz 实际发包统计尚未完成，不应视为实测通过。

- 2026-10-22 舱端联调修正：车端 TCP 数据区长度的接收由小端改为大端，与发送端一致；Linux 重新编译成功，大端报文的 11 项舱云/DDS/TCP 闭环检查通过。实际驾驶舱重连仍需现场确认。
- Linux CMake/Ninja 构建：`syzk-remotectl`、`sim-dds-bridge`、`dds-contract-check` 成功。
- 动态依赖确认使用 `libfastrtps.so.2.5`、`libfastcdr.so.1`、`libyaml-cpp.so.0.7`，无私有 SDK。
- Python 回归测试：5 项通过，包含启动时遥测线程抢先读取状态的回归案例。
- CTest：1 项通过，验证采集状态实际 CDR 长度、超过 32 位的 uint64 时长、编解码和复制/移动。
- 独立真实 DDS/TCP 闭环：9 个阶段通过。
- 集成 test_server 后：11 个阶段通过，包含 HTTP 登录、舱云登录/车辆列表/控车请求、DDS 匹配、地址 ACK、车端 TCP 登录、制动解锁、控车加速与反馈、障碍物/V2V、采集状态、急停/恢复和结束后失联制动。阶段详细数据见 [闭环报告](artifacts/cloud-e2e-report.json)。
- 从 Windows 访问正式 Linux 部署：两个 HTTP 服务返回 200；舱云 TCP 的 `0x09/0x0A/0x0B` 正确应答；控车请求启动真实车端，DDS 底盘及地址 Topic 均匹配 1 个端点并收到地址 ACK；`ended` 后进程回收。验证记录见 [跨机部署检查](artifacts/deployment-verification.json)。

闭环测试使用独立 Domain 73 和回环驾驶舱协议测试端，验证的是实际 Linux 程序、DDS 与动力学。正式服务使用 Domain 0。测试结束已停止临时车端；当前原生车端由后续驾驶舱的实际控车请求启动。

本次未操作 Windows 驾驶舱 GUI。初始检查时其 `60006` 端口没有监听；实际驾驶舱登录、UI 操作和实际下行报文仍需在该服务启动后确认，不能把地址 ACK 或协议测试端通过当作真实驾驶舱已接入。

## 复现

测试前先停正式服务，避免遥测端口冲突：

```bash
ROOT=/home/qi/remotectl-integration
sudo systemctl stop remotectl-cloud remotectl-simulator
cmake --build "$ROOT/build" -j 2
ctest --test-dir "$ROOT/build" --output-on-failure
"$ROOT/venv/bin/python" -m pytest -q "$ROOT/source/sim_tcp_gui/tests"
cd "$ROOT/source/sim_tcp_gui"
"$ROOT/venv/bin/python" tests/integration_remotectl.py --deploy-root "$ROOT" --with-cloud
sudo systemctl start remotectl-simulator remotectl-cloud
```

公开系统依赖版本：Fast DDS 2.5.0、Fast CDR 1.0.23、yaml-cpp 0.7.0；使用 GCC 11/C++17。原代码仍有未使用变量、旧线程入口缺失返回值等编译警告，本次构建通过，但没有将所有旧业务代码进行全面审计。
