import secrets
import string
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deps import get_current_user
from auth import User
from modules.audit.logger import log_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cpanelapi/storage/keys", tags=["Storage Keys"])


class AccessKeyInfo(BaseModel):
    id: int
    access_key: str
    secret_key: Optional[str] = None  # Returned only on creation
    owner: str
    label: str
    status: str
    bucket_id: Optional[int] = None
    bucket_name: Optional[str] = None
    created_at: str


class AccessKeyCreateRequest(BaseModel):
    label: str = ""
    bucket_id: Optional[int] = None


class AccessKeyStatusRequest(BaseModel):
    status: str  # 'active' or 'disabled'


def generate_access_key_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "HPK" + "".join(secrets.choice(alphabet) for _ in range(17))


def generate_secret_access_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(40))


@router.get("", response_model=List[AccessKeyInfo])
async def list_access_keys(current_user: User = Depends(get_current_user)):
    from db import get_conn
    with get_conn() as conn:
        if current_user.role == "admin":
            rows = conn.execute("""
                SELECT k.*, b.name as bucket_name
                FROM storage_access_keys k
                LEFT JOIN storage_buckets b ON k.bucket_id = b.id
                ORDER BY k.created_at DESC
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT k.*, b.name as bucket_name
                FROM storage_access_keys k
                LEFT JOIN storage_buckets b ON k.bucket_id = b.id
                WHERE k.owner = ?
                ORDER BY k.created_at DESC
            """, (current_user.username,)).fetchall()

    return [
        AccessKeyInfo(
            id=r["id"],
            access_key=r["access_key"],
            secret_key=None,  # Mask secret key after creation for security
            owner=r["owner"],
            label=r["label"],
            status=r["status"],
            bucket_id=r["bucket_id"],
            bucket_name=r["bucket_name"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("", response_model=AccessKeyInfo)
async def create_access_key(request: AccessKeyCreateRequest, current_user: User = Depends(get_current_user)):
    bucket_name = None
    if request.bucket_id is not None:
        from db import get_conn
        with get_conn() as conn:
            b_row = conn.execute("SELECT * FROM storage_buckets WHERE id = ?", (request.bucket_id,)).fetchone()
            if not b_row:
                raise HTTPException(status_code=404, detail="Bound bucket not found")
            if current_user.role != "admin" and b_row["owner"] != current_user.username:
                raise HTTPException(status_code=403, detail="Cannot bind key to a bucket you do not own")
            bucket_name = b_row["name"]

    access_key = generate_access_key_id()
    secret_key = generate_secret_access_key()
    owner = current_user.username

    from db import get_conn
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO storage_access_keys (access_key, secret_key, owner, label, bucket_id)
               VALUES (?, ?, ?, ?, ?)""",
            (access_key, secret_key, owner, request.label.strip(), request.bucket_id)
        )
        key_id = cursor.lastrowid
        r = conn.execute("SELECT * FROM storage_access_keys WHERE id = ?", (key_id,)).fetchone()

    log_action(current_user.username, "storage.key_create", access_key, f"label={request.label}")

    return AccessKeyInfo(
        id=r["id"],
        access_key=r["access_key"],
        secret_key=secret_key,  # Returned once on creation!
        owner=r["owner"],
        label=r["label"],
        status=r["status"],
        bucket_id=r["bucket_id"],
        bucket_name=bucket_name,
        created_at=r["created_at"],
    )


@router.put("/{access_key}/status", response_model=AccessKeyInfo)
async def update_key_status(access_key: str, payload: AccessKeyStatusRequest, current_user: User = Depends(get_current_user)):
    status = payload.status.lower()
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="Status must be 'active' or 'disabled'")

    from db import get_conn
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM storage_access_keys WHERE access_key = ?", (access_key,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Access key not found")
        if current_user.role != "admin" and r["owner"] != current_user.username:
            raise HTTPException(status_code=403, detail="Permission denied")

        conn.execute("UPDATE storage_access_keys SET status = ? WHERE access_key = ?", (status, access_key))
        updated = conn.execute("SELECT k.*, b.name as bucket_name FROM storage_access_keys k LEFT JOIN storage_buckets b ON k.bucket_id = b.id WHERE k.access_key = ?", (access_key,)).fetchone()

    log_action(current_user.username, "storage.key_update", access_key, f"status={status}")

    return AccessKeyInfo(
        id=updated["id"],
        access_key=updated["access_key"],
        secret_key=None,
        owner=updated["owner"],
        label=updated["label"],
        status=updated["status"],
        bucket_id=updated["bucket_id"],
        bucket_name=updated["bucket_name"],
        created_at=updated["created_at"],
    )


@router.delete("/{access_key}")
async def delete_access_key(access_key: str, current_user: User = Depends(get_current_user)):
    from db import get_conn
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM storage_access_keys WHERE access_key = ?", (access_key,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Access key not found")
        if current_user.role != "admin" and r["owner"] != current_user.username:
            raise HTTPException(status_code=403, detail="Permission denied")

        conn.execute("DELETE FROM storage_access_keys WHERE access_key = ?", (access_key,))

    log_action(current_user.username, "storage.key_delete", access_key)
    return {"message": f"Access key '{access_key}' revoked", "access_key": access_key}
