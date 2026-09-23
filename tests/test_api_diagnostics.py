"""Only allowlisted metadata may reach logs or the browser."""
import unittest
from unittest.mock import patch

import requests

from api_diagnostics import report_failure
from kreta_utils import AuthenticationError, KretaAPIError, KretaUtils


class APIDiagnosticTests(unittest.TestCase):
    def test_upstream_or_exception_text_never_leaks(self):
        private = 'synthetic-private-bank-message-token-marker'
        for error in (KretaAPIError(private, api_code='API_HTTP', status=403),
                      ValueError(private), RuntimeError(private),
                      AuthenticationError(private, status=401)):
            with self.subTest(error=type(error).__name__), self.assertLogs('kreten.api') as captured:
                message = report_failure('bank_update', error)
            self.assertIn('BANK_UPDATE', message)
            self.assertNotIn(private, message + str(captured.output))

    def test_unknown_operation_and_metadata_cannot_inject_logs(self):
        private = 'synthetic-private-marker'
        error = KretaAPIError(private)
        error.api_code = private
        error.http_status = private
        with self.assertLogs('kreten.api') as captured:
            message = report_failure(private, error)
        self.assertNotIn(private, message + str(captured.output))
        self.assertIn('UNKNOWN / CLIENT_ERROR', message)

    def test_transport_failure_retains_status_without_reading_body(self):
        api = KretaUtils(klik_id='synthetic-school')
        response = requests.Response()
        response.status_code = 403
        response._content = b'synthetic-private-body'
        with patch('requests.request', return_value=response), self.assertRaises(KretaAPIError) as caught:
            api._get('consulting_hours')
        self.assertEqual(caught.exception.http_status, 403)
        self.assertEqual(caught.exception.api_code, 'API_HTTP')
        self.assertNotIn('synthetic-private-body', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
