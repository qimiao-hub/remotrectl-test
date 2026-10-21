# 车端 0x02 TCP 帧接收器

独立、零第三方依赖的 TCP 服务，用来接收车端协议帧并保存原始报文。
默认监听驾驶舱车端端口 `60006`。

## 启动

```powershell
cd D:\Users\qim\Documents\remotectl\tcp02_receiver
python receiver.py
```

指定地址、端口或日志目录：

```powershell
python receiver.py --host 0.0.0.0 --port 60006 --log-dir logs
```

然后将 `test_server/simulator.json` 中的 `cockpit.ip/port` 指向接收器，
或者直接启动：

```powershell
cd ..\test_server
python vehicle_client.py `
  --config simulator.json `
  --cockpit-host 127.0.0.1 `
  --cockpit-port 60006
```

## 日志

每天生成三类文件：

- `logs/tcp-raw-YYYY-MM-DD.bin`：每次 `recv` 收到的字节原样追加。
- `logs/tcp-chunks-YYYY-MM-DD.jsonl`：原始 TCP 块、来源地址和十六进制。
- `logs/vehicle-frames-YYYY-MM-DD.jsonl`：按协议拆分后的完整帧，包含
  命令号、VIN、载荷长度、BCC 结果和完整原始十六进制。

程序记录所有命令帧；命令号 `0x02` 会在控制台和逐帧日志中标记为
`02实时上报`。即使 BCC 错误，原始帧仍会保留并标记
`"bcc_valid": false`。

## 测试

```powershell
python -m unittest discover -s tests -v
```
