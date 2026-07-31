import os
import hmac
import hashlib
import time
import secrets
import mimetypes
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from deps import get_current_user
from auth import User
from modules.audit.logger import log_action
from hostpanel_storage.settings import get_bucket_path, ensure_data_dir, get_storage_setting
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
    expires_in: int = 3600  # 0 means Never Expire


class PresignResponse(BaseModel):
    id: int
    url: str
    token: str
    expires_at: int
    is_never: bool
    object_key: str
    status: str


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
    use_uuid: Optional[bool] = Form(False),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    try:
        bucket = get_bucket_record(bucket_name)
        validate_bucket_ownership(current_user, bucket)

        if use_uuid or key == "auto_uuid":
            import uuid
            ext = os.path.splitext(file.filename or "")[1]
            object_key = f"{uuid.uuid4()}{ext}"
        else:
            raw_name = (key or file.filename or "file").strip()
            object_key = raw_name.lstrip("/") if raw_name else "file"

        b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
        target_path = safe_object_path(b_path, object_key)

        current_used, _ = get_dir_stats(b_path)
        quota_bytes = bucket["quota_mb"] * 1024 * 1024
        if current_used >= quota_bytes:
            raise HTTPException(status_code=413, detail=f"Bucket quota of {bucket['quota_mb']} MB exceeded.")

        target_dir = os.path.dirname(target_path)
        ensure_data_dir(target_dir)

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


@router.get("/presign/active")
async def get_active_presigned_url(
    bucket_name: str,
    object_key: str = Query(...),
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM storage_presigned_urls
               WHERE bucket_name = ? AND object_key = ? AND status = 'active'
               ORDER BY id DESC LIMIT 1""",
            (bucket_name, object_key)
        ).fetchone()

        if not row:
            return None

        record = dict(row)

        exp = record["expires_at"]
        if exp > 0 and time.time() > exp:
            conn.execute("UPDATE storage_presigned_urls SET status = 'expired' WHERE id = ?", (record["id"],))
            return None

        s3_domain = get_storage_setting("s3_domain", "s3.consoleapi.in").strip()
        if s3_domain:
            domain_prefix = s3_domain if (s3_domain.startswith("http://") or s3_domain.startswith("https://")) else f"https://{s3_domain}"
            url = f"{domain_prefix}/{bucket_name}/{object_key}?token={record['token']}"
        else:
            url = f"/cpanelapi/storage/buckets/{bucket_name}/objects/public/{object_key}?token={record['token']}"

        record["url"] = url
        record["is_never"] = (exp == 0)
        return record


@router.post("/presign")
async def create_presigned_url(
    bucket_name: str,
    request: PresignRequest,
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    safe_object_path(b_path, request.object_key)

    expires_at = (int(time.time()) + request.expires_in) if request.expires_in > 0 else 0
    token = f"ps_{secrets.token_hex(16)}"

    from db import get_conn
    with get_conn() as conn:
        # Revoke existing active URLs for this object
        conn.execute(
            "UPDATE storage_presigned_urls SET status = 'revoked' WHERE bucket_name = ? AND object_key = ? AND status = 'active'",
            (bucket_name, request.object_key)
        )
        cur = conn.execute(
            """INSERT INTO storage_presigned_urls (bucket_name, object_key, token, expires_at, status, created_by)
               VALUES (?, ?, ?, ?, 'active', ?)""",
            (bucket_name, request.object_key, token, expires_at, current_user.username)
        )
        row_id = cur.lastrowid

    s3_domain = get_storage_setting("s3_domain", "s3.consoleapi.in").strip()
    if s3_domain:
        domain_prefix = s3_domain if (s3_domain.startswith("http://") or s3_domain.startswith("https://")) else f"https://{s3_domain}"
        url = f"{domain_prefix}/{bucket_name}/{request.object_key}?token={token}"
    else:
        url = f"/cpanelapi/storage/buckets/{bucket_name}/objects/public/{request.object_key}?token={token}"

    log_action(current_user.username, "storage.presign_create", f"{bucket_name}/{request.object_key}", f"id={row_id}")
    return {
        "id": row_id,
        "url": url,
        "token": token,
        "expires_at": expires_at,
        "is_never": (expires_at == 0),
        "object_key": request.object_key,
        "status": "active"
    }


@router.delete("/presign/{presign_id}")
async def revoke_presigned_url(
    bucket_name: str,
    presign_id: int,
    current_user: User = Depends(get_current_user)
):
    bucket = get_bucket_record(bucket_name)
    validate_bucket_ownership(current_user, bucket)

    from db import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE storage_presigned_urls SET status = 'revoked' WHERE id = ? AND bucket_name = ?", (presign_id, bucket_name))

    log_action(current_user.username, "storage.presign_revoke", f"{bucket_name}/presign_id={presign_id}")
    return {"message": "Presigned URL revoked successfully"}


@router.get("/public/{object_key:path}")
async def serve_public_object(
    bucket_name: str,
    object_key: str,
    token: Optional[str] = Query(None),
    expires: Optional[int] = Query(None),
    sig: Optional[str] = Query(None)
):
    bucket = get_bucket_record(bucket_name)
    b_path = get_bucket_path(bucket_name, bucket.get("custom_path"))
    target_path = safe_object_path(b_path, object_key)

    if not os.path.exists(target_path) or os.path.isdir(target_path):
        raise HTTPException(status_code=404, detail="Object not found")

    is_authorized = False

    # Check 1: Token verification from storage_presigned_urls
    if token:
        from db import get_conn
        with get_conn() as conn:
            p_row = conn.execute("SELECT * FROM storage_presigned_urls WHERE token = ? AND status = 'active'", (token,)).fetchone()
            if p_row:
                exp = p_row["expires_at"]
                if exp == 0 or time.time() <= exp:
                    is_authorized = True

    # Check 2: Legacy sig/expires signature check
    if not is_authorized and expires and sig:
        if time.time() <= expires:
            message = f"{bucket_name}:{object_key}:{expires}:GET"
            secret_key = os.environ.get("JWT_SECRET", "hostpanel-storage-secret-key-change-me")
            expected_sig = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected_sig):
                is_authorized = True

    # Check 3: Bucket public access
    if not is_authorized and not bucket["public_access"]:
        raise HTTPException(status_code=403, detail="Access denied. Presigned URL is invalid, revoked, or expired.")

    content_type, _ = mimetypes.guess_type(target_path)
    return FileResponse(path=target_path, media_type=content_type or "application/octet-stream")
