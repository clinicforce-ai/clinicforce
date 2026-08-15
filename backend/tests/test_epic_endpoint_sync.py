from __future__ import annotations

from app.core import patient_store
from app.scripts.sync_epic_endpoints import _parse


def test_parse_resolves_epic_urn_uuid_endpoint_references() -> None:
    endpoint_id = "9bbe8119-65bf-4343-a0c2-22374f183c68"
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {"resource": {
                "resourceType": "Organization",
                "id": "houston-methodist",
                "name": "Houston Methodist",
                "endpoint": [{"reference": f"urn:uuid:{endpoint_id}"}],
            }},
            {"resource": {
                "resourceType": "Endpoint",
                "id": endpoint_id,
                "status": "active",
                "address": "https://fhir.houstonmethodist.org/interconnect-fhir-oauth/api/FHIR/R4",
            }},
        ],
    }

    assert _parse(bundle) == [{
        "id": f"epic:houston-methodist:{endpoint_id}",
        "name": "Houston Methodist",
        "aliases": [],
        "addresses": [],
        "fhir_base": "https://fhir.houstonmethodist.org/interconnect-fhir-oauth/api/FHIR/R4",
    }]


def test_replace_epic_endpoints_uses_a_psycopg_cursor_for_batch_insert(monkeypatch) -> None:
    class Cursor:
        def __init__(self) -> None:
            self.rows = []

        def executemany(self, statement, rows) -> None:
            self.rows = list(rows)

    class Connection:
        def __init__(self) -> None:
            self.cursor_value = Cursor()
            self.statements = []

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, statement) -> None:
            self.statements.append(statement)

        def cursor(self) -> Cursor:
            return self.cursor_value

    connection = Connection()
    monkeypatch.setattr(patient_store, "_connection", lambda: connection)

    patient_store.replace_epic_endpoints([{
        "id": "epic:houston-methodist:endpoint",
        "name": "Houston Methodist",
        "aliases": [],
        "addresses": [],
        "fhir_base": "https://fhir.houstonmethodist.org/fhir/r4",
    }])

    assert connection.statements == ["DELETE FROM epic_endpoints"]
    assert len(connection.cursor_value.rows) == 1
