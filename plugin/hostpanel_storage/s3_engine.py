import os
import hmac
import hashlib
import time
import mimetypes
import logging
import xml.etree.ElementTree as ET
from typing import Optional
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import FileResponse, Response

from hostpanel_storage.settings import get_bucket_path, get_data_path, ensure_data_dir
from hostpanel_storage.buckets import get_dir_stats

logger = logging.getLogger(__name__)

# Public S3 REST API router mounted without panel user session dependency
public_s3_router = APIRouter(prefix="", tags=["S3 Protocol API"])


def xml_response(content: str, status_code: int = 200) -> Response:
    xml_header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    return Response(content=xml_header + content, status_code=status_code, media_type="application/xml")


def authenticate_s3_request(req: Request) -> Optional[dict]:
    """
    Authenticate incoming AWS S3 API requests via Authorization header or Query params.
    Supports AWS SigV4, SigV2, and Basic Auth.
    """
    auth_header = req.headers.get("Authorization", "")

    # Extract Access Key ID from Authorization header
    access_key = None
    if "Credential=" in auth_header:
        # SigV4 format: AWS4-HMAC-SHA256 Credential=ACCESS_KEY/20260729/region/s3/aws4_request, ...
        cred_part = auth_header.split("Credential=", 1)[1].split(",", 1)[0]
        access_key = cred_part.split("/", 1)[0]
    elif auth_header.startswith("AWS "):
        # SigV2 format: AWS ACCESS_KEY:SIGNATURE
        access_key = auth_header.split(" ", 1)[1].split(":", 1)[0]
    elif "AWSAccessKeyId=" in req.url.query:
        access_key = req.query_params.get("AWSAccessKeyId")

    if not access_key:
        return None

    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            """SELECT k.*, b.name as bound_bucket_name
               FROM storage_access_keys k
               LEFT JOIN storage_buckets b ON k.bucket_id = b.id
               WHERE k.access_key = ? AND k.status = 'active'""",
            (access_key,)
        ).fetchone()
        return dict(row) if row else None


@public_s3_router.get("")
@public_s3_router.get("/")
async def s3_list_buckets(req: Request):
    key_info = authenticate_s3_request(req)
    if not key_info:
        return xml_response(
            "<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>",
            status_code=403
        )

    owner = key_info["owner"]
    from db import get_conn
    with get_conn() as conn:
        if key_info.get("bucket_id"):
            rows = conn.execute("SELECT * FROM storage_buckets WHERE id = ?", (key_info["bucket_id"],)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM storage_buckets WHERE owner = ?", (owner,)).fetchall()

    buckets_xml = ""
    for r in rows:
        mtime = r["created_at"]
        buckets_xml += f"""
        <Bucket>
            <Name>{r['name']}</Name>
            <CreationDate>{mtime}</CreationDate>
        </Bucket>"""

    content = f"""<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
    <Owner>
        <ID>{owner}</ID>
        <DisplayName>{owner}</DisplayName>
    </Owner>
    <Buckets>{buckets_xml}
    </Buckets>
</ListAllMyBucketsResult>"""
    return xml_response(content)


@public_s3_router.get("/{bucket_name}")
async def s3_list_objects(bucket_name: str, req: Request, prefix: str = "", delimiter: str = "", max_keys: int = 1000):
    key_info = authenticate_s3_request(req)
    from db import get_conn
    with get_conn() as conn:
        b_row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (bucket_name,)).fetchone()

    if not b_row:
        return xml_response(
            "<Error><Code>NoSuchBucket</Code><Message>The specified bucket does not exist</Message></Error>",
            status_code=404
        )

    b_dict = dict(b_row)

    # Check access permission: public read OR valid key owner
    if not b_dict["public_access"]:
        if not key_info:
            return xml_response("<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>", status_code=403)
        if key_info["owner"] != b_dict["owner"] and key_info.get("bound_bucket_name") != bucket_name:
            return xml_response("<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>", status_code=403)

    b_path = get_bucket_path(bucket_name, b_dict.get("custom_path"))
    contents_xml = ""
    if os.path.exists(b_path):
        count = 0
        for root, _, files in os.walk(b_path):
            for f in files:
                if count >= max_keys:
                    break
                full_p = os.path.join(root, f)
                rel_p = os.path.relpath(full_p, b_path).replace("\\", "/")
                if prefix and not rel_p.startswith(prefix):
                    continue
                stat = os.stat(full_p)
                mtime = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stat.st_mtime))
                etag = hashlib.md5(f"{rel_p}:{stat.st_mtime}".encode()).hexdigest()
                contents_xml += f"""
        <Contents>
            <Key>{rel_p}</Key>
            <LastModified>{mtime}</LastModified>
            <ETag>"{etag}"</ETag>
            <Size>{stat.st_size}</Size>
            <StorageClass>STANDARD</StorageClass>
        </Contents>"""
                count += 1

    xml_content = f"""<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
    <Name>{bucket_name}</Name>
    <Prefix>{prefix}</Prefix>
    <MaxKeys>{max_keys}</MaxKeys>
    <IsTruncated>false</IsTruncated>{contents_xml}
</ListBucketResult>"""
    return xml_response(xml_content)


@public_s3_router.get("/{bucket_name}/{object_key:path}")
async def s3_get_object(bucket_name: str, object_key: str, req: Request):
    key_info = authenticate_s3_request(req)
    from db import get_conn
    with get_conn() as conn:
        b_row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (bucket_name,)).fetchone()

    if not b_row:
        return xml_response("<Error><Code>NoSuchBucket</Code><Message>NoSuchBucket</Message></Error>", status_code=404)

    b_dict = dict(b_row)
    if not b_dict["public_access"]:
        is_valid_presigned = False
        token_param = req.query_params.get("token")
        if token_param:
            with get_conn() as conn:
                p_row = conn.execute("SELECT * FROM storage_presigned_urls WHERE token = ? AND status = 'active'", (token_param,)).fetchone()
                if p_row:
                    exp = p_row["expires_at"]
                    if exp == 0 or time.time() <= exp:
                        is_valid_presigned = True

        if not is_valid_presigned and not key_info:
            return xml_response("<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>", status_code=403)

    b_path = get_bucket_path(bucket_name, b_dict.get("custom_path"))
    clean_key = object_key.lstrip("/")
    target = os.path.abspath(os.path.join(b_path, clean_key))
    if not target.startswith(os.path.abspath(b_path)) or not os.path.exists(target) or os.path.isdir(target):
        return xml_response("<Error><Code>NoSuchKey</Code><Message>The specified key does not exist.</Message></Error>", status_code=404)

    ctype, _ = mimetypes.guess_type(target)
    return FileResponse(target, media_type=ctype or "application/octet-stream")


@public_s3_router.put("/{bucket_name}/{object_key:path}")
async def s3_put_object(bucket_name: str, object_key: str, req: Request):
    key_info = authenticate_s3_request(req)
    if not key_info:
        return xml_response("<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>", status_code=403)

    from db import get_conn
    with get_conn() as conn:
        b_row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (bucket_name,)).fetchone()
        if not b_row:
            return xml_response("<Error><Code>NoSuchBucket</Code><Message>NoSuchBucket</Message></Error>", status_code=404)

    b_dict = dict(b_row)
    b_path = get_bucket_path(bucket_name, b_dict.get("custom_path"))
    clean_key = object_key.lstrip("/")
    target = os.path.abspath(os.path.join(b_path, clean_key))

    if not target.startswith(os.path.abspath(b_path)):
        return xml_response("<Error><Code>InvalidArgument</Code><Message>Invalid Key</Message></Error>", status_code=400)

    ensure_data_dir(os.path.dirname(target))
    body = await req.body()

    # Check Quota
    current_used, _ = get_dir_stats(b_path)
    if (current_used + len(body)) > (b_dict["quota_mb"] * 1024 * 1024):
        return xml_response("<Error><Code>QuotaExceeded</Code><Message>Bucket Quota Exceeded</Message></Error>", status_code=413)

    with open(target, "wb") as f:
        f.write(body)

    etag = hashlib.md5(body).hexdigest()
    return Response(status_code=200, headers={"ETag": f'"{etag}"'})


@public_s3_router.delete("/{bucket_name}/{object_key:path}")
async def s3_delete_object(bucket_name: str, object_key: str, req: Request):
    key_info = authenticate_s3_request(req)
    if not key_info:
        return xml_response("<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>", status_code=403)

    from db import get_conn
    with get_conn() as conn:
        b_row = conn.execute("SELECT * FROM storage_buckets WHERE name = ?", (bucket_name,)).fetchone()

    if not b_row:
        return xml_response("<Error><Code>NoSuchBucket</Code><Message>NoSuchBucket</Message></Error>", status_code=404)

    b_dict = dict(b_row)
    b_path = get_bucket_path(bucket_name, b_dict.get("custom_path"))
    clean_key = object_key.lstrip("/")
    target = os.path.abspath(os.path.join(b_path, clean_key))

    if os.path.exists(target) and target.startswith(os.path.abspath(b_path)):
        os.remove(target)

    return Response(status_code=204)
