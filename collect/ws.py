"""최소 WebSocket 클라이언트 (RFC 6455, 표준 라이브러리만).

hs-collect는 맥미니에 받아서 바로 돌리는 게 목표라 pip 의존을 두지 않는다.
읽기 전용 용도에 필요한 것만 구현했다: 핸드셰이크, 텍스트 프레임 수신, ping 응답, 마스킹 송신.
"""
import base64, os, socket, ssl, struct, urllib.parse

class WSError(Exception): pass

class WebSocket:
    def __init__(self, url, timeout=30, origin=None):
        u = urllib.parse.urlparse(url)
        secure = u.scheme == "wss"
        host = u.hostname
        port = u.port or (443 if secure else 80)
        path = (u.path or "/") + (("?" + u.query) if u.query else "")

        self.sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=host)
        self.sock.settimeout(timeout)

        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36\r\n"
               + (f"Origin: {origin}\r\n" if origin else "") + "\r\n")
        self.sock.sendall(req.encode())

        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.sock.recv(4096)
            if not c: raise WSError("핸드셰이크 중 연결 종료")
            buf += c
            if len(buf) > 65536: raise WSError("핸드셰이크 헤더 과대")
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise WSError("업그레이드 거부: " + head.split(b"\r\n")[0].decode("latin1")[:120])
        self._buf = rest

    # ── 내부 ──
    def _read(self, n):
        while len(self._buf) < n:
            c = self.sock.recv(65536)
            if not c: raise WSError("연결 종료")
            self._buf += c
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode, payload=b""):
        n = len(payload)
        h = bytearray([0x80 | opcode])
        if n < 126:   h.append(0x80 | n)
        elif n < 1 << 16: h.append(0x80 | 126); h += struct.pack("!H", n)
        else:         h.append(0x80 | 127); h += struct.pack("!Q", n)
        m = os.urandom(4)
        h += m
        self.sock.sendall(bytes(h) + bytes(b ^ m[i % 4] for i, b in enumerate(payload)))

    # ── 공개 ──
    def send(self, text):
        self._send_frame(0x1, text.encode())

    def recv(self):
        """텍스트 메시지 하나를 반환한다. ping은 내부에서 pong으로 응답하고 계속 읽는다."""
        chunks, op = [], None
        while True:
            b0, b1 = self._read(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            ln = b1 & 0x7F
            if ln == 126:   ln = struct.unpack("!H", self._read(2))[0]
            elif ln == 127: ln = struct.unpack("!Q", self._read(8))[0]
            if b1 & 0x80:   self._read(4)          # 서버는 마스킹하지 않지만 방어적으로
            data = self._read(ln) if ln else b""

            if opcode == 0x9: self._send_frame(0xA, data); continue      # ping → pong
            if opcode == 0xA: continue                                    # pong 무시
            if opcode == 0x8: raise WSError("서버가 close 프레임 전송")
            if opcode in (0x1, 0x2): op = opcode; chunks = [data]
            elif opcode == 0x0:      chunks.append(data)
            if fin and op is not None:
                return b"".join(chunks).decode("utf-8", "replace")

    def ping(self): self._send_frame(0x9, b"")

    def close(self):
        try: self._send_frame(0x8, b""); self.sock.close()
        except Exception: pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()
