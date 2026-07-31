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
            seen_dirs = set()
            with os.scandir(target_dir) as entries:
                for entry in entries:
                    rel_path = os.path.relpath(entry.path, b_path).replace("\\", "/")
                    if entry.is_dir():
                        dir_key = rel_path + "/"
                        if dir_key not in seen_dirs:
                            seen_dirs.add(dir_key)
                            items.append(ObjectInfo(
                                key=dir_key,
                                size_bytes=0,
                                size_formatted="0 B",
                                last_modified=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(entry.stat().st_mtime)),
                                content_type="directory",
                                is_dir=True
                            ))
                    else:
                        stat = entry.stat()
                        content_type, _ = mimetypes.guess_type(entry.name)
                        items.append(ObjectInfo(
                            key=rel_path,
                            size_bytes=stat.st_size,
                            size_formatted=format_size(stat.st_size),
                            last_modified=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stat.st_mtime)),
                            content_type=content_type or "application/octet-stream",
                            is_dir=False
                        ))
        else:
            for root, dirs, files in os.walk(target_dir):
                for file_name in files:
                    full_path = os.path.join(root, file_name)
                    rel_path = os.path.relpath(full_path, b_path).replace("\\", "/")
                    stat = os.stat(full_path)
                    content_type, _ = mimetypes.guess_type(file_name)
                    items.append(ObjectInfo(
                        key=rel_path,
                        size_bytes=stat.st_size,
                        size_formatted=format_size(stat.st_size),
                        last_modified=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stat.st_mtime)),
                        content_type=content_type or "application/octet-stream",
                        is_dir=False
                    ))
    except Exception as e:
        logger.error(f"Error scanning objects for bucket {bucket_name}: {e}")

    return items


@router.post("/upload")
async def upload_object(
    bucket_name: str,
    key: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    try:
        bucket = get_bucket_record(bucket_name)
        validate_bucket_ownership(current_user, bucket)

        raw_name = (key or file.filename or "file").strip()
        object_key = raw_name.lstrip("/") if raw_name else "file"
        b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
        target_path = safe_object_path(b_path, object_key)

        current_used, _ = get_dir_stats(b_path)
        quota_bytes = bucket["quota_mb"] * 1024 * 1024
        if current_used >= quota_bytes:
            raise HTTPException(status_code=413, detail=f"Bucket quota of {bucket['quota_mb']} MB exceeded.")

        try:
            os.makedirs(os.path.dirname(target_path), mode=0o755, exist_ok=True)
        except PermissionError as pe:
            logger.error(f"Permission denied creating target directory for {target_path}: {pe}")
            raise HTTPException(status_code=500, detail=f"Permission denied creating target path: {os.path.dirname(target_path)}")

        bytes_written = 0
        try:
            with open(target_path, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    bytes_written += len(chunk)
                    if (current_used + bytes_written) > quota_bytes:
                        f.close()
                        if os.path.exists(target_path):
                            os.remove(target_path)
                        raise HTTPException(status_code=413, detail="File upload exceeds bucket quota limit.")
                    f.write(chunk)
        except PermissionError as pe:
            logger.error(f"Permission denied opening file {target_path}: {pe}")
            raise HTTPException(status_code=500, detail=f"Permission denied writing to file: {target_path}")
        except HTTPException:
            raise
        except Exception as fe:
            logger.error(f"File writing error for {target_path}: {fe}")
            raise HTTPException(status_code=500, detail=f"Failed to write file to disk: {str(fe)}")

        try:
            log_action(current_user.username, "storage.object_upload", f"{bucket_name}/{object_key}", f"size={bytes_written}")
        except Exception as le:
            logger.warning(f"Audit log action failed for upload: {le}")

        return {
            "bucket": bucket_name,
            "key": object_key,
            "size_bytes": bytes_written,
            "size_formatted": format_size(bytes_written),
            "status": "success"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected upload exception: {e}")
        raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")


@router.get("/download/{object_key:path}")
async def download_object(
    bucket_name: str,
    object_key: str,
    token: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path) or os.path.isdir(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    content_type, _ = mimetypes.guess_type(target_path)
    filename = os.path.basename(target_path)

    log_action(current_user.username, "storage.object_download", f"{bucket_name}/{object_key}")
    return FileResponse(
        path=target_path,
        filename=filename,
        media_type=content_type or "application/octet-stream"
    )


@router.delete("/{object_key:path}")
async def delete_object(
    bucket_name: str,
    object_key: str,
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    try:
        if os.path.isdir(target_path):
            os.rmdir(target_path)
        else:
            os.remove(target_path)
    except Exception as e:
        logger.error(f"Failed to delete object {target_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete object: {e}")

    log_action(current_user.username, "storage.object_delete", f"{bucket_name}/{object_key}")
    return {"message": f"Object '{object_key}' deleted from bucket '{bucket_name}'"}


@router.post("/presign", response_model=PresignResponse)
async def create_presigned_url(
    bucket_name: str,
    request: PresignRequest,
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    safe_object_path(b_path, request.object_key)

    expires_at = int(time.time()) + request.expires_in
    message = f"{bucket_name}:{request.object_key}:{expires_at}:{request.method.upper()}"
    secret_key = os.environ.get("JWT_SECRET", "hostpanel-storage-secret-key-change-me")
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    url = f"/cpanelapi/storage/buckets/{bucket_name}/objects/public/{request.object_key}?expires={expires_at}&sig={signature}"
    return PresignResponse(url=url, expires_at=expires_at, object_key=request.object_key)


@router.get("/public/{object_key:path}")
async def serve_public_object(
    bucket_name: str,
    object_key: str,
    expires: Optional[int] = Query(None),
    sig: Optional[str] = Query(None)
):
    bucket = get_bucket_record(bucket_name)
    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path) or os.path.isdir(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    # Access check: 1. Presigned signature URL OR 2. Public Bucket
    if expires and sig:
        if time.time() > expires:
            raise HTTPException(status_code=403, detail="Presigned URL has expired")
        message = f"{bucket_name}:{object_key}:{expires}:GET"
        secret_key = os.environ.get("JWT_SECRET", "hostpanel-storage-secret-key-change-me")
        expected_sig = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise HTTPException(status_code=403, detail="Invalid presigned URL signature")
    elif not bucket["public_access"]:
        raise HTTPException(status_code=403, detail="Bucket access is private. Authentication required.")

    content_type, _ = mimetypes.guess_type(target_path)
    return FileResponse(path=target_path, media_type=content_type or "application/octet-stream")
