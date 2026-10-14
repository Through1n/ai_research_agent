"""原始 socket WebSocket 握手测试：验证浏览器真实场景（带 Origin）能否建立连接。

判据：
  101 Switching Protocols → 握手成功，Streamlit 接受了本域名的 Origin
  403 Forbidden          → enableCORS 修复未生效，页面必然全白
"""
import base64
import os
import socket
import ssl
import sys
import time
from urllib.parse import urlparse

URL = os.environ.get("TARGET_URL", "https://ai4s-research-agent.app.workbuddy.host")
p = urlparse(URL)
host = p.hostname
port = p.port or 443


def handshake(origin: str) -> tuple[int, str]:
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET /_stcore/stream HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits\r\n"
        f"User-Agent: Mozilla/5.0\r\n"
        f"Accept-Language: zh-CN,zh;q=0.9\r\n"
    )
    if origin is not None:
        req += f"Origin: {origin}\r\n"
    req += "\r\n"

    raw = socket.create_connection((host, port), timeout=20)
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(raw, server_hostname=host)
    sock.settimeout(25)
    t0 = time.time()
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf and time.time() - t0 < 20:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    sock.close()
    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    code = int(head.split(" ", 2)[1])
    return code, head


CASES = [
    ("浏览器带本域名 Origin", f"https://{host}"),
    ("浏览器带 http Origin", f"http://{host}"),
    ("无 Origin（curl/探测工具）", None),
]

print(f"=== WebSocket 握手 @ {URL} ===")
for label, origin in CASES:
    try:
        code, head = handshake(origin)
        tail = ""
        if code != 101:
            for line in head.split("\r\n"):
                if line.lower().startswith(("content-type", "content-length", "x-")):
                    tail += f"  {line.strip()}\n"
        print(f"  {label:28s} → HTTP {code}" + (f"\n{tail.rstrip()}" if tail else ""))
    except Exception as e:  # noqa: BLE001
        print(f"  {label:28s} → 异常 {type(e).__name__}: {e}")

print("\n结论：三例全 101 = WebSocket 层已通；若仍全白，故障点在 Streamlit 脚本执行层。")
