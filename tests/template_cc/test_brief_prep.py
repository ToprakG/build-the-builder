"""brief_prep: email-thread ordering and de-duplication, override markers, and no change for a plain brief."""

from pathlib import Path

from builder.template_cc.brief_prep import prepare

REPO = Path(__file__).resolve().parents[2]

THREAD = """From: Ana <ana@x.example>
Subject: RE: RE: Widget rota
Date: 3 March 2027

Correction: the rota now starts at 08:00, not 09:00.

> The rota covers the night shift and the day shift.
> Please confirm the start time.

-----Original Message-----
From: Bo
Sent: 2 March 2027 10:15

The rota covers the night shift and the day shift.
Please confirm the start time.

-----Original Message-----
From: Ana
Sent: 1 March 2027 09:00

Start time is 09:00 for everyone.
"""


def test_thread_is_ordered_oldest_first_and_prefixed():
    out = prepare(THREAD)
    marks = [i for i, l in enumerate(out.splitlines()) if l.startswith("[Message ")]
    assert len(marks) == 3
    assert "[Message 1 of 3 — later messages override earlier ones]" in out
    assert out.index("Start time is 09:00") < out.index("The rota covers") < out.index("Correction: the rota now starts")


def test_quoted_repeat_is_removed():
    out = prepare(THREAD)
    assert out.count("The rota covers the night shift") == 1
    assert not any(l.lstrip().startswith(">") for l in out.splitlines())


def test_thread_without_dates_reverses_top_down_order():
    t = "Newest says B.\n\n-----Original Message-----\nOlder says A.\n"
    out = prepare(t)
    assert out.index("Older says A") < out.index("Newest says B")


def test_memo_and_amendment_markers_get_override():
    t = "# Policy 1\nRule one.\n\n## INTERNAL MEMO — amendments\nRule one is now two.\nEffective 5 May: change X.\n"
    out = prepare(t)
    assert "## [Override] INTERNAL MEMO — amendments" in out
    assert "[Override] Effective 5 May: change X." in out
    assert out.startswith("# Policy 1\n")


def test_plain_brief_is_byte_identical():
    for name in ("briefs/brief-a.md", "briefs/brief-b.md"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert prepare(text) == text
    plain = "Customers are 1 to 45 days late.\nThe agent replaces nothing.\n"
    assert prepare(plain) is plain
