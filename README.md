# Toolframe S3 object storage

`object-store-s3` keeps private Toolframe files in an existing S3-compatible bucket.
It implements `toolframe.object_store` v1, matching the local provider's SDK client
and Dashboard image workflow. RustFS is the local integration target.

## Quick start

Use Toolframe Core 0.7.0 and SDK 0.4.0 with object-storage support.
Create a private bucket first. Supply credentials through the standard AWS chain
in the environment that starts Core (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
optional `AWS_SESSION_TOKEN`, or a supported AWS profile/role).

With the plugin installed but disabled, set its configuration in `toolframe.toml`:

```toml
[plugins.object-store-s3]
source = "pypi:toolframe-object-store-s3==0.2.0"
enabled = false

[plugins.object-store-s3.config]
bucket = "toolframe-images"
endpoint_url = "http://127.0.0.1:9000"
region = "us-east-1"
prefix = "toolframe"
addressing_style = "path"
max_object_bytes = 67108864
```

Core downloads the pinned package from PyPI when applying this configuration.
Omit `endpoint_url` for AWS S3. TLS verification stays enabled for HTTPS endpoints.
Use `addressing_style = "virtual"` when required by your service. Do not put access
keys, secrets or URL credentials in plugin configuration.

```sh
toolframe-core --home APP plugin enable object-store-s3
```

Disable the local object-store provider before enabling S3; each application
selects one provider. Existing local files do not migrate automatically.
Business code continues to use `self.context.object_store()` unchanged.

## Storage and permissions

Objects live under `<prefix>/<sha256(application-id)>/<plugin-id>/<key>`. The
provider only accesses the existing bucket; it never creates buckets, changes
ACLs or enables public access. Grant ListBucket on the configured prefix and
GetObject, PutObject and DeleteObject for its objects. Keep application IDs and
prefixes stable across restarts. Use bucket backup/versioning policies for recovery.

Core inherits the AWS environment into trusted workers. Use an application-scoped
credential/profile; this is not a credential sandbox between untrusted plugins.
`head_bucket` checks access during activation. Service errors do not return SDK
exception text, credentials or endpoints to callers.

Uploads stage in bounded local temporary files before one atomic `PutObject`.
Content type and SHA-256 metadata are retained. Range reads use S3 conditional
requests to reject concurrent replacement. The common limit is 64 MiB per object,
256 KiB per wire chunk, 32 unfinished uploads and 256 MiB staging per worker;
`max_object_bytes` may lower the object limit. Sessions expire after ten minutes.
Completed data survives plugin removal; unfinished uploads do not survive restart.
There is no cross-resource transaction, durable upload resumption, presigned URL,
public download endpoint or automatic migration in this version.

## Test with RustFS

Follow the official [RustFS container instructions](https://docs.rustfs.com/en/installation/container).
Use a dedicated test bucket and credentials. Example local-only container:

```sh
# rustfs.env contains private RUSTFS_ACCESS_KEY and RUSTFS_SECRET_KEY values.
docker run -d --name toolframe-rustfs --env-file ./rustfs.env \
  -p 127.0.0.1:9000:9000 -p 127.0.0.1:9001:9001 \
  -v toolframe-rustfs-data:/data -v toolframe-rustfs-logs:/logs \
  rustfs/rustfs:latest
```

For sibling development checkouts with the matching SDK:

```sh
uv sync --group dev
uv run pytest -q                     # stub tests; live test explicitly skips
export TOOLFRAME_TEST_S3_ENDPOINT=http://127.0.0.1:9000
export TOOLFRAME_TEST_S3_BUCKET=toolframe-test
# Set AWS credentials separately, then create this dedicated bucket once.
uv run pytest -q                     # includes the live integration test
uv run ruff check src tests
uv build --no-sources
```

The integration test uses a unique prefix and removes only the objects it created.
It checks a >1 MiB upload, exact download, empty files, restart, application
isolation, replacement detection, pagination and deletion. Record the container
image digest with acceptance evidence. Runtime validation requires a separate
application because a scratch home does not isolate the remote bucket.
AWS S3 and other compatible services require their own acceptance run; RustFS
coverage does not establish compatibility with every S3 extension.

Licensed under Apache-2.0; see [LICENSE](LICENSE).

For release validation and Trusted Publisher setup, see [releasing](docs/releasing.md).

For development hooks and contribution rules, see [Contributing](CONTRIBUTING.md).

Plugin dependencies allow compatible SDK 0.x updates from the declared minimum.
See the [SDK compatibility policy](https://github.com/toolframe/toolframe-sdk/blob/main/docs/compatibility.md) for API guarantees and minimum/latest verification.
