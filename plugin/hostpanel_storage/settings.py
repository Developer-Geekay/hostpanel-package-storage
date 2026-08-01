import os
import shutil
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from deps import require_admin
    from auth import User
    from modules.audit.logger import log_action
except ImportError:
    def require_admin():
        pass
    class User:
        username: str = "system"
        role: str = "admin"
    def log_action(*args, **kwargs):
        pass

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cpanelapi/storage/settings", tags=["Storage Settings"])

DEFAULT_DATA_PATH = "/data/storage"
PLUGIN_ROOT = "/opt/hostpanel/plugins/storage"


def init_storage_tables():
    """Ensure storage tables exist in HostPanel SQLite DB."""
    from db import get_conn
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS storage_buckets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT UNIQUE NOT NULL,
            owner         TEXT NOT NULL,
            public_access INTEGER NOT NULL DEFAULT 0,
            quota_mb      INTEGER NOT NULL DEFAULT 5120,
            used_bytes    INTEGER NOT NULL DEFAULT 0,
            region        TEXT NOT NULL DEFAULT 'us-east-1',
            custom_path   TEXT,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS storage_access_keys (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            access_key    TEXT UNIQUE NOT NULL,
            secret_key    TEXT NOT NULL,
            owner         TEXT NOT NULL,
            label         TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'active',
            bucket_id     INTEGER,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY(bucket_id) REFERENCES storage_buckets(id) ON DELETE CASCADE
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS storage_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS storage_presigned_urls (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_name   TEXT NOT NULL,
            object_key    TEXT NOT NULL,
            token         TEXT UNIQUE NOT NULL,
            expires_at    INTEGER NOT NULL DEFAULT 0,
            status        TEXT NOT NULL DEFAULT 'active',
            created_by    TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS storage_object_acls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_name TEXT NOT NULL,
            object_key  TEXT NOT NULL,
            is_public   INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            UNIQUE(bucket_name, object_key)
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_storage_buckets_owner ON storage_buckets(owner);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_storage_keys_owner ON storage_access_keys(owner);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_storage_keys_access ON storage_access_keys(access_key);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_presigned_target ON storage_presigned_urls(bucket_name, object_key);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_presigned_token ON storage_presigned_urls(token);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_object_acls ON storage_object_acls(bucket_name, object_key);")

        # Insert default settings if missing
        conn.execute("INSERT OR IGNORE INTO storage_settings (key, value) VALUES ('storage_path', ?);", (DEFAULT_DATA_PATH,))
        conn.execute("INSERT OR IGNORE INTO storage_settings (key, value) VALUES ('s3_port', '9000');")
        conn.execute("INSERT OR IGNORE INTO storage_settings (key, value) VALUES ('s3_region', 'us-east-1');")
        conn.execute("INSERT OR IGNORE INTO storage_settings (key, value) VALUES ('s3_domain', 's3.consoleapi.in');")


def get_storage_setting(key: str, default: str = "") -> str:
    from db import get_conn
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM storage_settings WHERE key = ?", (key,)).fetchone()
            if row and row["value"] and row["value"].strip():
                return row["value"].strip()
            return default
    except Exception:
        return default


def set_storage_setting(key: str, value: str):
    from db import get_conn
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO storage_settings (key, value) VALUES (?, ?)", (key, str(value)))


def get_data_path() -> str:
    path = get_storage_setting("storage_path", DEFAULT_DATA_PATH).strip()
    return path or DEFAULT_DATA_PATH


def ensure_data_dir(path: str):
    import subprocess
    try:
        os.makedirs(path, mode=0o775, exist_ok=True)
    except PermissionError:
        try:
            subprocess.run(["sudo", "-n", "mkdir", "-p", path], capture_output=True, text=True, check=False)
            subprocess.run(["sudo", "-n", "chmod", "777", path], capture_output=True, text=True, check=False)
        except Exception as se:
            logger.warning(f"Could not sudo mkdir/chmod {path}: {se}")
    except Exception as e:
        logger.warning(f"Could not create data directory {path}: {e}")


def get_bucket_path(bucket_name: str, custom_path: Optional[str] = None) -> str:
    if custom_path and custom_path.strip():
        base = custom_path.strip()
    else:
        base = os.path.join(get_data_path(), "buckets", bucket_name)
    return base


class StorageSettingsUpdate(BaseModel):
    storage_path: Optional[str] = None
    s3_port: Optional[int] = None
    s3_region: Optional[str] = None
    s3_domain: Optional[str] = None


@router.get("")
async def get_settings(current_user: User = Depends(require_admin)):
    data_path = get_data_path()
    ensure_data_dir(data_path)

    total_bytes, used_bytes, free_bytes = 0, 0, 0
    try:
        usage = shutil.disk_usage(data_path if os.path.exists(data_path) else "/")
        total_bytes, used_bytes, free_bytes = usage.total, usage.used, usage.free
    except Exception as e:
        logger.warning(f"Could not get disk usage for {data_path}: {e}")

    return {
        "storage_path": data_path,
        "s3_port": int(get_storage_setting("s3_port", "9000")),
        "s3_region": get_storage_setting("s3_region", "us-east-1"),
        "s3_domain": get_storage_setting("s3_domain", ""),
        "plugin_root": PLUGIN_ROOT,
        "disk_total_mb": round(total_bytes / (1024 * 1024), 2),
        "disk_used_mb": round(used_bytes / (1024 * 1024), 2),
        "disk_free_mb": round(free_bytes / (1024 * 1024), 2),
    }


@router.post("")
async def update_settings(payload: StorageSettingsUpdate, current_user: User = Depends(require_admin)):
    if payload.storage_path is not None:
        new_path = payload.storage_path.strip()
        if not new_path.startswith("/"):
            raise HTTPException(status_code=400, detail="storage_path must be an absolute path (e.g. /data/storage)")
        ensure_data_dir(new_path)
        set_storage_setting("storage_path", new_path)
        log_action(current_user.username, "storage.settings_update", "storage_path", new_path)

    if payload.s3_port is not None:
        if not (1024 <= payload.s3_port <= 65535):
            raise HTTPException(status_code=400, detail="s3_port must be between 1024 and 65535")
        set_storage_setting("s3_port", str(payload.s3_port))
        log_action(current_user.username, "storage.settings_update", "s3_port", str(payload.s3_port))

    if payload.s3_region is not None:
        set_storage_setting("s3_region", payload.s3_region.strip() or "us-east-1")

    if payload.s3_domain is not None:
        set_storage_setting("s3_domain", payload.s3_domain.strip())

    return await get_settings(current_user)
