import os
import hmac
import hashlib
import time
import mimetypes
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from deps import get_current_user
from auth import User
from modules.audit.logger import log_action
from hostpanel_storage.settings import get_bucket_path
from hostpanel_storage.buckets import get_dir_stats, validate_bucket_ownership

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cpanelapi/storage/buckets/{bucket_name}/objects", tags=["Storage Objects"])


class ObjectInfo(BaseModel):
    key: str
    size_bytes: int
    size_formatted: str
    last_modified: str
    content_type: str
    is_dir: bool = False


class PresignRequest(BaseModel):
    object_key: str
    method: str = "GET"  # "GET" or "PUT"
    expires_in: int = 3600  # seconds


class PresignResponse(BaseModel):
    url: str
    expires_at: int
    object_key: str


def format_size(bytes_num: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"


def get_bucket_record(bucket_name: str) -> dict:
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (bucket_name,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Bucket '{bucket_name}' not found")
        return dict(row)


def safe_object_path(bucket_path: str, object_key: str) -> str:
    """Ensure the target path is strictly contained inside the bucket directory (prevent path traversal)."""
    clean_key = object_key.lstrip("/")
    target = os.path.abspath(os.path.join(bucket_path, clean_key))
    if not target.startswith(os.path.abspath(bucket_path)):
        raise HTTPException(status_code=400, detail="Invalid object path traversal attempt")
    return target


@router.get("", response_model=List[ObjectInfo])
async def list_objects(
    bucket_name: str,
    prefix: str = Query(""),
    delimiter: str = Query(""),
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    if not os.path.exists(b_path):
        return []

    prefix_clean = prefix.lstrip("/")
    target_dir = os.path.abspath(os.path.join(b_path, prefix_clean))
    if not target_dir.startswith(os.path.abspath(b_path)):
        raise HTTPException(status_code=400, detail="Invalid prefix")

    if not os.path.exists(target_dir):
        return []

    items = []
    try:
        if delimiter == "/":
            with os.scandir(target_dir) as entries:
                for entry in entries:
                    rel_path = os.path.relpath(entry.path, b_path).replace("\\", "/")
                    if entry.is_dir():
                        items.append(ObjectInfo(
                            key=rel_path + "/",
                            size_bytes=0,
                            size_formatted="--",
                            last_modified="--",
                            content_type="directory",
                            is_dir=True
                        ))
                    elif entry.is_file():
                        stat = entry.stat()
                        mtime = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stat.st_mtime))
                        ctype, _ = mimetypes.guess_type(entry.name)
                        items.append(ObjectInfo(
                            key=rel_path,
                            size_bytes=stat.st_size,
                            size_formatted=format_size(stat.st_size),
                            last_modified=mtime,
                            content_type=ctype or "application/octet-stream",
                            is_dir=False
                        ))
        else:
            for root, dirs, files in os.walk(target_dir):
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, b_path).replace("\\", "/")
                    if prefix_clean and not rel_p.startswith(prefix_clean):
                        continue
                    stat = os.stat(full_p)
                    mtime = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stat.st_mtime))
                    ctype, _ = mimetypes.guess_type(f)
                    items.append(ObjectInfo(
                        key=rel_p,
                        size_bytes=stat.st_size,
                        size_formatted=format_size(stat.st_size),
                        last_modified=mtime,
                        content_type=ctype or "application/octet-stream",
                        is_dir=False
                    ))
    except Exception as e:
        logger.error(f"Failed to scan object list for {bucket_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list bucket contents")

    return items


@router.post("/upload")
async def upload_object(
    bucket_name: str,
    key: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    object_key = (key or file.filename or "file").lstrip("/")
    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    # Check Quota before upload
    current_used, _ = get_dir_stats(b_path)
    quota_bytes = bucket["quota_mb"] * 1024 * 1024
    if current_used >= quota_bytes:
        raise HTTPException(status_code=413, detail=f"Bucket quota of {bucket['quota_mb']} MB exceeded.")

    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    bytes_written = 0
    with open(target_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            bytes_written += len(chunk)
            if (current_used + bytes_written) > quota_bytes:
                f.close()
                if os.path.exists(target_path):
                    os.remove(target_path)
                raise HTTPException(status_code=413, detail="File upload exceeds bucket quota limit.")
            f.write(chunk)

    log_action(current_user.username, "storage.object_upload", f"{bucket_name}/{object_key}", f"size={bytes_written}")

    return {
        "bucket": bucket_name,
        "key": object_key,
        "size_bytes": bytes_written,
        "size_formatted": format_size(bytes_written),
        "status": "success"
    }


@router.get("/download/{object_key:path}")
async def download_object(bucket_name: str, object_key: str, current_user: User = Depends(get_current_user)):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path) or os.path.isdir(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    ctype, _ = mimetypes.guess_type(target_path)
    filename = os.path.basename(target_path)
    return FileResponse(
        target_path,
        media_type=ctype or "application/octet-stream",
        filename=filename
    )


@router.delete("/{object_key:path}")
async def delete_object(bucket_name: str, object_key: str, current_user: User = Depends(get_current_user)):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    if os.path.isdir(target_path):
        shutil.rmtree(target_path)
    else:
        os.remove(target_path)

    log_action(current_user.username, "storage.object_delete", f"{bucket_name}/{object_key}")
    return {"message": f"Object '{object_key}' deleted from '{bucket_name}'"}


# ── Presigned URLs & Public Links ─────────────────────────────────────────────

PRESIGN_SECRET = "hp-s3-presign-secret-token-key-2026"


def create_presigned_token(bucket_name: str, object_key: str, expires_at: int) -> str:
    msg = f"{bucket_name}:{object_key}:{expires_at}".encode('utf-8')
    return hmac.new(PRESIGN_SECRET.encode('utf-8'), msg, hashlib.sha256).hexdigest()


def verify_presigned_token(bucket_name: str, object_key: str, expires_at: int, token: str) -> bool:
    if time.time() > expires_at:
        return False
    expected = create_presigned_token(bucket_name, object_key, expires_at)
    return hmac.compare_digest(expected, token)


@router.post("/presign", response_model=PresignResponse)
async def generate_presigned_url(
    bucket_name: str,
    payload: PresignRequest,
    req: Request,
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    expires_at = int(time.time()) + max(10, min(86400, payload.expires_in))
    clean_key = payload.object_key.lstrip("/")
    token = create_presigned_token(bucket_name, clean_key, expires_at)

    base_url = str(req.base_url).rstrip("/")
    presigned_url = f"{base_url}/cpanelapi/storage/buckets/{bucket_name}/objects/presigned/{clean_key}?expires={expires_at}&token={token}"

    return PresignResponse(
        url=presigned_url,
        expires_at=expires_at,
        object_key=clean_key
    )


@router.get("/presigned/{object_key:path}")
async def access_presigned_object(
    bucket_name: str,
    object_key: str,
    expires: int = Query(...),
    token: str = Query(...)
):
    if not verify_presigned_token(bucket_name, object_key, expires, token):
        raise HTTPException(status_code=403, detail="Presigned URL has expired or token signature is invalid")

    bucket = get_bucket_record(bucket_name)
    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path) or os.path.isdir(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    ctype, _ = mimetypes.guess_type(target_path)
    return FileResponse(target_path, media_type=ctype or "application/octet-stream")


@router.get("/public/{object_key:path}")
async def access_public_object(bucket_name: str, object_key: str):
    bucket = get_bucket_record(bucket_name)
    if not bucket["public_access"]:
        raise HTTPException(status_code=403, detail="This bucket is private")

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path) or os.path.isdir(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    ctype, _ = mimetypes.guess_type(target_path)
    return FileResponse(target_path, media_type=ctype or "application/octet-stream")
