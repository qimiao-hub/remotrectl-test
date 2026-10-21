# 本地平台模拟器

用于 C# 驾驶舱客户端本地联调的 HTTP + TCP 模拟平台。


配置文件在当前目录的 `simulator.json`，可通过命令行参数覆盖：


## Python 启动

安装依赖：

```bash
python -m pip install -r requirements.txt
```

启动云端 HTTP + TCP server：

```bash
python cloud_server.py --config simulator.json
```

启动云端，并在控车流程触发后自动启动车端 TCP client：

```bash
python run_simulator.py --config simulator.json
```

启动车端 TCP client：

```bash
python vehicle_client.py --config simulator.json --vehicle SYZKRMCAB25090501
```

车端实时上报 `0x02` 可通过车辆配置里的 `realtime_report` 设置；启动后也可通过 HTTP 接口更新，下一帧立即生效：

```bash
curl -X POST http://127.0.0.1:18081/__vehicle/realtime_report \
  -H 'Content-Type: application/json' \
  -d '{"speed_deci_kmh":120,"gear":3,"driving_state":3}'
```

常用覆盖参数：

- 云端：`--http-host`、`--http-port`、`--tcp-host`、`--tcp-port`、`--debug-hex`、`--log-dir`、`--no-file-log`
- 一键入口：默认收到 `0x0B` 请求控制车辆后，按帧里的 VIN 连接车端；可用 `--vehicle-trigger control-status` 改成等 `0x12` 控车状态上报后再连接
- 车端：默认读取 `cockpit.ip/port` 连接舱端 TCP；`--cockpit-host`、`--cockpit-port` 可临时覆盖；默认启动后每 200ms 发送一次 `0x02` 实时上报；`--control-http/--no-control-http`、`--control-host`、`--control-port` 控制实时上报 HTTP 接口


## 落盘日志

默认启用 JSONL 落盘日志，目录为 `logs/`：

- `logs/http-YYYY-MM-DD.jsonl`：HTTP 请求摘要、状态码、耗时
- `logs/cloud-tcp-YYYY-MM-DD.jsonl`：云端 TCP 收发帧、hex、解析字段、真实数据
- `logs/vehicle-tcp-YYYY-MM-DD.jsonl`：车端 TCP 收发帧、hex、解析字段、真实数据

示例：

```bash
python cloud_server.py --config simulator.json --log-dir logs
python vehicle_client.py --config simulator.json --vehicle 31X141304S0KE0274 --log-dir logs
```

关闭落盘日志：

```bash
python cloud_server.py --config simulator.json --no-file-log
python vehicle_client.py --config simulator.json --vehicle 31X141304S0KE0274 --no-file-log
```


## 协议工具

`sim_protocol.py` 可直接基于 `protocol_packet_schema.json` 做通用解析和构建：

- `parse_cloud_frames(..., decode_payload=True)`：解析云端 TCP 帧并补充 `decoded_payload`
- `parse_vehicle_frames(..., decode_payload=True)`：解析车端 TCP 帧，兼容车端长度字段大小端
- `build_packet_from_schema(...)`：按命令 schema 和字段值构建完整 TCP 帧
- `build_sample_packet(...)`：生成指定命令的最小非空样例帧

手动解析报文：

```bash
python parse_packet.py "23-23-05-0C-FE-..."
python parse_packet.py 23 23 02 FE ...
python parse_packet.py --interactive
```

解析结果中：

- `decoded_payload` 保留协议原始字段值
- `real_payload` 将原始字段进一步转换为真实业务数据，例如枚举标签、物理量单位、经纬度、时间字符串和 bit 位


## 已覆盖

- HTTP:
  - `POST /api/user/account/login`
  - `POST /api/user/account/logout`
  - `/api/metadata/cockpit/describeUserMenu`
  - `POST /api/metadata/role/DescribeRoles`
  - 常用任务接续、地图、任务查询接口
  - `/__simulator/status` 查看配置和 TCP session
  - `/__simulator/push` 触发 TCP 下行帧
- 一键入口：
  - `run_simulator.py` 启动云端 HTTP/TCP
  - 收到 `0x0B` 请求控制车辆后记录 VIN
  - 收到 `0x12` 控车状态上报后，云端返回 `0x12` 空应答
  - 默认收到 `0x0B` 请求控制车辆后启动车端 TCP client 连接驾驶舱
  - 车端触发后默认自动下发一次 `0x0E` 遥控任务状态
- TCP:
  - 云端与驾驶舱端帧格式，含半包/粘包解析和 BCC 校验
  - `0x09` 驾驶舱端云端登入
  - `0x0A` 可控车辆列表请求
  - `0x0B` 请求控制车辆
  - `0x0C` 驾驶舱端云端心跳
  - `0x0D` 驾驶舱端状态上报
  - `0x0F` 驾驶舱端云端登出
  - `0x10` 故障上报
  - `0x12` 控车状态上报

## 车辆配置字段

`config/simulator.json` 中的 `vehicles` 按协议可配置：

- TCP 可控车辆列表字段：`vin`、`urgency`、`default`、`vehicle_no`、`vehicle_type`、`work_mode`、`status`、`fault_level`
- HTTP/联调辅助字段：`name`、`plate_no`、`device_id`、`mine_id`、`video_id`
- 车端登录基础字段：`iccid`、`hardware_version`、`software_version`、`driving_state`、`gear`、`length_mm`、`width_mm`、`camera_height_mm`、`front_blind_spot_mm`、`camera_pitch_deg`
