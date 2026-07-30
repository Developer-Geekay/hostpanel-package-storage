import os
import re
import shutil
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from deps import get_current_user, require_admin
from auth import User
from modules.audit.logger import log_action
from hostpanel_storage.settings import get_bucket_path, get_data_path, ensure_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cpanelapi/storage/buckets", tags=["Storage Buckets"])

BUCKET_NAME_REGEX = re.compile(r'^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')


class BucketInfo(BaseModel):
    id: int
    name: str
    owner: str
    public_access: bool
    quota_mb: int
    used_bytes: int
    used_mb: float
    object_count: int
    region: str
    custom_path: Optional[str] = None
    created_at: str


class BucketCreateRequest(BaseModel):
    name: str
    public_access: bool = False
    quota_mb: int = 5120
    region: str = "us-east-1"
    custom_path: Optional[str] = None


class BucketUpdateRequest(BaseModel):
    public_access: Optional[bool] = None
    quota_mb: Optional[int] = None
    custom_path: Optional[str] = None


def get_dir_stats(path: str) -> tuple[int, int]:
    """Calculate total size in bytes and file count for a directory."""
    if not os.path.exists(path):
        return 0, 0
    total_size = 0
    file_count = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                file_path = os.path.join(root, f)
                if not os.path.islink(file_path):
                    total_size += os.path.getsize(file_path)
                    file_count += 1
    except Exception as e:
        logger.warning(f"Error calculating stats for {path}: {e}")
    return total_size, file_count


def validate_bucket_ownership(current_user: User, bucket: dict):
    if current_user.role != "admin" and bucket["owner"] != current_user.username:
        raise HTTPException(status_code=403, detail="Permission denied for this bucket")


@router.get("", response_model=List[BucketInfo])
async def list_buckets(current_user: User = Depends(get_current_user)):
    from db import get_conn
    with get_conn() as conn:
        if current_user.role == "admin":
            rows = conn.execute("SELECT * FROM storage_buckets ORDER BY name ASC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM storage_buckets WHERE owner = ? ORDER BY name ASC",
                (current_user.username,)
            ).fetchall()

    results = []
    for r in rows:
        b_dict = dict(r)
        b_path = get_bucket_path(b_dict["name"], b_dict.get("custom_path"))
        used_bytes, obj_count = get_dir_stats(b_path)
        results.append(BucketInfo(
            id=b_dict["id"],
            name=b_dict["name"],
            owner=b_dict["owner"],
            public_access=bool(b_dict["public_access"]),
            quota_mb=b_dict["quota_mb"],
            used_bytes=used_bytes,
            used_mb=round(used_bytes / (1024 * 1024), 2),
            object_count=obj_count,
            region=b_dict["region"],
            custom_path=b_dict.get("custom_path"),
            created_at=b_dict["created_at"],
        ))
    return results


@router.post("", response_model=BucketInfo)
async def create_bucket(request: BucketCreateRequest, current_user: User = Depends(get_current_user)):
    name = request.name.strip().lower()
    if not BUCKET_NAME_REGEX.match(name) or ".." in name or "/" in name:
        raise HTTPException(
            status_code=400,
            detail="Invalid bucket name. Must be 3-63 characters, lowercase letters, numbers, hyphens, or dots."
        )

    from db import get_conn
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM storage_buckets WHERE name = ?", (name,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"Bucket '{name}' already exists")

        b_path = get_bucket_path(name, request.custom_path)
        ensure_data_dir(b_path)

        owner = current_user.username
        cursor = conn.execute(
            """INSERT INTO storage_buckets (name, owner, public_access, quota_mb, region, custom_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, owner, int(request.public_access), request.quota_mb, request.region, request.custom_path)
        )
        bucket_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM storage_buckets WHERE id = ?", (bucket_id,)).fetchone()
        b_dict = dict(row)

    log_action(current_user.username, "storage.bucket_create", name, f"quota={request.quota_mb}MB")

    return BucketInfo(
        id=b_dict["id"],
        name=b_dict["name"],
        owner=b_dict["owner"],
        public_access=bool(b_dict["public_access"]),
        quota_mb=b_dict["quota_mb"],
        used_bytes=0,
        used_mb=0.0,
        object_count=0,
        region=b_dict["region"],
        custom_path=b_dict.get("custom_path"),
        created_at=b_dict["created_at"],
    )


@router.get("/{name}", response_model=BucketInfo)
async def get_bucket(name: str, current_user: User = Depends(get_current_user)):
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Bucket '{name}' not found")
        b_dict = dict(row)

    validate_bucket_ownership(current_user, b_dict)

    b_path = get_bucket_path(b_dict["name"], b_dict.get("custom_path"))
    used_bytes, obj_count = get_dir_stats(b_path)

    return BucketInfo(
        id=b_dict["id"],
        name=b_dict["name"],
        owner=b_dict["owner"],
        public_access=bool(b_dict["public_access"]),
        quota_mb=b_dict["quota_mb"],
        used_bytes=used_bytes,
        used_mb=round(used_bytes / (1024 * 1024), 2),
        object_count=obj_count,
        region=b_dict["region"],
        custom_path=b_dict.get("custom_path"),
        created_at=b_dict["created_at"],
    )


@router.put("/{name}", response_model=BucketInfo)
async def update_bucket(name: str, payload: BucketUpdateRequest, current_user: User = Depends(get_current_user)):
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Bucket '{name}' not found")
        b_dict = dict(row)

    validate_bucket_ownership(current_user, b_dict)

    updates = []
    params = []
    if payload.public_access is not None:
        updates.append("public_access = ?")
        params.append(int(payload.public_access))
    if payload.quota_mb is not None:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Only administrators can adjust bucket quotas")
        updates.append("quota_mb = ?")
        params.append(payload.quota_mb)
    if payload.custom_path is not None:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Only administrators can set custom bucket storage paths")
        updates.append("custom_path = ?")
        params.append(payload.custom_path)
        ensure_data_dir(payload.custom_path)

    if updates:
        params.append(name)
        with get_conn() as conn:
            conn.execute(f"UPDATE storage_buckets SET {', '.join(updates)} WHERE name = ?", params)
        log_action(current_user.username, "storage.bucket_update", name, ", ".join(updates))

    return await get_bucket(name, current_user)


@router.delete("/{name}")
async def delete_bucket(name: str, force: bool = Query(False), current_user: User = Depends(get_current_user)):
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Bucket '{name}' not found")
        b_dict = dict(row)

    validate_bucket_ownership(current_user, b_dict)

    b_path = get_bucket_path(name, b_dict.get("custom_path"))
    used_bytes, obj_count = get_dir_stats(b_path)

    if obj_count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Bucket '{name}' contains {obj_count} objects. Delete all objects first or set force=true."
        )

    if os.path.exists(b_path):
        try:
            shutil.rmtree(b_path)
        except Exception as e:
            logger.error(f"Failed to remove bucket directory {b_path}: {e}")

    with get_conn() as conn:
        conn.execute("DELETE FROM storage_access_keys WHERE bucket_id = ?", (b_dict["id"],))
        conn.execute("DELETE FROM storage_buckets WHERE name = ?", (name,))

    log_action(current_user.username, "storage.bucket_delete", name, f"force={force}")
    return {"message": f"Bucket '{name}' successfully deleted", "name": name}
