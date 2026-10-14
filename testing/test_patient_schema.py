"""The schema initializer must preserve migration order and the legacy-byte guard."""

import pytest

from app.core.patient_schema import _initialize_schema


class RecordingCursor:
    def __init__(self, *, legacy_bytes: int = 0):
        self.statements = []
        self.legacy_bytes = legacy_bytes
        self.last = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters=None):
        self.last = statement
        self.statements.append(statement)
        return self

    def fetchone(self):
        if "information_schema.columns" in self.last:
            return {"exists": bool(self.legacy_bytes)}
        return {"count": self.legacy_bytes}

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self, *, legacy_bytes=0):
        self.recording_cursor = RecordingCursor(legacy_bytes=legacy_bytes)
        self.committed = False

    def cursor(self):
        return self.recording_cursor

    def commit(self):
        self.committed = True


def test_patient_schema_keeps_lock_and_parent_tables_before_dependents():
    connection = RecordingConnection()
    _initialize_schema(connection)
    statements = connection.recording_cursor.statements
    def position(needle):
        return next(index for index, statement in enumerate(statements) if needle in statement)

    assert position("pg_advisory_xact_lock") == 0
    assert position("CREATE TABLE IF NOT EXISTS patients") < position("CREATE TABLE IF NOT EXISTS patient_documents")
    assert position("CREATE TABLE IF NOT EXISTS patient_drive_connections") < position("CREATE TABLE IF NOT EXISTS drive_file_crypto")
    assert position("CREATE TABLE IF NOT EXISTS insurance_cases") < position("CREATE TABLE IF NOT EXISTS commercial_eligibility_evidence")
    assert connection.committed


def test_patient_schema_never_drops_unmigrated_document_bytes():
    connection = RecordingConnection(legacy_bytes=1)
    with pytest.raises(RuntimeError, match="migrate them to S3 first"):
        _initialize_schema(connection)
    assert not connection.committed
    assert not any("DROP COLUMN pdf_bytes" in statement for statement in connection.recording_cursor.statements)
