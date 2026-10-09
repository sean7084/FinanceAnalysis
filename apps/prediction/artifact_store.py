"""Storage backends for trained-model artifacts.

Model artifacts (LightGBM pickles, LSTM ``.pt`` weights, ensemble JSON) used to
live in the repository under ``models/`` as plain git blobs, so every retrain grew
``.git`` permanently and a fresh clone dragged the whole history of weights. This
module puts a thin object-store abstraction in front of that I/O so artifacts can
live in an S3-compatible store (MinIO on the NAS) while a local on-disk cache keeps
inference fast and the default ``local`` backend behaves exactly as before.

Two backends, selected by ``settings.ARTIFACT_STORE_BACKEND``:

``local``
    The cache directory *is* the store (``BASE_DIR``). ``upload_dir`` /
    ``ensure_dir_local`` are no-ops, so the historical filesystem behaviour is
    preserved bit for bit. This is the default and what CI and tests use.

``s3``
    Objects live in an S3/MinIO bucket, mirrored to a local cache under
    ``BASE_DIR``. Saving writes the cache then uploads; loading downloads to the
    cache on a miss, then reads locally. ``boto3`` is imported lazily so the local
    backend never needs it installed.

Object keys are the ``BASE_DIR``-relative paths already stored in the registry
(``models/lightgbm/3d_lgb-3d-2020-01-01/model.pkl``), so ``artifact_path`` stays
portable and doubles as the key. That is what closes the "stored artifact paths are
not portable" backlog item: a key resolves the same on any host.

The seams in ``tasks_lightgbm.py`` / ``tasks_lstm.py`` keep writing and reading
local files (the cache); they only gain an ``upload_dir`` after a save and an
``ensure_dir_local`` before a load. Under the local backend both are no-ops.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

__all__ = [
    "ArtifactStore",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "get_artifact_store",
    "reset_artifact_store",
    "to_store_key",
]


def to_store_key(path, root=None):
    """Normalise a local path (or stored artifact_path) to a forward-slash store key.

    A path under ``root`` (default ``BASE_DIR``) becomes its relative key, e.g.
    ``/home/x/proj/models/lstm/v1/3d_model.pt`` -> ``models/lstm/v1/3d_model.pt``.
    A value that is already relative is returned with normalised separators. Legacy
    absolute paths from *other* hosts cannot be mapped and are returned unchanged,
    so callers can detect and skip them.
    """
    if not path:
        return path
    root_str = str(root or settings.BASE_DIR)
    path_str = str(path)
    if path_str.startswith(root_str):
        relative = path_str[len(root_str):].lstrip(os.sep).lstrip("/")
        return relative.replace(os.sep, "/")
    return path_str.replace(os.sep, "/")


class ArtifactStore:
    """Interface shared by the backends."""

    #: True when the canonical copy lives off-box (so save/load must sync).
    is_remote = False

    def put_bytes(self, key, data):
        raise NotImplementedError

    def get_bytes(self, key):
        raise NotImplementedError

    def exists(self, key):
        raise NotImplementedError

    def list_keys(self, prefix):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def upload_dir(self, local_dir, key_prefix):
        """Persist every file under ``local_dir`` to the store under ``key_prefix``."""
        raise NotImplementedError

    def ensure_dir_local(self, key_prefix, local_dir):
        """Make sure every object under ``key_prefix`` exists in ``local_dir``."""
        raise NotImplementedError


class LocalArtifactStore(ArtifactStore):
    """Filesystem backend: the cache directory *is* the store. No network I/O.

    ``upload_dir`` / ``ensure_dir_local`` are no-ops, which is what makes the
    default backend behaviourally identical to the pre-abstraction code.
    """

    is_remote = False

    def __init__(self, root=None):
        self.root = str(root or settings.BASE_DIR)

    def _abs(self, key):
        return os.path.join(self.root, str(key).replace("/", os.sep))

    def put_bytes(self, key, data):
        path = self._abs(key)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if isinstance(data, (bytes, bytearray)):
            with open(path, "wb") as handle:
                handle.write(data)
        else:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(data)

    def get_bytes(self, key):
        with open(self._abs(key), "rb") as handle:
            return handle.read()

    def exists(self, key):
        return os.path.exists(self._abs(key))

    def list_keys(self, prefix):
        base = self._abs(prefix)
        if not os.path.isdir(base):
            return []
        keys = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                keys.append(to_store_key(os.path.join(dirpath, name), self.root))
        return sorted(keys)

    def delete(self, key):
        path = self._abs(key)
        if os.path.isfile(path):
            os.remove(path)

    def upload_dir(self, local_dir, key_prefix):
        return 0

    def ensure_dir_local(self, key_prefix, local_dir):
        return 0


class S3ArtifactStore(ArtifactStore):
    """S3/MinIO backend with a local cache mirror rooted at ``BASE_DIR``.

    Objects are keyed by the ``BASE_DIR``-relative path, and the local cache uses
    the same layout, so a warm cache means inference never touches the network.
    """

    is_remote = True

    def __init__(
        self,
        bucket,
        endpoint_url=None,
        access_key_id=None,
        secret_access_key=None,
        region=None,
        cache_root=None,
        addressing_style="path",
    ):
        self.bucket = bucket
        self.endpoint_url = endpoint_url or None
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region or None
        self.cache_root = str(cache_root or settings.BASE_DIR)
        self.addressing_style = addressing_style
        self._client = None

    # -- client -------------------------------------------------------------
    def client(self):
        if self._client is None:
            import boto3  # lazy: the local backend never imports boto3
            from botocore.config import Config

            config = Config(
                signature_version="s3v4",
                s3={"addressing_style": self.addressing_style},
                retries={"max_attempts": 3, "mode": "standard"},
            )
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region,
                config=config,
            )
        return self._client

    def _cache_path(self, key):
        return os.path.join(self.cache_root, str(key).replace("/", os.sep))

    # -- single-object ops --------------------------------------------------
    def put_bytes(self, key, data):
        body = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
        self.client().put_object(Bucket=self.bucket, Key=str(key), Body=body)

    def get_bytes(self, key):
        response = self.client().get_object(Bucket=self.bucket, Key=str(key))
        return response["Body"].read()

    def exists(self, key):
        from botocore.exceptions import ClientError

        try:
            self.client().head_object(Bucket=self.bucket, Key=str(key))
            return True
        except ClientError as exc:
            code = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if code == 404:
                return False
            raise

    def list_keys(self, prefix):
        keys = []
        paginator = self.client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=str(prefix)):
            for item in page.get("Contents", []) or []:
                keys.append(item["Key"])
        return sorted(keys)

    def delete(self, key):
        self.client().delete_object(Bucket=self.bucket, Key=str(key))

    # -- directory mirror ops ----------------------------------------------
    def upload_dir(self, local_dir, key_prefix):
        """Upload every file under ``local_dir`` to ``key_prefix`` (idempotent)."""
        local_dir = str(local_dir)
        if not os.path.isdir(local_dir):
            return 0
        key_prefix = str(key_prefix).rstrip("/")
        uploaded = 0
        for dirpath, _dirnames, filenames in os.walk(local_dir):
            for name in filenames:
                abs_path = os.path.join(dirpath, name)
                relative = os.path.relpath(abs_path, local_dir).replace(os.sep, "/")
                key = f"{key_prefix}/{relative}" if key_prefix else relative
                with open(abs_path, "rb") as handle:
                    self.client().put_object(Bucket=self.bucket, Key=key, Body=handle.read())
                uploaded += 1
        return uploaded

    def ensure_dir_local(self, key_prefix, local_dir):
        """Download objects under ``key_prefix`` that are missing from ``local_dir``.

        A warm cache short-circuits the network entirely. If the store is
        unreachable and a needed key is absent, the underlying boto3 error
        propagates so the caller fails loudly rather than silently loading nothing.
        """
        local_dir = str(local_dir)
        key_prefix = str(key_prefix).rstrip("/")
        downloaded = 0
        for key in self.list_keys(key_prefix):
            relative = key[len(key_prefix):].lstrip("/") if key.startswith(key_prefix) else key
            if not relative:
                continue
            target = os.path.join(local_dir, relative.replace("/", os.sep))
            if os.path.exists(target):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            self.client().download_file(self.bucket, key, target)
            downloaded += 1
        return downloaded


_STORE = None


def get_artifact_store():
    """Return the configured backend, constructing it once per process."""
    global _STORE
    if _STORE is not None:
        return _STORE

    backend = str(getattr(settings, "ARTIFACT_STORE_BACKEND", "local")).lower()
    cache_root = getattr(settings, "ARTIFACT_LOCAL_CACHE_ROOT", None) or settings.BASE_DIR
    if backend == "s3":
        _STORE = S3ArtifactStore(
            bucket=settings.ARTIFACT_S3_BUCKET,
            endpoint_url=getattr(settings, "ARTIFACT_S3_ENDPOINT_URL", None),
            access_key_id=getattr(settings, "ARTIFACT_S3_ACCESS_KEY_ID", None),
            secret_access_key=getattr(settings, "ARTIFACT_S3_SECRET_ACCESS_KEY", None),
            region=getattr(settings, "ARTIFACT_S3_REGION", None),
            cache_root=cache_root,
            addressing_style=getattr(settings, "ARTIFACT_S3_ADDRESSING_STYLE", "path"),
        )
    else:
        _STORE = LocalArtifactStore(root=cache_root)
    return _STORE


def reset_artifact_store():
    """Drop the cached store (used by tests that flip ARTIFACT_STORE_BACKEND)."""
    global _STORE
    _STORE = None
