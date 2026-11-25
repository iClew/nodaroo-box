# nodaroo-box

One Python file that turns a free PaaS container (Pterodactyl / Nodaroo / any
"python generic" egg) into a working mihomo node: VLESS+Reality, Hysteria2 and
Shadowsocks 2022, with the share links printed to the console.

**English** | [中文说明](README.zh-CN.md)

> **Experimental.** This is a personal experiment shared as-is, not a maintained
> product. It runs on free hosts that are not meant for this and it can stop
> working at any time. Don't abuse it: no public proxy selling, no bulk
> registration, no traffic that gets a host suspended. You are responsible for
> what your node is used for and for complying with your host's terms and your
> local law. No warranty of any kind — see [LICENSE](LICENSE).

## Quick start

1. Create a server on the panel, pick the **Python generic** egg.
2. Upload `app.py` and `node.env` (copy `node.env.example` and edit it) into the
   server's file area.
3. Press **Start**. The console prints your `vless://`, `hysteria2://` and
   `ss://` links.

There is nothing to install: the script downloads the mihomo core (~14 MB) on
first start, generates credentials once, writes `config.json`, and restarts the
core if it dies.

## Configuration

Everything is read from `node.env` next to the script. Process environment
variables with the same names also work; `node.env` wins.

| Key | Default | Meaning |
| --- | --- | --- |
| `NODE_HOST` | *(empty)* | Public hostname the panel gives you. Required for correct links. |
| `SERVER_PORT` | from `SERVER_PORT` / `PORT` | The port the panel allocated. |
| `ENABLE` | `vless,hy2,ss` | Which entry points to run. |
| `NODE_NAME` | host name | Prefix for the link labels. |
| `REALITY_SNI` | `www.microsoft.com` | A real TLS 1.3 site, not behind Cloudflare. |
| `REALITY_PRIVATE_KEY` / `REALITY_PUBLIC_KEY` | *(empty)* | x25519 keypair for Reality. mihomo cannot generate one, see below. |
| `HY2_SNI` | `NODE_HOST` | SNI shown in the hysteria2 link. |
| `HY2_OBFS`, `HY2_OBFS_PASSWORD` | *(empty)* | `salamander` enables hysteria2 obfuscation. |
| `HY2_UP_MBPS` / `HY2_DOWN_MBPS` | *(empty)* | Bandwidth hints for Brutal CC. |
| `SS_METHOD` | `2022-blake3-aes-128-gcm` | Shadowsocks 2022 cipher. |
| `SS_PORT` | `SERVER_PORT + 1` | Shadowsocks port; see "Ports" below. |
| `CORE` | `mihomo` | `mihomo` or `singbox`. |
| `WATCHDOG` | `1` | Restart the core when it exits. |
| `LOG_LEVEL` | `warn` | Core log level. |
| `MIRROR` | *(empty)* | GitHub mirror prefix, e.g. `https://ghfast.top`. |

### Pre-generated credentials

mihomo takes the Reality private key but cannot print the matching public key,
so the keypair comes from `sing-box generate reality-keypair` (one command, any
machine) and is pasted into `node.env`:

```bash
sing-box generate reality-keypair
# PrivateKey: xxxxx   -> REALITY_PRIVATE_KEY
# PublicKey:  yyyyy   -> REALITY_PUBLIC_KEY
```

Leave those two empty and run `singbox` as the core instead — that core
generates and stores the keypair itself.

## Ports

A free panel usually opens one port. mihomo lets VLESS (TCP) and Hysteria2 (UDP)
share it, because those are separate transports:

```text
2522/tcp  VLESS + Reality
2522/udp  Hysteria2
2523/tcp  Shadowsocks (auto: SERVER_PORT + 1)
```

Shadowsocks cannot reuse the port mihomo already bound for TCP, so it moves up
one. If your panel opens only a single port, run `ENABLE=vless,hy2` and leave
`ss` out.

## Output

The console prints the links. The same data is written next to the script:

- `out/links.txt` — one link per line
- `out/subscription.txt` — the same links, base64, paste as a subscription URL file
- `out/node.json` — machine-readable node parameters
- `state.json` — generated credentials, mode `600`, kept across restarts

Clients: sing-box, mihomo, Clash Meta, Shadowrocket, NekoBox, v2rayN.

## Files

```text
app.py             the whole launcher (standard library only)
node.env.example   copy to node.env and edit
node.env           your settings (not committed)
state.json         generated credentials (not committed)
config.json        generated core config (not committed)
out/               generated links (not committed)
```

## Troubleshooting

- **`nothing is listening on port N`** — the panel's port range does not include
  it, or another listener took it. Lower `ENABLE` to the protocols you need.
- **Links point at the wrong address** — `NODE_HOST` is empty or wrong.
- **Download fails** — set `MIRROR=https://ghfast.top` or another GitHub mirror.
- **VLESS connects but no traffic** — check `REALITY_SNI` still terminates TLS
  1.3 on port 443 of the region you are in.

## License

MIT. The core it downloads (mihomo) has its own license; read it before use.
