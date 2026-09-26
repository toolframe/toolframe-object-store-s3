"""Private S3 objects partitioned by application and owning plugin."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from toolframe_sdk import AuthorizationSpec, PluginDefinition, PluginError
from toolframe_sdk.object_store import (
    MAX_OBJECT_BYTES,
    OBJECT_STORE_SERVICE,
    object_store_service,
)
from toolframe_sdk.object_store_provider import ObjectStoreProvider, check_key


class S3ObjectStore(ObjectStoreProvider):
    def open_backend(self):
        config = self.context.config
        endpoint = config.get("endpoint_url")
        if endpoint:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise PluginError(
                    "invalid_endpoint",
                    "S3 endpoint must be an HTTP(S) origin without credentials",
                )
        self.prefix = config.get("prefix", "toolframe").strip("/")
        check_key(self.prefix)
        self.bucket = config["bucket"]
        # Credentials come from the AWS chain, never serializable plugin config.
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=config.get("region", "us-east-1"),
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": config.get("addressing_style", "path")},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except BotoCoreError, ClientError:
            self.client.close()
            raise PluginError(
                "object_store_unavailable", "Cannot access the configured S3 bucket"
            ) from None

    def close_backend(self):
        if hasattr(self, "client"):
            self.client.close()

    def key(self, namespace, key=""):
        return f"{self.prefix}/{namespace}/{key}"

    def request(self, method, **params):
        try:
            return getattr(self.client, method)(Bucket=self.bucket, **params)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "NotFound", "404"}:
                raise PluginError("object_missing", "Object does not exist") from None
            if code in {"PreconditionFailed", "412"}:
                raise PluginError(
                    "object_changed", "Object changed; read its metadata again"
                ) from None
            raise PluginError("object_store_unavailable", "S3 request failed") from None

    def put(self, namespace, metadata, stream):
        self.request(
            "put_object",
            Key=self.key(namespace, metadata["key"]),
            Body=stream,
            ContentLength=metadata["size"],
            ContentType=metadata["content_type"],
            Metadata={
                "sha256": metadata["etag"],
                "modified-at": str(metadata["modified_at"]),
            },
        )

    def metadata(self, key, response):
        etag = response.get("Metadata", {}).get("sha256", "")
        if not re.fullmatch(r"[a-f0-9]{64}", etag):
            raise PluginError(
                "object_corrupt", "Object is missing Toolframe checksum metadata"
            )
        return {
            "key": key,
            "size": response["ContentLength"],
            "content_type": response.get("ContentType", "application/octet-stream"),
            "etag": etag,
            "modified_at": float(response["Metadata"]["modified-at"]),
        }

    def head(self, namespace, key):
        return self.metadata(
            key, self.request("head_object", Key=self.key(namespace, key))
        )

    def read(self, namespace, key, offset, length, etag):
        response = self.request("head_object", Key=self.key(namespace, key))
        metadata = self.metadata(key, response)
        if metadata["etag"] != etag:
            raise PluginError(
                "object_changed", "Object changed; read its metadata again"
            )
        if offset > metadata["size"]:
            raise PluginError("invalid_range", "Offset exceeds the object size")
        if offset == metadata["size"]:
            return b""
        result = self.request(
            "get_object",
            Key=self.key(namespace, key),
            IfMatch=response["ETag"],
            Range=f"bytes={offset}-{min(offset + length, metadata['size']) - 1}",
        )
        try:
            return result["Body"].read(length)
        finally:
            result["Body"].close()

    def list(self, namespace, prefix, after, limit):
        base = self.key(namespace)
        result = self.request(
            "list_objects_v2",
            Prefix=base + prefix,
            StartAfter=base + after,
            MaxKeys=limit + 1,
        )
        entries = result.get("Contents", [])
        items = [
            self.head(namespace, item["Key"][len(base) :]) for item in entries[:limit]
        ]
        more = len(entries) > limit or result.get("IsTruncated", False)
        return {
            "objects": items,
            "next_after": items[-1]["key"] if more and items else None,
        }

    def delete(self, namespace, key):
        self.request("delete_object", Key=self.key(namespace, key))


plugin = PluginDefinition(
    id="object-store-s3",
    kind="provider",
    provider_kind="object-store",
    factory="toolframe_object_store_s3.plugin:S3ObjectStore",
    authorization=AuthorizationSpec(),
    runtime_validation="separate_application",
    config_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["bucket"],
        "properties": {
            "bucket": {"type": "string", "minLength": 3, "maxLength": 63},
            "endpoint_url": {"type": "string", "maxLength": 2048},
            "region": {"type": "string", "minLength": 1, "maxLength": 64},
            "prefix": {"type": "string", "minLength": 1, "maxLength": 128},
            "addressing_style": {"enum": ["path", "virtual", "auto"]},
            "max_object_bytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_OBJECT_BYTES,
            },
        },
    },
    services={OBJECT_STORE_SERVICE: object_store_service()},
)
