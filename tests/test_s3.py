import io
import os
import time
import uuid
from types import SimpleNamespace

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from toolframe_sdk import PluginError, RuntimeContext, call_context
from toolframe_sdk.object_store import OBJECT_STORE_SERVICE

from toolframe_object_store_s3.plugin import S3ObjectStore


def make_provider():
    provider = S3ObjectStore(
        RuntimeContext(
            None,
            plugin_id="object-store-s3",
            generation="s",
            config={"bucket": "test-bucket"},
        )
    )
    provider.client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    provider.bucket, provider.prefix = "test-bucket", "toolframe"
    return provider


def test_conditional_range_and_stream_close():
    provider = make_provider()
    key = provider.key("app/gallery", "image")
    metadata = {
        "ContentLength": 3,
        "ETag": '"s3-etag"',
        "ContentType": "image/png",
        "Metadata": {"sha256": "a" * 64, "modified-at": "123"},
    }
    body = StreamingBody(io.BytesIO(b"bc"), 2)
    with Stubber(provider.client) as stub:
        stub.add_response(
            "head_object", metadata, {"Bucket": "test-bucket", "Key": key}
        )
        stub.add_response(
            "get_object",
            {"Body": body},
            {
                "Bucket": "test-bucket",
                "Key": key,
                "IfMatch": '"s3-etag"',
                "Range": "bytes=1-2",
            },
        )
        assert provider.read("app/gallery", "image", 1, 100, "a" * 64) == b"bc"
        assert body._raw_stream.closed
        stub.assert_no_pending_responses()
    provider.close_backend()


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("NoSuchKey", "object_missing"),
        ("PreconditionFailed", "object_changed"),
        ("AccessDenied", "object_store_unavailable"),
    ],
)
def test_error_mapping_does_not_disclose_backend_details(remote, expected):
    provider = make_provider()
    with Stubber(provider.client) as stub:
        stub.add_client_error(
            "head_object",
            service_error_code=remote,
            service_message="private-secret-endpoint",
        )
        with pytest.raises(PluginError) as error:
            provider.head("app/gallery", "image")
        assert error.value.code == expected
        assert "private-secret" not in str(error.value)
    provider.close_backend()


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/data",
        "https://secret:password@example.test",
        "https://example.test?token=secret",
    ],
)
def test_endpoint_rejects_embedded_credentials(endpoint):
    provider = S3ObjectStore(
        RuntimeContext(
            None,
            plugin_id="object-store-s3",
            generation="g",
            config={"bucket": "test", "endpoint_url": endpoint},
        )
    )
    with pytest.raises(PluginError) as error:
        provider.open_backend()
    assert error.value.code == "invalid_endpoint"


async def test_real_s3_roundtrip_and_application_isolation():
    endpoint = os.environ.get("TOOLFRAME_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip(
            "Set TOOLFRAME_TEST_S3_ENDPOINT and TOOLFRAME_TEST_S3_BUCKET for RustFS integration"
        )
    config = {
        "endpoint_url": endpoint,
        "bucket": os.environ["TOOLFRAME_TEST_S3_BUCKET"],
        "prefix": "integration-" + uuid.uuid4().hex,
    }
    provider = S3ObjectStore(
        RuntimeContext(None, plugin_id="object-store-s3", generation="s", config=config)
    )
    await provider.activate()

    async def request(method, params, **kwargs):
        return await provider.invoke(
            OBJECT_STORE_SERVICE, params["method"], params["params"]
        )

    client = RuntimeContext(
        SimpleNamespace(request=request), plugin_id="gallery", generation="g"
    ).object_store()
    identity = {
        "application_id": "app",
        "owner": "gallery",
        "owner_generation": "g",
        "root_id": "r",
        "deadline": time.time() + 60,
    }
    token = call_context.set(identity)
    try:
        data = bytes(range(256)) * 7000
        metadata = await client.put("images/a.png", data, content_type="image/png")
        assert await client.get("images/a.png") == data
        await client.put("images/b.png", b"")
        assert await client.get("images/b.png") == b""
        page = await client.list(prefix="images/", limit=1)
        assert page["next_after"] == "images/a.png"
        assert len((await client.list(after=page["next_after"]))["objects"]) == 1
        await provider.dispose()
        await provider.activate()
        assert (await client.head("images/a.png"))["etag"] == metadata["etag"]
        other = call_context.set({**identity, "application_id": "different"})
        try:
            assert not (await client.list())["objects"]
            with pytest.raises(PluginError) as error:
                await client.get("images/a.png")
            assert error.value.code == "object_missing"
        finally:
            call_context.reset(other)
        await client.put("images/a.png", b"new")
        with pytest.raises(PluginError) as error:
            await client.read_chunk("images/a.png", etag=metadata["etag"])
        assert error.value.code == "object_changed"
    finally:
        for key in ("images/a.png", "images/b.png"):
            await client.delete(key)
        call_context.reset(token)
        await provider.dispose()
