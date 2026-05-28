import json
import os
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(os.environ.get("MV_CONFIG_PATH", "config.json"))
DEFAULT_CACHE_DIR = "/var/lib/messagesviewer/cache"


class Config:
    def __init__(self) -> None:
        self.data_dir: Optional[str] = None
        self.cache_dir: str = os.environ.get("MV_CACHE_DIR", DEFAULT_CACHE_DIR)
        self.load()

    def load(self) -> None:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
            self.data_dir = data.get("data_dir")

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps({"data_dir": self.data_dir}, indent=2))

    def set_data_dir(self, path: str) -> None:
        self.data_dir = path
        self.save()

    @property
    def chat_db_path(self) -> Optional[Path]:
        if not self.data_dir:
            return None
        return Path(self.data_dir) / "chat.db"

    @property
    def attachments_dir(self) -> Optional[Path]:
        if not self.data_dir:
            return None
        return Path(self.data_dir) / "Attachments"

    @property
    def cache_db_path(self) -> Path:
        return Path(self.cache_dir) / "chat.db"

    def is_configured(self) -> bool:
        return self.chat_db_path is not None and self.chat_db_path.exists()


config = Config()
