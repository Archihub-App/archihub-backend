"""Authoring the report PDF "Firmar PDF" signs: fields, activities, attachments.

Nothing here reaches a database. `build_fields` and the mongo lookups are
monkeypatched so what is under test is the assembly itself - that nothing is
silently dropped, that a previous run's own signed PDF is not folded back in,
and that the result is a real, readable, non-empty PDF.
"""

from __future__ import annotations

import pytest

plugin = pytest.importorskip(
    "archihub.plugins.KoboConnector", reason="KoboConnector is not installed in this checkout"
)

from archihub.plugins.KoboConnector import pdf_signing as ps  # noqa: E402
from archihub.plugins.KoboConnector import report_builder as rb  # noqa: E402


def _resource(title="Informe de julio", files_obj=None, **extra):
    return {
        "_id": "report-1",
        "post_type": "informe-mensual",
        "metadata": {"firstLevel": {"title": title}},
        "filesObj": files_obj or [],
        **extra,
    }


# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------


def test_the_title_falls_back_when_the_resource_has_none():
    assert rb._title_of({"metadata": {}}) == "Informe"


def test_the_title_is_read_from_the_usual_path():
    assert rb._title_of(_resource(title="Julio 2026")) == "Julio 2026"


def test_a_scalar_field_is_one_line():
    assert rb._field_lines({"type": "text", "value": "Planadas"}) == ["Planadas"]


def test_a_select_fields_terms_are_joined():
    assert rb._field_lines({"type": "select", "value": ["Rural", "Urbano"]}) == ["Rural, Urbano"]


def test_an_empty_select_says_none_rather_than_a_blank_line():
    assert rb._field_lines({"type": "select", "value": []}) == ["(ninguno)"]


def test_a_relation_lists_the_related_titles():
    value = [{"name": "Informe de junio"}, {"name": "Informe de mayo"}]
    assert rb._field_lines({"type": "relation", "value": value}) == ["Informe de junio, Informe de mayo"]


def test_a_repeater_is_one_line_per_row():
    value = [
        [{"label": "Nombre", "value": "Ana"}, {"label": "Cargo", "value": "Coordinadora"}],
        [{"label": "Nombre", "value": "Luis"}, {"label": "Cargo", "value": "Técnico"}],
    ]
    lines = rb._field_lines({"type": "repeater", "value": value})
    assert lines == ["Nombre: Ana; Cargo: Coordinadora", "Nombre: Luis; Cargo: Técnico"]


def test_a_repeater_with_no_rows_says_so_rather_than_rendering_nothing():
    assert rb._field_lines({"type": "repeater", "value": []}) == ["(sin filas)"]


# ---------------------------------------------------------------------------
# Which attachments go in, and which do not
# ---------------------------------------------------------------------------


def test_attachments_are_read_in_their_declared_order(monkeypatch):
    resource = _resource(
        files_obj=[
            {"id": "rec-2", "tag": "x", "order": 1},
            {"id": "rec-1", "tag": "x", "order": 0},
        ]
    )
    records = {"rec-1": {"name": "first"}, "rec-2": {"name": "second"}}

    class FakeMongo:
        def get_record(self, collection, filters):
            return records.get(str(filters["_id"]))

    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: FakeMongo())
    monkeypatch.setattr("bson.objectid.ObjectId", lambda v: v)

    names = [r["name"] for r in rb._attachment_records(resource)]
    assert names == ["first", "second"]


# ---------------------------------------------------------------------------
# Converting a document that is neither a PDF nor an image
# ---------------------------------------------------------------------------


def test_an_unsupported_attachment_is_only_noted_when_no_converter_is_active(monkeypatch, tmp_path):
    from pypdf import PdfReader

    path = tmp_path / "acta.docx"
    path.write_bytes(b"not a real docx")
    monkeypatch.setattr(rb, "_record_path", lambda record: path)
    monkeypatch.setattr("archihub.plugins.framework.interop.has", lambda capability: False)

    segments = rb._attachment_segments({"mime": "application/msword", "name": "acta.docx"})

    assert len(segments) == 1
    assert "acta.docx" in segments[0].pages[0].extract_text()


def test_an_unsupported_attachment_is_converted_when_a_provider_is_active(monkeypatch, tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "acta.docx"
    path.write_bytes(b"not a real docx")
    monkeypatch.setattr(rb, "_record_path", lambda record: path)
    monkeypatch.setattr("archihub.plugins.framework.interop.has", lambda capability: True)

    def fake_convert(source, destination):
        c = canvas.Canvas(str(destination), pagesize=(300, 400))
        c.drawString(50, 350, "Contenido convertido")
        c.showPage()
        c.save()

    monkeypatch.setattr("archihub.plugins.framework.interop.convert_to_pdf", fake_convert)

    segments = rb._attachment_segments({"mime": "application/msword", "name": "acta.docx"})

    assert len(segments) == 2  # a caption page, then the converted content
    assert "acta.docx" in segments[0].pages[0].extract_text()
    assert "Contenido convertido" in segments[1].pages[0].extract_text()


def test_a_failed_conversion_falls_back_to_a_note_rather_than_raising(monkeypatch, tmp_path):
    path = tmp_path / "acta.docx"
    path.write_bytes(b"not a real docx")
    monkeypatch.setattr(rb, "_record_path", lambda record: path)
    monkeypatch.setattr("archihub.plugins.framework.interop.has", lambda capability: True)

    def broken_convert(source, destination):
        raise RuntimeError("LibreOffice is not installed")

    monkeypatch.setattr("archihub.plugins.framework.interop.convert_to_pdf", broken_convert)

    segments = rb._attachment_segments({"mime": "application/msword", "name": "acta.docx"})

    assert len(segments) == 1
    assert "acta.docx" in segments[0].pages[0].extract_text()


def test_a_pdf_attachment_is_merged_with_no_caption(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    path = tmp_path / "escaneo.pdf"
    c = canvas.Canvas(str(path), pagesize=(300, 400))
    c.drawString(50, 350, "Ya es un PDF")
    c.showPage()
    c.save()

    monkeypatch.setattr(rb, "_record_path", lambda record: path)

    segments = rb._attachment_segments({"mime": "application/pdf", "name": "escaneo.pdf"})

    assert len(segments) == 1
    assert "Ya es un PDF" in segments[0].pages[0].extract_text()


# ---------------------------------------------------------------------------
# The whole assembly, end to end
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_image(tmp_path):
    from PIL import Image

    path = tmp_path / "evidencia.jpg"
    Image.new("RGB", (200, 120), (10, 80, 40)).save(path)
    return path


def test_the_report_includes_fields_activities_and_attachments(monkeypatch, tmp_path, sample_image):
    from pypdf import PdfReader

    resource = _resource(title="Informe de julio")
    activity = {"_id": "act-1", "metadata": {"firstLevel": {"title": "Reunión comunitaria"}}}

    def fake_build_fields(target, user, public=False):
        if target is resource:
            return [{"label": "Municipio", "value": "Planadas", "type": "text"}]
        return [{"label": "Participantes", "value": "12", "type": "number"}]

    monkeypatch.setattr("archihub.api.resources.presentation.build_fields", fake_build_fields)
    monkeypatch.setattr(
        rb,
        "_attachment_records",
        lambda source: (
            [{"mime": "image/jpeg", "name": "evidencia.jpg"}] if source is resource else []
        ),
    )
    monkeypatch.setattr(rb, "_record_path", lambda record: sample_image)

    destination = tmp_path / "report.pdf"
    rb.build_report_pdf(resource, [activity], "admin@test.com", destination)

    reader = PdfReader(str(destination))
    assert len(reader.pages) >= 2  # at least the body page and the image page

    full_text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Informe de julio" in full_text
    assert "Planadas" in full_text
    assert "Reunión comunitaria" in full_text
    assert "12" in full_text
    assert "evidencia.jpg" in full_text


def test_an_unsupported_attachment_is_named_rather_than_dropped(monkeypatch, tmp_path):
    from pypdf import PdfReader

    resource = _resource()

    monkeypatch.setattr("archihub.api.resources.presentation.build_fields", lambda *a, **k: [])
    monkeypatch.setattr(
        rb, "_attachment_records", lambda source: [{"mime": "application/msword", "name": "acta.doc"}]
    )

    destination = tmp_path / "report.pdf"
    rb.build_report_pdf(resource, [], "admin@test.com", destination)

    full_text = "\n".join(page.extract_text() for page in PdfReader(str(destination)).pages)
    assert "acta.doc" in full_text


def test_a_report_with_nothing_at_all_still_produces_a_page(monkeypatch, tmp_path):
    from pypdf import PdfReader

    resource = _resource()
    monkeypatch.setattr("archihub.api.resources.presentation.build_fields", lambda *a, **k: [])
    monkeypatch.setattr(rb, "_attachment_records", lambda source: [])

    destination = tmp_path / "report.pdf"
    rb.build_report_pdf(resource, [], "admin@test.com", destination)

    assert len(PdfReader(str(destination)).pages) >= 1


def test_a_value_containing_markup_characters_does_not_break_the_pdf(monkeypatch, tmp_path):
    """A metadata value is data, never markup - reportlab's Paragraph parses its
    text as XML-like, so an unescaped '<' from a real submission would raise."""
    from pypdf import PdfReader

    resource = _resource(title="Título con <etiqueta> & \"comillas\"")
    monkeypatch.setattr(
        "archihub.api.resources.presentation.build_fields",
        lambda *a, **k: [{"label": "Nota", "value": "5 < 10 & listo", "type": "text"}],
    )
    monkeypatch.setattr(rb, "_attachment_records", lambda source: [])

    destination = tmp_path / "report.pdf"
    rb.build_report_pdf(resource, [], "admin@test.com", destination)  # must not raise

    full_text = "\n".join(page.extract_text() for page in PdfReader(str(destination)).pages)
    assert "5 < 10 & listo" in full_text
