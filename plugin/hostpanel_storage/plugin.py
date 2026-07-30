from fastapi import APIRouter, Depends
from deps import get_current_user
from auth import User

from hostpanel_storage.buckets import router as buckets_router
from hostpanel_storage.objects import router as objects_router
from hostpanel_storage.keys import router as keys_router
from hostpanel_storage.settings import router as settings_router
from hostpanel_storage.s3_engine import public_s3_router

PLUGIN_MANIFEST = {
    "requires_core": [1, 1, 0],
    "repository": "https://github.com/Developer-Geekay/hostpanel-package-storage",
    "nav_items": [{
        "nav_route":         "storage",
        "nav_label":         "Object Storage (S3)",
        "nav_icon":          "cloud_queue",
        "nav_section":       "hosting",
        "nav_section_label": "Hosting",
        "nav_section_order": 35,
        "admin_only":        False,
    }],
    "dashboard_blocks": [{
        "type":     "stat",
        "label":    "S3 Buckets",
        "icon":     "cloud_queue",
        "endpoint": "storage/count",
        "size":     "sm",
    }],
    "service": {
        "name":        "storage",
        "unit":        "hostpanel-storage",
        "label":       "S3 Storage Daemon",
        "icon":        "cloud_queue",
        "can_reload":  True,
    },
}

# Stat router for dashboard block
count_router = APIRouter(prefix="/cpanelapi/storage", tags=["Storage Dashboard"])

@count_router.get("/count")
async def get_storage_count(current_user: User = Depends(get_current_user)):
    from db import get_conn
    with get_conn() as conn:
        if current_user.role == "admin":
            row = conn.execute("SELECT COUNT(*) as count FROM storage_buckets").fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as count FROM storage_buckets WHERE owner = ?", (current_user.username,)).fetchone()
    return {"count": row["count"] if row else 0}

routers = [buckets_router, objects_router, keys_router, settings_router, count_router]
public_routers = [public_s3_router]
