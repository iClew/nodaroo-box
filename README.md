# nodaroo-box

A single Python file that runs a proxy node on a free PaaS container
(Pterodactyl, Nodaroo, any "python generic" egg). It downloads mihomo, generates
credentials, and prints VLESS, Hysteria2 and Shadowsocks links.

**English** | [中文说明](README.zh-CN.md)

Experimental, provided as is. Not affiliated with any hosting provider.

## Usage

1. Create a server on the panel with the **Python generic** egg.
2. Put `app.py` and `node.env` in the server root (`/home/container`). Either
   import this repo through the panel's Git option, or upload the two files.
3. Set the start command to `python app.py`, press Start.
4. Copy the `vless://` / `hysteria2://` links from the console.

The default startup line `if [[ -d .git ]] && …; then git pull; fi` only pulls
when a `.git` directory exists, so the panel's Git import has to be used once
(or the files uploaded by hand) before the first start.

The `node.env` in this repository works as is: set `NODE_HOST` to the hostname
your panel gives you, then start. `SERVER_PORT` can stay empty — the panel
already exports it. A Reality keypair is included, so nothing else is required.

## Configuration

`node.env` is read from the script directory. Environment variables set in the
panel also work and override the file.

| Key | Default | Notes |
| --- | --- | --- |
| `NODE_HOST` | `se2.nodaroo.fun` | Public hostname from the panel. Needed for correct links. |
| `SERVER_PORT` | from `SERVER_PORT` / `PORT` | Port the panel allocated. |
| `ENABLE` | `vless,hy2` | Entry points to run. Add `ss` only if you have a second port. |
| `NODE_NAME` | hostname | Link label prefix. |
| `REALITY_SNI` | `www.microsoft.com` | TLS 1.3 site used in the Reality handshake. |
| `REALITY_PRIVATE_KEY` / `REALITY_PUBLIC_KEY` | included | Reality keypair. Replace with your own if you want. |
| `HY2_SNI` | `NODE_HOST` | SNI in the Hysteria2 link. |
| `HY2_OBFS` | — | `salamander` turns on Hysteria2 obfuscation. |
| `HY2_OBFS_PASSWORD` | generated | Obfuscation password. |
| `HY2_UP_MBPS` / `HY2_DOWN_MBPS` | — | Bandwidth hints for Brutal CC. |
| `SS_METHOD` | `2022-blake3-aes-128-gcm` | Shadowsocks cipher. |
| `SS_PORT` | `SERVER_PORT + 1` | Shadowsocks port. |
| `CORE` | `mihomo` | `mihomo` or `singbox`. |
| `WATCHDOG` | `1` | Restart the core if it exits. |
| `LOG_LEVEL` | `warn` | Core log level. |
| `MIRROR` | — | GitHub mirror prefix, e.g. `https://ghfast.top`. |

A new Reality keypair comes from `sing-box generate reality-keypair`, or from
running with `CORE=singbox`, which generates one itself.

## Ports

Panels usually open one port. VLESS (TCP) and Hysteria2 (UDP) share it:

```
2467/tcp  VLESS + Reality
2467/udp  Hysteria2
```

Shadowsocks is off in the shipped `node.env` for that reason — mihomo cannot
bind the same TCP port twice. Turn it on with `ENABLE=vless,hy2,ss` only if the
panel gives you a second port, and set `SS_PORT`.

## Output

Links are printed to the console and written next to the script:

- `out/links.txt` — one link per line
- `out/subscription.txt` — same links, base64, usable as a subscription file
- `out/node.json` — node parameters
- `state.json` — generated credentials, reused on every later start
- `launcher.log` — one line per lifecycle event, rotated at 1 MB
- `core.log` — the core's own output, rotated at 1 MB

Clients: sing-box, mihomo, Clash Meta, Shadowrocket, NekoBox, v2rayN.

## Lifecycle

```
start -> read node.env -> core present? install -> credentials (state.json)
      -> write config.json -> validate -> print links -> run core
      -> core exits? restart with backoff 3s..60s (reset after 120s uptime)
      -> SIGTERM/SIGINT/SIGHUP -> stop core (SIGTERM, then SIGKILL)
       -> remove config.json -> exit
```

Second and later starts reuse `state.json`: same UUID, same Reality keypair,
same passwords, so clients keep working. Delete that file to rotate everything.

`config.json` is generated on every start and removed on shutdown, so it is
never a stale config left behind after a crash.

## Notes

- `NODE_HOST` must be the public hostname, not the container IP.
- If the console says `nothing is listening on port N`, the panel's port range
  does not include it. Reduce `ENABLE`.
- If a download fails, set `MIRROR`.
- `state.json` holds live credentials and stays out of git.
- Delete `state.json` to rotate every credential; clients need the new links.
- `WATCHDOG=0` stops after the first core exit, useful when debugging.
- Logs (`launcher.log`, `core.log`) rotate at 1 MB and stay out of git.

## License

MIT.
