# TCP 通信协议整理

## 基本信息

| 项目 | 内容 |
| --- | --- |
| 协议类型 | TCP 通信协议 |
| 协议版本 | 1.2 |
| IP | 192.168.101.10 |
| Port | 2000 |
| 源文件 | C:\Users\qim\Desktop\新建文本文档.txt |

## 字段说明

| 字段 | 说明 |
| --- | --- |
| 发送方 | Client/Server 客户端/服务器，或 Server 服务器 |
| 报文名称 | Message Header 包头，或 Message Data 数据 |
| 起始字节 | 信号在报文中的起始字节 |
| 信号长度(Bit) | 信号占用的 bit 数 |
| Data Type | 数据类型，原始表头中的 Date Type 按协议语义整理为 Data Type |
| 位序号 | 单 bit 信号的 bit 位置；多字节信号为 `/` |
| 精度 | 物理值换算精度 |
| 单位 | 物理单位；无单位为 `/` |
| Hex 范围 | 原始值十六进制范围 |
| 初始值(Hex) | 初始原始值 |

## Message Header 包头

| 发送方 | 报文名称 | 信号名称 | 信号描述 | 起始字节 | 信号长度(Bit) | Data Type | 位序号 | 精度 | 单位 | 最小值 | 最大值 | 最小值(Hex) | 最大值(Hex) | 初始值(Hex) | 取值说明 | 备注 |
| --- | --- | --- | --- | ---: | ---: | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- | --- | --- |
| Client/Server 客户端/服务器 | Message Header 包头 | 版本号 | 版本号 | 0 | 8 | unsigned | / | 0.1 | / | 0 | 255 | 0x00 | 0xFF | 0x0C | TCP 通信协议的版本号 | 协议版本 1.2；IP：192.168.101.10；Port：2000 |
| Client/Server 客户端/服务器 | Message Header 包头 | 报文类型 | 0x00：读取；0xFF：写入 | 1 | 8 | unsigned | / | 1 | / | 0 | 255 | 0x00 | 0xFF | 0x00 | 写入时服务器可正常反馈读取数据；只读取时服务器忽略数据包写入数据 |  |
| Client/Server 客户端/服务器 | Message Header 包头 | 报文 ID | 报文 ID | 2 | 8 | unsigned | / | 1 | / | 0 | 255 | 0x00 | 0xFF | 0x00 |  |  |
| Client/Server 客户端/服务器 | Message Header 包头 | 报文长度 | 包头 + 数据的字节数 | 3 | 8 | unsigned | / | 1 | / | 0 | 255 | 0x00 | 0xFF | 0x00 |  |  |

## Message Data 数据

| 发送方 | 报文名称 | 信号名称 | 信号描述 | 起始字节 | 信号长度(Bit) | Data Type | 位序号 | 精度 | 单位 | 最小值 | 最大值 | 最小值(Hex) | 最大值(Hex) | 初始值(Hex) | 取值说明 | 备注 |
| --- | --- | --- | --- | ---: | ---: | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- | --- | --- |
| Server 服务器 | Message Data 数据 | S2-N | 空挡 | 4 | 1 | unsigned | Bit0 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | S2-R | 后退挡 | 4 | 1 | unsigned | Bit1 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | S2-D | 前进挡 | 4 | 1 | unsigned | Bit2 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | A1 | 自复位按钮 | 4 | 1 | unsigned | Bit3 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | A2 | 自锁按钮 | 4 | 1 | unsigned | Bit4 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | A3 | 自锁按钮 | 4 | 1 | unsigned | Bit5 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | B1 | 自锁拨杆 | 4 | 1 | unsigned | Bit6 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | B2 | 自锁拨杆 | 4 | 1 | unsigned | Bit7 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | B3 | 自锁拨杆 | 5 | 1 | unsigned | Bit0 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | B4 | 自锁拨杆 | 5 | 1 | unsigned | Bit1 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | B5 | 自锁拨杆 | 5 | 1 | unsigned | Bit2 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q1 | 自锁翘板 | 5 | 1 | unsigned | Bit3 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q2 | 自锁翘板 | 5 | 1 | unsigned | Bit4 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q3 | 自锁翘板 | 5 | 1 | unsigned | Bit5 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q4 | 自锁翘板 | 5 | 1 | unsigned | Bit6 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q5 | 自锁翘板 | 5 | 1 | unsigned | Bit7 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q6 | 自锁翘板 | 6 | 1 | unsigned | Bit0 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q7-1 | 自锁翘板 | 6 | 1 | unsigned | Bit1 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q7-2 | 自锁翘板 | 6 | 1 | unsigned | Bit2 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q8-1 | 自锁翘板 | 6 | 1 | unsigned | Bit3 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Q8-2 | 自锁翘板 | 6 | 1 | unsigned | Bit4 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | J1 | 急停开关 | 6 | 1 | unsigned | Bit5 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X1 | 点火 | 7 | 1 | unsigned | Bit0 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X2 | 喇叭 | 7 | 1 | unsigned | Bit1 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X3-1 | 示宽灯 | 7 | 1 | unsigned | Bit2 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X3-2 | 近光灯 | 7 | 1 | unsigned | Bit3 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X3-3 | 远光灯 | 7 | 1 | unsigned | Bit4 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X4 | 雾灯 | 7 | 1 | unsigned | Bit5 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X5-1 | 左转向 | 7 | 1 | unsigned | Bit6 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X5-2 | 右转向 | 7 | 1 | unsigned | Bit7 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X6-1 | 雨刮 INT | 8 | 1 | unsigned | Bit0 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X6-2 | 雨刮 LO | 8 | 1 | unsigned | Bit1 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | X6-3 | 雨刮 HI | 8 | 1 | unsigned | Bit2 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 开关关闭；1 = 开关打开 |  |
| Server 服务器 | Message Data 数据 | Steering | 方向盘转角 | 9 | 16 | signed | / | 1 | / | -450 | 450 | 0xFE3E | 0x01C2 | 0x0000 | -450 = 方向盘左转至极限；450 = 方向盘右转至极限 |  |
| Server 服务器 | Message Data 数据 | Throttle | 油门踏板 | 11 | 8 | unsigned | / | 1 | % | 0 | 100 | 0x00 | 0x64 | 0x00 | 0x00 = 0%；0x64 = 100% |  |
| Server 服务器 | Message Data 数据 | Brake | 刹车踏板 | 12 | 8 | unsigned | / | 1 | % | 0 | 100 | 0x00 | 0x64 | 0x00 | 0x00 = 0%；0x64 = 100% |  |
| Server 服务器 | Message Data 数据 | 按键面板掉线故障 | 按键面板掉线故障 | 13 | 8 | unsigned | Bit0 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |
| Server 服务器 | Message Data 数据 | 方向盘组件掉线故障 | 方向盘组件掉线故障 | 13 | 8 | unsigned | Bit1 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |
| Server 服务器 | Message Data 数据 | 电机掉线故障 | 电机掉线故障 | 13 | 8 | unsigned | Bit2 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |
| Server 服务器 | Message Data 数据 | 预留 | 预留 | 13 | 8 | unsigned | Bit3 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |
| Server 服务器 | Message Data 数据 | 预留 | 预留 | 13 | 8 | unsigned | Bit4 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |
| Server 服务器 | Message Data 数据 | 预留 | 预留 | 13 | 8 | unsigned | Bit5 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |
| Server 服务器 | Message Data 数据 | 预留 | 预留 | 13 | 8 | unsigned | Bit6 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |
| Server 服务器 | Message Data 数据 | 预留 | 预留 | 13 | 8 | unsigned | Bit7 | 1 | / | 0 | 1 | 0x0 | 0x1 | 0x0 | 0 = 有故障；1 = 无故障 |  |

## 取值说明汇总

| 类型 | 取值说明 |
| --- | --- |
| 开关类信号 | 0 = 关闭；1 = 打开 |
| 故障类信号 | 0 = 有故障；1 = 无故障 |
| 方向盘转角 | -450 = 左转极限；450 = 右转极限 |
| 油门/刹车踏板 | 0x00 = 0%；0x64 = 100% |

## 整理校验

| 项目 | 结果 |
| --- | --- |
| 源文件编码 | UTF-8 |
| 协议记录数 | 48 条 |
| Message Header 字段数 | 4 条 |
| Message Data 字段数 | 44 条 |
