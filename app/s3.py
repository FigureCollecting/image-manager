from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import boto3
from botocore.client import Config

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


def get_s3(settings: Optional[Settings] = None):
    s = settings or get_settings()
    session = boto3.session.Session()
    client = session.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        region_name=s.s3_region,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        config=Config(s3={"addressing_style": "virtual"}, signature_version="s3v4"),
        use_ssl=s.s3_secure,
        verify=s.s3_secure,
    )
    return client


def presign_post_for_upload(key: str, *, content_type: str, size: int, settings: Optional[Settings] = None) -> Dict[str, Any]:
    s = settings or get_settings()
    client = get_s3(s)
    conditions = [
        ["content-length-range", max(1, size // 100), size * 2],  # basic sanity range
        {"Content-Type": content_type},
        {"acl": "private"},
    ]
    fields = {"Content-Type": content_type, "acl": "private"}
    resp = client.generate_presigned_post(
        Bucket=s.s3_bucket,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=s.s3_presign_expiry_post,
    )
    return resp


def presign_get(key: str, *, expires_in: Optional[int] = None, settings: Optional[Settings] = None) -> str:
    s = settings or get_settings()
    client = get_s3(s)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": s.s3_bucket, "Key": key},
        ExpiresIn=expires_in or s.s3_presign_expiry_get,
    )


def move_object(src_key: str, dest_key: str, *, settings: Optional[Settings] = None) -> None:
    s = settings or get_settings()
    client = get_s3(s)
    client.copy({"Bucket": s.s3_bucket, "Key": src_key}, s.s3_bucket, dest_key)
    client.delete_object(Bucket=s.s3_bucket, Key=src_key)

