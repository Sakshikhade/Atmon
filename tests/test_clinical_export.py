from src.clinical_export import NON_DIAGNOSTIC, export_lines, pdf_bytes, pdf_text


def test_export_keeps_the_statement_and_leaves_out_video():
    lines = export_lines(
        "Milo",
        1,
        [
            {
                "setting": "Home",
                "when": "27 Sep 2026",
                "events": [
                    {
                        "time": "0:12",
                        "name": "Hand flapping",
                        "duration": "4s",
                        "family": "confirmed",
                        "clinician": "confirmed",
                        "before": "A demand was placed",
                    }
                ],
            }
        ],
    )
    payload = pdf_bytes(lines)
    text = pdf_text(payload)
    assert NON_DIAGNOSTIC in text
    assert "This export contains no video and no audio." in text
    assert "Hand flapping" in text
    assert b"/EmbeddedFile" not in payload


def test_rejected_events_are_not_in_the_lines():
    lines = export_lines("Milo", 0, [{"setting": "Home", "when": "27 Sep 2026", "events": []}])
    assert "No events in this export." in "\n".join(lines)
    assert NON_DIAGNOSTIC in lines
