"""Explorer package — file tree browser security + watcher utilities."""

from src.explorer.security import authorize_root, is_authorized, validate_path
from src.explorer.files import copy_node
from src.explorer.watcher import WatchManager, get_watch_manager

__all__ = [
    "authorize_root",
    "copy_node",
    "is_authorized",
    "validate_path",
    "WatchManager",
    "get_watch_manager",
]
