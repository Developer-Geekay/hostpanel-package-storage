import os
import shutil
import subprocess
import logging
from fastapi import HTTPException

from hostpanel_storage.settings import (
    init_storage_tables,
    get_data_path,
    ensure_data_dir,
    PLUGIN_ROOT,
    DEFAULT_DATA_PATH
)

logger = logging.getLogger(__name__)


def configure_ufw_port(port: int = 9000, action: str = "allow"):
    """Enable or disable UFW firewall rule for S3 port."""
    try:
        if action == "delete allow":
            subprocess.run(["sudo", "-n", "ufw", "delete", "allow", f"{port}/tcp"], capture_output=True, text=True, check=False)
        else:
            subprocess.run(["sudo", "-n", "ufw", "allow", f"{port}/tcp"], capture_output=True, text=True, check=False)
    except Exception as e:
        logger.warning(f"Could not configure UFW port {port}/tcp: {e}")


def on_install():
    """Run on package installation. Safe to re-run."""
    logger.info("Initializing hostpanel-package-storage...")
    os.makedirs(PLUGIN_ROOT, mode=0o755, exist_ok=True)
    os.makedirs(os.path.join(PLUGIN_ROOT, "conf"), mode=0o755, exist_ok=True)

    init_storage_tables()

    data_path = get_data_path()
    ensure_data_dir(data_path)
    ensure_data_dir(os.path.join(data_path, "buckets"))

    configure_ufw_port(9000, "allow")
    logger.info(f"hostpanel-package-storage installed cleanly. Data path: {data_path}, UFW port 9000 allowed.")


def on_update():
    """Run on package update. Refresh schemas safely."""
    logger.info("Updating hostpanel-package-storage...")
    init_storage_tables()
    data_path = get_data_path()
    ensure_data_dir(data_path)
    configure_ufw_port(9000, "allow")
    logger.info("hostpanel-package-storage update complete.")


def on_startup():
    """Run at HostPanel boot to repair missing directory structures."""
    try:
        os.makedirs(PLUGIN_ROOT, mode=0o755, exist_ok=True)
        init_storage_tables()
        data_path = get_data_path()
        ensure_data_dir(data_path)
        ensure_data_dir(os.path.join(data_path, "buckets"))
        configure_ufw_port(9000, "allow")
    except Exception as e:
        logger.warning(f"Storage on_startup check failed: {e}")


def pre_uninstall(force: bool = False):
    """Run before package uninstallation."""
    logger.info(f"Running pre_uninstall for hostpanel-package-storage (force={force})...")
    from db import get_conn
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM storage_buckets").fetchone()
            bucket_count = row["count"] if row else 0

        if bucket_count > 0 and not force:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot uninstall storage package: {bucket_count} bucket(s) exist. "
                       f"Delete all buckets first or pass force=true."
            )

        if force:
            logger.info("Force uninstall specified. Cleaning up buckets, firewall rules, and database records...")
            configure_ufw_port(9000, "delete allow")
            data_path = get_data_path()
            if os.path.exists(data_path):
                shutil.rmtree(data_path, ignore_errors=True)

            with get_conn() as conn:
                conn.execute("DROP TABLE IF EXISTS storage_access_keys")
                conn.execute("DROP TABLE IF EXISTS storage_buckets")
                conn.execute("DROP TABLE IF EXISTS storage_settings")

        logger.info("Pre-uninstall hook completed successfully.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in storage pre_uninstall: {e}")
        if not force:
            raise HTTPException(status_code=500, detail=f"Uninstall failed: {e}")


def on_user_delete(username: str, **kwargs):
    """Cleanup buckets owned by a deleted tenant user."""
    logger.info(f"Processing storage cleanup for deleted user '{username}'...")
    from db import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM storage_buckets WHERE owner = ?", (username,)).fetchall()
            for r in rows:
                b_path = r["custom_path"] or os.path.join(get_data_path(), "buckets", r["name"])
                if os.path.exists(b_path):
                    shutil.rmtree(b_path, ignore_errors=True)
            conn.execute("DELETE FROM storage_access_keys WHERE owner = ?", (username,))
            conn.execute("DELETE FROM storage_buckets WHERE owner = ?", (username,))
        logger.info(f"Cleaned up storage buckets for user '{username}'.")
    except Exception as e:
        logger.error(f"Failed to cleanup storage for deleted user '{username}': {e}")
