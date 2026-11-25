# nodaroo-box

一个 Python 文件，在免费面板（Pterodactyl、Nodaroo、任意 "python generic"
egg）的容器里跑起一个代理节点。脚本会下载 mihomo、生成凭据，输出 VLESS、
Hysteria2 和 Shadowsocks 链接。

[English](README.md) | **中文说明**

实验性项目，按原样提供，与任何主机商无关。

## 用法

1. 在面板建服务器，选 **Python generic** 环境。
2. 把 `app.py` 和 `node.env` 上传到服务器的文件区。
3. 启动命令设为 `python app.py`，点 Start。
4. 从控制台复制 `vless://` / `hysteria2://` 链接。

仓库里的 `node.env` 里 `NODE_HOST` 和 `SERVER_PORT` 都已经填好。这两个一般都
得手动指定，因为域名和端口是面板定的 —— 改成你自己服务器的值就行。Reality
密钥对已经填好，其它不用动。

## 配置

脚本读取同目录的 `node.env`。面板里设置的同名环境变量也生效，并覆盖文件。

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `NODE_HOST` | `se2.nodaroo.fun` | 面板给的公网域名，必须设置。 |
| `SERVER_PORT` | `2467` | 面板分配的端口，一般要显式指定。 |
| `ENABLE` | `vless,hy2` | 要开的入口。有第二个端口才加 `ss`。 |
| `NODE_NAME` | 域名 | 链接名称前缀。 |
| `REALITY_SNI` | `www.microsoft.com` | Reality 握手用的 TLS 1.3 站点。 |
| `REALITY_PRIVATE_KEY` / `REALITY_PUBLIC_KEY` | 已填 | Reality 密钥对，想换可以自己换。 |
| `HY2_SNI` | `NODE_HOST` | Hysteria2 链接里的 SNI。 |
| `HY2_OBFS` | — | 填 `salamander` 开启 Hysteria2 混淆。 |
| `HY2_OBFS_PASSWORD` | 自动生成 | 混淆密码。 |
| `HY2_UP_MBPS` / `HY2_DOWN_MBPS` | — | Brutal 拥塞控制的带宽提示。 |
| `SS_METHOD` | `2022-blake3-aes-128-gcm` | Shadowsocks 加密方式。 |
| `SS_PORT` | `SERVER_PORT + 1` | Shadowsocks 端口。 |
| `CORE` | `mihomo` | `mihomo` 或 `singbox`。 |
| `WATCHDOG` | `1` | 内核退出后自动重启。 |
| `LOG_LEVEL` | `warn` | 内核日志级别。 |
| `MIRROR` | — | GitHub 加速前缀，例如 `https://ghfast.top`。 |

要新密钥对，用 `sing-box generate reality-keypair`，或者 `CORE=singbox` 启动让
内核自己生成。

## 端口

面板一般只开一个端口，VLESS（TCP）和 Hysteria2（UDP）共用它：

```
2467/tcp  VLESS + Reality
2467/udp  Hysteria2
```

仓库里交的 `node.env` 因此关掉了 Shadowsocks —— mihomo 不能在同一个 TCP 端口
绑两次。面板给了第二个端口，才改 `ENABLE=vless,hy2,ss` 并设置 `SS_PORT`。

## 输出

链接会打到控制台，同时写到脚本目录：

- `out/links.txt` — 每行一条链接
- `out/subscription.txt` — 同样内容，base64，可以当订阅文件
- `out/node.json` — 节点参数
- `state.json` — 生成的凭据，之后每次启动复用
- `launcher.log` — 生命周期日志，超过 1MB 自动轮转
- `core.log` — 内核自己的输出，超过 1MB 自动轮转

客户端：sing-box、mihomo、Clash Meta、Shadowrocket、NekoBox、v2rayN。

## 生命周期

```
启动 -> 读 node.env -> 内核在不在？没有就下载 -> 凭据（state.json）
     -> 写 config.json -> 校验 -> 打印链接 -> 启动内核
     -> 内核退出？退避重拉 3s..60s（稳定跑满 120s 后重置）
     -> SIGTERM/SIGINT/SIGHUP -> 先 SIGTERM 内核，超时 SIGKILL
     -> 删除 config.json -> 退出
```

第二次及以后启动复用 `state.json`：UUID、Reality 密钥对、密码都不变，客户端
配置继续有效。想全部换新，删掉这个文件即可。

`config.json` 每次启动重新生成、退出时删除，不会留下崩溃前的旧配置。

## 注意事项

- `NODE_HOST` 填公网域名，不是容器 IP。
- 控制台出现 `nothing is listening on port N`，说明面板端口范围没包含它，
  把 `ENABLE` 减一减。
- 下载失败就设置 `MIRROR`。
- `state.json` 里是有效凭据，不会进 git。
- 删掉 `state.json` 会换一整套凭据，客户端要用新链接。
- `WATCHDOG=0` 内核退出后不再重拉，调试时用。
- 日志（`launcher.log`、`core.log`）超过 1MB 自动轮转，不进 git。

## 许可

MIT。
