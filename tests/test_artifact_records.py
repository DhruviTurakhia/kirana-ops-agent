from __future__ import annotations


def test_artifact_record_round_trip_cache_and_missing_file(store, tmp_path) -> None:
    first_path = tmp_path / "first.pdf"
    first_path.write_bytes(b"%PDF-test")
    arguments = {
        "artifact_type": "INVOICE_PDF",
        "source_id": "bill-123",
        "source_hash": "hash-123",
        "template_version": "template-v1",
    }

    first = store.service.record_artifact(file_path=first_path, **arguments)
    found = store.service.find_artifact(**arguments)

    assert found == first
    assert found["file_path"] == str(first_path.resolve())
    assert store.scalar("SELECT COUNT(*) FROM artifacts") == 1

    second_path = tmp_path / "replacement.pdf"
    second_path.write_bytes(b"%PDF-replacement")
    second = store.service.record_artifact(file_path=second_path, **arguments)
    assert second["id"] == first["id"]
    assert second["file_path"] == str(second_path.resolve())
    assert store.scalar("SELECT COUNT(*) FROM artifacts") == 1

    second_path.unlink()
    assert store.service.find_artifact(**arguments) is None
