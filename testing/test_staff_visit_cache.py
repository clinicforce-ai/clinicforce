"""The appointment projection stores only list fields for one verified link."""

import json
from unittest.mock import MagicMock, patch

from app.core import staff_dashboard, staff_store


def test_visit_cache_excludes_raw_encounter_and_scopes_update():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    with patch.object(staff_store, '_connection', return_value=connection):
        staff_store.save_ecw_visit_cache('patient-a', 'clinic-a', 'ecw-a', [
            {'from_ecw': True, 'date': '2026-11-07', 'start': '2026-11-07T09:00:00-05:00',
             'status': 'finished', 'reason': 'private visit reason'},
            {'from_ecw': False, 'date': '2026-11-08', 'status': 'Saved visit'},
        ])
    query, params = connection.execute.call_args.args
    assert 'practice_id=%s AND ecw_patient_id=%s' in query
    assert "identity_status='confirmed'" in query
    assert json.loads(params[0]) == [{
        'date': '2026-11-07', 'start': '2026-11-07T09:00:00-05:00', 'status': 'finished',
    }]
    assert params[1] == 'ecw-a'
    assert params[-3:] == ('patient-a', 'clinic-a', 'ecw-a')


def test_dashboard_only_exposes_cache_for_current_confirmed_link():
    patient = {
        'id': 'patient-a', 'practice_id': 'clinic-a', 'given_name': 'Example',
        'family_name': 'Patient', 'birth_date': '2000-01-01', 'created_at': '2026-11-01',
        'identity_status': 'confirmed', 'ecw_patient_id': 'ecw-a',
        'ecw_visit_cache_patient_id': 'ecw-a', 'ecw_visit_cached_at': '2026-11-10T12:00:00Z',
        'ecw_visit_cache': [{'date': '2026-11-07', 'start': None, 'status': 'finished'}],
    }
    connection = MagicMock()
    connection.__enter__.return_value = connection

    def execute(query, _params):
        result = MagicMock()
        if 'FROM staff_patients' in query:
            result.fetchall.return_value = [patient]
        elif 'FROM staff_deletions' in query:
            result.fetchone.return_value = {'count': 0}
        else:
            result.fetchall.return_value = []
        return result

    connection.execute.side_effect = execute
    def dashboard():
        return staff_dashboard.patient_dashboard('clinic-a', connection=lambda: connection,
                                                 hash_value=lambda value: value)

    assert dashboard()['patients'][0]['ecw_visit_cache'] == patient['ecw_visit_cache']
    patient['ecw_visit_cache_patient_id'] = 'old-link'
    assert dashboard()['patients'][0]['ecw_visit_cache'] is None
    patient['ecw_visit_cache_patient_id'] = 'ecw-a'
    patient['identity_status'] = 'unverified'
    assert dashboard()['patients'][0]['ecw_visit_cache'] is None


def test_requested_upload_is_received_on_target_patient_without_confirmation():
    patient = {
        'id': 'target', 'given_name': 'Example', 'family_name': 'Patient',
        'birth_date': '2000-01-01', 'created_at': '2026-11-01',
        'identity_status': 'confirmed', 'ecw_patient_id': 'ecw-target',
        'ecw_visit_cache_patient_id': None, 'email': None, 'ecw_account_number': None,
    }
    upload = {
        'id': 'upload', 'patient_id': None, 'auto_patient_id': 'other',
        'expected_patient_id': 'target', 'source_portal_patient_id': None,
        'fields': {'given_name': 'Different', 'family_name': 'Name', 'birth_date': '1990-01-01'},
        'status': 'needs_confirmation', 'source': 'patient_upload',
        'created_at': '2026-11-02', 'updated_at': '2026-11-02', 'has_card': True,
    }
    connection = MagicMock()
    connection.__enter__.return_value = connection

    def execute(query, _params):
        result = MagicMock()
        if 'FROM staff_patients' in query:
            result.fetchall.return_value = [patient]
        elif 'FROM staff_deletions' in query:
            result.fetchone.return_value = {'count': 0}
        else:
            result.fetchall.return_value = [upload]
        return result

    connection.execute.side_effect = execute
    row = staff_dashboard.patient_dashboard('clinic-a', connection=lambda: connection,
                                            hash_value=lambda value: value)['patients'][0]
    assert row['id'] == 'target'
    assert row['card_status'] == 'received'
    assert row['latest_intake_id'] == 'upload'
