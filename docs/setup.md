# Configure an S3-compatible bucket

Use Toolframe Core 0.7.0 and SDK 0.4.0 with object-storage support.
Create a private bucket first. Supply credentials through the standard AWS chain
in the environment that starts Core (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
optional `AWS_SESSION_TOKEN`, or a supported AWS profile/role).

Declare the package and its required configuration together in `toolframe.toml`,
initially disabled. An empty configuration fails validation during installation:

```toml
[plugins.object-store-s3]
source = "pypi:toolframe-object-store-s3==0.2.1"
enabled = false

[plugins.object-store-s3.config]
bucket = "toolframe-images"
endpoint_url = "http://127.0.0.1:9000"
region = "us-east-1"
prefix = "toolframe"
addressing_style = "path"
max_object_bytes = 67108864
```

Core resolves the pinned package from PyPI when the provider is enabled.
Omit `endpoint_url` for AWS S3. TLS verification stays enabled for HTTPS endpoints.
Use `addressing_style = "virtual"` when required by your service. Do not put access
keys, secrets or URL credentials in plugin configuration.

```sh
toolframe --home APP apply
toolframe --home APP plugin enable object-store-s3
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
