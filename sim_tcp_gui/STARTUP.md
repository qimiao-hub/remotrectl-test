# 模拟器、舱云服务与车端启动说明

适用部署：`qi@192.168.48.128`，目录 `/home/qi/remotectl-integration`。Windows 驾驶舱地址为 `192.168.48.1:60006`。

## 1. 启动顺序

1. 启动 Linux 虚拟机，确认 IP 为 `192.168.48.128`。
2. 启动 Linux 模拟器，建立动力学和原生 DDS 桥接端点。
3. 启动 Linux 舱云服务，提供 HTTP、舱云 TCP 和车端进程监管。
4. 启动 Windows 驾驶舱，配置云端地址并确保其车端 TCP 服务监听 `60006`。
5. 在驾驶舱登录、选择车辆并发起遥控请求。
6. 舱云收到 `0x0B` 请求后，根据所选 VIN 自动启动 `syzk-remotectl`。
7. 车端与模拟器匹配 DDS，收到驾驶舱地址后连接 `192.168.48.1:60006`，完成车端登录。
8. 完成驾驶舱接管操作后开始遥控，车端发送实时和环境报文。

推荐使用 systemd 服务方式。模拟器和舱云已配置开机启动，虚拟机启动后可先检查服务，无需重复启动多个 Python 或车端进程。

```text
Linux 模拟器（动力学 + 原生 DDS 桥）
    ↓ 发布定位、底盘反馈、障碍物及周边车辆
Linux 舱云服务 ← Windows 驾驶舱 HTTP / 舱云 TCP
    ↓ 收到所选 VIN 的 0x0B 请求
syzk-remotectl ← DDS 驾驶舱地址及车辆反馈
    ↓ TCP 连接 Windows 60006，登录版本标识 0030
Windows 驾驶舱 ← 车端组装的 0x02 / 0x07 等报文
    ↓ 控制报文
syzk-remotectl → DDS 控制输出 → Linux 模拟器动力学
```

## 2. Linux 开启方式

从 Windows PowerShell 连接：

```powershell
ssh qi@192.168.48.128
```

以下命令均在 Linux SSH 终端中执行。首次部署或用户配置被重置后，先启用 linger：

```bash
sudo loginctl enable-linger qi
loginctl show-user qi -p Linger
```

应显示 `Linger=yes`。该设置防止用户完全退出后，系统清理常驻 DDS 服务的共享内存；设置会持久保留，不必每次重复操作。

按顺序启动服务：

```bash
sudo systemctl start remotectl-simulator.service
sudo systemctl start remotectl-cloud.service
systemctl is-active remotectl-simulator.service remotectl-cloud.service
```

两个服务均应显示 `active`。检查开机启动设置：

```bash
systemctl is-enabled remotectl-simulator.service remotectl-cloud.service
```

如果尚未启用：

```bash
sudo systemctl enable remotectl-simulator.service remotectl-cloud.service
```

## 3. 地址与端口

| 用途 | 地址 | 说明 |
| --- | --- | --- |
| 模拟器网页 | `http://192.168.48.128:50000` | 动力学、驾驶模式、气压、地图 |
| 模拟器状态 | `http://192.168.48.128:50000/api/state` | DDS 状态、有效反馈、环境文件状态 |
| JSON 控制 TCP | `192.168.48.128:50001` | 可选的外部 JSON 控制入口，不是驾驶舱车端 TCP |
| 舱云 HTTP | `http://192.168.48.128:18080` | 驾驶舱登录、车辆列表和地图接口 |
| 舱云 TCP | `192.168.48.128:25300` | 驾驶舱云端登录、选车和控车请求 |
| 驾驶舱车端 TCP | `192.168.48.1:60006` | Windows 监听，由 Linux 车端主动连接 |

Linux 查看监听端口：

```bash
ss -ltn | grep -E ':(50000|50001|18080|25300) '
```

Windows 检查驾驶舱是否监听：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 60006
```

驾驶舱云端 HTTP/TCP 指向 Linux 地址；驾驶舱车端 TCP 使用 Windows 自己的 `60006`。虚拟机必须能访问 Windows 该端口。

## 4. 车端何时启动、如何确认

车端由舱云服务监管，不是常驻的独立 systemd 服务。只有收到所选车辆的 `0x0B` 请求才会启动。尚未发起控车时，没有 `syzk-remotectl` 进程、DDS 匹配数为 0，属于等待请求状态。

在 Linux 查看进程：

```bash
pgrep -ax syzk-remotectl
```

车端程序路径：

```text
/home/qi/remotectl-integration/build/bin/syzk-remotectl
```

监管器根据所选 VIN 生成车辆身份配置：

```text
/home/qi/remotectl-integration/run/native-vehicles/vehicle-<VIN>.yaml
```

不要在监管器已启动车端时再次手工运行该程序。当前单套动力学和 DDS Topic 只支持一个 VIN；切换车辆前先在驾驶舱结束当前控车。

查看连接：

```bash
ss -tnp | grep 60006
```

`ESTAB` 表示 TCP 已建立；`SYN-SENT` 表示仍在等待 Windows 接受连接，不代表车端登录成功。

在 Windows PowerShell 读取状态：

```powershell
$state = Invoke-RestMethod http://192.168.48.128:50000/api/state
$state.dds | ConvertTo-Json -Depth 5
$state.environment_config | ConvertTo-Json
```

接到请求、车端启动后，正常 DDS 状态应出现：`ready=true`、`matched_wire>=1`、`matched_address>=1`，以及 `address_ack=1`。这些值仅代表 DDS/地址交付正常，TCP 登录和遥控接管还应结合驾驶舱状态、连接和日志确认。

## 5. 模拟器配置与数据链路

| 配置 | Linux 文件 |
| --- | --- |
| 模拟器运行入口配置 | `/home/qi/remotectl-integration/run/bridge_config.json` |
| 舱云运行配置 | `/home/qi/remotectl-integration/run/cloud_config.json` |
| DDS Domain / Topic | `/home/qi/remotectl-integration/source/remotectl/config/dds_topics.json` |
| 默认模式及两路气压 | `/home/qi/remotectl-integration/source/sim_tcp_gui/local_state/vehicle_feedback.json` |
| 环境场景 | `/home/qi/remotectl-integration/source/sim_tcp_gui/environment_config.json` |

网页保存驾驶模式和气压后立即生效，并在重启后保留。有效 JSON/DDS 控制可覆盖网页默认反馈；释放或超时后回退。气压沿用现有车舱协议，网页输入与舱端显示的限制见原配置说明。

本车经纬度和航向由模拟器经 `GpsImuInfo` DDS 发送，车端接收后写入 `0x07`。前后障碍物及周边车辆也先生成 DDS，由车端合并组包，不由网页直接发送 `0x07`。

环境文件当前含前后各 2 个车辆障碍物（中心距 10 m，无行人），以及前后左右各 1 辆 V2V（距离 20 m，绝对航向按顺序为 30°、100°、200°、320°），结构计算的 `0x07` 整包为 127 字节。每 500 ms 检查文件变化，无需重启；非法更新保留上一份有效数据，通过 `environment_config.last_error` 报告。空列表需明确写为空数组，以清除旧数据。

配置字段、相对坐标和编码说明见 [ENVIRONMENT_CONFIG.md](ENVIRONMENT_CONFIG.md)。修改本地文件不会自动同步到 Linux；运行环境的热加载读取上表中的 Linux 文件。

## 6. 查看日志

两个服务使用独立 journal 命名空间 `remotectl`：

```bash
sudo journalctl --namespace=remotectl -u remotectl-simulator.service -f
sudo journalctl --namespace=remotectl -u remotectl-cloud.service -f
```

查看近期记录：

```bash
sudo journalctl --namespace=remotectl -u remotectl-cloud.service --since '-5 minutes' --no-pager
```

应用文本日志和专用 journal 每 5 分钟循环清理。应用日志位于 `logs/`，DDS 桥日志位于 `source/sim_tcp_gui/dds_bridge.log`。日志清理定时器为 `remotectl-log-clear.timer`；不清理 Linux 其他服务的 journal。

## 7. 编译更新与重启

已有部署环境中编译原生程序：

```bash
ROOT=/home/qi/remotectl-integration
cmake --build "$ROOT/build" --target syzk-remotectl sim-dds-bridge -j 2
```

Python 模拟器不需要单独构建。修改 Python、桥接二进制或初次启用环境文件入口后，需要重启模拟器；仅修改环境 JSON 内容时不需要重启。

```bash
sudo systemctl restart remotectl-simulator.service
```

更新车端二进制或舱云配置后，先结束控车，再重启舱云服务并从驾驶舱重新发起请求：

```bash
sudo systemctl restart remotectl-cloud.service
```

重启舱云会清理旧车端会话；车端由新的 `0x0B` 请求重新启动。上述命令不执行测试、抓包或联调验收。`0x02` 目标周期为 50 ms（20 Hz），实际时序仍需单独验收，不能仅以服务运行状态判断。

## 8. 关闭顺序与常见情况

关闭顺序：驾驶舱结束控车 → 云端处理 `0x12 ended=1` 并回收车端 → 关闭驾驶舱 → 停止舱云 → 停止模拟器。

```bash
sudo systemctl stop remotectl-cloud.service
sudo systemctl stop remotectl-simulator.service
```

| 现象 | 检查方向 |
| --- | --- |
| 模拟器网页打不开 | 模拟器服务、50000 监听、虚拟机 IP |
| 登录云端失败 | 舱云服务、18080/25300 和驾驶舱配置 |
| 没有车端进程 | 是否发出 0x0B；查看舱云日志中的 VIN 和单车占用提示 |
| 车端运行但 DDS 匹配为 0 | 两端 Domain/Topic、桥接状态、`Linger=yes`；排查共享内存是否被删除 |
| DDS 正常但 TCP 为 SYN-SENT | Windows 60006 监听、防火墙和地址 |
| 环境配置未生效 | 实际加载路径、500 ms 检查周期、`environment_config.last_error` |
| 驾驶模式仍是自动 | 是否已收到有效遥控控制，结合 `dds.last_control` 与 `vehicle_feedback.effective_mode` |

不要在 DDS 进程运行时手工删除 `/dev/shm` 的 Fast DDS 文件。发生共享内存失效时，先修正用户 linger 设置，再结束旧会话并重建相关服务。
