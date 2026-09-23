"""Only synthetic values and mocked network calls; no account access."""
import unittest
from unittest.mock import patch
from kreta_utils import KretaUtils, KretaAPIError


class MessageBankContractTests(unittest.TestCase):
    def setUp(self):
        self.client = KretaUtils('synthetic', None, 'demo')

    def test_bank_json_uses_api_field_names_and_numeric_owner_type(self):
        with patch.object(self.client, 'get_student_data', return_value={'bank_read_only': False}), patch.object(self.client, '_change', return_value='') as send:
            self.client.update_bank_account('00000000-00000000', 'Synthetic Owner', '2', 'Synthetic Bank')
        send.assert_called_once_with('POST', 'bank_account', {
            'BankszamlaSzam': '00000000-00000000',
            'BankszamlaTulajdonosNeve': 'Synthetic Owner',
            'BankszamlaTulajdonosTipusId': 2,
            'SzamlavezetoBank': 'Synthetic Bank',
        })

    def test_bank_rejects_invalid_owner_type_before_sending(self):
        for value in (None, True, 'student', '2.5', {}, []):
            with self.subTest(value=value), patch.object(self.client, '_change') as send:
                with self.assertRaises(ValueError):
                    self.client.update_bank_account('00000000-00000000', 'Synthetic Owner', value, 'Synthetic Bank')
                send.assert_not_called()

    def test_bank_read_only_prevents_update_and_delete(self):
        with patch.object(self.client, 'get_student_data', return_value={'bank_read_only': True}), patch.object(self.client, '_change') as send:
            for action in (lambda: self.client.update_bank_account('00000000-00000000', 'Synthetic Owner', '2', 'Synthetic Bank'), self.client.delete_bank_account):
                with self.assertRaises(KretaAPIError) as caught:
                    action()
                self.assertEqual(caught.exception.http_status, 403)
            send.assert_not_called()

    def test_current_official_folder_endpoints_do_not_use_empty_sajat_endpoint(self):
        rows = [{'azonosito': 1, 'uzenetTargy': 'Synthetic'}]
        for folder in ('beerkezett', 'elkuldott', 'torolt'):
            with self.subTest(folder=folder), patch.object(self.client, '_get', return_value=rows) as get:
                self.assertEqual(len(self.client.get_messages(folder)), 1)
            get.assert_called_once_with('messages', base_url=self.client.admin_url, suffix='/' + folder)

    def test_mailbox_http_errors_are_not_converted_to_empty_lists(self):
        for status in (403, 404, 500):
            with self.subTest(status=status), patch.object(self.client, '_get', side_effect=KretaAPIError('failed', api_code='API_HTTP', status=status)) as get:
                with self.assertRaises(KretaAPIError):
                    self.client.get_messages('beerkezett')
                self.assertEqual(get.call_count, 1)

    def test_legacy_message_summary_allows_optional_missing_fields(self):
        rows = self.client.convert_messages([{
            'azonosito': 1, 'uzenetAzonosito': 101, 'uzenetKuldesDatum': '2026-09-01T10:00:00Z',
            'uzenetTargy': 'Synthetic subject', 'uzenetFeladoNev': None,
        }])
        self.assertEqual(rows[0]['subject'], 'Synthetic subject')
        self.assertEqual(rows[0]['sender_name'], '')
        self.assertEqual(rows[0]['date'], '2026-09-01 12:00')
        self.assertFalse(rows[0]['has_attachment'])

    def test_nested_mailbox_filters_deleted_received_and_sent(self):
        rows = [
            {'azonosito': 1, 'isElolvasva': False, 'tipus': {'kod': 'BEERKEZETT'},
             'uzenet': {'azonosito': 101, 'targy': 'Received', 'kuldesDatum': '2026-09-01T12:00:00',
                        'csatolmanyok': [{'azonosito': 9, 'fajlNev': 'synthetic.txt'}]}},
            {'azonosito': 2, 'tipus': {'kod': 'ELKULDOTT'}, 'uzenet': {'azonosito': 102, 'targy': 'Sent'}},
            {'azonosito': 3, 'isToroltElem': True, 'tipus': {'kod': 'BEERKEZETT'},
             'uzenet': {'azonosito': 103, 'targy': 'Deleted'}},
        ]
        received = self.client.convert_messages(rows, 'beerkezett')
        sent = self.client.convert_messages(rows, 'elkuldott')
        deleted = self.client.convert_messages(rows, 'torolt')
        self.assertEqual([row['id'] for row in received], [1])
        self.assertTrue(received[0]['has_attachment'])
        self.assertEqual([row['id'] for row in sent], [2])
        self.assertEqual([row['id'] for row in deleted], [3])

    def test_message_details_tolerate_null_metadata_and_collections(self):
        result = self.client.convert_message_details({
            'azonosito': 3, 'tipus': None, 'isToroltElem': False,
            'uzenet': {'azonosito': 103, 'szoveg': '<p>Safe synthetic text</p>',
                       'feladoTitulus': None, 'statusz': None, 'cimzettLista': None, 'csatolmanyok': None}
        })
        self.assertEqual(result['message']['recipients'], [])
        self.assertEqual(result['message']['attachments'], [])
        self.assertEqual(result['message']['text'], '<p>Safe synthetic text</p>')

    def test_admin_localization_header_is_scoped_to_admin_requests(self):
        response = __import__('requests').Response()
        response.status_code = 200
        response._content = b'[]'
        with patch('requests.request', return_value=response) as request:
            self.client._get('messages', base_url=self.client.admin_url, suffix='/sajat')
            self.assertEqual(request.call_args.kwargs['headers']['X-Uzenet-Lokalizacio'], 'hu-HU')
            self.assertNotIn('User-Agent', request.call_args.kwargs['headers'])
            self.client._get('student')
            self.assertNotIn('X-Uzenet-Lokalizacio', request.call_args.kwargs['headers'])
            self.assertEqual(request.call_args.kwargs['headers']['User-Agent'], self.client.headers['User-Agent'])

    def test_unknown_mailbox_kind_does_not_silently_hide_messages(self):
        with self.assertRaises(KretaAPIError) as caught:
            self.client.convert_messages([{'azonosito': 1, 'tipus': {'kod': 'UNKNOWN'}, 'uzenet': {'targy': 'Synthetic'}}], 'beerkezett')
        self.assertEqual(caught.exception.api_code, 'API_FORMAT')

    def test_contact_update_is_form_encoded(self):
        response = __import__('requests').Response()
        response.status_code = 204
        response._content = b''
        with patch('requests.request', return_value=response) as request:
            self.client.update_contact_info('synthetic@example.invalid', '000000')
        self.assertNotIn('json', request.call_args.kwargs)
        self.assertEqual(request.call_args.kwargs['data'], {'email': 'synthetic@example.invalid', 'telefonszam': '000000'})

    def test_bad_list_or_detail_shape_is_not_silently_empty(self):
        for value in ({'error': 'private upstream message'}, None, 'invalid', [None]):
            with self.subTest(value=value):
                with self.assertRaises(KretaAPIError) as raised:
                    self.client.convert_messages(value)
                self.assertNotIn('private', str(raised.exception))
        with self.assertRaises(KretaAPIError):
            self.client.convert_message_details({'error': 'private upstream message'})


if __name__ == '__main__':
    unittest.main()
