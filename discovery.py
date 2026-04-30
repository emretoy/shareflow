"""ShareFlow - Otomatik keşif modülü.

UDP broadcast ile ağdaki ShareFlow cihazlarını bulur.
Server kendini duyurur, client dinleyerek server'ı otomatik bulur.
"""

import json
import socket
import threading
import time

DISCOVERY_PORT = 24801
BROADCAST_INTERVAL = 3  # saniye
MAGIC = "SHAREFLOW"


def get_local_ip() -> str:
    """Makinenin yerel ağ IP'sini bul."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class DiscoveryBroadcaster:
    """Server tarafı - varlığını UDP broadcast ile duyurur."""

    def __init__(self, service_port: int, hostname: str = None):
        self.service_port = service_port
        self.hostname = hostname or socket.gethostname()
        self.running = False
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1)

        local_ip = get_local_ip()
        msg = json.dumps({
            "magic": MAGIC,
            "host": local_ip,
            "port": self.service_port,
            "name": self.hostname,
        }).encode("utf-8")

        print(f"[Discovery] Broadcast başladı: {local_ip}:{self.service_port}")

        while self.running:
            try:
                sock.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
            except Exception:
                pass
            time.sleep(BROADCAST_INTERVAL)

        sock.close()


def discover_server(timeout: float = 15) -> dict | None:
    """Client tarafı - ağda ShareFlow server'ı ara.

    Bulunursa {"host": "...", "port": ..., "name": "..."} döner.
    Bulunamazsa None döner.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(2)

    print(f"[Discovery] Server aranıyor ({timeout}s)...")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(1024)
            msg = json.loads(data.decode("utf-8"))
            if msg.get("magic") == MAGIC:
                print(f"[Discovery] Server bulundu: {msg['name']} ({msg['host']}:{msg['port']})")
                sock.close()
                return msg
        except socket.timeout:
            continue
        except Exception:
            continue

    sock.close()
    print("[Discovery] Server bulunamadı")
    return None
