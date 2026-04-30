"""ShareFlow - Konfigürasyon"""

import json
import os

DEFAULT_CONFIG = {
    "port": 24800,
    "host": "0.0.0.0",
    "screen_width": 1920,
    "screen_height": 1080,
    "edge_threshold": 2,         # Piksel - kenar algılama eşiği
    "edge_dwell_time": 0.1,      # Saniye - kenarda bekleme süresi
    "switch_direction": "right",  # Mac'ten Windows'a geçiş yönü
    "clipboard_sync": True,
    "file_transfer": True,
    "file_chunk_size": 65536,     # 64KB chunk
    "ssl_enabled": False,         # İlk aşamada SSL kapalı
    "ssl_certfile": "cert.pem",
    "ssl_keyfile": "key.pem",
}

CONFIG_PATH = os.path.expanduser("~/.shareflow.json")


def load_config():
    """Konfigürasyonu dosyadan yükle, yoksa varsayılanı kullan."""
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            user_config = json.load(f)
            config.update(user_config)
    return config


def save_config(config):
    """Konfigürasyonu dosyaya kaydet."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
