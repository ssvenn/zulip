from mock import patch

from django.utils.timezone import now as timezone_now
from django.conf import settings

from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_helpers import use_s3_backend, create_s3_buckets

from zerver.models import RealmAuditLog
from zerver.views.public_export import public_only_realm_export

import os
import re

def create_tarball_path() -> str:
    tarball_path = os.path.join(settings.TEST_WORKER_DIR, 'test-export.tar.gz')
    with open(tarball_path, 'w') as f:
        f.write('zulip!')
    return tarball_path

class RealmExportTest(ZulipTestCase):
    def test_export_as_not_admin(self) -> None:
        user = self.example_user('hamlet')
        self.login(user.email)
        with self.assertRaises(JsonableError):
            public_only_realm_export(self.client_post, user)

    @use_s3_backend
    def test_endpoint_s3(self) -> None:
        admin = self.example_user('iago')
        self.login(admin.email)
        bucket = create_s3_buckets(settings.S3_AVATAR_BUCKET)[0]
        tarball_path = create_tarball_path()

        with patch('zerver.lib.export.do_export_realm',
                   return_value=tarball_path) as mock_export:
            with self.settings(LOCAL_UPLOADS_DIR=None):
                result = self.client_post('/json/export/realm')
            self.assert_json_success(result)
            self.assertFalse(os.path.exists(tarball_path))

        args = mock_export.call_args_list[0][1]
        self.assertEqual(args['realm'], admin.realm)
        self.assertEqual(args['public_only'], True)
        self.assertIn('/tmp/zulip-export-', args['output_dir'])
        self.assertEqual(args['threads'], 6)

        export_object = RealmAuditLog.objects.filter(
            event_type='realm_exported').first()
        uri = getattr(export_object, 'extra_data')
        self.assertIsNotNone(uri)
        path_id = re.sub('https://test-avatar-bucket.s3.amazonaws.com:443/', '', uri)
        self.assertEqual(bucket.get_key(path_id).get_contents_as_string(),
                         b'zulip!')

    def test_endpoint_local_uploads(self) -> None:
        admin = self.example_user('iago')
        self.login(admin.email)
        tarball_path = create_tarball_path()

        with patch('zerver.lib.export.do_export_realm',
                   return_value=tarball_path) as mock_export:
            result = self.client_post('/json/export/realm')
        self.assert_json_success(result)
        self.assertFalse(os.path.exists(tarball_path))

        args = mock_export.call_args_list[0][1]
        self.assertEqual(args['realm'], admin.realm)
        self.assertEqual(args['public_only'], True)
        self.assertIn('/tmp/zulip-export-', args['output_dir'])
        self.assertEqual(args['threads'], 6)

        export_object = RealmAuditLog.objects.filter(
            event_type='realm_exported').first()
        uri = getattr(export_object, 'extra_data')
        response = self.client_get(uri)
        self.assertEqual(response.status_code, 200)
        self.assert_url_serves_contents_of_file(uri, b'zulip!')

    def test_realm_export_rate_limited(self) -> None:
        admin = self.example_user('iago')
        self.login(admin.email)

        current_log = RealmAuditLog.objects.filter(
            event_type=RealmAuditLog.REALM_EXPORTED)
        self.assertEqual(len(current_log), 0)

        exports = []
        for i in range(0, 5):
            exports.append(RealmAuditLog(realm=admin.realm,
                                         event_type=RealmAuditLog.REALM_EXPORTED,
                                         event_time=timezone_now()))
        RealmAuditLog.objects.bulk_create(exports)

        result = public_only_realm_export(self.client_post, admin)
        self.assert_json_error(result, 'Exceeded rate limit.')
