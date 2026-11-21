"""PDF files in the "My documents" source (pdftotext, poppler-utils)."""

import shutil
import sys

import pytest

from predictive import build_ui as ui
from predictive import model_builder as mb

# Stand-in for pdftotext: prints the "PDF" file (plain text here) to stdout,
# or fails like pdftotext when the file starts with "ERR".
FAKE = """
import sys
data = open(sys.argv[-2], encoding="utf-8").read()
if data.startswith("ERR"):
    sys.stderr.write("Syntax Error: broken file\\n")
    sys.exit(1)
sys.stdout.buffer.write(data.encode("utf-8"))
"""


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_pdftotext.py"
    script.write_text(FAKE, encoding="utf-8")
    return [sys.executable, str(script)]


def test_lines_are_joined_into_paragraphs(tmp_path, fake):
    pdf = tmp_path / "a.pdf"
    pdf.write_text(
        "Le chat dort\nsur le lit de la prédic-\ntion.\n\nSecond paragraphe\n"
        "\fPage deux, porte-\nMonnaie reste.\n",
        encoding="utf-8",
    )
    assert list(mb.iter_pdf_text(pdf, fake)) == [
        "Le chat dort sur le lit de la prédiction.",
        "Second paragraphe",
        "Page deux, porte- Monnaie reste.",  # capital: not a cut word
    ]


def test_pdftotext_error_is_an_oserror(tmp_path, fake):
    pdf = tmp_path / "bad.pdf"
    pdf.write_text("ERR", encoding="utf-8")
    with pytest.raises(OSError, match="Syntax Error"):
        list(mb.iter_pdf_text(pdf, fake))


def test_folder_reads_pdf_and_reports_bad_ones(tmp_path, fake):
    (tmp_path / "a.pdf").write_text("un deux trois\n", encoding="utf-8")
    (tmp_path / "b.PDF").write_text("ERR", encoding="utf-8")
    (tmp_path / "c.txt").write_text("quatre\n", encoding="utf-8")
    skipped = []
    texts = list(
        mb.iter_folder_texts(tmp_path, lambda p, e: skipped.append(p.name), fake)
    )
    assert texts == ["un deux trois", "quatre\n"]
    assert skipped == ["b.PDF"]


def test_without_pdftotext_pdfs_are_skipped_once(tmp_path):
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    skipped = []
    texts = list(mb.iter_folder_texts(tmp_path, lambda p, e: skipped.append(e)))
    assert texts == [] and len(skipped) == 1
    assert isinstance(skipped[0], mb.PdfToolMissing) and skipped[0].files == 2
    assert "poppler-utils" in str(skipped[0])


def test_build_counts_skipped_files(tmp_path, fake, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.pdf").write_text("le chat dort\n" * 50, encoding="utf-8")
    (docs / "b.pdf").write_text("ERR", encoding="utf-8")
    events = []
    result = mb.build_model(
        [mb.FolderSource(str(docs), pdftotext=fake)],
        str(tmp_path / "m.sqlite"),
        "fr_FR",
        mb.BuildLimits(min_count=1),
        progress=events.append,
    )
    assert result["words"] == 150 and result["skipped"] == 1
    done = [e for e in events if e["event"] == "done"][0]
    text, _ = ui.progress(dict(done, event="installed"), 1)
    assert "1 file(s) could not be read" in text


def test_estimate_warns_when_pdftotext_is_missing(tmp_path, monkeypatch):
    (tmp_path / "a.pdf").write_bytes(b"x" * 2500)
    (tmp_path / "b.txt").write_text("x" * 600, encoding="utf-8")
    monkeypatch.setattr(mb, "find_pdftotext", lambda: None)
    monkeypatch.setattr(mb, "find_pdfinfo", lambda: None)
    source = mb.FolderSource(str(tmp_path))
    assert source.estimate_words() == 100 and source.pdf_files == 1
    text = ui.estimate_text(["folder:%s" % tmp_path], "fr_FR", 10, 30, 2)
    assert "1 PDF file(s) will be skipped" in text and "poppler-utils" in text
    monkeypatch.setattr(mb, "find_pdftotext", lambda: ["pdftotext"])
    # Without pdfinfo: from the size, 100 bytes per word.
    assert mb.FolderSource(str(tmp_path)).estimate_words() == 125
    text = ui.estimate_text(["folder:%s" % tmp_path], "fr_FR", 10, 30, 2)
    assert "skipped" not in text


# Stand-in for pdfinfo: the "PDF" holds "PAGES=<n>"; anything else fails.
FAKE_INFO = """
import sys
data = open(sys.argv[-1], encoding="utf-8").read()
if not data.startswith("PAGES="):
    sys.exit(1)
print("Title:          test")
print("Pages:          %s" % data.split("=")[1].split()[0])
"""


@pytest.fixture
def fake_info(tmp_path):
    script = tmp_path / "fake_pdfinfo.py"
    script.write_text(FAKE_INFO, encoding="utf-8")
    return [sys.executable, str(script)]


def test_pdf_pages(tmp_path, fake_info):
    good = tmp_path / "good.pdf"
    good.write_text("PAGES=12", encoding="utf-8")
    bad = tmp_path / "bad.pdf"
    bad.write_text("broken", encoding="utf-8")
    assert mb.pdf_pages(str(good), fake_info) == 12
    assert mb.pdf_pages(str(bad), fake_info) is None
    assert mb.pdf_pages(str(tmp_path / "missing.pdf"), fake_info) is None
    # Cached: same size and date, no new pdfinfo run.
    assert mb.pdf_pages(str(good), ["/no/such/pdfinfo"]) == 12


def test_pdf_estimate_from_pages_and_sample(tmp_path, fake_info, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    # 10 pages in 1000 bytes (images do not change the page count).
    (docs / "a.pdf").write_text("PAGES=10".ljust(1000), encoding="utf-8")
    (docs / "b.pdf").write_text("broken".ljust(500), encoding="utf-8")
    source = mb.FolderSource(str(docs), pdftotext=["x"], pdfinfo=fake_info)
    # a: 10 x 300 words; b (unreadable): scaled by size from a, 3 words/byte.
    assert source.estimate_words() == 3000 + 1500
    monkeypatch.setattr(mb, "PDF_PAGES_SAMPLE", 0)  # nothing measured: by size
    assert source.estimate_words() == 15


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF showing ``text`` in Helvetica (ASCII only)."""
    stream = "BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (number, body)).encode("latin-1")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)).encode()
    out += "".join("%010d 00000 n \n" % o for o in offsets).encode()
    out += (
        "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref)
    ).encode()
    return out


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="needs poppler-utils")
def test_real_pdftotext(tmp_path):
    pdf = tmp_path / "real.pdf"
    pdf.write_bytes(_minimal_pdf("Le petit chat dort sur le lit"))
    assert list(mb.iter_pdf_text(pdf, mb.find_pdftotext())) == [
        "Le petit chat dort sur le lit"
    ]


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="needs poppler-utils")
def test_real_pdfinfo(tmp_path):
    pdf = tmp_path / "real.pdf"
    pdf.write_bytes(_minimal_pdf("Bonjour"))
    assert mb.pdf_pages(str(pdf), mb.find_pdfinfo()) == 1
