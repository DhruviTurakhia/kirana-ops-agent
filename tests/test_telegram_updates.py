from __future__ import annotations


def test_telegram_update_claim_complete_and_replay(store, tmp_path) -> None:
    attachment = tmp_path / "invoice.pdf"
    attachment.write_bytes(b"document")
    artifacts = [
        {
            "path": str(attachment),
            "filename": "invoice.pdf",
            "caption": "Invoice AKD/1",
            "kind": "pdf",
        }
    ]

    claimed = store.service.claim_telegram_update(
        update_id="telegram-100", chat_id="chat-1", user_id="owner-1"
    )
    duplicate_processing = store.service.claim_telegram_update(
        update_id="telegram-100", chat_id="chat-1", user_id="owner-1"
    )
    store.service.complete_telegram_update(
        update_id="telegram-100", response_text="Completed once", artifacts=artifacts
    )
    replay = store.service.claim_telegram_update(
        update_id="telegram-100", chat_id="chat-1", user_id="owner-1"
    )

    assert claimed == {"claimed": True, "status": "PROCESSING"}
    assert duplicate_processing["claimed"] is False
    assert duplicate_processing["status"] == "PROCESSING"
    assert replay == {
        "claimed": False,
        "status": "COMPLETED",
        "response_text": "Completed once",
        "artifacts": artifacts,
    }
    assert store.scalar(
        "SELECT COUNT(*) FROM telegram_updates WHERE update_id = ?", ("telegram-100",)
    ) == 1


def test_failed_telegram_update_can_be_retried_once_then_completed(store) -> None:
    store.service.claim_telegram_update(
        update_id="telegram-fail", chat_id="chat-2", user_id="owner-1"
    )
    store.service.fail_telegram_update(
        update_id="telegram-fail", error_text="temporary failure"
    )
    failed = store.service.claim_telegram_update(
        update_id="telegram-fail", chat_id="chat-2", user_id="owner-1"
    )

    assert failed["claimed"] is False
    assert failed["status"] == "FAILED"
    assert store.service.retry_failed_telegram_update("telegram-fail") is True
    assert store.service.retry_failed_telegram_update("telegram-fail") is False

    processing = store.service.claim_telegram_update(
        update_id="telegram-fail", chat_id="chat-2", user_id="owner-1"
    )
    assert processing["status"] == "PROCESSING"
    store.service.complete_telegram_update(
        update_id="telegram-fail", response_text="Recovered", artifacts=[]
    )
    completed = store.service.claim_telegram_update(
        update_id="telegram-fail", chat_id="chat-2", user_id="owner-1"
    )
    assert completed["status"] == "COMPLETED"
    assert completed["response_text"] == "Recovered"
