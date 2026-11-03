# TCP、DDS 接口与配置文件定义及使用说明

日期：2026-10-22。范围：当前工作目录中的源码、IDL、INI 和 CMake 文件。方向均以车端遥控程序为参照。本文为源码静态梳理，未编译、未测试，也未连接车辆或外部服务。

## 1. 阅读范围与构建状态

现存业务代码入口为 [main_thread.cpp](../main_thread.cpp)，TCP 初始化位于 [InitTask.cpp](../InitTask.cpp)，业务协议位于 [protocol_cockpit.cpp](../protocol_cockpit.cpp)。DDS 使用外部 `zkbs` 封装和 Fast DDS 生成类型。

当前 [CMakeLists.txt](../../CMakeLists.txt) 已声明 gRPC 版本目标，明确将旧 TCP、协议和调试入口退出构建，但其引用的 `src/remote/*.cpp`、`proto/vehicle_cockpit.proto`、`config/vehicle-grpc.example.json` 在本次工作目录中不存在。因此，下文说明的是**当前可见的 TCP/DDS 源码实现**，不能据此认定当前 CMake 能构建或部署这套旧实现。gRPC 接口和 JSON 配置字段无法从缺失文件中确认。

“接口使用”分为业务代码实际创建的发布订阅、仅提供的底层 API，以及公共库中仅有定义的类型。`PubSubType::setName()` 是 DDS 类型名；未显式传入 Topic 的构造调用，其最终 Topic 取决于外部 SDK，不能直接把类型名当成已确认的 Topic。

## 2. 通信链路

1. `main()` 调用 `zkbs::init("syzk-remotectl")`，初始化故障 SDK、守护心跳，然后调用 `m_Config->Init()` 读取车辆 YAML。
2. `InitTask::InitStart()` 创建 `Protocol_Cockpit`，初始化业务 DDS 发布订阅，再创建驾驶舱地址订阅和地址 ACK 发布，以及 TCP 收发线程。
3. `cockpit_addressCallback()` 接收 `CmdtoRemoteCtrl.ipaddr`，先回传 `Seq_Num`，再解析地址并主动连接驾驶舱。此 ACK 表示收到地址，**不代表 TCP 已连接成功**。
4. 连接成功后 `Task_start()` 重读倒车 INI、按开关加载地图边界，启动遥控任务。TCP 收到的二进制报文进入接收环形缓冲区，按命令与应答标志分发。
5. 控车报文转换为 `ImpleResultInfo` 发布到 DDS；底盘、定位、安全、后向挡墙 DDS 数据用于反馈、策略计算及 TCP 上报；采集控制通过单独工作线程转换为 DDS 请求。

## 3. TCP 网络接口

### 3.1 地址和连接参数

| 项目 | 定义 | 使用与生效状态 |
| --- | --- | --- |
| 远端地址 | `CmdtoRemoteCtrl.ipaddr`，内容为 IPv4:端口 | DDS 动态下发，经 `ProtoCockpit_setServerName()` 保留数字、点和冒号，存入 `cockpitServerName[25]` |
| 地址解析 | `socket_if_server_name_tcpip()` | 总长度不超过 21，IP 部分不超过 15；使用 `inet_addr()`、`atoi()`；现有校验不完整，不等同于严格端口校验 |
| 本地地址字面量 | `HostclientName = "192.168.8.249:8080"` | 创建客户端时 `b_fix_port=false`，因此业务链路不绑定该地址，实际源地址/源端口由系统决定 |
| 旧默认地址宏 | `SA_COMM_SOCKET_SERVER_NAME = "114.255.128.132:11245"` | 头文件保留，现存业务连接未引用；不得作为实际驾驶舱地址 |
| Socket 类型 | IPv4、`SOCK_STREAM` | 车端为客户端，驾驶舱为服务端；未见 TLS 握手 |
| 单次连接等待 | `select()` 最多 10 秒 | `socket_client_connect_tcpip()` 非阻塞连接分支；可写时恢复阻塞模式 |
| 首次连接重试 | 失败后休眠 1 秒，`g_count > 10` 后退出 | 实际总耗时还包含每次连接等待，不是固定 10 秒 |
| 断线重连 | 失败间隔 500 毫秒，失败计数到 20 后停止任务 | 不能将日志中的“10 seconds”视为严格时间上限 |
| 接收等待 | 外层 `select()` 为 1 秒 | 随后的底层定长 `read()` 可继续等待；不是整包 1 秒超时保证 |
| 收包容量 | `BUF_SIZE=65535`、`Message_Max_Len=65535` | 网络接收上限；业务环形缓冲单包容量另为 1024 |

定义：[InitTask.h](../../include/InitTask.h)、[terminal_client.h](../../include/terminal_client.h)、[sa_socket_comm.h](../../include/sa_socket_comm.h)。实现：[InitTask.cpp](../InitTask.cpp)、[terminal_client.cpp](../terminal_client.cpp)、[sa_socket_comm.cpp](../sa_socket_comm.cpp)。

### 3.2 Socket 底层 API 全表

以下方法属于 `sa_socket_comm`，声明与参数类型以链接的头文件为准。

| 方法 | 参数/返回值 | 用途与调用情况 |
| --- | --- | --- |
| `sa_socket_comm()` | 无参数 | 构造，未执行网络操作 |
| `sa_comm_socket_new(server_name, client_name, b_fix_client)` | 两个地址字符串、固定本地地址开关；返回 `sa_comm_socket*` | 分配配置结构并解析地址；由客户端封装调用 |
| `sa_comm_socket_release(sock)` | 配置结构指针；`void` | 释放地址字符串和配置结构，不负责关闭 fd |
| `sa_comm_socket_client_open(sock)` | 返回 `int` fd | 根据 TCP 标识创建客户端 Socket |
| `sa_comm_socket_client_connect(sock, fd)` | 返回底层连接结果 | 使用配置 IP/端口连接 |
| `sa_comm_socket_server_open(sock, max_connection)` | 返回监听 fd | 服务端封装，现存业务未调用 |
| `sa_comm_socket_server_accept(sock, sock_fd)` | 返回已连接 fd | 服务端封装，现存业务未调用 |
| `sa_comm_socket_close(fd)` | 声明返回 `int`，实现无返回语句 | fd 大于 0 时关闭；调用者不应使用其返回值 |
| `sa_comm_socket_read(fd, buffer, buffer_size, len)` | 返回已读字节；EOF 为 0，参数错误为 -1 | 定长循环读取；负错误后等待 1 毫秒并计数，超过 3000 次结束；不构成阻塞 read 的整体超时 |
| `sa_comm_socket_write(fd, buffer, len)` | 全部写完返回字节数；失败为 0 | 循环写入处理部分写入；客户端发送使用 |
| `socket_if_server_name_tcpip(server_name, ip, port)` | 输出 IP/端口；成功 1，失败 0 | 解析 IPv4:端口 |
| `socket_client_open_tcpip()` | 返回 fd | 调用 `socket(PF_INET, SOCK_STREAM, 0)` |
| `socket_client_connect_tcpip(sock_fd, ip, port)` | 成功路径返回 0，失败 -1 | 非阻塞 connect + select；现存实现未读取 `SO_ERROR`，且立即连接成功分支仍会返回 -1，结果应按源码理解 |
| `socket_server_open_tcpip(ip, port, max_connection)` | 成功 fd，失败 -1 | `SO_REUSEADDR`、bind、listen；仅提供能力 |
| `socket_server_accept_tcpip(sock_fd)` | `accept4()` 结果 | 新连接使用 `SOCK_NONBLOCK | SOCK_CLOEXEC`；仅提供能力 |

### 3.3 客户端封装 API 全表

| `terminal_client` 方法 | 参数/返回值 | 用途 |
| --- | --- | --- |
| `terminal_client()` | 无参数 | 构造，无网络操作 |
| `sa_comm_socket_client_new(server_name, local_name, b_fix_port)` | 返回客户端结构指针，失败为空 | 分配配置与客户端对象；`InitSocket_Cockpit()` 调用 |
| `sa_comm_socket_client_release(socket_client)` | `void` | 释放客户端和地址配置；不隐式关闭 Socket |
| `comm_socket_client_do_connect(socket_client)` | 本次连接成功 1，失败或已连接分支 0 | 创建 fd，可选 bind，调用连接函数并更新 `b_connected` |
| `sa_comm_socket_client_send(socket_client, data, data_len)` | 成功 1，失败 0 | 写完整报文，失败则断连；`funcSendData()` 调用 |
| `sa_comm_socket_client_read(fd, userdata)` | 当前实现最终返回初始化为 0 的 `length` | 仅读取 `WP_Header_st` 的旧辅助实现，现存车舱收包线程未调用 |
| `comm_socket_client_do_disconnect(socket_client)` | `void` | 关闭 fd，fd 归零，清除连接标志 |

`sa_comm_socket_client_cb` 是 `(char *data, int data_len, void *userdata)` 回调类型，当前未见业务注册使用。`sa_comm_socket` 保存远端及可选固定本地地址；`sa_comm_socket_client` 保存配置指针、fd、连接标志和接收数组。

### 3.4 TCP 报文框架

定义：[protoDef.h](../../include/protoDef.h) 中 `CP_Header_st`，实现为 `ProtoCockpit_PackHeader()`、`ProtoCockpit_CalcXOR()`、`ProtoCockpit_ParsePacket()`。

| 偏移 | 字段 | 字节数 | 定义/使用 |
| --- | --- | --- | --- |
| 0 | `ucHeader` | 2 | 发送填写 `0x23 0x23`，即 `##` |
| 2 | `ucCommand` | 1 | 命令字 |
| 3 | `ucAckflg` | 1 | `0xFE` 命令；`0x01` 成功应答；`0x02` 失败；`0x03` VIN 重复；通用应答结果枚举另定义 `0xFF` 无效 |
| 4 | `ucVIN` | 17 | 来自车辆 YAML |
| 21 | `ucEncryption` | 1 | 发送为 `EV_WP_NO_ENCRYPT`，即 `0x01` |
| 22 | `ucDataByteSize` | 2 | 数据区长度；发送使用大端宏，接收线程存在小端解释，见第 7 节 |
| 24 | `ucData` | 变长 | 命令专属数据区 |
| 24+N | XOR | 1 | 调用校验函数跳过前两个 `#`，对命令字至数据区末尾异或 |

总长度为 `24 + N + 1`。`SYS2NET`/`NET2SYS` 按高字节在前编码/解码 16 位值。`ucTime[8]` 是协议时间数组：`ProtoCockpit_GetLocalTime()` 前 6 字节依次为年减 2000、月、日、时、分、秒，使用本地时间；后 2 字节按当前实现由 `t.millitm/1000` 计算，正常情况下均为 0。年份小于 2020 时填入固定回退时间，后 2 字节为 `1,9`，并返回错误。此数组不能直接当作 Unix 毫秒时间戳，与采集 DDS 的 `time_stamp` 含义不同。

### 3.5 TCP 业务接口全表

| 命令 | 枚举 | 方向 | 数据结构 | 实现/使用 |
| --- | --- | --- | --- | --- |
| `0x01` | `EV_CP_VEHICLE_REGISTER` | 发起登录→，响应← | `CP_Register_Cmd_st` / `CP_Register_Ack_st` | `ProtoCockpit_SendCmdLogin()` / `ProtoCockpit_RecvAckLoginPacket()`；按登录状态周期发送，校验匹配状态后置登录成功 |
| `0x02` | `EV_CP_INFO_REALTIME_REP` | →驾驶舱 | `CP_Realtime_Rep_Cmd_st` | `ProtoCockpit_SendCmdRealtimeRep()`，汇总底盘/定位/安全/采集状态 |
| `0x03` | `EV_CP_REQ_CTRL` | 请求←，应答→ | `CP_Req_Ctrl_Cmd_st` / `CP_Req_Ctrl_Ack_st` | `ProtoCockpit_RecvCmdRepCtrl()` / `ProtoCockpit_PackAckRepCtrl()`，切换驾驶请求状态 |
| `0x04` | `EV_CP_REALTIME_CTRL` | ←驾驶舱 | `CP_Realtime_Ctrl_Cmd_st` | `ProtoCockpit_RecvCmdRealtimeCtrl()`，更新内部控车输入；反馈合并在 `0x02` 中 |
| `0x05` | `EV_CP_HEART_BEAT` | 心跳←，应答→ | `CP_HeartBeat_Cmd_st` / `CP_HeartBeat_Ack_st` | `ProtoCockpit_RecvCmdHeartBeat()` / `ProtoCockpit_PackAckHeartBeat()`，16 位流水号 |
| `0x06` | `EV_CP_SEND_EMERGENCY_STOP` | 急停←，应答→ | `CP_Emergency_Stop_Cmd_st` / `CP_Emergency_Stop_Ack_st` | `ProtoCockpit_RecvCmdEmergencyStop()` / `ProtoCockpit_PackAckEmergencyStop()`；停止标志 0 解除、1 急停 |
| `0x07` | `EV_CP_OTHER_VEHICLE_INFO` | →驾驶舱 | `CP_Other_Vehicle_Info_Cmd_st` + 障碍物列表 + V2V 列表 | `ProtoCockpit_SendOtherVehicleInfo()`；本车定位、前后融合障碍物和周边车辆，布局及换算见第 3.7 节 |
| `0x08` | `EV_CP_VEHICLE_SIGNOUT` | 登出←，应答→ | `CP_LoginOut_Cmd_st` / `CP_LoginOut_Ack_st` | `ProtoCockpit_RecvCmdLoginOut()` / `ProtoCockpit_PackAckLoginOut()`，触发任务退出 |
| `0x14` | `EV_CP_DUMP_BACK_SAFE_REP` | →驾驶舱 | `CP_Dump_Back_Rep_st` | `ProtoCockpit_SendCmdDumpBackSafeRep()`，上报倒车距离、停车标志及油门补偿；实际发送长度见第 7 节 |
| 原命令字 | 通用应答 | →驾驶舱 | `WP_ProAck_st` | `ProtoCockpit_SendAckCom()`；接收空数据或校验失败等路径使用 |

收包分发表 `InitFuncArray_Cockpit()` 注册 5 类下行命令及登录成功应答，共 6 项。其他 `EV_WP_*` 枚举为公共协议定义，不能据此认为本程序提供同名 TCP 业务。

高频上报由 `SEND_RTDATA_UPLOAD_TICK=200` 和约 1 毫秒线程休眠触发，名义约 200 毫秒一次；同轮发送 `0x02` 和 `0x14`，实际周期受线程和网络影响。控车 DDS 任务线程休眠 50 毫秒，名义约 20 Hz。丢失计数以约 1 毫秒循环推进：控车反馈状态下阈值 500，非控车下阈值 5000；触发制动/网络复位和后续重连，不能视为实时系统的严格时限。

### 3.6 编码、缓冲及状态使用

| 内容 | 使用规则 |
| --- | --- |
| 实时反馈缩放 | 车速、航向角乘 10；轮角为 `(角度+40)*10`；累计/小计里程乘 10；俯仰角为 `角度+90`；水温为 `温度+50`；气压乘 100；以对应数组宽度存储 |
| 倒车距离 | `ucMinD1`、`ucD2`、`ucDisToEnd` 为米值乘 100，转 16 位；补偿值转单字节 |
| 控车转换 | `ProtoCockpit_getCtrlDate()` 将转角、油门、刹车、档位、组合开关、按键及安全措施转换到 `ImpleResultInfo`；车型分支来自 `m_Config->vehicle_type` |
| 发送缓冲 | `ProtoCockpit_WriteSendBuffer()` / `ProtoCockpit_ReadSendBuffer()`，`ProtoCockpit_SendBufferInit()` 初始化；40 槽，每槽 1024 字节，pthread mutex 保护 |
| 接收缓冲 | `ProtoCockpit_WriteRecvBuffer()` / `ProtoCockpit_ReadRecvBuffer()`，`ProtoCockpit_RecvBufferInit()` 初始化；40 槽，每槽 1024 字节，pthread mutex 保护 |
| 网络状态 | `g_u8IpFlag` 表示已取得地址；`g_u8NetReady` 表示业务网络就绪；`g_u8TaskStartFlag` 表示任务活动 |
| 登录状态 | `g_u8LoginFlag`：0 未登录、1 已登录、2 登录失败/退出；不能与 TCP fd 已连接混为一谈 |

### 3.7 车辆周围信息（`0x07`）

依据 [protocol_cockpit.cpp](src/protocol_cockpit.cpp) 中 `ProtoCockpit_SendOtherVehicleInfo()`、`ProtoCockpit_getFuseSensorData()`、`ProtoCockpit_getOneObstacleData()` 和 `ProtoCockpit_getV2vData()`。结构声明位于 [protoDef.h](include/protoDef.h) 和 [protocol_cockpit.h](include/protocol_cockpit.h)。

`funcAppUploadData()` 在任务活动、登录成功且发送缓冲未满时，每 5 次约 200 毫秒的上报轮次调用一次，名义周期约 1 秒；计数初始化为 0，首次进入上报轮次即发送，缓冲满时计数重置为 0。报文使用通用 TCP 头，命令字为 `0x07`，应答标志为 `EV_WP_CMD`（`0xFE`）；组包后计算 XOR 并调用 `ProtoCockpit_WriteSendBuffer()` 入队，入队成功不代表驾驶舱已经收到。

下表偏移从 TCP 数据区起算。设 `O` 为前向与后向障碍物数量之和，`V` 为周边车辆数量；前后障碍物合并后只保留总数，不另传方向标志。

| 数据区偏移 | 内容 | 字节数 | 来源/编码 |
| --- | --- | --- | --- |
| 0 | `ucTime` | 8 | 组包时本地时间，格式见第 3.4 节；不是 DDS 数据时间戳 |
| 8 | `u8Lontitude` | 4 | `struGpsInfo.gps_lng()`；`uint32((经度+180)*10000000)`，大端 |
| 12 | `u8Latitude` | 4 | `struGpsInfo.gps_lat()`；`uint32((纬度+90)*10000000)`，大端 |
| 16 | `ucHeadingAngle` | 2 | `struGpsInfo.gps_heading()*10` 转整数，再写入 16 位大端值，精度 0.1° |
| 18 | `u16ObstacleCount` | 2 | `O`，16 位大端 |
| 20 | `SensorFuseResult[O]` | `9*O` | 先前向 `objects()`，再后向 `objects()`，各自保留原顺序 |
| `20+9*O` | `u16V2vCount` | 2 | `V`，16 位大端 |
| `22+9*O` | `V2vResult[V]` | `11*V` | `m_V2vInfo.vehicles()`，保留原顺序 |

数据区长度 `N=22+9*O+11*V`，含头和 XOR 的整包长度为 `47+9*O+11*V`。即使两类列表均为空，也会发送本车定位和两个零计数，整包 47 字节。`u8Next[0]`、`u8ObstacleData[0]`、`u8V2vData[0]` 均为变长区起点，不占线上的字节，也不传指针。

单条障碍物固定 9 字节，所有 2 字节字段均通过 `SYS2NET` 写为大端：

| 条目内偏移 | 字段 | 字节数 | DDS `type()==0`（包围盒） | DDS `type()==1`（散点） |
| --- | --- | --- | --- | --- |
| 0 | `iObstacleType` | 1 | `box().object_type()+3` 转单字节 | 固定为 1，不使用 `scatter().object_type()` |
| 1 | `iObstacleWidth` | 2 | `int(box().object_width()*100)`，单位 cm | 0 |
| 3 | `iObstacleHeight` | 2 | `int(box().object_height()*100)`，单位 cm；实际取高度 | 0 |
| 5 | `iObstacleDistLong` | 2 | `int(box().center()[1]*100)`，纵向距离 cm | `int(scatter().center()[1]*100)` |
| 7 | `iObstacleDisLat` | 2 | `int(box().center()[0]*100)`，横向距离 cm | `int(scatter().center()[0]*100)` |

上述尺寸/距离先截断小数，再赋给 `SANY_U16`；负距离按该无符号 16 位转换后的位模式传输，接收侧若按有符号距离解释，应先以大端读取，再按 `int16` 解释并除以 100。代码不作范围钳制。头文件中旧类型说明（散点 0、矿卡 1 等）与当前发送实现不一致，对接应采用表中映射；`iObstacleHeight` 的旧“长度”注释也不能代替实际 `object_height()` 来源。其他 DDS `type()` 仅记录错误，仍占一个计数和 9 字节条目；由于组包缓冲初始清零，该条目保持全零。

单条周边车辆固定 11 字节：

| 条目内偏移 | 字段 | 字节数 | 来源/编码 |
| --- | --- | --- | --- |
| 0 | `iType` | 1 | `CloudVehicleInfo.device_type()` 转单字节；头文件约定 0 矿车、1 挖机、2 RSU、3 装载机、4 洒水车、5 指挥车、6 平地机 |
| 1 | `iLontitude` | 4 | `uint32((longitude()+180)*10000000)`，大端；解码为 `值/10000000-180` 度 |
| 5 | `iLatitude` | 4 | `uint32((latitude()+90)*10000000)`，大端；解码为 `值/10000000-90` 度 |
| 9 | `iHeadingAngle` | 2 | `uint16(heading()*10)`，大端；解码为 `值/10` 度，头文件约定正北 0°、正东 90° |

本车经纬度使用相同解码公式。该报文不携带障碍物追踪 ID、速度、轮廓点、V2V VIN、车辆路径或 DDS 原始时间戳，也没有按距离筛选、去重或按数据时间淘汰的逻辑。当前组包和发送槽容量均为 1024 字节，要求 `9*O+11*V<=977`；实现未在逐条写入前检查该限制，也未分包或截断列表。两路障碍物的 IDL 各允许 200 条，V2V 允许 50 条，这些上限不能当作 TCP 单包容量。

## 4. DDS 接口

### 4.1 业务实际创建的 13 个发布订阅

| 类型 | 方向 | 显式 Topic | 创建/处理位置 | 使用 |
| --- | --- | --- | --- | --- |
| `CmdtoRemoteCtrl` | 订阅 | 未传；由 SDK 决定 | `InitTask::InitStart()` → `cockpit_addressCallback()` | `ipaddr` 提供 TCP IPv4:端口；仅 `g_u8IpFlag==0` 时接受新地址 |
| `CmdCtrlACK` | 发布 | 未传；由 SDK 决定 | `InitTask::cockpit_ctrlACK()` | 原样回传地址指令 `Seq_Num`，先于 TCP 建连 |
| `WirectlFeedbackInfo` | 订阅 | 未传；由 SDK 决定 | `InitProtocol()` → `wireFeedbackCallback()` | 任务活动时更新 `WireInfo`；底盘反馈、驾驶状态、车型数据及倒车策略 |
| `GpsImuInfo` | 订阅 | 未传；由 SDK 决定 | `InitProtocol()` → `gpsFeedbackCallback()` | 更新 `struGpsInfo`；经纬度、航向、速度、俯仰及溜车判断 |
| `SafeFeedbackInfo` | 订阅 | 未传；由 SDK 决定 | `InitProtocol()` → `safeFeedbackCallback()` | 更新安全故障位，`ProtoCockpit_PackSafeFeedbackInfo()` 汇入 TCP 上报 |
| `DDS_SensorFuseResults` | 订阅 | `fusion_front_scatter_box_` | `InitProtocol()` → `fuseFrontObstacleCallback()` | 任务活动时更新 `m_fuseFrontObstaclesInfo`，作为 `0x07` 障碍物列表的前段 |
| `DDS_SensorFuseResults` | 订阅 | `fusion_back_scatter_box_` | `InitProtocol()` → `fuseBackObstacleCallback()` | 任务活动时更新 `m_fuseBackObstaclesInfo`，接在前向障碍物之后 |
| `CloudRoundVehicleList` | 订阅 | 未传；由 SDK 决定 | `InitProtocol()` → `v2vInfoCallback()` | 任务活动时更新 `m_V2vInfo`，其 `vehicles()` 转换为 `0x07` 的 V2V 列表 |
| `DDS_FusionCurbMessage` | 订阅 | `back_lidar_curb_message` | `InitProtocol()` → `fusionCurbMsgCallback()` | 更新后向挡墙距离，参与近墙停车和倒车上报 |
| `ImpleResultInfo` | 发布 | 未传；由 SDK 决定 | `InitProtocol()` 创建，`funcTask()` 发布 | 遥控时 `Remote_enable=3`、`unmanned_enable=1`、`remote_unman_flag=8`；退出分支 `Remote_enable=5`，最多连续发布 10 次 |
| `mine_collection::CollectionControlRequest` | 发布 | `CollectionControlRequest` | `send_collection_control_request()` | 发送采集命令、区域、任务 ID、请求 ID、毫秒时间戳，客户端固定 `REMOTE_COCKPIT` |
| `mine_collection::CollectionControlReply` | 订阅 | `CollectionControlReply` | `collectionControlReplyCallback()` | 任务活动且 task/request 均匹配才接收，唤醒等待工作线程 |
| `mine_collection::CollectionStatusReport` | 订阅 | `CollectionStatusReport` | `collectionStatusReportCallback()` | 匹配 task 后更新采集反馈：0 正常/处理中、1 失败、2 上传完成 |

类型定义位于 [Cockpit_address.idl](../../include/ddsType/Cockpit_address.idl) 和 [公共 IDL 目录](../../dvpn-pnc-common/idl)。`*PubSubTypes.cxx` 的 `setName()` 确认类型名；采集三种类型有 `mine_collection::` 命名空间，显式 Topic 没有该前缀。

本仓库未提供 `zkbs/ddsBase/ddsPubBase.h`、`ddsSubBase.h` 的实现，未见业务显式配置 Domain ID、可靠性、History、Durability、发现服务器、组播地址或 DDS 传输端口。上述运行参数须查看实际部署 SDK/环境，不能由本仓库推定默认值。

### 4.2 地图采集命令与应答

| TCP 输入 | 提取方式 | DDS 映射 |
| --- | --- | --- |
| 采集命令 | 左组合开关 bit6–7，即 `(iCombinedGearL >> 6) & 3` | 0 → `CMD_STOP_AND_UPLOAD`；1 → `CMD_START`；2 → `CMD_STOP_AND_UPLOAD`；3 保留并忽略 |
| 区域 | 右组合开关 bit3–5，即 `(iCombinedGearR >> 3) & 7` | 0 装载区、1 道路、2 排土场、3 排土线、4 其他；其余不发送 |
| 首帧/重复处理 | `collectionHasWireBaseline`、`collectionLastWireCommand` | 首帧 0 仅建立基线；后续命令变化才入队，队尾相同命令去重；仅改变区域不会触发命令 |
| 启动任务 | 命令 1 | task ID 为 `VIN_当前毫秒数`，VIN 空时用 `UNKNOWN`；请求序号成员递增 |
| 等待响应 | 发布后条件变量等待 1 秒 | 超时或非 `ACCEPTED` 设置会话错误；未见自动重发同一请求 |
| 状态反馈 | 匹配当前 task | `STATE_ERROR` 锁存错误；无错误且 `FILE_UPLOADED` 才设为 2 |

IDL 中的 `CMD_PAUSE` 和 `CMD_STOP_AND_ABORT` 已定义，但当前 TCP 适配未发送。反馈码通过 `collectionReportCode` 合入实时反馈，具体位布局以 `ProtoCockpit_getFeedbackData()` 为准。

### 4.3 仅定义或停用的 DDS 接口

| 类型 | 定义 | 当前使用状态 |
| --- | --- | --- |
| `Remote2Control` | [Remote2Ctrl.idl](../../include/ddsType/Remote2Ctrl.idl)：`RemoteIn`、`MaxRemoteVehSpd` | 遥控接管与最大车速定义；相关业务函数声明已注释，未创建发布者 |
| `StateMachine` | [StateMachine.idl](../../include/ddsType/StateMachine.idl)：`mainState` | 枚举顺序为无效 0、人工 1、无人 2、遥控 3；未创建发布订阅 |
| `HelloWorld` | [include/ddsType/.idl](../../include/ddsType/.idl) | 测试式类型声明，未见业务使用或对应 PubSubType |
| `TaskBehaviorState` | 公共库 `decide_state_machine.idl` | `Polygon_Boundary` 中订阅创建与回调实现被注释；不能认定存在运行时订阅 |
| 其他公共库类型 | 见附录公共 DDS 定义目录 | 为共享契约/生成类型，当前遥控业务未创建对应端点；类型存在不代表本应用提供接口 |

## 5. 配置文件定义与使用

### 5.1 配置入口总表

| 文件 | 仓库/部署位置 | 读取时机与入口 | 生效范围 |
| --- | --- | --- | --- |
| `runSetup.yaml` | `/data/sany/config/runSetup.yaml`；仓库未附实例 | 启动时 `Remote_Config::Init()` → `ReadVehicleConfig()` | 车辆身份、车型、应用分区 |
| `remote_config.ini` | 仓库 [src/remote_config.ini](../remote_config.ini)；读取路径 `../config/remote_config.ini` | 每次 `Protocol_Cockpit::Task_start()` → `ReadRemoteConfig()` | `[DumpBack]` 倒车安全参数；路径相对进程工作目录 |
| `remote_config sdk.ini` | [src/remote_config sdk.ini](../remote_config%20sdk.ini) | 未找到按此文件名读取的位置 | 包含车辆尺寸、相机、音频及部分倒车参数；不会因与主 INI 同目录而自动生效 |
| `remote.ini` | [src/remote.ini](../remote.ini) | 未找到读取位置 | 旧车辆身份及最大速度配置，当前可见代码不读取 |
| `hd_object_polygon.geojson` | `/data/sany/config/hd_object_polygon.geojson`；仓库未附实例 | 任务开始且倒车开关非零，`Polygon_Boundary::loadBoundary()` | 排土场区域和边界 |
| `vehicle-grpc.example.json` | CMake 引用 `config/vehicle-grpc.example.json`，文件缺失 | 仅能确认安装声明 | 无法确认字段或加载行为 |

`Remote_Config::Init()` 中直接调用 `ReadRemoteConfig()` 虽然被注释，但 `Task_start()` 中有实际调用，不能将倒车 INI 判定为完全未使用。当前 CMake 未安装旧 INI/YAML，部署路径需要由外部部署流程提供。

### 5.2 YAML 全部被读取字段

| YAML 路径 | 读取类型 | 成员/变换 | 使用 |
| --- | --- | --- | --- |
| `vehicle.vehicle_VIN` | `std::string` | `vehicle_vin` | TCP 包头 17 字节 VIN；采集任务 ID 前缀 |
| `vehicle.vehicle_ICCID` | `std::string` | `vehicle_iccid` | TCP 登录 ICCID，拷贝 20 字节 |
| `vehicle.vehicle_type` | `int` | 1 → `SKT-I`；2 → `SKT-E`；3 → `SKT-H`；其他值降级为 `SKT-I` | 底盘反馈、档位、制动、转速及控制分支 |
| `system.currentPart` | `std::string` | 全部转小写，再加 `syzk_` 前缀，保存到 `current_part` | 组合 sshpass 路径 `/data/sany/<current_part>/project/application/bin/sshpass` |

`vehicle_delay`、`vehicle_brake` 的读取已注释。硬件版本 `vehicle_HardVer="0101"`、软件版本 `vehicle_SoftVer="0222"` 是 [remote_config.h](../../include/remote_config.h) 中成员默认值，当前未从 `remote.ini` 或 YAML 读取。

YAML 无字符串缺省值；`YAML::LoadFile()` 及 `.as<>()` 未见本地 try/catch。文件不存在、字段缺失或类型错误不能理解为总会自动回退。

### 5.3 `[DumpBack]` 全部读取字段

定义和缺省值：[remote_config.h](../../include/remote_config.h)。读取：[remote_config.cpp](../remote_config.cpp)。下表缺省值与仓库主 INI 数值一致。

| 键 | 类型 | 缺省值 | 含义/单位 | 主要使用函数 |
| --- | --- | --- | --- | --- |
| `dumpBack_isOpen` | `SANY_U32`，`ReadInt` 读取 | 1 | 非零启用倒车策略和边界加载 | `Task_start()`、`funcTask()` |
| `boundary_stop_without_curb_dis` | float | 3.0 | 无有效挡墙时边界停车阈值，米 | `ProtoCockpit_GeneralBackCurbCheck()` |
| `boundary_curb_diff_dis` | float | 4.0 | 边界与挡墙距离差允许值，米 | 同上 |
| `curb_stop_dis` | float | 1.9 | 挡墙过近停车阈值，米 | 同上 |
| `boundary_stop_with_curb_dis` | float | 1.2 | 有效挡墙时边界停车阈值，米 | 同上 |
| `full_back_throttle_limit` | float | 45.0 | 倒车油门/扭矩控制基准 | `ProtoCockpit_getCtrlDate()` |
| `slope_throttle_compensation` | float | 2.0 | 坡度补偿系数 | `ProtoCockpit_dumpBackThrottleLimit()` |
| `dev_angle_throttle_compensation` | float | 1.0 | 转角补偿系数 | 同上 |
| `vehile_back_throttle_max` | float | 52.0 | 倒车油门/扭矩最大控制值；键名按源码拼写 | `ProtoCockpit_getCtrlDate()`、`ProtoCockpit_dumpBackThrottleLimit()` |
| `top_level_pitch_limit` | float | 1.0 | 二次起步相对基准俯仰上界，度 | `ReEvaluatePitchStable()` |
| `low_level_pitch_limit` | float | 0.8 | 二次起步相对基准俯仰下界，度 | 同上 |
| `pitch_abnormal_dis` | float | 2.0 | 启用近边界俯仰检测的距离，米 | `ProtoCockpit_dumpBackPitchCheck()`、`pitch_Attitude_montoring()` |
| `pitch_pair_chang_value` | float | 0.5 | 俯仰波动幅度阈值，度 | `pitch_Attitude_montoring()` |
| `up_level_pitch_max` | float | 3.5 | 相对基准上仰异常阈值，度 | 同上 |
| `down_level_pitch_max` | float | 1.6 | 相对基准下俯异常阈值，度 | 同上 |

文件打开失败时 `ReadRemoteConfig()` 直接返回，保留已有成员值；首次为头文件初始值，后续可能是上次任务读入值。成功打开但键/节缺失时，使用每次 `ReadInt`/`ReadFloat` 的缺省参数。转换使用 `atoi`/`atof`，没有范围、正负或格式完整性校验；无效字符串可能转为 0，不保证回退。仓库中的 `45.0f` 等文本按浮点前缀读取。

### 5.4 SDK 示例 INI 与旧 INI

`remote_config sdk.ini` 未被按名称加载。如果外部部署将它复制为 `../config/remote_config.ini`，本程序才会读取其中 `[DumpBack]` 已支持的键，其他节仍不会被本程序读取。

| 文件/节 | 全部键及文件值 | 本程序使用 |
| --- | --- | --- |
| SDK INI `[Vehicle]` | `length=9260`、`width=3980`、`height=4350`，毫米 | 未读取 |
| SDK INI `[Camera]` | `height=2600`、`blindDist=2400`，毫米；`pitchDegree=80`，度 | 未读取 |
| SDK INI `[Audio]` | `isOpen=0`、`isSaved=0` | 未读取 |
| SDK INI `[DumpBack]` | `dumpBack_isOpen=1`、`full_back_throttle_limit=34.0f`、`slope_throttle_compensation=1.0f`、`dev_angle_throttle_compensation=1.0f`、`vehile_back_throttle_max=52.0f`、`pitch_pair_chang_value=1.5f;`、`up_level_pitch_max=3.5f;`、`down_level_pitch_max=1.6f;` | 与主 INI 部分值不同；只有部署到实际读取路径才生效，缺少的键走缺省值 |
| `remote.ini` `[Vehicle]` | `ICCID=SKT90202011300002001`、`VIN=SKT90202011300002`、`hardware_version=0101`、`software_version=0102`、`vehicle_type=SKT-I` | 均未读取；车辆身份由 YAML 提供，版本来自成员默认值 |
| `remote.ini` `[Speed]` | `MaxSpeed=20` | 未读取，不能据此认定遥控限速为 20 km/h |

### 5.5 地图文件和 SSH/SCP 通道

这是通过外部 `ssh`/`scp` 进程使用 TCP 的文件通道，不是车舱二进制协议，也不是 DDS Topic。

| 项目 | 定义/使用 |
| --- | --- |
| 候选主机 | `NVIDIA_A_IP=192.168.1.102`、`NVIDIA_B_IP=192.168.1.103`，定义在 [polygon_boundary.h](../../include/polygon_boundary.h) |
| 主机选择 | 先在 A 上 SSH 查询 `syzk-netcloud`，结果不含 `./syzk-netcloud` 时选 B；A 查询失败直接返回失败 |
| SSH 端口 | 命令未传 `-p`/`-P`，由外部 SSH 配置或默认值决定；本程序未显式配置 |
| 工具 | 先检查工作目录 `./sshpass`，执行时使用由 `current_part` 拼接的绝对路径 |
| 远端文件 | `/home/sany/config/map/hd_object_polygon.geojson` |
| 本地文件 | SCP 到 `/data/sany/config/`，随后打开 `/data/sany/config/hd_object_polygon.geojson` |
| 调用 | `Task_start()` → `loadBoundary()` → `scpMapFromOrinToZXK()` → `execuCommandRet()` |
| 失败行为 | SCP 失败、打不开文件、版本为空或 JSON 解析异常返回 false；当前加载流程不会在 SCP 失败后自动使用旧本地文件 |

| JSON 路径 | 数据定义及使用 |
| --- | --- |
| `crs.properties.version` | 必须取得非空字符串，否则加载失败 |
| `features` | 特征数组；逐项加载 |
| `features[].properties.area_type` | 存在且为字符串时，仅 `dump` 保留；缺失时源码没有主动排除 |
| `features[].properties.obj_id` | 字符串区域 ID，保存到 `AreaInfo.areaId` |
| `features[].properties.area_name` | 字符串名称，保存到 `AreaInfo.areaName` |
| `features[].geometry.coordinates[0][0][]` | 逐个点读取，点必须有 3 个元素，前两个为经度、纬度，第三维不参与计算；按实际读取层级准备数据，不能直接假定兼容任意 Polygon |

边界点按 0.5 米间距插值，保存在 `m_dumpPolygons`；经纬度用于区域归属及车后边界距离计算，结合 DDS 定位和挡墙距离参与倒车停车决策。

## 6. 查找定义与使用的方法

TCP 新命令需同时检查 `protoDef.h` 的枚举/结构体、发送或接收函数、`InitFuncArray_Cockpit()` 分发表及收发缓冲调用。DDS 类型先查 IDL，再查同名 `*PubSubTypes.cxx` 的类型注册，最后查业务 `new ddsPublisher` / `new ddsSubscriber` 与 `publish()` / 回调。配置以实际 `ReadConfig`、`YAML::LoadFile` 或 `ifstream` 的路径和调用链为准，文件存在不代表已加载。

## 7. 对接时需要按现存实现处理的差异

| 位置 | 源码事实及影响 |
| --- | --- |
| TCP 数据长度字节序 | 发送 `SYS2NET` 按大端；`InitTask::funcRecvData()` 用 `size[1]*256+size[0]` 按小端取长度。双方不能仅凭发送宏推断收包规则一致 |
| 倒车上报长度 | `ProtoCockpit_SendCmdDumpBackSafeRep()` 填充 `CP_Dump_Back_Rep_st`，却以 `sizeof(CP_Realtime_Rep_Cmd_st)` 写入长度并计算校验；线上的实际长度并非倒车结构体大小 |
| 单包容量 | TCP 接收允许较大报文，但业务缓存为 1024 字节；不能把 65535 视为全链路支持的有效负载 |
| 周围信息类型和容量 | `0x07` 当前发送散点类型 1、包围盒类型 `object_type+3`；尺寸取宽度和高度。旧头文件注释不完全一致；整包 `47+9*O+11*V`，未见列表越界前检查，详见第 3.7 节 |
| 登录前命令处理 | `ProtoCockpit_ParsePacket()` 中登录状态拦截位于 `#if 0`，不能根据注释认定所有下行命令都会被登录前统一拦截 |
| DDS 回调并发 | 底盘/GPS/安全/挡墙/前后障碍物/V2V 回调以任务标志过滤，直接拷贝到共享缓存；未见这些缓存与读取线程共用的互斥保护。障碍物和 V2V 含动态序列，回调仍使用 `memcpy`，不能视为安全的独立快照。采集队列/应答则使用 mutex、条件变量及原子状态码 |
| 底层服务端函数 | 存在 bind/listen/accept 能力，但现存业务车端只主动连接驾驶舱；未发现本程序业务监听端口 |

## 附录 A：TCP 数据区逐字段定义

下表直接依据 `protoDef.h` 的车舱结构体整理，保留字段拼写与字节数组长度。字段注释表示声明含义；实际缩放和发送长度以第 3、7 节为准。

### CP_Header_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucHeader` | `SANY_U8[2]` | Start char |
| `ucCommand` | `SANY_U8[1]` | Command Flag |
| `ucAckflg` | `SANY_U8[1]` | Ack Flag; |
| `ucVIN` | `SANY_U8[WP_VIN_SIZE]` | Vehicle VIN; |
| `ucEncryption` | `SANY_U8[1]` | 0x01 NO ; 0x02 RSA ; 0x03 AES128; 0xFF invalid ; |
| `ucDataByteSize` | `SANY_U8[2]` | Data unit size; |
| `ucData` | `SANY_U8[0]` | 见协议处理函数 |

### CP_Register_Cmd_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 登入流水号 |
| `ucIccid` | `SANY_U8[20]` | ICCID |
| `ucHardVer` | `SANY_U8[4]` | 硬件版本 |
| `ucSoftVer` | `SANY_U8[4]` | 软件版本 |
| `ucWorkStatus` | `SANY_U8[1]` | 车辆工作状态 |
| `ucCurrentGear` | `SANY_U8[1]` | 当前档位 |

### CP_Register_Ack_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 响应流水号 |
| `ucLoginStatus` | `SANY_U8[1]` | 登入状态 |
| `ucSecretKey` | `SANY_U8[915]` | 公/私钥 |

### CP_LoginOut_Cmd_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 流水号（与登入流水号一致） |
| `ucloginOutFlag` | `SANY_U8[1]` | 登出标志 0x01主动登出；0x02被动登出 |

### CP_LoginOut_Ack_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |

### CP_Req_Ctrl_Cmd_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 指令流水号 |
| `ucCtrlSwitch` | `SANY_U8[1]` | 遥控接管开关 |

### CP_Req_Ctrl_Ack_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 响应流水号 |
| `ucCtrlStatus` | `SANY_U8[1]` | 控车状态 |

### CP_Realtime_Ctrl_Cmd_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 响应流水号 |
| `ucWheelAngle` | `SANY_U8[2]` | 车轮转角 |
| `ucThrottleOpening` | `SANY_U8[1]` | 油门开度 |
| `ucBrakeOpening` | `SANY_U8[1]` | 制动开度 |
| `ucGearOpening` | `SANY_U8[1]` | 档位操作 |
| `ucCombinedGearL` | `SANY_U8[1]` | 组合开关左 |
| `ucCombinedGearR` | `SANY_U8[1]` | 组合开关右 |
| `ucKeyOperation1` | `SANY_U8[1]` | 按键操作1 |
| `ucKeyOperation2` | `SANY_U8[1]` | 按键操作2 |
| `ucSafeRelease` | `SANY_U8[1]` | 安全措施-清除故障 |
| `UCSafeProtect` | `SANY_U8[1]` | 安全措施-进入安全模式 |

### CP_Emergency_Stop_Cmd_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 指令流水号 |
| `ucStopFlag` | `SANY_U8[1]` | 0x00解除急停；0x01紧急停车 |

### CP_Emergency_Stop_Ack_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[1]` | 响应流水号 |

### CP_HeartBeat_Cmd_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[2]` | 流水号 |

### CP_HeartBeat_Ack_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucSequenceNum` | `SANY_U8[2]` | 响应流水号 |

### CP_Realtime_Rep_Cmd_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucCarSpeed` | `SANY_U8[2]` | 车速 |
| `ucHeadingAngle` | `SANY_U8[2]` | 航向角 |
| `ucWheelAngleL` | `SANY_U8[2]` | 左轮转角 |
| `ucThrottleOpening` | `SANY_U8[1]` | 油门开度 |
| `ucBrakeOpening` | `SANY_U8[1]` | 制动开度 |
| `ucEngineSpeed` | `SANY_U8[2]` | 发动机转速 |
| `ucAccumulatedMileage` | `SANY_U8[4]` | 累计里程 |
| `ucSubtotalMileage` | `SANY_U8[2]` | 小计里程 |
| `ucPitchAngle` | `SANY_U8[2]` | 俯仰角 |
| `ucGearOper` | `SANY_U8[1]` | 档位操作 |
| `ucRemainingOil` | `SANY_U8[1]` | 剩余油、电量 |
| `ucWaterTemp` | `SANY_U8[1]` | 水温 |
| `ucBrakeAirPre1` | `SANY_U8[1]` | 制动气压1 |
| `ucBrakeAirPre2` | `SANY_U8[1]` | 制动气压2 |
| `ucCombinedGearL` | `SANY_U8[1]` | 组合开关左 |
| `ucCombinedGearR` | `SANY_U8[1]` | 组合开关右 |
| `ucKeyOperation1` | `SANY_U8[1]` | 按键操作1 |
| `ucKeyOperation2` | `SANY_U8[1]` | 按键操作2 |
| `ucWorkStatus` | `SANY_U8[1]` | 车辆驾驶状态 |
| `ucLiftUpStatus` | `SANY_U8[1]` | 举升到位状态 |
| `ucLiftDownStatus` | `SANY_U8[1]` | 下降到位状态 |
| `ucSafeInfo1` | `SANY_U8[1]` | 安全措施-故障信息 |
| `ucSafeInfo2` | `SANY_U8[1]` | 见协议处理函数 |

### CP_Other_Vehicle_Info_Cmd_st / CP_Other_Obstacle_Info_st / CP_Other_V2v_Indo_st

`0x07` 数据区依次拼接本车固定信息、障碍物计数及条目、V2V 计数及条目；完整偏移、长度及换算见第 3.7 节。`CP_Other_V2v_Indo_st` 保留源码中的 `Indo` 拼写。

| 所属结构 | 字段 | 类型/数组长度 | 实际使用 |
| --- | --- | --- | --- |
| `CP_Other_Vehicle_Info_Cmd_st` | `ucTime` | `SANY_U8[8]` | 本地组包时间 |
| 同上 | `u8Lontitude` | `SANY_U8[4]` | 本车经度偏移缩放值，大端 |
| 同上 | `u8Latitude` | `SANY_U8[4]` | 本车纬度偏移缩放值，大端 |
| 同上 | `ucHeadingAngle` | `SANY_U8[2]` | 本车航向角乘 10，大端 |
| 同上 | `u8Next` | `SANY_U8[0]` | 障碍物计数区起点 |
| `CP_Other_Obstacle_Info_st` | `u16ObstacleCount` | `SANY_U8[2]` | 前后障碍物总数，大端 |
| 同上 | `u8ObstacleData` | `SANY_U8[0]` | 连续 `SensorFuseResult` 条目，单条 9 字节 |
| `CP_Other_V2v_Indo_st` | `u16V2vCount` | `SANY_U8[2]` | 周边车辆数，大端 |
| 同上 | `u8V2vData` | `SANY_U8[0]` | 连续 `V2vResult` 条目，单条 11 字节 |

`SensorFuseResult` 和 `V2vResult` 声明在 `include/protocol_cockpit.h`，其全部字段及实际编码见第 3.7 节；二者的 `u8Next[0]` 均用于定位下一条目，不增加条目长度。

### CP_Dump_Back_Rep_st

| 字段 | 类型/数组长度 | 声明说明 |
| --- | --- | --- |
| `ucTime` | `SANY_U8[8]` | 指令时间 |
| `ucMinD1` | `SANY_U8[2]` | 边界距离 |
| `ucD2` | `SANY_U8[2]` | 挡墙距离 |
| `ucDisToEnd` | `SANY_U8[2]` | 时是距离 |
| `ucCurbStop` | `SANY_U8[1]` | 举升到位状态 |
| `ucPitchStop` | `SANY_U8[1]` | 下降到位状态 |
| `ucThrottleCom` | `SANY_U8[1]` | 油门补偿 |

## 附录 B：业务使用的 DDS 及嵌套类型字段

IDL 的 long/unsigned long 为 32 位，long long/unsigned long long 为 64 位；不要按宿主平台 C++ long 宽度解释。DDS 使用生成代码的 CDR 序列化，不直接复用 TCP 字节数组。

### [Cockpit_address.idl](../../include/ddsType/Cockpit_address.idl)


#### CmdtoRemoteCtrl

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `ipaddr` | `string` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Seq_Num` | `long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### CmdCtrlACK

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `Seq_Num` | `long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

### [ChassisCtrl.idl](../../dvpn-pnc-common/idl/ChassisCtrl.idl)


#### ActiveDiagnosticTroubleCodes

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `protect_lamp_status` | `unsigned long` | 00:OFF 01:ON |
| `amber_warning_lamp_status` | `unsigned long` | 00:OFF 01:ON |
| `red_stop_lamp_status` | `unsigned long` | 00:OFF 01:ON |
| `malfunction_indicator_lamp_status` | `unsigned long` | 00:OFF 01:ON |
| `flash_protect_lamp` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `flash_amber_warn_lamp` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `flash_red_stop_lamp` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `flash_malfunction_indicator_lamp` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `SPN_LSB` | `unsigned long` | suspect parameter number |
| `SPN_MSB` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `FMI` | `unsigned long` | failure mode identifier |
| `occurence_count` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `conversion_method` | `boolean` | 0:低字节在前 1：低字节在后 |

#### FaultDiagnosisFrom297Info

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `tbox_on_line` | `boolean` | Tbox在线状态  1在线状态 |
| `acu_on_line` | `boolean` | ACU在线状态 |
| `symc_on_line` | `boolean` | SYMC在线状态 |
| `ebs_on_line` | `boolean` | EBS在线状态 |
| `ic_on_line` | `boolean` | IC在线状态 |
| `ecm_on_line` | `boolean` | ECM在线状态 |
| `tcu_on_line` | `boolean` | TCU在线状态 |
| `steering_controller_1_on_line` | `boolean` | 转向控制器1在线状态 |
| `sas_sensor_on_line` | `boolean` | SAS传感器在线状态 |
| `wheel_angle_sensor_on_line` | `boolean` | 车轮角度传感器在线状态 |
| `steering_controller_2_on_line` | `boolean` | 转向控制器2在线状态 |
| `wheel_angle_sensor_2_on_line` | `boolean` | 车轮角度传感器2在线状态 |
| `vcu_on_line` | `boolean` | VCU在线状态 |
| `inertial_navigation_on_line` | `boolean` | 惯导在线状态 |
| `electromagnetic_switch_state` | `boolean` | 电磁开关状态 |
| `electric_start_enable_state` | `boolean` | On档电启动使能状态 |
| `vehicle_start_control_state` | `boolean` | 车辆启动控制状态 |
| `enters_unmanned_driving_state` | `boolean` | 进入无人驾驶状态 |
| `vehicle_speed` | `boolean` | 车速 |
| `vehicle_start_state` | `boolean` | 车辆启动状态 |
| `vehicle_gear_state` | `boolean` | 车辆档位状态 |
| `parking` | `boolean` | 驻车状态，0：OFF 1：ON |
| `request_switch_reset_state` | `boolean` | 请求开关复位状态 |
| `car_reset_state` | `boolean` | 车厢复位状态 |
| `can_bus_communication_state` | `boolean` | CAN总线通信状态 |
| `unmanned_driving_system_state` | `boolean` | 无人驾驶系统状态 |
| `steering_system_state` | `boolean` | 转向系统状态 |
| `symc_agrees_state` | `boolean` | SYMC同意状态 |
| `tbox_request_state` | `boolean` | TBOX请求状态 |
| `shift_switching_state` | `boolean` | 档位切换状态 |
| `cloud_exit` | `boolean` | (无人驾驶系统退出) |
| `traffic_brake_take_over` | `boolean` | 行车制动接管 |
| `unmanned_warping_switch_Reset` | `boolean` | 无人驾驶翘板开关复位 |
| `symc_offline` | `boolean` | SYMC掉线 |
| `power_CAN_offline` | `boolean` | 动力CAN掉线 |
| `steering_system_Abnormal` | `boolean` | 转向系统异常 |
| `car_end_Level_3_fault` | `boolean` | 车端3级故障 |
| `unmanned_driving_system_Level_3_fault` | `boolean` | 无人驾驶系统3级故障 |
| `SYMC_abnormally_exits` | `boolean` | SYMC异常退出 |
| `emergency_stop_switch` | `boolean` | 急停开关 |
| `vehicle_end_control_signal_does_not_respond_substandard` | `boolean` | 车端控制信号不响应不达标 |
| `vehicle_control_deviates_from_the_route` | `boolean` | 车控偏离路线 |
| `throttle_take_over_state` | `boolean` | 油门接管状态 |
| `vehicle_failure_grade` | `unsigned long` | 车辆故障等级 |
| `vehicle_error_source` | `unsigned long` | 车辆故障来源 |
| `gearbox_fault_level` | `unsigned long` | 变速箱故障等级 |
| `gearbox_SPN_LSN` | `unsigned long` | 变速箱SPN_LSN |
| `gearbox_SPN_MSB` | `unsigned long` | 变速箱SPN_MSB |
| `gearbox_FMI` | `unsigned long` | 变速箱FMI |
| `steering_1_safety_controller_failure_level` | `unsigned long` | 转向1安全控制器故障等级 |
| `turn_to_1_safety_controller` | `unsigned long` | 转向1安全控制器SPN_LSN |
| `turn_to_1_safety_controller_SPN_MSB` | `unsigned long` | 转向1安全控制器SPN_MSB |
| `steering_1_safety_controller_FMI` | `unsigned long` | 转向1安全控制器FMI |
| `steering_1_main_controller_failure_level` | `unsigned long` | 转向1主控制器故障等级 |
| `turn_to_1_master_controller_SPN_LSN` | `unsigned long` | 转向1主控制器SPN_LSN |
| `go_to_1_master_controller_SPN_MSB` | `unsigned long` | 转向1主控制器SPN_MSB |
| `turn_to_1_main_controller_FMI` | `unsigned long` | 转向1主控制器FMI |
| `steering_2_safety_controller_failure_level` | `unsigned long` | 转向2安全控制器故障等级 |
| `steering_2_safety_controller_SPN_LSN` | `unsigned long` | 转向2安全控制器SPN_LSN |
| `steering_2_Safety_Controller_SPN_MSB` | `unsigned long` | 转向2安全控制器SPN_MSB |
| `steering_2_Safety_Controller_FMI` | `unsigned long` | 转向2安全控制器FMI |
| `steering_2_main_controller_failure_level` | `unsigned long` | 转向2主控制器故障等级 |
| `turn_to_2_master_controller_SPN_LSN` | `unsigned long` | 转向2主控制器SPN_LSN |
| `go_to_2_master_controller_SPN_MSB` | `unsigned long` | 转向2主控制器SPN_MSB |
| `turn_to_2_main_controller_FMI` | `unsigned long` | 转向2主控制器FMI |
| `ControlByte` | `unsigned long` | 控制字节 |
| `TotalNumberByte` | `float` | 总字节包数 |
| `TotalNumberPacket` | `unsigned long` | 总包数 |
| `PGN` | `float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `SequenceNumber` | `unsigned long` | 多包序号 |
| `TP_DT_Byte1` | `unsigned long` | FMI |
| `TP_DT_Byte2_3` | `float` | SPN_LSB |
| `TP_DT_Byte4` | `unsigned long` | 故障等级 |
| `TP_DT_Byte5` | `unsigned long` | 0：无故障 1：车身-组合仪表IC 2：SYMC电器控制系统 3：车身-变速箱TCU 4：车身-潍柴发动机ECU 5：车身-丹佛斯转向系统1 6：车身-丹佛斯转向系统2 |
| `TP_DT_Byte6` | `unsigned long` | SPN_MSB |
| `TP_DT_Byte7` | `unsigned long` | num |
| `DTC_Sequence_number` | `unsigned short` | 电车故障包序号 |
| `FMI` | `unsigned short` | DTC FMI |
| `SPN_MSB` | `unsigned short` | DTC SPN_MSB |
| `SPN_LSB` | `unsigned short` | DTC SPN_LSB |
| `Error_level` | `unsigned short` | 故障等级 |
| `Multi_Send_Enable` | `boolean` | 故障码多包发送使能位 |
| `Fault_resource` | `unsigned short` | 0：无故障 1：车身-组合仪表IC 2：SYMC电器控制系统 3：车身-变速箱TCU 4：车身-潍柴发动机ECU 5：车身-丹佛斯转向系统1 6：车身-丹佛斯转向系统2 |
| `Multi_Send_Num` | `unsigned short` | 多包发送包数 |
| `Auxiliary_driveMotor_Error_list` | `float` | 辅助电机故障列表 |
| `Main_driveMotor_Error_list` | `float` | 主电机故障列表 |
| `Mauxiliary_DriveMotor_Fault` | `unsigned long` | 辅助电机故障码 |
| `Main_DriveMotor_Fault_Code` | `unsigned long` | 主电机故障码 |
| `TCU_SystemFault_Code_Table` | `unsigned long` | TCU系统故障码 |
| `TCU_OtherFault_Code_Table` | `unsigned long` | TCU其他故障码 |
| `occurrence_Count1Copy2` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Conversion_MethodCopy_2` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `steering_1_safety_controller_failure_level_elec` | `unsigned long` | 转向1安全控制器故障等级 |
| `turn_to_1_safety_controller_elec` | `unsigned long` | 转向1安全控制器SPN_LSN |
| `turn_to_1_safety_controller_SPN_MSB_elec` | `unsigned long` | 转向1安全控制器SPN_MSB |
| `steering_1_safety_controller_FMI_elec` | `unsigned long` | 转向1安全控制器FMI |
| `Protect_LampStatusc_Copy_2` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Amber_WarningLamp_Status_Copy_2` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Red_StopLamp_Status_Copy_2` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Malfunction_IndicatorLamp3` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `occurrence_Count1Copy3` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Conversion_MethodCopy_3` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `steering_1_main_controller_failure_level_elec` | `unsigned long` | 转向1主控制器故障等级 |
| `turn_to_1_master_controller_SPN_LSN_elec` | `unsigned long` | 转向1主控制器SPN_LSN |
| `go_to_1_master_controller_SPN_MSB_elec` | `unsigned long` | 转向1主控制器SPN_MSB |
| `turn_to_1_main_controller_FMI_elec` | `unsigned long` | 转向1主控制器FMI |
| `Protect_LampStatusc_Copy_3` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Amber_WarningLamp_Status_Copy_3` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Red_StopLamp_Status_Copy_3` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Malfunction_IndicatorLamp4` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `occurrence_Count1Copy4` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Conversion_MethodCopy_4` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `steering_2_safety_controller_failure_level_elec` | `unsigned long` | 转向2安全控制器故障等级 |
| `steering_2_safety_controller_SPN_LSN_elec` | `unsigned long` | 转向2安全控制器SPN_LSN |
| `steering_2_Safety_Controller_SPN_MSB_elec` | `unsigned long` | 转向2安全控制器SPN_MSB |
| `steering_2_Safety_Controller_FMI_elec` | `unsigned long` | 转向2安全控制器FMI |
| `Protect_LampStatusc_Copy_4` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Amber_WarningLamp_Status_Copy_4` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Red_StopLamp_Status_Copy_4` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Malfunction_IndicatorLamp5` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `occurrence_Count1Copy5` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Conversion_MethodCopy_5` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `steering_2_main_controller_failure_level_elec` | `unsigned long` | 转向2主控制器故障等级 |
| `turn_to_2_master_controller_SPN_LSN_elec` | `unsigned long` | 转向2主控制器SPN_LSN |
| `go_to_2_master_controller_SPN_MSB_elec` | `unsigned long` | 转向2主控制器SPN_MSB |
| `turn_to_2_main_controller_FMI_elec` | `unsigned long` | 转向2主控制器FMI |
| `Protect_LampStatusc_Copy_5` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Amber_WarningLamp_Status_Copy_5` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Red_StopLamp_Status_Copy_5` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Malfunction_IndicatorLamp6` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `course_Angle_condition` | `unsigned long` | 航向角状态(GPS航向初始化标志位) |
| `vehicle_status_marker` | `unsigned long` | 车辆状态标志位 |
| `GpsNumSatsUsed` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `GpsNumSats2Used` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `longegrated_navigation_overall_status_flag_bits` | `unsigned long` | 组合导航总体状态标志位 |

#### TransportProtocolConnectionManage

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `control_byte` | `unsigned long` | 控制字节 |
| `total_message_size` | `unsigned long` | 总信息字节数 |
| `total_number_of_packets` | `unsigned long` | 总包数 |
| `PGN` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### TransportProtocolDataTransfer

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `sequence_number` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `packetized_data` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### WirectlFeedbackInfo

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `engine_torque_mode` | `unsigned long` | 发动机扭矩模式 |
| `main_DriveMotor_Tem_raw` | `short` | 主电机温度 offset -40 |
| `main_driveMotor_speed_raw` | `float` | 主电机转速 offset -6000 |
| `main_DriveMotor_Current_raw` | `float` | 主电机电流 offset -3000 |
| `mainDriveMotorControllerTemp_raw` | `short` | 主控制器温度 offset -40 |
| `Powertrain_outputTorque` | `float` | 动力总成输出扭矩 offset -3000 |
| `actual_engine_fractional_torque` | `float` | 发动机附加扭矩，单位：% |
| `driver_demand_engine_percent_torque` | `short` | 驾驶指令百分比扭矩，单位：% |
| `actual_engine_torque` | `short` | 发动机实际扭矩百分比，单位：% |
| `engine_speed` | `unsigned long` | 发动机转速，单位：rpm |
| `src_address_ctrl_dev_for_engine` | `unsigned long` | 控制发动机的设备地址 |
| `accel_pedal_position` | `short` | 油门踏板位置，单位：% |
| `accelerator_requestStatus` | `boolean` | 油门请求状态 |
| `engine_demand_percent_torque` | `short` | 发动机需求扭矩百分比,单位：% |
| `vehicle_loss_ctrl` | `boolean` | 车辆失控 |
| `brake_pedal_SYMC` | `short` | SYMC制动深度，单位：% |
| `actual_retarder_percent_torque` | `short` | 缓速器档位，单位：% |
| `foot_valve_info` | `float` | 脚阀传感器信号，单位：Mpa |
| `parking_brake_signal_status` | `boolean` | 驻车制动信号状态，0：驻车 1：不驻车(车辆直接反馈) |
| `exhaust_brake_status` | `boolean` | 排气制动状态，0：断开 1：闭合 |
| `hydraulic_startup_status` | `boolean` | 液压缓行启动状态，0：OFF 1:ON |
| `steering_angle_request` | `float` | 单位：度 |
| `steering_left_angle_info` | `float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `steering_right_angle_info` | `float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `trans_current_gear` | `long` | 变速箱当前档位信息 |
| `Accumulated_charged_electric_quanlity` | `float` | 累计充电电量 |
| `Accumulated_discharged_electric_quanlity` | `float` | 累计放电电量 |
| `Single_charged_electric_quanlity` | `short` | 单次充电电量 |
| `Battery_power_raw` | `float` | 电池功率 |
| `engine_total_hours` | `float` | 发动机工作总时长 |
| `battery_voltage` | `float` | 蓄电池电压 |
| `Acc_Power_On_Status` | `unsigned long` | Acc电源状态 |
| `Charge_Gun_Status` | `unsigned long` | 充电枪状态 |
| `Force_State` | `unsigned long` | 取力状态 |
| `Vehicle_Remote_Start_Error` | `unsigned long` | 车辆远程启动错误 |
| `transmission_requested_range` | `unsigned long` | 变速器请求换档范围 |
| `shifting_gears` | `unsigned long` | 是否正在换档 |
| `Locking_gearboxTorqueConverter` | `unsigned long` | 缓速器锁止状态 |
| `lift_condition` | `unsigned long` | 货箱升降状态， |
| `lift_longo_position_status` | `boolean` | 举升到位接近开光状态，0：OFF 1：ON |
| `drop_longo_position_status` | `boolean` | 下降到位接近开光状态，0：OFF 1：ON |
| `left_turn_start_status` | `boolean` | 左转向灯启动状态，0：OFF 1：ON |
| `right_turn_start_status` | `boolean` | 右转向灯启动状态，0：OFF 1：ON |
| `hazard_start_status` | `boolean` | 危险警报灯启动状态，0：OFF 1：ON |
| `flash_start_status` | `boolean` | 爆闪灯启动状态，0：OFF 1：ON |
| `fog_lamp_start_status` | `boolean` | 雾灯启动状态，0：OFF 1：ON |
| `outline_lamp_start_status` | `boolean` | 示廓灯启动状态，0：OFF 1：ON |
| `low_light_start_status` | `boolean` | 近光灯启动状态，0：OFF 1：ON |
| `high_beam_start_status` | `boolean` | 远光灯启动状态，0：OFF 1：ON |
| `head_light_status` | `boolean` | 前照灯状态，0：OFF 1：ON |
| `rear_light_start_status` | `boolean` | 后照灯启动状态，0：OFF 1：ON |
| `wiper_start_status` | `boolean` | 雨刮喷水启动状态，0：OFF 1：ON |
| `wiper_longermit_start_status` | `boolean` | 雨刮间歇启动状态，0：OFF 1：ON |
| `wiper_slow_start_status` | `boolean` | 雨刮低速启动状态，0：OFF 1：ON |
| `wiper_high_start_status` | `boolean` | 雨刮高速启动状态，0：OFF 1：ON |
| `horn_start_status` | `boolean` | 喇叭启动状态，0：OFF 1：ON |
| `axle_differential` | `boolean` | 轴差，0：OFF 1：ON |
| `go_to_unmanned_status` | `boolean` | 进入无人驾驶状态，0：不允许 1：允许 |
| `slow_light_start_status` | `boolean` | 缓行灯启动状态，0：OFF 1：ON |
| `brake_light_start_status` | `boolean` | 制动灯启动状态，0：OFF 1：ON |
| `revers_light_start_status` | `boolean` | 倒车灯启动状态，0：OFF 1：ON |
| `charge_indicator` | `boolean` | 充电指示，0：OFF 1：ON |
| `vehicle_status` | `unsigned long` | 车辆状态 0： On档 1：启动 2：启动失败 |
| `Front_L_tire_pressure` | `unsigned long` | 前左轮轮胎压力 |
| `Front_R_tire_pressure` | `unsigned long` | 前右轮轮胎压力 |
| `MID_LL_tire_pressure` | `unsigned long` | 中左左轮轮胎压力 |
| `MID_LR_tire_pressure` | `unsigned long` | 中左右轮轮胎压力 |
| `MID_RL_tire_pressure` | `unsigned long` | 中右左轮轮胎压力 |
| `MID_RR_tire_pressure` | `unsigned long` | 中右右轮轮胎压力 |
| `BACK_LL_tire_pressure` | `unsigned long` | 后左左轮轮胎压力 |
| `BACK_LR_tire_pressure` | `unsigned long` | 后左右轮轮胎压力 |
| `BACK_RL_tire_pressure` | `unsigned long` | 后右左轮轮胎压力 |
| `BACK_RR_tire_pressure` | `unsigned long` | 后右右轮轮胎压力 |
| `Brake_Pressure_FA_Left` | `unsigned long` | 制动压力前桥左 |
| `Brake_Pressure_FA_Right` | `unsigned long` | 制动压力前桥右 |
| `Brake_Pressure_RA1_Left` | `unsigned long` | 制动压力后桥左 |
| `Brake_Pressure_RA1_Right` | `unsigned long` | 制动压力前桥右 |
| `Front_L_tire_temperature` | `short` | 前左轮胎温 |
| `Front_R_tire_temperature` | `short` | 前右轮胎温 |
| `MID_LL_tire_temperature` | `short` | 中左左轮胎温 |
| `MID_LR_tire_temperature` | `short` | 中左右轮胎温 |
| `MID_RL_tire_temperature` | `short` | 中右左轮胎温 |
| `MID_RR_tire_temperature` | `short` | 中右右轮胎温 |
| `BACK_LL_tire_temperature` | `short` | 后左左轮胎温 |
| `BACK_LR_tire_temperature` | `short` | 后左右轮胎温 |
| `BACK_RL_tire_temperature` | `short` | 后右左轮胎温 |
| `BACK_RR_tire_temperature` | `short` | 后右右轮胎温 |
| `Front_L_tire_code` | `unsigned short` | 前左轮胎压故障码 |
| `Front_R_tire_code` | `unsigned short` | 前右轮胎压故障码 |
| `MID_LL_tire_code` | `unsigned short` | 中左左轮胎压故障码 |
| `MID_LR_tire_code` | `unsigned short` | 中左右轮胎压故障码 |
| `MID_RL_tire_code` | `unsigned short` | 中右左轮胎压故障码 |
| `MID_RR_tire_code` | `unsigned short` | 中右右轮胎压故障码 |
| `BACK_LL_tire_code` | `unsigned short` | 后左左轮胎压故障码 |
| `BACK_LR_tire_code` | `unsigned short` | 后左右轮胎压故障码 |
| `BACK_RL_tire_code` | `unsigned short` | 后右左轮胎压故障码 |
| `BACK_RR_tire_code` | `unsigned short` | 后右右轮胎压故障码 |
| `air_power_status` | `boolean` | 空调开关状态 |
| `air_auto_status` | `boolean` | 空调自动状态 |
| `air_ac_status` | `boolean` | 空调AC制冷状态 |
| `air_defrost_status` | `boolean` | 空调除霜状态 |
| `air_cyclemode_status` | `boolean` | 循环模式状态 |
| `air_outletmode_status` | `unsigned short` | 吹风模式状态 |
| `air_level_status` | `unsigned short` | 空调风量状态 |
| `air_temperature_statusR` | `unsigned short` | 设定温度 |
| `cab_temperature_status` | `short` | 驾驶舱温度 |
| `TM_Torque` | `short` | TM电机实际扭矩 -3200-3200 |
| `IM_Torque` | `short` | IM电机实际扭矩 -3200-3225 |
| `IM_Speed` | `short` | IM电机实际转速 -8000-8063 |
| `Parking_generation_status` | `unsigned short` | 停车发电状态 |
| `chassis_rolling` | `boolean` | 底盘自检测溜车状态状态 |
| `Bucket_pitch` | `float` | 货箱俯仰角 |
| `friction_torque` | `short` | 摩擦扭矩 |
| `L_brake_temperature` | `short` | 左刹车片温度-100～700 |
| `R_brake_temperature` | `short` | 右刹车片温度-100～700 |
| `Maximum_remaining_electric_braking` | `short` | 最大剩余电制动% |
| `OutputTorqueGearbox` | `short` | 变速箱输出轴扭据 |
| `symcReqElecBrake` | `short` | SYMC请求的电制动0-100% |
| `symcElecTorqueResp` | `short` | 底盘反馈的SYMC请求的电制动0-100% |
| `autoElecTorqueResp` | `short` | 底盘反馈的无人驾驶请求的电制动0-100% |
| `gear_state` | `short` | 档位状态0--N档 1--D档 2--R档 3--M档4--S档 |
| `clutchState` | `short` | 离合状态 1结合 0分离 2半联动 |
| `high_voltage_state` | `boolean` | 高压状态 |
| `is_arrving_wall` | `boolean` | 是否抵墙 |
| `spray_state` | `boolean` | 喷淋状态反馈 |
| `TarpaulinCloseLimitSwStatus` | `boolean` | 篷布关闭到位开关状态（盖上/合上到位） ０:不使能　1: 使能 |
| `TarpaulinOpenLimitSwStatus` | `boolean` | 篷布打开到位开关状态（开启到位）０:不使能　1: 使能 |
| `TarpaulinState` | `short` | 篷布状态   篷布状态  0:未收到传感器数值  1:篷布正在覆盖  2:篷布正在敞开  3:篷布覆盖到位  4:篷布敞开到位 5:篷布系统故障  6：篷布静止未到位 |
| `EBS_red_alarmlight` | `unsigned short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `EBS_yellow_alarmlight` | `unsigned short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `Brake_Hold_state` | `unsigned short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `VCU_Version` | `string` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `ramp_sensor` | `short` | 坡度传感器 |
| `rxauxiliary_DriveMotor_Tem_raw` | `short` | 辅电机温度 offset -40 |
| `rxauxiliary_driveMotor_speed_raw` | `float` | 辅电机转速 offset -6000 |
| `rxauxiliary_DriveMotor_Current_raw` | `float` | 辅电机电流 offset -3000 |
| `rxauxiliarDriveMotorControllerTemp_raw` | `short` | 辅电机控制器温度 offset -40 |
| `endurance_mileage` | `unsigned long` | 剩余里程 |
| `power_consumption` | `unsigned long` | 瞬时电耗 |
| `SOC` | `unsigned long` | 荷电状态（剩余电量） |
| `charging_state` | `unsigned long` | 充电状态 |
| `Battery_State` | `unsigned long` | 电池状态 |
| `Battery_Current` | `float` | 电池电流 offset -1000 |
| `Battery_Temperature_raw` | `short` | 电池温度 offset -40 |
| `Battery_Fault_Status` | `unsigned long` | 电池故障状态 |
| `energy_recover` | `unsigned long` | 能量回收状态 |
| `engine_coolant_temperature` | `float` | 发动机冷却水温度，单位：摄氏度 |
| `fuel_temperature` | `float` | 燃油温度，单位：摄氏度 |
| `engine_oil_temperature` | `float` | 机油温度，单位：摄氏度 |
| `engine_oil_level` | `float` | 机油液位，单位：% |
| `engine_oil_pressure` | `float` | 机油压力，单位：kpa |
| `engine_fuel_rate` | `float` | 油耗，单位：L/h |
| `engine_instant_fuel_economy` | `float` | 瞬时油耗，单位：km/L |
| `engine_average_fuel_economy` | `float` | 平均燃油经济性，单位：km/L |
| `fuel_level` | `short` | 油量，单位：% |
| `Engine_wait_to_lamp` | `unsigned long` | 发动机进气预热指示 |
| `air_pressure_1` | `float` | 单位：MPa |
| `air_pressure_2` | `float` | 单位：MPa |
| `front_axle_running_brake_pressure_input` | `float` | 前桥行车制动压力输入0~2.55 |
| `brake_pressure_input_in_middle_axle` | `float` | 中桥行车制动压力输入0~2.55 |
| `rear_axle_running_brake_pressure_input` | `float` | 后桥行车制动压力输入0~2.55 |
| `weighing_cell_1` | `unsigned long` | 称重传感器10~255 |
| `weighing_cell_2` | `unsigned long` | 称重传感器20~255 |
| `num_297` | `unsigned long` | 297与控制的心跳 |
| `front_left_wheel_speed` | `float` | 前左轮车速，单位：km/h |
| `front_right_wheel_speed` | `float` | 前右轮车速，单位：km/h |
| `rear_left_wheel_speed` | `float` | 后左轮车速，单位：km/h |
| `rear_right_wheel_speed` | `float` | 后右轮车速，单位：km/h |
| `tachograph_output_shaft_speed` | `float` | 变速箱输出轴转速，单位： rpm |
| `tachograph_vehicle_speed` | `float` | 车速，单位：km/h |
| `total_vehicle_distance` | `float` | 单位：m |
| `engine_totalFuel_used` | `float` | 发动机总油耗 单位：L |
| `trip_fuel_consumption` | `float` | 发动机旅程油耗 单位：L |
| `steering_left_angle_info_mv` | `float` | 单位：mv |
| `steering_right_angle_info_mv` | `float` | 单位：mv |
| `vcu_state` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `udpindex` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `dm1_ecm_diag_trouble` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `ecm_tpcm` | `TransportProtocolConnectionManage` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `ecm_tpdt` | `TransportProtocolDataTransfer` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `dm1_tcu_diag_trouble` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tcu_tpcm` | `TransportProtocolConnectionManage` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tcu_tpdt` | `TransportProtocolDataTransfer` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `sca_diag_trouble` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `sca_diag_trouble1` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `sca_diag_trouble2` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `mca_diag_trouble` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `mca_diag_trouble1` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `mca_diag_trouble2` | `ActiveDiagnosticTroubleCodes` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `on_voltage` | `float` | On档电压 |
| `parking` | `boolean` | 驻车，0：OFF 1：ON |
| `low_air_pressure_alarm` | `unsigned long` | 气压过低警报，01：ON 10：OFF |
| `Autopilot_enable_bit` | `boolean` | 自动驾驶使能位 |
| `Power_control_enable_bit` | `boolean` | 动力控制使能位 |
| `Shift_request_status` | `boolean` | 挂挡请求状态 |
| `accelerator_request_status` | `boolean` | 油门请求状态 |
| `Parking_request` | `boolean` | 驻车请求 |
| `Brake_request_status` | `boolean` | 刹车请求状态 |
| `No15_power_supply_control` | `boolean` | 15号电源控制 |
| `Brake_light_control` | `boolean` | 制动灯控制 |
| `Gear_request` | `unsigned long` | 挡位请求 |
| `ACC_power_control` | `boolean` | ACC电源控制 |
| `Container_control` | `unsigned long` | 货箱控制 |
| `Left_turn_signal` | `boolean` | 左转向灯 |
| `Right_turn_signal` | `boolean` | 右转向灯 |
| `accelerator_request` | `unsigned long` | 油门请求 |
| `Brake_request` | `unsigned long` | 制动请求 |
| `backup_light` | `boolean` | 倒车灯控制 |
| `Fog_lamp_control` | `boolean` | 雾灯控制 |
| `Small_lights` | `boolean` | 小灯控制 |
| `lower_beam_lamb` | `boolean` | 近光灯控制 |
| `higher_beam_lamb` | `boolean` | 远光灯控制 |
| `Wipers_spray_water` | `boolean` | 雨刮喷水控制 |
| `Wiper_intermission` | `boolean` | 雨刮间歇控制 |
| `Wiper_low_speed` | `boolean` | 雨刮低速控制 |
| `Wiper_high_speed` | `boolean` | 雨刮高速控制 |
| `Axle_lock_control` | `boolean` | 轴锁控制 |
| `Power_takeoff_start` | `boolean` | 取力启动 |
| `Flash_lamp` | `boolean` | 爆闪灯控制 |
| `Headlights` | `boolean` | 前照灯控制 |
| `Rear_light` | `boolean` | 后照灯控制 |
| `Horn_on` | `boolean` | 喇叭启动 |
| `S_gear_control` | `boolean` | S档控制 |
| `Emergency_Stop_Switch_Status60` | `boolean` | 急停开关状态 |
| `Hazard_Warning_Lamp_Control54` | `boolean` | 危险警报灯控制 |
| `Remote_or_Unman` | `unsigned long` | 反馈8为遥控   6为无人 |
| `Steer_1_Curvature_control` | `unsigned short` | VCU外发转向系统1控制曲率 |
| `Steer_2_Curvature_control` | `unsigned short` | VCU外发转向系统2控制曲率 |

### [gps_imu_msg.idl](../../dvpn-pnc-common/idl/gps_imu_msg.idl)


#### tmdesc

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `tm_sec` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_min` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_hour` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_mday` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_mon` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_year` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_wday` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_yday` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_isdst` | `short` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_gmtoff` | `long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `tm_zone[20]` | `char` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### GpsImuInfo

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `gps_week` | `long` | 自 1980-1-6 至当前的星期数（格林尼治时间）     wwww                1451 |
| `gps_time` | `float` | 自本周日 0:00:00 至当前的秒数（格林尼治时间）  ssssss.sss           368123.300 |
| `time_record` | `tmdesc` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_year` | `float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_month` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_day` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_hour` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_minute` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_second` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `gps_msec` | `unsigned long long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `gps_rtk_status` | `long` | 差分状态 |
| `gps_lat` | `double` | 纬度 |
| `gps_lng` | `double` | 经度 |
| `gps_heading` | `float` | 航向角 |
| `gps_ve` | `float` | 东向速度 |
| `gps_vn` | `float` | 北向速度 |
| `gps_vd` | `float` | 天向速度 |
| `gps_speed` | `float` | 合速度 km/h |
| `gps_days_to_speed` | `float` | 天向速度 |
| `gps_height` | `float` | 海拔 |
| `imu_acc_y` | `float` | 纵向加速度 |
| `imu_acc_x` | `float` | 侧向加速度 |
| `imu_yaw_z` | `float` | 横摆角速度 |
| `imu_v_y` | `float` | y轴角速度 |
| `imu_v_x` | `float` | x轴角速度 |
| `imu_v_z` | `float` | z轴角速度 |
| `gps_satellite_front` | `long` | 前天线卫星数 |
| `gps_satellite_rear` | `long` | 后天线卫星数 |
| `gps_yaw` | `float` | 偏航 |
| `gps_roll` | `float` | 横滚 |
| `gps_pitch` | `float` | 俯仰 |

### [imple_msg.idl](../../dvpn-pnc-common/idl/imple_msg.idl)


#### ImpleResultInfo

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `engine_override_ctrl_mode` | `unsigned long` | 控制模式，00:Override disabled |
| `engine_req_speed_ctrl_condition` | `unsigned long` | 请求速度控制状态 |
| `override_ctrl_mode_priority` | `unsigned long` | 控制模式优先级 |
| `engine_req_speed` | `float` | 请求速度，单位：rpm |
| `engine_req_torque` | `float` | 请求转矩,单位：% |
| `trans_req_gear` | `unsigned long` | 档位 |
| `trans_gearbox_highest_gear_setting` | `unsigned long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `driver_demand_retarder_CCU` | `float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `unmanned_enable` | `boolean` | 无人驾驶就绪状态，0:未就绪　1:就绪 |
| `on_start_enable` | `boolean` | ON档电启动使能，０:不使能　1: 使能 |
| `vehicle_startup_ctrl` | `boolean` | 车辆启动控制，0:断开 1:闭合 |
| `flash_ctrl` | `boolean` | 爆闪灯控制，0: 断开 1: 闭合 |
| `container_ctrl` | `unsigned long` | 货箱控制 |
| `Remote_enable` | `octet` | 遥控驾驶使能位 非5:启动  5：未启动 |
| `left_turn_signal_ctrl` | `boolean` | 左转向灯控制，0:断开 1:闭合 |
| `right_turn_signal_ctrl` | `boolean` | 右转向灯控制，0:断开 1:闭合 |
| `fog_lamp_ctrl` | `boolean` | 雾灯控制，0:断开 1:闭合 |
| `outline_lamp_ctrl` | `boolean` | 示廓灯控制，0：断开 1：闭合 |
| `low_light_ctrl` | `boolean` | 近光灯控制，0：断开 1：闭合 |
| `high_beam_ctrl` | `boolean` | 远光灯控制，0：断开 1：闭合 |
| `wiper_ctrl` | `boolean` | 雨刮喷水控制，0：断开 1：闭合 |
| `wiper_longermittent_ctrl` | `boolean` | 雨刮间歇控制，0：断开 1：闭合 |
| `wiper_low_speed_ctrl` | `boolean` | 雨刮低速控制，0：断开 1：闭合 |
| `wiper_high_speed_ctrl` | `boolean` | 雨刮高速控制，0：断开 1：闭合 |
| `hazard_warn_lamp_ctrl` | `boolean` | 危险警报灯控制，0：断开 1：闭合 |
| `rear_light_ctrl` | `boolean` | 后照灯控制，0：断开 1：闭合 |
| `horn_start` | `boolean` | 喇叭控制，0：断开 1：闭合 |
| `parking_req` | `boolean` | 驻车请求，0：驻车 1：解除 |
| `brake_req_status` | `boolean` | 刹车请求状态，0：未请求 1：已请求 |
| `hydraulic_retarder_ctrl` | `boolean` | 液压缓行控制，0：断开 1：闭合 |
| `axial_ctrl` | `boolean` | 轴差控制，0：断开 1：闭合 |
| `traffic_brake_req` | `float` | 行车制动请求 |
| `exhaust_brake_req` | `boolean` | 排气制动控制，0：断开 1：闭合 |
| `brake_light_ctrl` | `boolean` | 制动灯控制，0：断开 1：闭合 |
| `slow_light_ctrl` | `boolean` | 液压缓行灯控制，0：断开 1：闭合 |
| `head_light_ctrl` | `boolean` | 前照灯控制，0：断开 1：闭合 |
| `steering_angle_req` | `float` | Factor:0.1; Offset: -50 |
| `active_spd` | `float` | 车速 |
| `yaw_rate` | `float` | 横摆角速度 |
| `drive_direction` | `long` | 车辆行驶方向 |
| `desire_speed` | `float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `write_speaker_control_ctr` | `boolean` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `set_max_gear` | `long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `vilid_count` | `octet` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `dump_complete` | `boolean` | 卸料完成 |
| `remote_unman_flag` | `int32` | 无人或者遥控发送消息的标志位 |
| `release_measure` | `boolean` | 释放安全措施 -true请求触发 |
| `protect_measure` | `boolean` | 进入安全措施 -true请求触发 |
| `Electric_braking` | `float` | 电制动 |
| `Parking_airSup_state` | `boolean` | 停车补气状态 |
| `Engine_cylinder_braking` | `boolean` | 发动机缸内制动 |
| `rolling_flag` | `boolean` | 是否溜车 |
| `spray_control` | `boolean` | 喷淋控制 |
| `tarpaulin_control` | `short` | 蓬布控制 //蓬布 0 "无效" 1 "篷布开" 2 "篷布关" 3 "动作暂停" |
| `clutchControlCmd` | `boolean` | 离合控制 |

### [safe_ctrl_msg.idl](../../dvpn-pnc-common/idl/safe_ctrl_msg.idl)


#### SafeFeedbackInfo

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `rolling_need_stop` | `boolean` | 溜车 |
| `parking_rolling` | `boolean` | 驻车溜车 |
| `braking_failure` | `boolean` | 刹车异常 |
| `slip_rep` | `boolean` | 打滑 |
| `distance_stop_flag` | `boolean` | 位置突变停车 |
| `position_nochange_flag` | `boolean` | 位置不变停车 |
| `parking_failure` | `boolean` | 驻车异常 |
| `braking_deceleration_weakened` | `boolean` | 制动力衰减 |
| `braking_heat_fade` | `boolean` | 热衰退故障 |
| `AEB_stop` | `boolean` | AEB停车 |
| `transmission_state` | `boolean` | 退出安全防护状态反馈 |

### [obst_msg.idl](../../dvpn-pnc-common/idl/obst_msg.idl)

#### DDS_SensorFuseResults / DDS_SensorFuseResult（周围信息读取字段）

以下列出 `0x07` 转换链实际读取的字段；其他完整字段以 [obst_msg.idl](dvpn-pnc-common/idl/obst_msg.idl) 为准。`DDS_SensorFuseResults.stamp`、雾尘比例、天气和 `idx` 不写入此 TCP 报文。

| 结构 | 字段 | IDL 类型 | 转换用途 |
| --- | --- | --- | --- |
| `DDS_SensorFuseResults` | `objects` | `sequence<DDS_SensorFuseResult, 200>` | 每路最多 200 条；前后两路依次合并 |
| `DDS_SensorFuseResult` | `type` | `unsigned long`，默认 0 | 0 读取包围盒，1 读取散点 |
| 同上 | `box` | `DDS_Box` | `type==0` 时读取 |
| 同上 | `scatter` | `DDS_ScatterObject` | `type==1` 时读取 |
| `DDS_Box` | `object_type` | `unsigned long`，默认 0 | 加 3 后写 TCP 障碍物类型；IDL 注释给出 0 未知、1 宽体车，其他类别依标注定义 |
| 同上 | `object_width` / `object_height` | `float`，默认 0 | 宽度/高度乘 100，转整数厘米 |
| 同上 | `center` | `float[3]`，默认 0 | IDL 注明以车辆后轴中心为原点；取 `[1]` 为纵向、`[0]` 为横向并乘 100 |
| `DDS_ScatterObject` | `center` | `float[3]`，默认 0 | 同样取 `[1]` 为纵向、`[0]` 为横向并乘 100；`[2]` 不发送 |


#### DDS_LidarPoint

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `point_x` | `@ default(0) float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `point_y` | `@ default(0) float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `point_z` | `@ default(0) float` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `intensity` | `@ default(0) octet` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_stamp` | `@ default(0) octet` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `ring` | `@ default(0) octet` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### DDS_LidarCloud

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `stamp` | `@ default(0) long long` | 发出时间戳（雷达/驱动侧），ms |
| `points` | `sequence<DDS_LidarPoint, 800000>` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### DDS_FusionCurbMessage

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `stamp` | `@ default(0) long long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `support_secondary_discharge` | `@ default(true) boolean` | 是否支持二次卸料 |
| `curbs` | `DDS_LidarCloud` | 输出后向的所有挡墙点云，包括左右 |
| `distance` | `@ default(100) float` | 后向挡墙与车辆后轴中心的y方向的最短距离，单位m |
| `left_wheel_distance` | `@ default(100) float` | 左边轮到后向挡墙的距离，单位m |
| `right_wheel_distance` | `@ default(100) float` | 右边轮到后向挡墙的距离，单位m |
| `comple_rate` | `@ default(0) float` | 挡墙高度完整率，挡墙高度超过0.8m的占比 |

### [v2x_msg.idl](dvpn-pnc-common/idl/v2x_msg.idl)

#### CloudRoundVehicleList / CloudVehicleInfo（周围信息读取字段）

以下只列出 `0x07` 实际读取的字段；`CloudRoundVehicleList.timestamp` 与车辆的 VIN、速度、状态、尺寸和路径等字段不写入该报文。

| 结构 | 字段 | IDL 类型 | 转换用途 |
| --- | --- | --- | --- |
| `CloudRoundVehicleList` | `vehicles` | `sequence<CloudVehicleInfo, 50>` | 周边车辆列表，按原顺序逐条转换 |
| `CloudVehicleInfo` | `device_type` | `int32` | 转为 TCP 单字节设备类型 |
| 同上 | `longitude` | `double` | 经度加 180、乘 10000000 后转 `SANY_U32` |
| 同上 | `latitude` | `double` | 纬度加 90、乘 10000000 后转 `SANY_U32` |
| 同上 | `heading` | `float` | 航向角乘 10 后转 `SANY_U16` |

### [collection_control_request.idl](../../dvpn-pnc-common/idl/collection_control_request.idl)


#### CollectionControlRequest

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `task_id` | `string` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_stamp` | `unsigned long long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `request_id` | `unsigned long` | 指令序号，同一task内逐条自增 |
| `collection_area_type` | `AreaType` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `control_client_type` | `ControlClientType` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `collection_command` | `CollectionCommand` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### 枚举 ControlClientType

| 值 | 序号 | 声明说明 |
| --- | --- | --- |
| `PAD` | 0 |  PAD端 |
| `REMOTE_COCKPIT` | 1 |  遥控驾驶舱 |
| `WEB` | 2 |  Web端 |

#### 枚举 CollectionCommand

| 值 | 序号 | 声明说明 |
| --- | --- | --- |
| `CMD_NULL` | 0 |  占位 |
| `CMD_START` | 1 |  开始采集 |
| `CMD_PAUSE` | 2 |  暂停 |
| `CMD_STOP_AND_UPLOAD` | 3 |  停止采集并上传 |
| `CMD_STOP_AND_ABORT` | 4 |  停止采集并中止 |

#### 枚举 AreaType

| 值 | 序号 | 声明说明 |
| --- | --- | --- |
| `LOAD_AREA` | 0 |  装载区 |
| `ROAD` | 1 |  道路 |
| `DUMP_AREA` | 2 |  排土场 |
| `DUMP_LINE` | 3 |  排土线 |
| `OTHER_AREA` | 4 |  其他区域  |

### [collection_control_reply.idl](../../dvpn-pnc-common/idl/collection_control_reply.idl)


#### CollectionControlReply

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `task_id` | `string` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `time_stamp` | `unsigned long long` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `request_id` | `unsigned long` | 原样返回请求中的request_id |
| `reply_code` | `CollectionReplyCode` | 按 IDL 声明传输；业务用途见 DDS 接口表 |
| `reply_message` | `string` | 按 IDL 声明传输；业务用途见 DDS 接口表 |

#### 枚举 CollectionReplyCode

| 值 | 序号 | 声明说明 |
| --- | --- | --- |
| `ACCEPTED` | 0 |  命令已接受 |
| `REJECTED_BUSY` | 1 |  系统忙碌 |
| `REJECTED_UNSUPPORTED` | 2 |  不支持的命令 |
| `INTERNAL_ERROR` | 3 |  内部错误 |

### [collection_status_report.idl](../../dvpn-pnc-common/idl/collection_status_report.idl)


#### CollectionStatusReport

| 字段 | IDL 类型/默认注解 | 声明说明 |
| --- | --- | --- |
| `task_id` | `string` | 当前任务ID |
| `time_stamp` | `unsigned long long` | 状态上报时间戳(ms) |
| `lon` | `double` | 经度 |
| `lat` | `double` | 纬度 |
| `alt` | `double` | 高程 |
| `heading` | `double` | 航向角 |
| `speed` | `double` | 速度 |
| `state` | `CollectionState` | 采集状态 |
| `status_text` | `string` | 状态文本 |
| `laser_status` | `SensorStatus` | 点云状态 |
| `ins_status` | `SensorStatus` | 定位状态 |
| `file_state` | `FileState` | 文件状态 |
| `collect_duration_ms` | `unsigned long long` | 采集持续时间(ms) |

#### 枚举 CollectionState

| 值 | 序号 | 声明说明 |
| --- | --- | --- |
| `STATE_IDLE` | 0 |  空闲 |
| `STATE_COLLECTING` | 1 |  采集中 |
| `STATE_PAUSED` | 2 |  暂停采集 |
| `STATE_STOPPING` | 3 |  停止中 |
| `STATE_COLLECTION_ENDED` | 4 |  采集结束 |
| `STATE_ERROR` | 5 |  任务错误 |

#### 枚举 SensorStatus

| 值 | 序号 | 声明说明 |
| --- | --- | --- |
| `SENSOR_OFFLINE` | 0 |  无数据 |
| `SENSOR_ONLINE` | 1 |  有数据 |

#### 枚举 FileState

| 值 | 序号 | 声明说明 |
| --- | --- | --- |
| `FILE_IDLE` | 0 |  空闲 |
| `FILE_SAVING` | 1 |  保存中 |
| `FILE_UPLOADING` | 2 |  上传中 |
| `FILE_UPLOADED` | 3 |  上传完成 |

## 附录 C：仓库全部 DDS IDL 定义目录

以下穷举 include/ddsType 与 dvpn-pnc-common/idl 下的 IDL 文件及其结构体/枚举。除第 4 节明确列出的端点外，未发现现存遥控业务创建其他发布订阅；点击文件查看完整字段契约。嵌套结构不是独立业务端点。

| IDL 文件 | 全部结构体 | 全部枚举 |
| --- | --- | --- |
| [dvpn-pnc-common/idl/ahead_vehicle_trigger.idl](../../dvpn-pnc-common/idl/ahead_vehicle_trigger.idl) | AheadVehicleInfo、AheadVehicleTrigger |  |
| [dvpn-pnc-common/idl/ChassisCtrl.idl](../../dvpn-pnc-common/idl/ChassisCtrl.idl) | ActiveDiagnosticTroubleCodes、FaultDiagnosisFrom297Info、TransportProtocolConnectionManage、TransportProtocolDataTransfer、WirectlFeedbackInfo |  |
| [dvpn-pnc-common/idl/collection_control_reply.idl](../../dvpn-pnc-common/idl/collection_control_reply.idl) | CollectionControlReply | CollectionReplyCode |
| [dvpn-pnc-common/idl/collection_control_request.idl](../../dvpn-pnc-common/idl/collection_control_request.idl) | CollectionControlRequest | ControlClientType、CollectionCommand、AreaType |
| [dvpn-pnc-common/idl/collection_status_report.idl](../../dvpn-pnc-common/idl/collection_status_report.idl) | CollectionStatusReport | CollectionState、SensorStatus、FileState |
| [dvpn-pnc-common/idl/ConfigMsg.idl](../../dvpn-pnc-common/idl/ConfigMsg.idl) | ConfigReq、ConfigRes |  |
| [dvpn-pnc-common/idl/cross_msg.idl](../../dvpn-pnc-common/idl/cross_msg.idl) | RoadCrossType | RoadCrossType_Enum |
| [dvpn-pnc-common/idl/ctl_msg.idl](../../dvpn-pnc-common/idl/ctl_msg.idl) | CtlResultInfo |  |
| [dvpn-pnc-common/idl/data_aggregation.idl](../../dvpn-pnc-common/idl/data_aggregation.idl) | DataRecSetting、DataRecConfVersion、TriggerDataRec、CompleteRecord、CompleteRecord、DataRecordReport、UploadCmd、UploadReport、UploadReportAck |  |
| [dvpn-pnc-common/idl/decide_behavior_control.idl](../../dvpn-pnc-common/idl/decide_behavior_control.idl) | TaskParkPoint、TaskBehaviorControl | Task_Drive_Control |
| [dvpn-pnc-common/idl/decide_msg.idl](../../dvpn-pnc-common/idl/decide_msg.idl) | VehiclePos、xyPathInfo、ObstTTCInfo、ObstInfoOut、ObstAvoidInfo | Plan_type、DriveStatus |
| [dvpn-pnc-common/idl/decide_plan_msg.idl](../../dvpn-pnc-common/idl/decide_plan_msg.idl) | DecidePlanInfo |  |
| [dvpn-pnc-common/idl/decide_platform_msg.idl](../../dvpn-pnc-common/idl/decide_platform_msg.idl) | DecidePlatformInfo |  |
| [dvpn-pnc-common/idl/decide_state_machine.idl](../../dvpn-pnc-common/idl/decide_state_machine.idl) | TaskBehaviorState | Task_Main_State、Task_Sub_State |
| [dvpn-pnc-common/idl/FaultRepair.idl](../../dvpn-pnc-common/idl/FaultRepair.idl) | FaultRepair_req、FaultRepair_res |  |
| [dvpn-pnc-common/idl/FaultReport.idl](../../dvpn-pnc-common/idl/FaultReport.idl) | FaultReport_req、FaultReport_res |  |
| [dvpn-pnc-common/idl/fluctuation_msg.idl](../../dvpn-pnc-common/idl/fluctuation_msg.idl) | roadFluctuationInfo |  |
| [dvpn-pnc-common/idl/gk_debug_msg.idl](../../dvpn-pnc-common/idl/gk_debug_msg.idl) | GkDebugMsg |  |
| [dvpn-pnc-common/idl/global_planning.idl](../../dvpn-pnc-common/idl/global_planning.idl) | GlobalPlanning |  |
| [dvpn-pnc-common/idl/gps_imu_msg.idl](../../dvpn-pnc-common/idl/gps_imu_msg.idl) | tmdesc、GpsImuInfo |  |
| [dvpn-pnc-common/idl/gps_msg.idl](../../dvpn-pnc-common/idl/gps_msg.idl) | GPSInfo | Slope_type |
| [dvpn-pnc-common/idl/imple_msg.idl](../../dvpn-pnc-common/idl/imple_msg.idl) | ImpleResultInfo |  |
| [dvpn-pnc-common/idl/local_task_msg.idl](../../dvpn-pnc-common/idl/local_task_msg.idl) | obsPosition、behavioralFeedbackInfo |  |
| [dvpn-pnc-common/idl/log_local_msg.idl](../../dvpn-pnc-common/idl/log_local_msg.idl) | logLocalInfo |  |
| [dvpn-pnc-common/idl/log_task_msg.idl](../../dvpn-pnc-common/idl/log_task_msg.idl) | v2vTrafficCtlInfo、logTaskInfo |  |
| [dvpn-pnc-common/idl/map_boundary_msgs.idl](../../dvpn-pnc-common/idl/map_boundary_msgs.idl) | GPS_POINTS、DDS_MAP_BOUNDARY |  |
| [dvpn-pnc-common/idl/map_covert_points.idl](../../dvpn-pnc-common/idl/map_covert_points.idl) | MapCovertPoint、MapCovertPoint1 |  |
| [dvpn-pnc-common/idl/map_cross_point.idl](../../dvpn-pnc-common/idl/map_cross_point.idl) | CrossRoadPoint、MapCrossPoint、CrossRoadPoints |  |
| [dvpn-pnc-common/idl/map_main.idl](../../dvpn-pnc-common/idl/map_main.idl) | NearbyInfo、MaxSpeed、MapMain |  |
| [dvpn-pnc-common/idl/map_platform.idl](../../dvpn-pnc-common/idl/map_platform.idl) | MapInfo、BoundaryInfo、RoadOffsetInfo、EvenPresssInfo、RoadSpeedInfo、LimitSpeedInfo、Map_cmd、Map_cmd_Result、Map_cmd_Result_Ack | MapCtlTypeEnum |
| [dvpn-pnc-common/idl/map_preview_path.idl](../../dvpn-pnc-common/idl/map_preview_path.idl) | MapPreviewPath |  |
| [dvpn-pnc-common/idl/map_road_request.idl](../../dvpn-pnc-common/idl/map_road_request.idl) | MapRoadRequest、RelatedRoadPoints |  |
| [dvpn-pnc-common/idl/map_server_heart.idl](../../dvpn-pnc-common/idl/map_server_heart.idl) | MapHeartBeat |  |
| [dvpn-pnc-common/idl/multi_parking.idl](../../dvpn-pnc-common/idl/multi_parking.idl) | MultiParkingState |  |
| [dvpn-pnc-common/idl/Net2Control.idl](../../dvpn-pnc-common/idl/Net2Control.idl) | NetToControl_Cmd、NetToControl_Result、NetToControl_Result_Ack | NetToCtlTypeEnum |
| [dvpn-pnc-common/idl/Net2Decide.idl](../../dvpn-pnc-common/idl/Net2Decide.idl) | Slow_Stop_Point、ReqTime_Arrive_Point、NetToDecide、NetToDecide_Ack、Expect_Arrive_Point、Expect_Arrive_PointTime、RSU_Crossing、RsuAndV2x_Conf、RsuUse_Conf、NetToV2x、NetToV2x_Ack | Cmd_To_Decide、CmdStopReason_Enum、Cmd_To_V2X |
| [dvpn-pnc-common/idl/obst_msg.idl](../../dvpn-pnc-common/idl/obst_msg.idl) | DDS_PS_Online_Status、DDS_PointXYZ、DDS_CurbPoint、DDS_CurbLidarPoints、DDS_CurbFusePoints、DDS_RadarInfo、DDS_RadarInfos、DDS_LidarPoint、DDS_LidarCloud、DDS_Image、DDS_ScatterObject、DDS_ScatterObjects、DDS_Box、DDS_BoxObjects、DDS_AlgorithmInfo、DDS_RadarObject、DDS_RadarObjects、DDS_Scatter、DDS_SensorFuseResult、DDS_SensorFuseResults、DDS_IMU、DDS_RTK、DDS_FusionCurbMessage、DDS_Ego、DDS_PathPoint、DDS_PathPoints |  |
| [dvpn-pnc-common/idl/obst_upload.idl](../../dvpn-pnc-common/idl/obst_upload.idl) | VehicleGPS、ScatterGPS、ObstInfo、ObstUpdateInfo |  |
| [dvpn-pnc-common/idl/plan_back_wall.idl](../../dvpn-pnc-common/idl/plan_back_wall.idl) | PlanBackWallInfo |  |
| [dvpn-pnc-common/idl/plan_control_msg.idl](../../dvpn-pnc-common/idl/plan_control_msg.idl) | LocalPlanResultInfo | Moving_Direction |
| [dvpn-pnc-common/idl/plan_msg.idl](../../dvpn-pnc-common/idl/plan_msg.idl) | PlanResult |  |
| [dvpn-pnc-common/idl/plat_config.idl](../../dvpn-pnc-common/idl/plat_config.idl) | SingleConfPro、ConfigUpdateInfo、ConfigUpdateAck、ConfigUpdateReq |  |
| [dvpn-pnc-common/idl/plat_msg.idl](../../dvpn-pnc-common/idl/plat_msg.idl) | PointDuration、CloudFeedStatus、ParkPoint、PointInfo、InnerPoint、RoadInMap、SlowStopInfo、JobExecResult、JobControl、GPSPointMsg、PreviewPathMsg、DownPreviewPathMsg、AreaVehicleMsg、JobCtrlResPonse、JobCtrlRespAck、UpdateUnloadingStatus、UpdateUnloadingPoint | JobStatus_Enum、DrivStatus_Enum、VehicleStatus_Enum、JobControl_Enum、ParkPoint_Enum、Destination_Enum |
| [dvpn-pnc-common/idl/remote_contrl.idl](../../dvpn-pnc-common/idl/remote_contrl.idl) | CloudRemoteCtl、CloudRemoteCtlAck、VehicleLightCtrl、VhehicleAirDCtrl、CloudVehicleCtr、VehicleStatus、CloudVehicleCtrAck | RemoteCmd_Enum、CtrLight_Enum |
| [dvpn-pnc-common/idl/safe_ctrl_msg.idl](../../dvpn-pnc-common/idl/safe_ctrl_msg.idl) | SafeFeedbackInfo |  |
| [dvpn-pnc-common/idl/safeimple_msg.idl](../../dvpn-pnc-common/idl/safeimple_msg.idl) | SafeImpleResultInfo |  |
| [dvpn-pnc-common/idl/server_request.idl](../../dvpn-pnc-common/idl/server_request.idl) | ServerRequest、LngLatPoint、RSULngLatPoint、LimitSpeed、ObsPoint、JobToMapServer、RoadToMapServer、MapServerRequest、MapLaneTypeRequest、MapLaneTypeResult | Destination_Enum |
| [dvpn-pnc-common/idl/server_response_hdmap.idl](../../dvpn-pnc-common/idl/server_response_hdmap.idl) | ServerResponseHdmap |  |
| [dvpn-pnc-common/idl/short_range_remote_ctrl.idl](../../dvpn-pnc-common/idl/short_range_remote_ctrl.idl) | ShortRangeRemoteCtrl | TaskCmd_Enum |
| [dvpn-pnc-common/idl/task_state_msg.idl](../../dvpn-pnc-common/idl/task_state_msg.idl) |  | Drive_state_child、Drive_state、Main_state |
| [dvpn-pnc-common/idl/traffic_light.idl](../../dvpn-pnc-common/idl/traffic_light.idl) | MapRoadInfo、VehicleCtrlCmd | VehicleCtrlCmd_Enum |
| [dvpn-pnc-common/idl/traffic_light_rsu.idl](../../dvpn-pnc-common/idl/traffic_light_rsu.idl) | directionInfo、juncInfo |  |
| [dvpn-pnc-common/idl/v2v_msg.idl](../../dvpn-pnc-common/idl/v2v_msg.idl) | VehicleInfo、RoundVehicleList |  |
| [dvpn-pnc-common/idl/v2x_msg.idl](../../dvpn-pnc-common/idl/v2x_msg.idl) | CloudVehicleInfo、CloudRoundVehicleList |  |
| [dvpn-pnc-common/idl/vehicle_task_msg.idl](../../dvpn-pnc-common/idl/vehicle_task_msg.idl) | V2XGPSInfo、VehicleTaskInfo |  |
| [dvpn-pnc-common/idl/yield_dispatch_msg.idl](../../dvpn-pnc-common/idl/yield_dispatch_msg.idl) | CtrlCmd_req、CtrlCmd_res | CtrlCmdType |
| [dvpn-pnc-common/idl/yield_msg.idl](../../dvpn-pnc-common/idl/yield_msg.idl) | MeetYieldType | MeetYieldType_Enum |
| [include/ddsType/.idl](../../include/ddsType/.idl) | HelloWorld |  |
| [include/ddsType/Cockpit_address.idl](../../include/ddsType/Cockpit_address.idl) | CmdtoRemoteCtrl、CmdCtrlACK |  |
| [include/ddsType/Remote2Ctrl.idl](../../include/ddsType/Remote2Ctrl.idl) | Remote2Control |  |
| [include/ddsType/StateMachine.idl](../../include/ddsType/StateMachine.idl) | StateMachine | StateMachine_Enum |

公共目录中还存在 plat_msg_split 的生成类型文件，但未找到配套 IDL；生成类型注册名为 CloudFeedStatusSplit，现存业务未创建对应端点。

