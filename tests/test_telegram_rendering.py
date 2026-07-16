from kirana_agent.telegram_bot import _telegram_html


def test_telegram_html_converts_commonmark_bold() -> None:
    rendered = _telegram_html(
        "- **Fogg Body Spray 120ml** — **2 bottles** (reorder level: 4)"
    )

    assert rendered == (
        "- <b>Fogg Body Spray 120ml</b> — <b>2 bottles</b> (reorder level: 4)"
    )


def test_telegram_html_escapes_html_from_dynamic_text() -> None:
    rendered = _telegram_html("**A&B <Special>** costs ₹10")

    assert rendered == "<b>A&amp;B &lt;Special&gt;</b> costs ₹10"


def test_telegram_html_leaves_unmatched_markers_as_text() -> None:
    rendered = _telegram_html("Use **carefully & safely")

    assert rendered == "Use **carefully &amp; safely"


def test_telegram_html_does_not_treat_space_delimited_markers_as_bold() -> None:
    rendered = _telegram_html("Keep ** this literal ** please")

    assert rendered == "Keep ** this literal ** please"
