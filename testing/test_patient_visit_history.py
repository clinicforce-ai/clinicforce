import unittest
from unittest.mock import AsyncMock, patch

from app.insurance import ecw_backend


class PatientVisitHistoryTests(unittest.IsolatedAsyncioTestCase):
    def test_account_is_the_unique_secondary_identifier_preserving_leading_zeros(self):
        patient = {'id': 'opaque', 'identifier': [{'use': 'usual', 'value': 'opaque'},
                                                 {'use': 'secondary', 'value': '001234'}]}
        self.assertEqual(ecw_backend.patient_account_number(patient), '001234')
        patient['identifier'].append({'use': 'secondary', 'value': '56789'})
        self.assertIsNone(ecw_backend.patient_account_number(patient))
        self.assertIsNone(ecw_backend.patient_account_number({'id': 'opaque'}))

    async def test_old_history_is_patient_scoped_and_not_limited_to_twelve_visits(self):
        def encounter(patient_id, visit_id, status='finished'):
            return {'id': visit_id, 'subject': {'reference': f'Patient/{patient_id}'},
                    'status': status, 'period': {'start': '2001-01-01T09:00:00-05:00'}}
        rows = [encounter('p', f'v{i}') for i in range(15)]
        rows += [encounter('other', 'wrong'), encounter('p', 'deleted', 'entered-in-error')]
        with patch.object(ecw_backend, 'settings', return_value=('client', 'https://example.test/fhir')), \
             patch.object(ecw_backend, '_access_token', AsyncMock(return_value='token')), \
             patch.object(ecw_backend, '_patient_for_profile', AsyncMock(return_value=({}, 'p', None))), \
             patch.object(ecw_backend, '_bounded_search', AsyncMock(return_value=(rows, None))) as read:
            result = await ecw_backend.patient_appointment_history({'ecw_patient_id': 'p'}, {})
        self.assertEqual(len(result['appointments']), 15)
        self.assertEqual(read.call_args.args[-1], {'patient': 'p'})
        self.assertEqual(result['patient_id'], 'p')

    async def test_future_planned_encounter_is_returned_for_date_grouping(self):
        future = {'id': 'future-visit', 'subject': {'reference': 'Patient/p'},
                  'status': 'planned', 'period': {'start': '2099-01-01T09:00:00-05:00'}}
        with patch.object(ecw_backend, 'settings', return_value=('client', 'https://example.test/fhir')), \
             patch.object(ecw_backend, '_access_token', AsyncMock(return_value='token')), \
             patch.object(ecw_backend, '_patient_for_profile', AsyncMock(return_value=({}, 'p', None))), \
             patch.object(ecw_backend, '_bounded_search', AsyncMock(return_value=([future], None))):
            result = await ecw_backend.patient_appointment_history({'ecw_patient_id': 'p'}, {})
        self.assertEqual(result['appointments'][0]['date'], '2099-01-01')
        self.assertEqual(result['appointments'][0]['status'], 'planned')
