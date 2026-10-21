# 车辆 TCP 仿真工具

本项目基于 [OpenXC Vehicle Simulator](https://github.com/openxc/openxc-vehicle-simulator) 改造，保留其 Flask Web UI、状态管理和 TCP 车辆数据流的核心思路，并扩展为导航式高程地图仿真控制台。

## 功能

- Web 控制油门、刹车、转向、档位、点火和驻车制动。
- TCP `50001` 接受外部 JSON 控制命令，并以 20Hz 输出车辆状态。
- 实时反馈速度、发动机转速、经纬度、航向、俯仰、高程和地形坡度。
- Web 界面显示跟随车辆的高程地图，不再显示道路段。
- 默认地图为 1000m x 1000m、5m/格的纯平高程地图。
- 地图宽度、高度、分辨率和基准高程可在 Web 中修改，修改后保存到 `local_state/elevation_map.json`，下次启动自动生效。
- 导入地图会立即替换当前仿真地图，并覆盖保存到本地持久化文件。
- 高程地图支持画笔抬高、降低、平滑、整平，以及 JSON 导入/导出。
- Web 可调速度响应参数，包括发动机力、刹车力、滚阻、空气阻力和最大转速。

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r pip-requirements.txt
.\.venv\Scripts\python simulator.py
```

打开 `http://localhost:50000`。

## TCP 协议

连接地址默认是 Web 页面右上角显示的本机 IP，端口 `50001`。

下发控制命令时发送 JSON 行，也兼容以 `\0` 分隔的 JSON：

```json
{"throttle":35,"brake":0,"steering":12,"gear":"drive"}
```

支持字段：

- `throttle` 或 `accelerator`: 0-100
- `brake`: 0-100
- `steering`: -600 到 600，单位为方向盘角度
- `gear`: `park`, `reverse`, `neutral`, `drive`, `first`, `second`, `third`, `fourth`, `fifth`
- `parking_brake`: `true` 或 `false`
- `ignition`: `off`, `accessory`, `run`, `start`
- `latitude`, `longitude`: 重设当前位置
- `heading`: 重设航向，单位为度

服务端会持续输出如下遥测 JSON 行：

```json
{"vehicle_speed":12.4,"engine_speed":1350,"latitude":31.230416,"longitude":121.473701,"heading":3.2,"pitch":4.1,"road_slope":7,"terrain_grade":7,"elevation_m":31.2,"x_m":0.0,"y_m":42.5,"gear_lever_position":"drive","accelerator_pedal_position":35,"brake_pedal_position":0}
```

## HTTP API

- `GET /api/state`: 当前控制量、车辆状态、地图元数据、速度响应配置和 TCP 状态。
- `GET /api/map`: 当前高程地图 JSON。
- `POST /api/elevation/brush`: 高程画笔，字段为 `x_m`, `y_m`, `radius_m`, `strength_m`, `mode`。
- `GET /api/map/export`: 导出当前高程地图 JSON。
- `POST /api/map/import`: 导入高程地图 JSON。
- `POST /api/map/settings`: 更新地图宽度、高度、分辨率和基准高程，并保存到本地。
- `POST /api/map/reset`: 重置默认高程地图。
- `GET /api/response-config`: 读取速度响应参数。
- `POST /api/response-config`: 更新速度响应参数。

## 开源基底

调研后选择 OpenXC Vehicle Simulator 作为基底，原因是它本身就是“本地 Web 界面 + TCP 输出车辆数据 + 简单动力学模型”的结构。原项目采用 BSD-3-Clause 许可证，相关版权声明保留在 `LICENSE` 中。
