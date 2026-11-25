# nodaroo-box

一个 Python 文件，把免费面板（Pterodactyl / Nodaroo / 任意 "python generic"
egg）里的容器变成可用的 mihomo 节点：VLESS+Reality、Hysteria2、Shadowsocks
2022，启动即把订阅链接打到控制台。

[English](README.md) | **中文说明**

> **实验性项目。** 这是个人实验，按原样分享，不是维护中的产品。它跑在本来不
> 欢迎这类用途的免费主机上，随时可能失效。请勿滥用：不要拿去卖代理、不要批量
> 注册、不要跑会让主机被封的流量。节点用来干什么、是否符合主机条款和当地法律，
> 由你自己负责。不提供任何担保，见 [LICENSE](LICENSE)。

## 快速开始

1. 在面板上建服务器，选 **Python generic** 环境。
2. 把 `app.py` 和 `node.env`（复制 `node.env.example` 改一下）上传到服务器文件区。
3. 点 **Start**。控制台会打印 `vless://`、`hysteria2://`、`ss://` 链接。

不需要安装任何东西：首次启动时脚本自己下载 mihomo 内核（约 14 MB），生成一次
凭据，写出 `config.json`，进程掉了自动重拉。

## 配置

全部从脚本同目录的 `node.env` 读取；同名环境变量也生效，`node.env` 优先。

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `NODE_HOST` | 空 | 面板给你的公网域名，填了链接才对。 |
| `SERVER_PORT` | 读 `SERVER_PORT` / `PORT` | 面板分配的端口。 |
| `ENABLE` | `vless,hy2,ss` | 要开哪几个入口。 |
| `NODE_NAME` | 域名 | 链接名称前缀。 |
| `REALITY_SNI` | `www.microsoft.com` | 一个真实的 TLS 1.3 站点，别选套了 Cloudflare 的。 |
| `REALITY_PRIVATE_KEY` / `REALITY_PUBLIC_KEY` | 空 | Reality 的 x25519 密钥对，mihomo 生成不了，见下。 |
| `HY2_SNI` | `NODE_HOST` | hysteria2 链接里的 SNI。 |
| `HY2_OBFS`、`HY2_OBFS_PASSWORD` | 空 | 填 `salamander` 开启 hysteria2 混淆。 |
| `HY2_UP_MBPS` / `HY2_DOWN_MBPS` | 空 | Brutal 拥塞控制的带宽提示。 |
| `SS_METHOD` | `2022-blake3-aes-128-gcm` | Shadowsocks 2022 加密。 |
| `SS_PORT` | `SERVER_PORT + 1` | Shadowsocks 端口，见「端口」。 |
| `CORE` | `mihomo` | `mihomo` 或 `singbox`。 |
| `WATCHDOG` | `1` | 内核退出后自动重启。 |
| `LOG_LEVEL` | `warn` | 内核日志级别。 |
| `MIRROR` | 空 | GitHub 加速前缀，例如 `https://ghfast.top`。 |

### 预生成密钥

mihomo 只吃 Reality 私钥，不会打印对应的公钥，所以密钥对用
`sing-box generate reality-keypair`（一条命令，随便哪台机器）生成后贴进
`node.env`：

```bash
sing-box generate reality-keypair
# PrivateKey: xxxxx   -> REALITY_PRIVATE_KEY
# PublicKey:  yyyyy   -> REALITY_PUBLIC_KEY
```

这两项留空、改用 `CORE=singbox` 的话，内核会自己生成并存下来。

## 端口

免费面板通常只开一个端口。mihomo 允许 VLESS（TCP）和 Hysteria2（UDP）共用，
因为两者是不同传输层：

```text
2522/tcp  VLESS + Reality
2522/udp  Hysteria2
2523/tcp  Shadowsocks（自动：SERVER_PORT + 1）
```

Shadowsocks 抢不到 mihomo 已占的 TCP 端口，所以自动往上挪一位。面板只开一个
端口时，用 `ENABLE=vless,hy2`，别开 `ss`。

## 输出

控制台打印链接，同一份数据也会写到脚本目录：

- `out/links.txt` — 每行一条链接
- `out/subscription.txt` — 同样内容，base64，可以当订阅文件用
- `out/node.json` — 机器可读的节点参数
- `state.json` — 生成的凭据，权限 600，重启不变

客户端：sing-box、mihomo、Clash Meta、Shadowrocket、NekoBox、v2rayN。

## 文件

```text
app.py             启动器全部代码（只用标准库）
node.env.example   复制成 node.env 再改
node.env           你的配置（不入库）
state.json         生成的凭据（不入库）
config.json        生成的内核配置（不入库）
out/               生成的链接（不入库）
```

## 排错

- **`nothing is listening on port N`** — 面板端口范围没包含它，或被别的监听占了。
  把 `ENABLE` 缩到真正需要的协议。
- **链接里的地址不对** — `NODE_HOST` 没填或填错。
- **下载失败** — 设置 `MIRROR=https://ghfast.top` 或换其它 GitHub 加速。
- **VLESS 连上但没流量** — 检查 `REALITY_SNI` 在你所在地区是否仍是 TLS 1.3。

## 许可

MIT。脚本下载的内核（mihomo）有自己的许可证，用之前先读。
