# Test with RustFS

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
