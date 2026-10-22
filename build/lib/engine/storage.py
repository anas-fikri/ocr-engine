"""Adapter S3 untuk SILO (dan S3-compatible lain). Hanya memakai S3 API standar."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote, unquote, urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from engine.settings import Settings, get_settings


class ObjectNotFound(Exception):
    """Objek tidak ada di bucket. Error permanen: tidak di-retry."""


@dataclass
class StoredObject:
    data: bytes
    etag: str | None
    content_type: str | None
    metadata: dict[str, str]  # x-amz-meta-* (key huruf kecil)


@dataclass
class ListedObject:
    key: str
    etag: str | None
    size: int


def _client(s: Settings, endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=s.aws_default_region,
        aws_access_key_id=s.aws_access_key_id or None,
        aws_secret_access_key=s.aws_secret_access_key or None,
        config=Config(
            s3={"addressing_style": "path" if s.aws_use_path_style_endpoint else "auto"},
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


class Storage:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.client = _client(self.s, self.s.aws_endpoint)
        # presigned URL harus ditandatangani dengan host yang diakses browser/pelanggan
        public = self.s.aws_public_endpoint or self.s.aws_endpoint
        self.presign_client = self.client if public == self.s.aws_endpoint else _client(self.s, public)

    def get(self, bucket: str, key: str) -> StoredObject:
        try:
            r = self.client.get_object(Bucket=bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NoSuchBucket"):
                raise ObjectNotFound(f"{bucket}/{key}") from e
            raise
        return StoredObject(
            data=r["Body"].read(),
            etag=(r.get("ETag") or "").strip('"') or None,
            content_type=r.get("ContentType"),
            metadata={k.lower(): unquote(v) for k, v in (r.get("Metadata") or {}).items()},
        )

    def head(self, bucket: str, key: str) -> dict:
        try:
            r = self.client.head_object(Bucket=bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                raise ObjectNotFound(f"{bucket}/{key}") from e
            raise
        return {
            "etag": (r.get("ETag") or "").strip('"') or None,
            "metadata": {k.lower(): unquote(v) for k, v in (r.get("Metadata") or {}).items()},
            "size": r.get("ContentLength"),
        }

    def put(self, bucket: str, key: str, data: bytes, content_type: str, metadata: dict | None = None) -> None:
        meta = {k: quote(str(v), safe="") for k, v in (metadata or {}).items() if v is not None}
        self.client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type, Metadata=meta)

    def delete(self, bucket: str, key: str) -> None:
        self.client.delete_object(Bucket=bucket, Key=key)

    def list(self, bucket: str, prefix: str = "", start_after: str | None = None) -> Iterator[ListedObject]:
        paginator = self.client.get_paginator("list_objects_v2")
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if start_after:
            kwargs["StartAfter"] = start_after
        for page in paginator.paginate(**kwargs):
            for o in page.get("Contents", []) or []:
                yield ListedObject(o["Key"], (o.get("ETag") or "").strip('"') or None, int(o.get("Size", 0)))

    def presign(self, bucket: str | None, key: str | None, ttl: int) -> str | None:
        if not bucket or not key:
            return None
        return self.presign_client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl
        )

    def ensure_bucket(self, bucket: str) -> None:
        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError:
            self.client.create_bucket(Bucket=bucket)

    def ping(self, bucket: str) -> bool:
        self.client.head_bucket(Bucket=bucket)
        return True


@lru_cache
def get_storage() -> Storage:
    return Storage()


def reset_storage_cache() -> None:
    get_storage.cache_clear()


def split_url(url: str, settings: Settings | None = None) -> tuple[str, str] | None:
    """Ubah link foto (https://s3.pcpexpress.com/<bucket>/<key>) menjadi (bucket, key).

    Mendukung path-style dan virtual-host style. Query string (presigned) diabaikan.
    """
    s = settings or get_settings()
    u = urlparse(url)
    path = unquote(u.path.lstrip("/"))
    endpoint_hosts = {urlparse(x).netloc for x in (s.aws_endpoint, s.aws_public_endpoint or "") if x}
    if u.netloc in endpoint_hosts or not u.netloc:
        if "/" not in path:
            return None
        bucket, key = path.split("/", 1)
        return bucket, key
    # virtual-host: <bucket>.<endpoint-host>/<key>
    for host in endpoint_hosts:
        if host and u.netloc.endswith("." + host):
            return u.netloc[: -len(host) - 1], path
    return None
