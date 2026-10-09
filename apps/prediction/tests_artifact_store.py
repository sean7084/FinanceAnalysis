"""Tests for apps/prediction/artifact_store.py.

These exercise the local backend directly and the S3 backend against an in-memory
fake client, so they need no MinIO and no network -- CI runs them like any other
unit test. The point is to lock in two invariants the migration depends on:

* the local backend is a pure filesystem passthrough (dir-mirror ops are no-ops),
  which is what keeps default behaviour byte-identical to the pre-abstraction code;
* the S3 backend uploads a whole family on save and downloads only missing keys on
  a cold cache, so a warm cache never touches the network.
"""
import os
import tempfile

from django.test import SimpleTestCase, override_settings

from apps.prediction.artifact_store import (
    LocalArtifactStore,
    S3ArtifactStore,
    get_artifact_store,
    reset_artifact_store,
    to_store_key,
)


class ToStoreKeyTests(SimpleTestCase):
    def test_absolute_under_root_becomes_relative_key(self):
        self.assertEqual(
            to_store_key('/proj/models/lightgbm/3d_v1/model.pkl', root='/proj'),
            'models/lightgbm/3d_v1/model.pkl',
        )

    def test_relative_path_is_normalised_to_forward_slashes(self):
        self.assertEqual(
            to_store_key(os.path.join('models', 'lstm', 'v1', '3d_model.pt')),
            'models/lstm/v1/3d_model.pt',
        )

    def test_legacy_absolute_from_another_host_is_unchanged(self):
        # Cannot be mapped under root, so it is returned as-is and callers skip it.
        self.assertEqual(
            to_store_key('/home/other/proj/models/x.pkl', root='/proj'),
            '/home/other/proj/models/x.pkl',
        )

    def test_empty_and_none_pass_through(self):
        self.assertEqual(to_store_key(''), '')
        self.assertIsNone(to_store_key(None))


class LocalArtifactStoreTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalArtifactStore(root=self._tmp.name)

    def test_bytes_roundtrip(self):
        self.store.put_bytes('models/x/a.pkl', b'payload')
        self.assertTrue(self.store.exists('models/x/a.pkl'))
        self.assertEqual(self.store.get_bytes('models/x/a.pkl'), b'payload')

    def test_str_is_written_as_utf8_text(self):
        self.store.put_bytes('models/x/metadata.json', '{"a": 1}')
        self.assertEqual(self.store.get_bytes('models/x/metadata.json'), b'{"a": 1}')

    def test_list_keys_is_sorted(self):
        self.store.put_bytes('models/x/b.pkl', b'2')
        self.store.put_bytes('models/x/a.pkl', b'1')
        self.assertEqual(self.store.list_keys('models/x'), ['models/x/a.pkl', 'models/x/b.pkl'])

    def test_list_keys_missing_prefix_is_empty(self):
        self.assertEqual(self.store.list_keys('models/nope'), [])

    def test_delete_removes_file(self):
        self.store.put_bytes('models/x/a.pkl', b'1')
        self.store.delete('models/x/a.pkl')
        self.assertFalse(self.store.exists('models/x/a.pkl'))

    def test_dir_mirror_ops_are_noops(self):
        # The cache IS the store locally, so both return 0 and touch nothing.
        self.assertEqual(self.store.upload_dir(self._tmp.name, 'models/x'), 0)
        self.assertEqual(self.store.ensure_dir_local('models/x', self._tmp.name), 0)

    def test_is_remote_false(self):
        self.assertFalse(self.store.is_remote)


class _FakeS3Client:
    """In-memory stand-in for the subset of a boto3 S3 client that we use."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode()

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({'ResponseMetadata': {'HTTPStatusCode': 404}}, 'GetObject')

        class _Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {'Body': _Body(self.objects[Key])}

    def head_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({'ResponseMetadata': {'HTTPStatusCode': 404}}, 'HeadObject')
        return {'ContentLength': len(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def download_file(self, Bucket, Key, Filename):
        parent = os.path.dirname(Filename)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(Filename, 'wb') as handle:
            handle.write(self.objects[Key])

    def get_paginator(self, name):
        client = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                contents = [{'Key': k} for k in sorted(client.objects) if k.startswith(Prefix)]
                yield {'Contents': contents}

        return _Paginator()


class S3ArtifactStoreTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_root = self._tmp.name
        self.store = S3ArtifactStore(bucket='test-bucket', cache_root=self.cache_root)
        self.fake = _FakeS3Client()
        self.store._client = self.fake  # injected; a real boto3 client is never built

    def test_is_remote_true(self):
        self.assertTrue(self.store.is_remote)

    def test_put_get_exists_delete(self):
        self.store.put_bytes('models/x/a.pkl', b'payload')
        self.assertTrue(self.store.exists('models/x/a.pkl'))
        self.assertEqual(self.store.get_bytes('models/x/a.pkl'), b'payload')
        self.store.delete('models/x/a.pkl')
        self.assertFalse(self.store.exists('models/x/a.pkl'))

    def test_exists_is_false_on_404(self):
        self.assertFalse(self.store.exists('models/missing.pkl'))

    def test_list_keys_scopes_to_prefix(self):
        self.store.put_bytes('models/x/a.pkl', b'1')
        self.store.put_bytes('models/x/b.pkl', b'2')
        self.store.put_bytes('models/y/c.pkl', b'3')
        self.assertEqual(self.store.list_keys('models/x'), ['models/x/a.pkl', 'models/x/b.pkl'])

    def test_upload_dir_uploads_every_file_under_the_prefix(self):
        local_dir = os.path.join(self.cache_root, 'models', 'lgb', 'v1')
        os.makedirs(local_dir)
        with open(os.path.join(local_dir, 'model.pkl'), 'wb') as handle:
            handle.write(b'model')
        with open(os.path.join(local_dir, 'metadata.json'), 'w', encoding='utf-8') as handle:
            handle.write('{}')

        count = self.store.upload_dir(local_dir, 'models/lgb/v1')

        self.assertEqual(count, 2)
        self.assertEqual(self.fake.objects['models/lgb/v1/model.pkl'], b'model')
        self.assertIn('models/lgb/v1/metadata.json', self.fake.objects)

    def test_ensure_dir_local_downloads_only_missing_keys(self):
        self.fake.objects['models/lgb/v1/model.pkl'] = b'model'
        self.fake.objects['models/lgb/v1/metadata.json'] = b'{}'
        local_dir = os.path.join(self.cache_root, 'models', 'lgb', 'v1')
        os.makedirs(local_dir)
        with open(os.path.join(local_dir, 'model.pkl'), 'wb') as handle:
            handle.write(b'model')  # already cached

        downloaded = self.store.ensure_dir_local('models/lgb/v1', local_dir)

        self.assertEqual(downloaded, 1)  # only metadata.json fetched
        self.assertTrue(os.path.exists(os.path.join(local_dir, 'metadata.json')))

    def test_ensure_dir_local_warm_cache_downloads_nothing(self):
        self.fake.objects['models/lgb/v1/model.pkl'] = b'model'
        local_dir = os.path.join(self.cache_root, 'models', 'lgb', 'v1')
        os.makedirs(local_dir)
        with open(os.path.join(local_dir, 'model.pkl'), 'wb') as handle:
            handle.write(b'model')

        self.assertEqual(self.store.ensure_dir_local('models/lgb/v1', local_dir), 0)


class GetArtifactStoreFactoryTests(SimpleTestCase):
    def tearDown(self):
        reset_artifact_store()

    def test_defaults_to_local_backend(self):
        reset_artifact_store()
        with override_settings(ARTIFACT_STORE_BACKEND='local'):
            store = get_artifact_store()
        self.assertIsInstance(store, LocalArtifactStore)
        self.assertFalse(store.is_remote)

    def test_s3_backend_is_selected_and_client_is_lazy(self):
        reset_artifact_store()
        with override_settings(
            ARTIFACT_STORE_BACKEND='s3',
            ARTIFACT_S3_BUCKET='finance-analysis-artifacts',
            ARTIFACT_S3_ENDPOINT_URL='http://192.168.31.8:9000',
            ARTIFACT_S3_ACCESS_KEY_ID='id',
            ARTIFACT_S3_SECRET_ACCESS_KEY='secret',
            ARTIFACT_S3_REGION='us-east-1',
        ):
            store = get_artifact_store()
        self.assertIsInstance(store, S3ArtifactStore)
        self.assertTrue(store.is_remote)
        self.assertEqual(store.bucket, 'finance-analysis-artifacts')
        # Constructing the store must not require a reachable endpoint.
        self.assertIsNone(store._client)
