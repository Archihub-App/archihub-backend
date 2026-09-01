"""KoboConnector's "Firmar PDF" action: stamping a report with a logo and a signature.

Nothing here reaches a database, Kobo, or a real file. The pieces under test are
the ones a wrong answer would not be loud about: matching a repeater row to the
right stored image, refusing an ambiguous or missing PDF instead of guessing,
and producing a signed PDF that keeps its page count and carries a real,
correctly-addressed link - not a literal backslash-n or a dead footer.
"""

from __future__ import annotations

import io

import pytest

#: SKIPPED WHEN THE PLUGIN IS NOT INSTALLED. `archihub/plugins/*` is gitignored
#: apart from the five that ship with the backend, so this file can be committed
#: while the package it tests is not - importing at module scope would turn a
#: checkout without it into a collection error, which reads as a broken suite
#: rather than as an absent optional component.
plugin = pytest.importorskip(
    "archihub.plugins.KoboConnector", reason="KoboConnector is not installed in this checkout"
)

from archihub.plugins.KoboConnector import pdf_signing as ps  # noqa: E402

#: The repeater field this form declares, matching the live shape: two
#: subfields whose `destiny` is empty, which is exactly what makes a row's
#: stored keys not match either subfield's declared name.
FIELD = {
    "type": "repeater",
    "label": "Logos y firmas",
    "destiny": "metadata.firstLevel.admin_logos_firmas",
    "subfields": [
        {"type": "text", "name": "nombre", "destiny": ""},
        {"type": "file", "name": "archivo", "destiny": ""},
    ],
}


def _resource(rows, files_obj=None, **extra):
    return {
        "_id": "r1",
        "post_type": "logos-firmas",
        "metadata": {"firstLevel": {"admin_logos_firmas": rows}},
        "filesObj": files_obj or [],
        **extra,
    }


# ---------------------------------------------------------------------------
# Reading a row: matched by value type, not by key name
# ---------------------------------------------------------------------------


def test_a_row_with_no_destiny_is_still_read_by_value_type():
    """The live shape: keys are '' and 'undefined', neither is a subfield name."""
    assert ps._row_name({"": [], "undefined": "PBF"}) == "PBF"


def test_a_properly_keyed_row_is_read_the_same_way():
    assert ps._row_name({"nombre": "Ana", "archivo": []}) == "Ana"


def test_a_row_with_no_text_value_has_no_name():
    assert ps._row_name({"": [], "otro": []}) == ""


def test_dotted_get_walks_a_nested_path():
    doc = {"metadata": {"firstLevel": {"admin_logos_firmas": [1, 2]}}}
    assert ps._dotted_get(doc, "metadata.firstLevel.admin_logos_firmas") == [1, 2]


def test_dotted_get_is_none_off_a_missing_branch():
    assert ps._dotted_get({"metadata": {}}, "metadata.firstLevel.title") is None


def test_dotted_get_does_not_descend_into_a_non_dict():
    assert ps._dotted_get({"metadata": "not a dict"}, "metadata.firstLevel") is None


def test_the_file_subfield_is_found_by_type_not_position():
    reordered = {
        "subfields": [
            {"type": "file", "name": "archivo"},
            {"type": "text", "name": "nombre"},
        ]
    }
    assert ps._file_subfield_name(reordered) == "archivo"


def test_a_repeater_with_no_file_subfield_names_none():
    assert ps._file_subfield_name({"subfields": [{"type": "text", "name": "nombre"}]}) == ""


# ---------------------------------------------------------------------------
# Resolving one row to its stamp image, through the tag convention
# ---------------------------------------------------------------------------


def test_a_row_resolves_through_its_own_numbered_tag(monkeypatch, tmp_path):
    resource = _resource(
        rows=[{"": [], "undefined": "PBF"}, {"": [], "undefined": "CHR"}],
        files_obj=[
            {"id": "rec-1", "tag": "Logos y firmas - archivo #1.1", "order": 0},
            {"id": "rec-2", "tag": "Logos y firmas - archivo #2.1", "order": 1},
        ],
    )
    records = {
        "rec-1": {"mime": "image/png", "filepath": "2026/08/28/one.png"},
        "rec-2": {"mime": "image/jpeg", "filepath": "2026/08/28/two.jpg"},
    }
    two_path = tmp_path / "two.jpg"
    two_path.write_bytes(b"fake")
    monkeypatch.setattr(ps, "_load_record", lambda rid: records.get(rid))
    monkeypatch.setattr(
        ps, "_record_path", lambda record: two_path if record["filepath"].endswith("two.jpg") else None
    )

    name, path = ps._resolve_row(resource, FIELD, 1)
    assert name == "CHR"
    assert path == two_path


def test_a_row_with_no_matching_tag_has_no_image():
    resource = _resource(
        rows=[{"": [], "undefined": "PBF"}],
        files_obj=[{"id": "rec-9", "tag": "Something else entirely", "order": 0}],
    )
    name, path = ps._resolve_row(resource, FIELD, 0)
    assert name == "PBF"
    assert path is None


def test_a_row_index_out_of_range_is_refused():
    resource = _resource(rows=[{"": [], "undefined": "PBF"}])
    with pytest.raises(ps.SigningError):
        ps._resolve_row(resource, FIELD, 5)


def test_a_tag_for_row_eleven_does_not_match_a_lookup_for_row_one():
    """"#1." must not match "#11.1" - a startswith with no trailing dot would."""
    resource = _resource(
        rows=[{}] * 11,
        files_obj=[{"id": "rec-11", "tag": "Logos y firmas - archivo #11.1", "order": 0}],
    )
    name, path = ps._resolve_row(resource, FIELD, 0)  # row index 0 -> prefix "#1."
    assert path is None


def test_a_non_image_record_behind_a_matching_tag_is_not_used(monkeypatch):
    resource = _resource(
        rows=[{"": [], "undefined": "PBF"}],
        files_obj=[{"id": "rec-1", "tag": "Logos y firmas - archivo #1.1", "order": 0}],
    )
    monkeypatch.setattr(ps, "_load_record", lambda rid: {"mime": "application/pdf", "filepath": "x.pdf"})
    name, path = ps._resolve_row(resource, FIELD, 0)
    assert path is None


# ---------------------------------------------------------------------------
# Loading the activities filed under the report
# ---------------------------------------------------------------------------


def test_no_activity_type_configured_means_no_activities():
    """A report with no repeat-group activities is normal, not an error."""
    assert ps._load_activities(_resource(rows=[]), "") == []


def test_activities_are_this_resources_own_children_only(monkeypatch):
    resource = _resource(rows=[], _id="report-1")
    seen = {}

    class FakeMongo:
        def get_all_records(self, collection, filters, sort=None):
            seen["collection"] = collection
            seen["filters"] = filters
            return iter([{"_id": "child-1"}, {"_id": "child-2"}])

    monkeypatch.setattr("archihub.infra.mongo.get_mongo", lambda: FakeMongo())

    activities = ps._load_activities(resource, "kobo_activities")

    assert seen["collection"] == "resources"
    assert seen["filters"]["parents.id"] == "report-1"
    assert seen["filters"]["post_type"] == "kobo_activities"
    assert [a["_id"] for a in activities] == ["child-1", "child-2"]


# ---------------------------------------------------------------------------
# The live selectors
# ---------------------------------------------------------------------------


def test_selector_fields_with_nothing_configured_offer_no_real_choice():
    fields = ps.selector_fields({})
    ids = {f["id"] for f in fields}
    assert ids == {"logo_row", "firma_row"}
    for field in fields:
        assert field["default"] == ""
        assert field["options"][0]["value"] == ""


def test_selector_fields_list_the_configured_resources_rows(monkeypatch):
    monkeypatch.setattr(ps, "_repeater_field", lambda post_type: FIELD)
    resources = {
        "logos-id": _resource(rows=[{"": [], "undefined": "PBF"}, {"": [], "undefined": "CHR"}]),
        "firmas-id": _resource(rows=[{"": [], "undefined": "Laura"}]),
    }
    monkeypatch.setattr(ps, "load_resource_by_id", lambda rid: resources.get(rid))

    fields = ps.selector_fields(
        {
            "logos_firmas_post_type": "logos-firmas",
            "logos_resource_id": "logos-id",
            "firmas_resource_id": "firmas-id",
        }
    )
    logo_field = next(f for f in fields if f["id"] == "logo_row")
    firma_field = next(f for f in fields if f["id"] == "firma_row")

    assert [o["label"] for o in logo_field["options"]] == ["PBF", "CHR"]
    assert [o["label"] for o in firma_field["options"]] == ["Laura"]
    assert logo_field["default"] == "0"


def test_selector_fields_names_a_missing_configured_resource(monkeypatch):
    monkeypatch.setattr(ps, "_repeater_field", lambda post_type: FIELD)
    monkeypatch.setattr(ps, "load_resource_by_id", lambda rid: None)

    fields = ps.selector_fields(
        {
            "logos_firmas_post_type": "logos-firmas",
            "logos_resource_id": "gone",
            "firmas_resource_id": "gone",
        }
    )
    assert "ya no existe" in fields[0]["options"][0]["label"]


# ---------------------------------------------------------------------------
# The generated PDF itself
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_pdf(tmp_path):
    """A real, tiny multi-page PDF built the same way the plugin builds an overlay."""
    from reportlab.pdfgen import canvas

    path = tmp_path / "source.pdf"
    c = canvas.Canvas(str(path), pagesize=(300, 400))
    for text in ("Page one", "Page two", "Page three"):
        c.drawString(50, 350, text)
        c.showPage()
    c.save()
    return path


@pytest.fixture()
def sample_image(tmp_path):
    from PIL import Image

    path = tmp_path / "stamp.png"
    Image.new("RGBA", (40, 20), (10, 20, 30, 255)).save(path)
    return path


def test_a_signed_pdf_keeps_the_source_page_count(tmp_path, sample_pdf, sample_image):
    from pypdf import PdfReader

    destination = tmp_path / "signed.pdf"
    ps.build_signed_pdf(
        sample_pdf,
        destination,
        logo_path=sample_image,
        signature_path=sample_image,
        signer_name="Jhon Fredy León González",
    )

    assert len(PdfReader(str(destination)).pages) == 3


def test_the_footer_link_points_at_the_documentation_site(tmp_path, sample_pdf, sample_image):
    from pypdf import PdfReader

    destination = tmp_path / "signed.pdf"
    ps.build_signed_pdf(
        sample_pdf, destination, logo_path=sample_image, signature_path=sample_image, signer_name="X"
    )

    reader = PdfReader(str(destination))
    for page in reader.pages:
        urls = [
            annot.get_object().get("/A", {}).get("/URI")
            for annot in (page.get("/Annots") or [])
        ]
        assert ps.FOOTER_URL in urls


def test_the_approver_name_is_only_on_the_last_page(tmp_path, sample_pdf, sample_image):
    from pypdf import PdfReader

    destination = tmp_path / "signed.pdf"
    ps.build_signed_pdf(
        sample_pdf,
        destination,
        logo_path=sample_image,
        signature_path=sample_image,
        signer_name="Jhon Fredy Leon Gonzalez",
    )

    reader = PdfReader(str(destination))
    texts = [page.extract_text() for page in reader.pages]
    assert "Jhon Fredy Leon Gonzalez" not in texts[0]
    assert "Jhon Fredy Leon Gonzalez" in texts[-1]


def test_a_single_page_source_gets_both_stamps(tmp_path, sample_image):
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader

    source = tmp_path / "one_page.pdf"
    c = canvas.Canvas(str(source), pagesize=(300, 400))
    c.drawString(50, 350, "Only page")
    c.showPage()
    c.save()

    destination = tmp_path / "signed.pdf"
    ps.build_signed_pdf(
        source, destination, logo_path=sample_image, signature_path=sample_image, signer_name="Ana"
    )

    reader = PdfReader(str(destination))
    assert len(reader.pages) == 1
    assert "Ana" in reader.pages[0].extract_text()


# ---------------------------------------------------------------------------
# The result message
# ---------------------------------------------------------------------------


def test_the_message_names_the_approver():
    assert "Ana" in ps.SignResult(download_path="/x/y.pdf", signer="Ana").as_message()


def test_the_message_stands_alone_without_a_signer():
    result = ps.SignResult(download_path="/x/y.pdf", signer="")
    assert result.as_message()
