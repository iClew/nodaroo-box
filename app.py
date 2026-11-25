#!/usr/bin/env python3
"""mihomo launcher for free PaaS containers (Pterodactyl / Nodaroo / similar).

One file, standard library only. The host gives you a container, an allocated
TCP+UDP port and a public hostname; this script turns that into a working node.

What it does on start:
  * downloads mihomo (Clash Meta), once
  * generates and persists credentials, once
  * writes config.json from node.env + environment variables
  * prints share links and base64 subscription files to the console
  * runs the core in the foreground and restarts it if it dies

Everything is configured through node.env (same directory) or the process
environment; node.env wins. Nothing secret is ever baked into this file.

MIT License. See README.md for the tutorial.
"""

import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))

# pinned releases; override with SINGBOX_VERSION / MIHOMO_VERSION when testing
DEFAULT_SINGBOX_VERSION = "1.14.4"
DEFAULT_MIHOMO_VERSION = "1.19.34"
CORE_VERSIONS = {
    "singbox": os.environ.get("SINGBOX_VERSION", DEFAULT_SINGBOX_VERSION).lstrip("v"),
    "mihomo": os.environ.get("MIHOMO_VERSION", DEFAULT_MIHOMO_VERSION).lstrip("v"),
}
CORE_ORDER = ("mihomo", "singbox")

BIN = os.path.join(BASE, "core")
CONFIG_FILE = os.path.join(BASE, "config.json")
STATE_FILE = os.path.join(BASE, "state.json")
LOG_FILE = os.path.join(BASE, "core.log")
ENV_FILE = os.path.join(BASE, "node.env")
OUT_DIR = os.path.join(BASE, "out")

DEFAULTS = {
    # where the host exposes this container
    "NODE_HOST": "",
    "SERVER_PORT": "",
    "PORT": "",
    # entry points: comma separated tags from vless / hy2 / ss
    "ENABLE": "vless,hy2,ss",
    # label used in share link names and client config filenames
    "NODE_NAME": "",
    # reality: a real TLS 1.3 site that terminates TCP 443 and does not sit
    # behind Cloudflare. www.microsoft.com / addons.mozilla.org are common picks.
    "REALITY_SNI": "www.microsoft.com",
    "REALITY_HANDSHAKE": "",
    "REALITY_SHORT_ID": "",
    # hysteria2
    "HY2_SNI": "",
    "HY2_OBFS": "",
    "HY2_OBFS_PASSWORD": "",
    "HY2_UP_MBPS": "",
    "HY2_DOWN_MBPS": "",
    # shadowsocks 2022
    "SS_METHOD": "2022-blake3-aes-128-gcm",
    # behaviour
    "WATCHDOG": "1",
    "LOG_LEVEL": "warn",
    "INSECURE_CERT": "1",
    "PRINT_CONFIG": "0",
    "MIRROR": "",
    "CORE": "mihomo",
    # keep shadowsocks on UDP too; only works when it is the sole UDP inbound
    "SS_UDP": "0",
    # shadowsocks port; empty = share the node port when that is possible
    "SS_PORT": "",
    # only used when CORE=mihomo and REALITY_SNI is not set: the core cannot
    # derive an x25519 public key from the private one, so both come from here
    "REALITY_PRIVATE_KEY": "",
    "REALITY_PUBLIC_KEY": "",
}

# Keys written to state.json. Everything else on disk is disposable.
STATE_KEYS = (
    "uuid",
    "reality_private",
    "reality_public",
    "short_id",
    "hy2_password",
    "hy2_obfs_password",
    "ss_password",
    "tls_created",
)


def log(msg):
    print(f"[nodaroo-box] {msg}", flush=True)


# --------------------------------------------------------------------------
# settings


def parse_env_file(path):
    vals = {}
    if not os.path.isfile(path):
        return vals
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            vals[k.strip()] = v
    return vals


def load_settings():
    s = dict(DEFAULTS)
    for k in DEFAULTS:
        if os.environ.get(k):
            s[k] = os.environ[k]
    # an unset key in node.env must not blank out a value the panel exported
    s.update({k: v for k, v in parse_env_file(ENV_FILE).items() if v != ""})
    return s


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def pick_port(s):
    for key in ("SERVER_PORT", "PORT"):
        raw = str(s.get(key) or "").strip()
        if raw.isdigit():
            return int(raw)
    raise SystemExit(
        "[nodaroo-box] no port found: set SERVER_PORT or PORT to the port the "
        "host allocated for this container"
    )


def pick_host(s):
    host = str(s.get("NODE_HOST") or "").strip()
    if host:
        return host
    return "<set NODE_HOST>"


# --------------------------------------------------------------------------
# core binaries (sing-box / mihomo)


def run_cli(cmd, timeout=20):
    """stdout+stderr of a short-lived CLI call; None on failure.

    Deliberately uses Popen+communicate: on this kernel subprocess.run with a
    pipe inside a multi-threaded parent can block on the stray grandchild fds
    sing-box/mihomo leave behind, which made `version` probes hang for 20s.
    """
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError:
        return None
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return None
    return (out or b"").decode(errors="replace") + (err or b"").decode(errors="replace")


def core_version(path):
    """Return the CLI's reported version, or None when the binary is unusable."""
    if not os.path.isfile(path):
        return None
    text = run_cli([path, "-v"], timeout=8)
    if not text:
        text = run_cli([path, "version"], timeout=8)
    if not text:
        return None
    m = re.search(r"Mihomo Meta v(\S+)", text)
    if m:
        return m.group(1)
    m = re.search(r"sing-box version (\S+)", text)
    if m:
        return m.group(1)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and re.fullmatch(r"\d+\.\d+\.\d+\S*", lines[0]):
        return lines[0]
    m = re.search(r"Mihomo Meta v(\S+)", text)
    if m:
        return m.group(1)
    return None


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "nodaroo-box"})
    with urllib.request.urlopen(req, timeout=240) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def github_urls(s, repo, rel_filename, rel_path):
    mirror = str(s.get("MIRROR") or "").strip().rstrip("/")
    rel = f"{rel_path}/{rel_filename}"
    urls = []
    if mirror:
        urls.append(f"{mirror}/{repo}/releases/download/{rel}")
    urls.append(f"https://github.com/{repo}/releases/download/{rel}")
    urls.append(f"https://ghfast.top/https://github.com/{repo}/releases/download/{rel}")
    return urls


def fetch_first(s, urls, dest):
    last = None
    for url in urls:
        try:
            log(f"downloading {url.rsplit('/', 2)[-2]}/{url.rsplit('/', 1)[-1]}")
            download(url, dest)
            return
        except Exception as e:  # noqa: BLE001 - try the next mirror
            last = e
            log(f"download failed: {e}")
    raise SystemExit(f"[nodaroo-box] could not download core: {last}")


def install_singbox(s):
    version = CORE_VERSIONS["singbox"]
    arch = arch_name()
    name = f"sing-box-{version}-linux-{arch}"
    tar_path = os.path.join(BASE, f"{name}.tar.gz")
    fetch_first(
        s,
        github_urls(s, "SagerNet/sing-box", f"{name}.tar.gz", f"v{version}"),
        tar_path,
    )
    unpack = os.path.join(BASE, ".unpack")
    shutil.rmtree(unpack, ignore_errors=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        safe_extract(tf, unpack)
    src = os.path.join(unpack, name, "sing-box")
    if not os.path.isfile(src):
        found = [os.path.join(r, f) for r, _, fs in os.walk(unpack) for f in fs if f == "sing-box"]
        if not found:
            shutil.rmtree(unpack, ignore_errors=True)
            raise RuntimeError("sing-box binary not found in the release archive")
        src = found[0]
    try:
        os.replace(src, BIN)
        os.chmod(BIN, 0o755)
    except OSError as e:
        raise RuntimeError(f"could not place the sing-box binary: {e}")
    finally:
        if os.path.isfile(tar_path):
            os.remove(tar_path)
        shutil.rmtree(unpack, ignore_errors=True)
    if not os.path.isfile(BIN):  # pragma: no cover - defensive
        raise RuntimeError(f"{BIN} missing right after install")


def install_mihomo(s):
    version = CORE_VERSIONS["mihomo"]
    arch = arch_name()
    # "compatible" builds avoid the v1/v3 microarchitecture requirements of
    # default Go builds and run on any VPS the free panels hand out.
    name = f"mihomo-linux-{arch}-compatible-v{version}.gz"
    gz = os.path.join(BASE, name)
    urls = github_urls(s, "MetaCubeX/mihomo", name, f"v{version}")
    mirror = str(s.get("MIHOMO_MIRROR") or "").strip().rstrip("/")
    if mirror:
        urls.insert(0, f"{mirror}/{name}")
    fetch_first(s, urls, gz)
    import gzip

    tmp = BIN + ".new"
    with gzip.open(gz, "rb") as src, open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.chmod(tmp, 0o755)
    os.replace(tmp, BIN)
    os.remove(gz)


def safe_extract(tf, dest):
    """tarfile filter="data" needs Python 3.12; container images ship 3.8-3.11."""
    try:
        tf.extractall(dest, filter="data")
    except TypeError:
        tf.extractall(dest)


def arch_name():
    machine = platform.machine().lower()
    return "arm64" if machine in ("aarch64", "arm64") else "amd64"


def wanted_core(s):
    raw = str(s.get("CORE") or "auto").strip().lower()
    if raw in ("", "auto"):
        return None
    return "mihomo" if raw in ("mihomo", "clash") else "singbox"


def core_kind(path):
    """'mihomo' or 'singbox' depending on what the installed binary reports."""
    if not os.path.isfile(path):
        return None
    text = run_cli([path, "-v"], timeout=8)
    if not text:
        text = run_cli([path, "version"], timeout=8)
    if not text:
        return None
    if "Mihomo" in text:
        return "mihomo"
    if "sing-box" in text:
        return "singbox"
    if re.search(r"^sing-box \S+", text, re.M):
        return "singbox"
    if re.search(r"\d+\.\d+\.\d+", text) and "Usage:" not in text:
        return "singbox"
    return None


def ensure_core(s):
    """Install the preferred core; fall back to the other one if it cannot run here."""
    wanted = wanted_core(s)
    order = [wanted] if wanted else list(CORE_ORDER)

    kind = core_kind(BIN)
    version = core_version(BIN)
    if kind and version == CORE_VERSIONS[kind]:
        if not wanted or wanted == kind:
            return kind

    last = None
    for candidate in order:
        try:
            log(f"installing {candidate} {CORE_VERSIONS[candidate]}")
            (install_mihomo if candidate == "mihomo" else install_singbox)(s)
            got = core_version(BIN)
            if got != CORE_VERSIONS[candidate]:
                raw = (run_cli([BIN, "-v"]) or "") + (run_cli([BIN, "version"]) or "")
                raise RuntimeError(f"unexpected version {got!r}, raw output {raw[:200]!r}")
            log(f"{candidate} {got} ready")
            return candidate
        except Exception as e:  # noqa: BLE001 - try the next core
            last = e
            log(f"{candidate} unavailable here: {e}")
            try:
                os.remove(BIN)
            except OSError:
                pass
    raise SystemExit(f"[nodaroo-box] no usable core: {last}")


# --------------------------------------------------------------------------
# credentials


def sb(*args):
    out = subprocess.run([BIN, *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise SystemExit(f"[nodaroo-box] sing-box {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def sh_id(existing):
    v = str(existing or "").strip().lower()
    if v and re.fullmatch(r"[0-9a-f]{1,16}", v):
        return v
    return secrets.token_hex(4)


def ss_key_bytes(method):
    return 32 if "256" in method or "chacha" in method else 16


def load_state():
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            missing = [k for k in STATE_KEYS if k not in state]
            if not missing:
                return state
            log(f"state.json missing {', '.join(missing)}, regenerating those")
        except (OSError, json.JSONDecodeError) as e:
            log(f"state.json unreadable ({e}), generating new credentials")
            state = {}
    else:
        state = {}
    return state


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


def ensure_state(s, state, core):
    fresh = []
    priv = state.get("reality_private")
    pub = state.get("reality_public")
    if (not priv or not pub) and core == "singbox":
        out = sb("generate", "reality-keypair")
        priv = pub = None
        for line in out.splitlines():
            if "PrivateKey" in line:
                priv = line.split()[-1].strip()
            if "PublicKey" in line:
                pub = line.split()[-1].strip()
        if not priv or not pub:
            raise SystemExit("[nodaroo-box] could not parse reality-keypair output")
        fresh.append("reality keypair")
    if priv and pub:
        state["reality_private"], state["reality_public"] = priv, pub
    if not state.get("uuid"):
        if core == "singbox":
            state["uuid"] = sb("generate", "uuid").strip().splitlines()[0].strip()
        else:
            import uuid as _uuid

            state["uuid"] = str(_uuid.uuid4())
        fresh.append("vless uuid")
    if not state.get("hy2_password"):
        state["hy2_password"] = secrets.token_urlsafe(18)
        fresh.append("hysteria2 password")
    need_obfs = str(s.get("HY2_OBFS") or "").strip().lower() == "salamander"
    if need_obfs and not state.get("hy2_obfs_password") and not str(s.get("HY2_OBFS_PASSWORD") or "").strip():
        state["hy2_obfs_password"] = secrets.token_urlsafe(18)
        fresh.append("hysteria2 obfs password")
    env_priv = str(s.get("REALITY_PRIVATE_KEY") or "").strip()
    env_pub = str(s.get("REALITY_PUBLIC_KEY") or "").strip()
    if env_priv and env_pub and (env_priv, env_pub) != (state.get("reality_private"), state.get("reality_public")):
        state["reality_private"], state["reality_public"] = env_priv, env_pub
        fresh.append("reality keypair (from node.env)")
    method = str(s.get("SS_METHOD") or DEFAULTS["SS_METHOD"])
    if not state.get("ss_password"):
        state["ss_password"] = b64(secrets.token_bytes(ss_key_bytes(method)))
        fresh.append("shadowsocks 2022 key")
    state["short_id"] = sh_id(s.get("REALITY_SHORT_ID") or state.get("short_id"))
    state.setdefault("tls_created", 0)
    if fresh:
        save_state(state)
        log("generated: " + ", ".join(fresh))
    return state


def b64(raw_bytes):
    import base64

    return base64.b64encode(raw_bytes).decode()


# --------------------------------------------------------------------------
# TLS material for hysteria2


def pem_blocks(text):
    out = []
    for m in re.finditer(r"-----BEGIN ([A-Z ]+)-----", text):
        label = m.group(1)
        end = text.find(f"-----END {label}-----", m.end())
        if end == -1:
            continue
        out.append(text[m.start() : end + len(f"-----END {label}-----")])
    return out


def ensure_cert(s, core):
    cert = os.path.join(BASE, "cert.pem")
    key = os.path.join(BASE, "key.pem")
    want_ok = os.path.isfile(cert) and os.path.isfile(key)
    if want_ok and not os.path.getsize(cert):
        want_ok = False
    if want_ok:
        return cert, key

    sni = str(s.get("HY2_SNI") or "www.microsoft.com").strip()
    log(f"creating self-signed certificate for {sni}")
    cert_pem = key_pem = None
    if core == "singbox":
        out = sb("generate", "tls-keypair", "--months", "3600", sni)
        blocks = pem_blocks(out)
        key_pem = next((b for b in blocks if "PRIVATE KEY" in b), None)
        cert_pem = next((b for b in blocks if "CERTIFICATE" in b), None)
    if not cert_pem or not key_pem:
        cert_pem, key_pem = openssl_pair(sni)
    if not cert_pem or not key_pem:
        raise SystemExit("[nodaroo-box] could not create a certificate (no openssl, no core helper)")

    tmp = os.path.join(BASE, ".cert.tmp")
    with open(tmp, "w") as f:
        f.write(cert_pem.rstrip("\n") + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, cert)
    with open(key, "w") as f:
        f.write(key_pem.rstrip("\n") + "\n")
    os.chmod(key, 0o600)
    return cert, key


def openssl_pair(sni):
    """Self-signed EC certificate via openssl; returns (cert_pem, key_pem) or (None, None)."""
    cert = os.path.join(BASE, ".ossl-cert.pem")
    key = os.path.join(BASE, ".ossl-key.pem")
    try:
        out = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "ec",
                "-pkeyopt", "ec_paramgen_curve:P-256",
                "-days", "3650", "-nodes",
                "-keyout", key, "-out", cert,
                "-subj", f"/CN={sni}",
                "-addext", f"subjectAltName=DNS:{sni}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if out.returncode != 0 or not os.path.isfile(cert) or not os.path.isfile(key):
        return None, None
    with open(cert) as f:
        cert_pem = f.read()
    with open(key) as f:
        key_pem = f.read()
    for p in (cert, key):
        try:
            os.remove(p)
        except OSError:
            pass
    return cert_pem, key_pem


def cert_common_name(path):
    try:
        out = subprocess.run(
            ["openssl", "x509", "-in", path, "-noout", "-subject"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        m = re.search(r"CN\s*=\s*([^\s,/]+)", out.stdout or "")
        return m.group(1) if m else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------
# configuration


def enabled(s):
    tags = [t.strip().lower() for t in str(s.get("ENABLE") or "").split(",") if t.strip()]
    return [t for t in tags if t in ("vless", "hy2", "ss")]


def reality_sni(s):
    return str(s.get("REALITY_SNI") or DEFAULTS["REALITY_SNI"]).strip()


def build_config(s, st, port, core):
    entries = enabled(s)
    inbounds = []
    if core == "mihomo":
        return build_mihomo_config(s, st, port, entries)
    log_level = str(s.get("LOG_LEVEL") or "warn").strip().lower()

    if "vless" in entries:
        sni = reality_sni(s)
        inbound = {
            "type": "vless",
            "tag": "vless-in",
            "listen": "::",
            "listen_port": port,
            "users": [{"name": "default", "uuid": st["uuid"], "flow": "xtls-rprx-vision"}],
            "tls": {
                "enabled": True,
                "server_name": sni,
                "reality": {
                    "enabled": True,
                    "handshake": {
                        "server": str(s.get("REALITY_HANDSHAKE") or sni).strip(),
                        "server_port": 443,
                    },
                    "private_key": st["reality_private"],
                    "short_id": [st["short_id"]],
                },
            },
            "multiplex": {"enabled": True, "padding": True},
        }
        inbounds.append(inbound)

    if "hy2" in entries:
        cert, key = s["_cert"], s["_key"]
        sni = str(s.get("HY2_SNI") or "__cert__").strip()
        if sni == "__cert__":
            sni = cert_common_name(cert) or reality_sni(s)
        tls = {
            "enabled": True,
            "alpn": ["h3"],
            "certificate_path": cert,
            "key_path": key,
            "min_version": "1.3",
        }
        if not truthy(s.get("INSECURE_CERT", "1")):
            tls["server_name"] = sni
        inbound = {
            "type": "hysteria2",
            "tag": "hy2-in",
            "listen": "::",
            "listen_port": port,
            "users": [{"name": "default", "password": st["hy2_password"]}],
            "tls": tls,
            "masquerade": {
                "type": "string",
                "status_code": 404,
                "headers": {"Content-Type": ["text/plain"]},
                "content": "not found",
            },
        }
        obfs = str(s.get("HY2_OBFS") or "").strip().lower()
        if obfs == "salamander":
            inbound["obfs"] = {
                "type": "salamander",
                "password": str(s.get("HY2_OBFS_PASSWORD") or st.get("hy2_obfs_password") or ""),
            }
        up = str(s.get("HY2_UP_MBPS") or "").strip()
        down = str(s.get("HY2_DOWN_MBPS") or "").strip()
        if up.isdigit() and down.isdigit():
            inbound["up_mbps"] = int(up)
            inbound["down_mbps"] = int(down)
        else:
            inbound["ignore_client_bandwidth"] = True
        inbounds.append(inbound)

    if "ss" in entries:
        method = str(s.get("SS_METHOD") or DEFAULTS["SS_METHOD"]).strip()
        inbound = {
            "type": "shadowsocks",
            "tag": "ss-in",
            "listen": "::",
            "method": method,
            "password": st["ss_password"],
            "listen_port": ss_port(s, port),
            # sing-box opens every inbound as a v6+v4 dual stack socket, and two
            # of those cannot share a port. hy2 already owns UDP there, so when
            # both are enabled shadowsocks moves to its own port.
            "listen_port": ss_port(s, port),
        }
        # hy2 owns UDP on the node port, so shadowsocks may not bind it
        if inbound["listen_port"] != port and not truthy(s.get("SS_UDP")):
            inbound["network"] = "tcp"
        inbounds.append(inbound)
        if inbound["listen_port"] != port:
            s["_ss_port"] = inbound["listen_port"]

    config = {
        "log": {"level": log_level, "timestamp": True},
        "inbounds": inbounds,
        "outbounds": [{"type": "direct", "tag": "direct"}],
    }
    return config


def ss_port(s, port):
    """Shadowsocks port: shares the node port unless hy2 needs it for UDP."""
    entries = enabled(s)
    if "hy2" not in entries:
        return port
    raw = str(s.get("SS_PORT") or "").strip()
    if raw.isdigit():
        return int(raw)
    # vless grabs the port first; hy2 needs UDP so it cannot piggyback on it
    # either, and mihomo will not let a second listener reuse the port cleanly.
    # Shadowsocks therefore gets the next port up. Set SS_PORT when the panel
    # opens only one port (ss then has nowhere to go and is skipped).
    if "vless" not in entries:
        return port
    return port + 1


def hy2_obfs_password(s, st):
    return str(s.get("HY2_OBFS_PASSWORD") or st.get("hy2_obfs_password") or "")


def build_mihomo_config(s, st, port, entries):
    """mihomo (Clash Meta) takes the same listeners under a different schema."""
    listeners = []
    level = str(s.get("LOG_LEVEL") or "warn").strip().lower()
    level = {"warning": "warning", "warn": "warning", "error": "error", "info": "info", "debug": "debug"}.get(
        level, "warning"
    )

    if "vless" in entries and not st.get("reality_private"):
        raise SystemExit(
            "[nodaroo-box] VLESS+Reality needs an x25519 keypair, and mihomo cannot\n"
            "generate one. Two ways out:\n"
            "  1) run `sing-box generate reality-keypair` once anywhere and put the\n"
            "     two values in node.env as REALITY_PRIVATE_KEY / REALITY_PUBLIC_KEY\n"
            "  2) drop vless from ENABLE and run hysteria2 + shadowsocks only"
        )
    if "vless" in entries:
        sni = reality_sni(s)
        reality = {
            "dest": f"{str(s.get('REALITY_HANDSHAKE') or sni).strip()}:443",
            "private-key": st["reality_private"],
            "short-id": [st["short_id"]],
            "server-names": [sni],
        }
        listeners.append(
            {
                "name": "vless-in",
                "type": "vless",
                "port": port,
                "listen": "0.0.0.0",
                "users": [{"username": "default", "uuid": st["uuid"], "flow": "xtls-rprx-vision"}],
                "reality-config": reality,
            }
        )

    if "hy2" in entries:
        sni = str(s.get("HY2_SNI") or "").strip() or cert_common_name(s["_cert"]) or reality_sni(s)
        listener = {
            "name": "hy2-in",
            "type": "hysteria2",
            "port": port,
            "listen": "0.0.0.0",
            "users": {"default": st["hy2_password"]},
            "certificate": s["_cert"],
            "private-key": s["_key"],
            "masquerade": "https://www.bing.com",
        }
        if not truthy(s.get("INSECURE_CERT", "1")):
            listener["sni"] = sni
        if str(s.get("HY2_OBFS") or "").strip().lower() == "salamander":
            listener["obfs"] = "salamander"
            listener["obfs-password"] = hy2_obfs_password(s, st)
        up = str(s.get("HY2_UP_MBPS") or "").strip()
        down = str(s.get("HY2_DOWN_MBPS") or "").strip()
        if up.isdigit():
            listener["up"] = f"{int(up)} Mbps"
        if down.isdigit():
            listener["down"] = f"{int(down)} Mbps"
        listeners.append(listener)

    if "ss" in entries:
        listeners.append(
            {
                "name": "ss-in",
                "type": "shadowsocks",
                "port": port,
                "listen": "0.0.0.0",
                "cipher": str(s.get("SS_METHOD") or DEFAULTS["SS_METHOD"]).strip(),
                "password": st["ss_password"],
                "port": ss_port(s, port),
            }
        )
        # mihomo can share a port only on a transport nobody else took; hy2 owns
        # UDP there. TCP sharing is attempted, but mihomo's own listener set is
        # greedy, so shadow-a-ss/SS_PORT is the reliable escape hatch.
        if "hy2" in entries and not truthy(s.get("SS_UDP")):
            listeners[-1]["udp"] = False

    return {
        "log-level": level,
        "ipv6": True,
        "listeners": listeners,
        "proxies": [{"name": "direct", "type": "direct"}],
        "rules": ["MATCH,direct"],
    }


def verify_listeners(config, core):
    """mihomo logs a failed bind but keeps running; surface it as a hard error."""
    by_port = {}
    for listener in config.get("listeners", []):
        by_port.setdefault(listener.get("port"), []).append(listener.get("type", "?"))
    for port, kinds in by_port.items():
        if len(kinds) > 1:
            log(f"note: port {port} carries {' + '.join(kinds)} (TCP and UDP are separate sockets)")


def probe_ports(config, core):
    """Connect to every listener port once the core is up; report dead entries."""
    # hy2 is UDP-only, so a TCP connect can never prove it is up
    ports = sorted({l.get("port") for l in config.get("listeners", [])
                    if l.get("port") and l.get("type") not in ("hysteria2", "tuic")})
    dead = []
    for port in ports:
        alive = False
        for attempt in range(5):
            sock = socket.socket()
            sock.settimeout(2)
            try:
                sock.connect(("127.0.0.1", port))
                alive = True
            except OSError:
                time.sleep(1)
            finally:
                sock.close()
            if alive:
                break
        if not alive:
            dead.append(port)
    for port in dead:
        log(f"warning: nothing is listening on port {port}; check the host's port range")


def write_config(config, core):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_FILE)


def check_config(core):
    cmd = [BIN, "-t", "-f", CONFIG_FILE] if core == "mihomo" else [BIN, "check", "-c", CONFIG_FILE]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        print((out.stderr or out.stdout or "").strip(), flush=True)
        raise SystemExit(f"[nodaroo-box] {core} rejected the generated config")
    return True


# --------------------------------------------------------------------------
# share links


def label(s, suffix):
    base = str(s.get("NODE_NAME") or "").strip() or str(s.get("NODE_HOST") or "").strip() or "node"
    return f"{base}-{suffix}"


def links(s, st, port):
    host = pick_host(s)
    out = []
    entries = enabled(s)
    if "vless" in entries:
        sni = reality_sni(s)
        q = (
            f"encryption=none&security=reality&sni={sni}&fp=chrome"
            f"&pbk={st['reality_public']}&sid={st['short_id']}"
            "&type=tcp&flow=xtls-rprx-vision"
        )
        out.append(
            ("vless", f"vless://{st['uuid']}@{host}:{port}?{q}#{urllib.parse.quote(label(s, 'vless'))}")
        )
    if "hy2" in entries:
        sni = str(s.get("HY2_SNI") or "").strip() or cert_common_name(s["_cert"]) or reality_sni(s)
        q = f"insecure={'1' if truthy(s.get('INSECURE_CERT', '1')) else '0'}&sni={sni}"
        obfs = str(s.get("HY2_OBFS") or "").strip().lower()
        if obfs == "salamander":
            pw = str(s.get("HY2_OBFS_PASSWORD") or st.get("hy2_obfs_password") or "")
            q += f"&obfs=salamander&obfs-password={urllib.parse.quote(pw)}"
        out.append(
            (
                "hy2",
                f"hysteria2://{urllib.parse.quote(st['hy2_password'])}@{host}:{port}/?{q}"
                f"#{urllib.parse.quote(label(s, 'hy2'))}",
            )
        )
    if "ss" in entries:
        method = str(s.get("SS_METHOD") or DEFAULTS["SS_METHOD"]).strip()
        userinfo = urllib.parse.quote(f"{method}:{st['ss_password']}", safe="")
        ss_port_ = ss_port(s, port)
        out.append(
            ("ss", f"ss://{userinfo}@{host}:{ss_port_}#{urllib.parse.quote(label(s, 'ss'))}")
        )
    return out


def write_outputs(items, s, st, port):
    os.makedirs(OUT_DIR, exist_ok=True)
    all_links = [uri for _, uri in items]

    with open(os.path.join(OUT_DIR, "links.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(all_links) + "\n")

    import base64

    sub = base64.b64encode("\n".join(all_links).encode()).decode()
    with open(os.path.join(OUT_DIR, "subscription.txt"), "w", encoding="utf-8") as f:
        f.write(sub + "\n")

    meta = {
        "host": pick_host(s),
        "port": port,
        "entries": enabled(s),
        "uuid": st["uuid"],
        "reality_public_key": st.get("reality_public", ""),
        "reality_short_id": st.get("short_id", ""),
        "reality_sni": reality_sni(s) if "vless" in enabled(s) else "",
        "hy2_password": st.get("hy2_password", ""),
        "ss_method": str(s.get("SS_METHOD") or DEFAULTS["SS_METHOD"]),
        "ss_password": st.get("ss_password", ""),
        "ss_port": ss_port(s, port),
        "links": all_links,
    }
    with open(os.path.join(OUT_DIR, "node.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    os.chmod(os.path.join(OUT_DIR, "node.json"), 0o600)


def print_links(items):
    print("", flush=True)
    print("=" * 74, flush=True)
    print(" share links (copy into your client, or use out/subscription.txt)", flush=True)
    print("=" * 74, flush=True)
    for kind, uri in items:
        print(f"{kind:>5}: {uri}", flush=True)
    print("=" * 74, flush=True)
    print("", flush=True)


# --------------------------------------------------------------------------
# process supervision


class Runner:
    def __init__(self, core):
        self.core = core
        self.proc = None
        self.stopping = False
        self.delay = 3

    def request_stop(self, *_):
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def start(self):
        if self.proc and self.proc.poll() is not None:
            # close the previous child's pipe before replacing it, and give the
            # kernel a moment to release the listen socket it just closed
            try:
                self.proc.stdout.close()
            except (OSError, AttributeError):
                pass
            if not self.stopping:
                time.sleep(1)
        out = open(LOG_FILE, "ab", buffering=0)
        cmd = [BIN, "-d", BASE, "-f", CONFIG_FILE] if self.core == "mihomo" else [
            BIN, "run", "-c", CONFIG_FILE
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=BASE,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.started = time.time()
        time.sleep(3)  # let the core finish binding before anything probes it

    def watch(self, watchdog):
        while True:
            code = self.proc.poll()
            if code is None:
                time.sleep(2)
                continue
            if self.stopping:
                return code
            if not watchdog:
                log(f"{self.core} exited with code {code}, WATCHDOG=0 so not restarting")
                return code
            if time.time() - self.started > 120:
                self.delay = 3
            log(f"{self.core} exited with code {code}, restarting in {self.delay}s")
            for _ in range(self.delay * 2):
                if self.stopping:
                    return code
                time.sleep(0.5)
            self.delay = min(self.delay * 2, 60)
            self.start()


def port_free_hint(port):
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("::", port))
    except OSError as e:
        log(f"warning: TCP {port} looks busy ({e})")
    finally:
        s.close()


def main():
    os.chdir(BASE)
    s = load_settings()
    port = pick_port(s)
    if not str(s.get("NODE_HOST") or "").strip():
        log("NODE_HOST is empty, printed links will contain a placeholder host")

    core = ensure_core(s)
    state = ensure_state(s, load_state(), core)
    if "hy2" in enabled(s):
        s["_cert"], s["_key"] = ensure_cert(s, core)
    else:
        s["_cert"] = s["_key"] = ""

    config = build_config(s, state, port, core)
    write_config(config, core)
    check_config(core)
    if truthy(s.get("PRINT_CONFIG", "0")):
        print(json.dumps(config, indent=2), flush=True)

    verify_listeners(config, core)
    items = links(s, state, port)
    write_outputs(items, s, state, port)
    print_links(items)
    log(f"starting {core} on TCP+UDP {port} ({', '.join(enabled(s)) or 'nothing enabled'})")
    port_free_hint(port)

    runner = Runner(core)
    signal.signal(signal.SIGTERM, runner.request_stop)
    signal.signal(signal.SIGINT, runner.request_stop)
    runner.start()
    probe_ports(config, core)
    code = runner.watch(truthy(s.get("WATCHDOG", "1")))
    log("stopped")
    sys.exit(code or 0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
