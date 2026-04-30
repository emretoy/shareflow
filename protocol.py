"""ShareFlow - Protokol ve mesaj tanımları.

Her mesaj JSON formatında, newline ile ayrılmış (JSON Lines).
"""

import json
import struct

HEADER_SIZE = 4  # 4 byte mesaj uzunluğu (big-endian uint32)


def encode_message(msg: dict) -> bytes:
    """Mesajı length-prefixed binary formatına çevir."""
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(data)) + data


def decode_message(data: bytes) -> dict:
    """Binary datadan mesajı çöz."""
    return json.loads(data.decode("utf-8"))


def read_message(sock) -> dict | None:
    """Soket'ten tam bir mesaj oku. Bağlantı kapanırsa None döner."""
    header = _recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None
    length = struct.unpack("!I", header)[0]
    if length > 10 * 1024 * 1024:  # 10MB güvenlik limiti
        raise ValueError(f"Mesaj çok büyük: {length} bytes")
    data = _recv_exact(sock, length)
    if data is None:
        return None
    return decode_message(data)


def send_message(sock, msg: dict):
    """Soket'e mesaj gönder."""
    sock.sendall(encode_message(msg))


def _recv_exact(sock, n: int) -> bytes | None:
    """Soket'ten tam n byte oku."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(remaining, 4096))
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# Mesaj oluşturma yardımcıları

def mouse_move(x: int, y: int) -> dict:
    return {"type": "mouse_move", "x": x, "y": y}


def mouse_click(button: str, action: str) -> dict:
    return {"type": "mouse_click", "button": button, "action": action}


def mouse_scroll(dx: int, dy: int) -> dict:
    return {"type": "mouse_scroll", "dx": dx, "dy": dy}


def key_event(key: str, action: str) -> dict:
    return {"type": "key", "key": key, "action": action}


def clipboard_msg(content: str) -> dict:
    return {"type": "clipboard", "content": content}


def file_start(name: str, size: int) -> dict:
    return {"type": "file_start", "name": name, "size": size}


def file_chunk(data: str) -> dict:
    return {"type": "file_chunk", "data": data}


def file_end() -> dict:
    return {"type": "file_end"}


def switch_back() -> dict:
    return {"type": "switch_back"}
