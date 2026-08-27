"""KoboConnector — the decisions that decide whether a sync is correct.

Nothing here reaches a Kobo instance or a database. This plugin's real risk is
the one every background importer has: it answers 201, logs "finished", and the
archive quietly fills with the wrong thing. So the pieces under test are the
ones where that happens — how a submission is read, what a question converts to,
which attachment belongs to which question, and where the credential is allowed
to travel.

The fixture is the real form this connector was written for, expressed the way
the Kobo API returns it.
"""

from __future__ import annotations

import pytest

#: SKIPPED WHEN THE PLUGIN IS NOT INSTALLED. `archihub/plugins/*` is gitignored
#: apart from the five that ship with the backend, so this file can be committed
#: while the package it tests is not — importing at module scope would turn a
#: checkout without it into a collection error, which reads as a broken suite
#: rather than as an absent optional component.
plugin = pytest.importorskip(
    "archihub.plugins.KoboConnector",
    reason="KoboConnector is not installed in this checkout",
)

from archihub.plugins.KoboConnector import client, mapping, scaffold, sync  # noqa: E402


def _q(kind, name, label=None):
    return {"type": kind, "name": name, "label": [label or name]}


#: The monthly-report form, as `GET /api/v2/assets/{uid}/` returns it.
FORM = {
    "content": {
        "survey": [
            _q("begin_group", "info_general", "Información general"),
            _q("select_one_from_file personas.csv", "persona_sel", "Seleccione su nombre"),
            _q("text", "nombre_persona", "Nombre"),
            _q("text", "cedula", "Cédula de ciudadanía"),
            _q("integer", "anio_reporte", "Año del reporte"),
            _q("date", "fecha_inicio", "Fecha de inicio"),
            _q("note", "aviso", "Un aviso"),
            _q("end_group", ""),
            _q("begin_repeat", "actividades", "Actividades del mes"),
            _q("date", "act_fecha", "Fecha de la actividad"),
            _q("text", "act_lugar", "Lugar / Municipio"),
            _q("integer", "act_participantes", "Número de participantes"),
            _q("file", "act_archivo", "Archivo de evidencia"),
            _q("image", "act_imagen", "Fotografía"),
            _q("end_repeat", ""),
            _q("file", "anexos_generales", "Documentos anexos"),
            _q("calculate", "total_participantes"),
        ]
    }
}


# ---------------------------------------------------------------------------
# Reading the form
# ---------------------------------------------------------------------------


def test_questions_carry_their_full_group_path():
    """A submission names an answer by its path, so the mapping must too.

    Mapping by bare name works until a form has two groups, at which point the
    same name in each resolves to whichever the loop saw last.
    """
    by_name = {q["name"]: q for q in mapping.survey_questions(FORM)}

    assert by_name["nombre_persona"]["path"] == "info_general/nombre_persona"
    assert by_name["act_lugar"]["path"] == "actividades/act_lugar"
    assert by_name["anexos_generales"]["path"] == "anexos_generales"


def test_structural_rows_and_notes_hold_no_answer():
    names = {q["name"] for q in mapping.survey_questions(FORM)}

    assert "info_general" not in names
    assert "actividades" not in names
    assert "aviso" not in names, "a note is displayed, never answered"


def test_a_repeat_question_knows_which_repeat_it_belongs_to():
    """The settings screen offers only that repeat's questions for a child row.

    Offering the whole form there lets an operator map a top-level answer onto
    every activity, which produces rows that all look correct and all say the
    same thing.
    """
    by_name = {q["name"]: q for q in mapping.survey_questions(FORM)}

    assert by_name["act_fecha"]["repeat"] == "actividades"
    assert by_name["nombre_persona"]["repeat"] == ""
    assert by_name["anexos_generales"]["repeat"] == "", "closing a repeat leaves it"


def test_a_label_is_read_from_either_spelling():
    """Kobo stores a label as a list with several languages and as a plain
    string with one; an XLSForm column arrives as `label::Español`."""
    survey = {
        "content": {
            "survey": [
                {"type": "text", "name": "a", "label": ["Uno"]},
                {"type": "text", "name": "b", "label": "Dos"},
                {"type": "text", "name": "c", "label::Español": "Tres"},
                {"type": "text", "name": "d"},
            ]
        }
    }
    labels = [q["label"] for q in mapping.survey_questions(survey)]

    assert labels == ["Uno", "Dos", "Tres", "d"]


# ---------------------------------------------------------------------------
# Reading a submission
# ---------------------------------------------------------------------------


SUBMISSION = {
    "_id": 41,
    "_uuid": "0d5f-aaa",
    "_submission_time": "2026-03-25T21:12:00",
    "info_general/nombre_persona": "Ana Ruiz",
    "info_general/cedula": "1234567",
    "info_general/anio_reporte": "2026",
    "info_general/fecha_inicio": "2026-01-15",
    "anexos_generales": "planilla marzo.pdf",
    "actividades": [
        {
            "actividades/act_fecha": "2026-03-04",
            "actividades/act_lugar": "Quibdó",
            "actividades/act_participantes": "18",
            "actividades/act_imagen": "foto taller.jpg",
        },
        {
            "actividades/act_fecha": "2026-03-11",
            "actividades/act_lugar": "Istmina",
            "actividades/act_participantes": "7",
        },
    ],
    "_attachments": [
        {
            "download_url": "https://kobo.example.org/media/1/foto_taller.jpg",
            "filename": "someone/attachments/foto_taller.jpg",
            "mimetype": "image/jpeg",
        },
        {
            "download_url": "https://kobo.example.org/media/1/planilla_marzo.pdf",
            "filename": "someone/attachments/planilla_marzo.pdf",
            "mimetype": "application/pdf",
        },
    ],
}


def test_an_answer_is_reachable_by_path_and_by_bare_name():
    values = mapping.flatten(SUBMISSION)

    assert values["info_general/nombre_persona"] == "Ana Ruiz"
    assert values["nombre_persona"] == "Ana Ruiz"


def test_a_name_used_by_two_questions_is_not_bound_at_all():
    """Binding it to whichever came last writes one group's answer into the
    other group's field, and every layer still reports success."""
    values = mapping.flatten({"uno/fecha": "2026-01-01", "dos/fecha": "2026-02-02"})

    assert values["uno/fecha"] == "2026-01-01"
    assert values["dos/fecha"] == "2026-02-02"
    assert "fecha" not in values


def test_a_repeat_is_read_through_repeat_rows_not_flatten():
    """A repeat is a list, and folding it into the flat answers would make it
    unreachable as rows while looking present."""
    assert "actividades" not in mapping.flatten(SUBMISSION)

    rows = mapping.repeat_rows(SUBMISSION, "actividades")
    assert len(rows) == 2
    assert rows[0]["act_lugar"] == "Quibdó"
    assert rows[1]["actividades/act_participantes"] == "7"


def test_an_absent_repeat_is_no_rows_rather_than_a_failure():
    assert mapping.repeat_rows(SUBMISSION, "no_existe") == []
    assert mapping.repeat_rows({}, "actividades") == []


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def test_a_kobo_answer_is_converted_to_what_the_field_type_accepts():
    """Every answer is a string on the wire, and the resource validators reject
    a string where the form declared a number."""
    assert mapping.coerce("18", "number") == 18
    assert isinstance(mapping.coerce("18", "number"), int)
    assert mapping.coerce("1.5", "number") == 1.5
    assert mapping.coerce("Ana", "text") == "Ana"
    assert mapping.coerce("2026-01-15", "simple-date") == "2026-01-15"
    assert mapping.coerce("taller reunion", "select-multiple2") == ["taller", "reunion"]
    assert mapping.coerce("1", "checkbox") is True
    assert mapping.coerce("0", "checkbox") is False


def test_an_unanswered_question_contributes_nothing():
    """`None` means the field is omitted, not written empty.

    A required field satisfied by an empty string passes validation and then
    reads as answered when nobody answered it.
    """
    for empty in (None, "", "   "):
        assert mapping.coerce(empty, "text") is None
    assert mapping.coerce("", "number") is None

    # The one type where "empty" and "no" are different answers, and so the one
    # that shows whether emptiness is handled before the per-type conversion or
    # left to it: a bare `bool("")` writes False, which reads as answered.
    assert mapping.coerce("", "checkbox") is None
    assert mapping.coerce(None, "checkbox") is None


def test_an_unparseable_number_is_dropped_not_raised():
    """One bad answer must not lose the other forty fields of the submission."""
    assert mapping.coerce("no es un número", "number") is None


def test_a_geopoint_becomes_a_point_in_longitude_latitude_order():
    """GeoJSON is longitude first and Kobo sends latitude first. Swapped, every
    imported point lands in a different hemisphere and still renders."""
    point = mapping.coerce("5.6947 -76.6611 0 4", "location")

    assert point == {"type": "Point", "coordinates": [-76.6611, 5.6947]}


def test_a_coordinate_out_of_range_is_refused():
    assert mapping.coerce("120.0 -76.0", "location") is None
    assert mapping.coerce("solo-texto", "location") is None


# ---------------------------------------------------------------------------
# Building the resource body
# ---------------------------------------------------------------------------


FIELDS = [
    {"destiny": "metadata.firstLevel.title", "type": "text", "label": "Nombre"},
    {"destiny": "metadata.firstLevel.description", "type": "text-area", "label": "Descripción"},
    {"destiny": "metadata.firstLevel.date", "type": "simple-date", "label": "Fecha de inicio"},
    {"destiny": "metadata.firstLevel.year", "type": "number", "label": "Año del reporte"},
    {"destiny": "", "type": "file", "label": "Archivos"},
]


def test_a_mapped_answer_is_written_at_its_destiny():
    body = mapping.build_body(
        mapping.flatten(SUBMISSION),
        [
            {"question": "info_general/nombre_persona", "destiny": "metadata.firstLevel.title"},
            {"question": "info_general/anio_reporte", "destiny": "metadata.firstLevel.year"},
            {"question": "info_general/fecha_inicio", "destiny": "metadata.firstLevel.date"},
        ],
        FIELDS,
        "informe",
        "published",
    )

    assert body["post_type"] == "informe"
    assert body["status"] == "published"
    assert body["metadata"]["firstLevel"]["title"] == "Ana Ruiz"
    assert body["metadata"]["firstLevel"]["year"] == 2026, "converted using the field's type"
    assert body["metadata"]["firstLevel"]["date"] == "2026-01-15"


def test_a_question_this_submission_skipped_writes_nothing():
    """A skipped question is not an empty answer. Writing one would overwrite a
    field on every re-import and would satisfy a `required` rule falsely."""
    body = mapping.build_body(
        mapping.flatten(SUBMISSION),
        [{"question": "info_general/no_respondida", "destiny": "metadata.firstLevel.description"}],
        FIELDS,
        "informe",
        "published",
    )

    assert body["metadata"] == {}


def test_a_row_missing_either_half_is_ignored():
    body = mapping.build_body(
        mapping.flatten(SUBMISSION),
        [
            {"question": "", "destiny": "metadata.firstLevel.title"},
            {"question": "info_general/cedula", "destiny": ""},
            "not a row",
        ],
        FIELDS,
        "informe",
        "published",
    )

    assert body["metadata"] == {}


def test_a_suggestion_never_offers_one_field_twice():
    """Two questions matching one destiny would make the second silently
    overwrite the first, and the screen would look correctly filled in.

    The fixture deliberately contains a collision — a form that has none cannot
    tell a de-duplicating suggester from one that does not.
    """
    colliding = [
        {"name": "nombre_persona", "path": "info_general/nombre_persona", "label": "Nombre", "repeat": ""},
        {"name": "nombre_taller", "path": "actividades/nombre_taller", "label": "Nombre", "repeat": ""},
    ]
    suggested = mapping.suggest_mapping(colliding, FIELDS)

    destinies = [row["destiny"] for row in suggested]
    assert destinies == ["metadata.firstLevel.title"], "the second question finds nothing left"
    assert len(destinies) == len(set(destinies))


def test_a_suggestion_over_the_real_form_maps_each_field_once():
    questions = mapping.survey_questions(FORM)
    destinies = [row["destiny"] for row in mapping.suggest_mapping(questions, FIELDS)]

    assert destinies, "the fixture form does share labels with the fixture fields"
    assert len(destinies) == len(set(destinies))


def test_a_suggestion_matches_across_an_accent():
    """The Kobo label and the ArchiHUB label are written by different people;
    "Cédula"/"Cedula" is not a difference either of them intended."""
    suggested = mapping.suggest_mapping(
        [{"name": "cedula", "path": "info_general/cedula", "label": "Cedula", "repeat": ""}],
        [{"destiny": "metadata.firstLevel.title", "type": "text", "label": "Cédula"}],
    )

    assert suggested == [
        {"question": "info_general/cedula", "destiny": "metadata.firstLevel.title"}
    ]


def test_a_file_field_is_never_a_mapping_destination():
    """A file field carries no value; mapping an answer onto it writes a string
    where the file pipeline expects attachments."""
    suggested = mapping.suggest_mapping(
        [{"name": "x", "path": "x", "label": "Archivos", "repeat": ""}], FIELDS
    )

    assert suggested == []


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def test_an_attachment_is_paired_with_the_question_that_produced_it():
    """By stored name, not by position. A submission that skipped one of three
    file questions still lists two attachments, and position pairs them with
    the wrong questions — which is invisible in the result."""
    questions = mapping.survey_questions(FORM)
    found = mapping.attachments_for(SUBMISSION, mapping.flatten(SUBMISSION), questions)

    by_name = {row["filename"]: row["question"] for row in found}
    assert by_name["planilla_marzo.pdf"] == "anexos_generales"


def test_a_filename_with_a_space_still_matches():
    """Kobo stores the answer with spaces already turned into underscores and
    lists the attachment under that stored name. Comparing the two unmangled
    pairs nothing whenever somebody's filename had a space in it."""
    found = mapping.attachments_for(
        {
            "anexos_generales": "planilla marzo.pdf",
            "_attachments": [
                {
                    "download_url": "https://kobo.example.org/m/planilla_marzo.pdf",
                    "filename": "x/planilla_marzo.pdf",
                }
            ],
        },
        {"anexos_generales": "planilla marzo.pdf"},
        [{"name": "anexos_generales", "path": "anexos_generales", "type": "file", "repeat": ""}],
    )

    assert [row["question"] for row in found] == ["anexos_generales"]


def test_an_unmatched_attachment_is_kept_and_marked():
    """It is evidence somebody uploaded. Dropping it because the pairing failed
    loses a file for a bookkeeping reason."""
    found = mapping.attachments_for(
        {
            "_attachments": [
                {"download_url": "https://kobo.example.org/m/suelto.pdf", "filename": "suelto.pdf"}
            ]
        },
        {},
        [],
    )

    assert len(found) == 1
    assert found[0]["question"] == ""


def test_an_attachment_with_no_download_url_is_dropped():
    found = mapping.attachments_for({"_attachments": [{"filename": "x.pdf"}]}, {}, [])

    assert found == []


def test_a_deleted_attachment_is_not_downloaded():
    found = mapping.attachments_for(
        {"_attachments": [{"download_url": "https://k/x.pdf", "filename": "x.pdf", "is_deleted": True}]},
        {},
        [],
    )

    assert found == []


def test_the_question_xpath_wins_over_matching_by_filename():
    """It comes from Kobo beside the file, so it survives the form being edited
    after submissions were made — which is the normal state of a form in use.

    The fixture is a real shape from this connector's own instance: the deployed
    form no longer declares the question these submissions answered, so name
    matching finds nothing and only the xpath identifies it.
    """
    found = mapping.attachments_for(
        {
            "_attachments": [
                {
                    "download_url": "https://kobo.example.org/a/1",
                    "filename": "u/attachments/abc/IMG-20260223-WA0074-14_0_15.jpg",
                    "media_file_basename": "IMG-20260223-WA0074-14_0_15.jpg",
                    "question_xpath": "actividades[2]/act_evidencia",
                }
            ]
        },
        {},
        mapping.survey_questions(FORM),
    )

    assert found[0]["question"] == "actividades/act_evidencia"
    assert found[0]["repeat"] == "actividades"
    assert found[0]["row"] == 1, "Kobo's index is 1-based; a row index here is not"
    assert found[0]["filename"] == "IMG-20260223-WA0074-14_0_15.jpg"


def test_an_attachment_outside_any_repeat_carries_no_row():
    found = mapping.attachments_for(
        {
            "_attachments": [
                {
                    "download_url": "https://kobo.example.org/a/2",
                    "media_file_basename": "planilla.pdf",
                    "question_xpath": "anexos_generales",
                }
            ]
        },
        {},
        mapping.survey_questions(FORM),
    )

    assert found[0]["question"] == "anexos_generales"
    assert found[0]["repeat"] == ""


def test_a_repeat_attachment_is_filed_under_its_own_row():
    """An activity's evidence is about that activity. Attaching every row's
    files to the report keeps them but loses which is which, which is the whole
    value of an evidence attachment."""
    from archihub.plugins.KoboConnector import sync as sync_module

    attachments = [
        {"repeat": "actividades", "row": 0, "filename": "a.jpg"},
        {"repeat": "actividades", "row": 1, "filename": "b.jpg"},
        {"repeat": "", "row": 0, "filename": "anexo.pdf"},
    ]
    for_report, per_row = sync_module._split_attachments(attachments, "actividades", True)

    assert [row["filename"] for row in for_report] == ["anexo.pdf"]
    assert [row["filename"] for row in per_row[0]] == ["a.jpg"]
    assert [row["filename"] for row in per_row[1]] == ["b.jpg"]


def test_without_an_activity_type_every_file_still_reaches_the_report():
    """There would be no resource to attach a row's file to, and dropping it
    would mean switching off the child resources silently discards evidence."""
    from archihub.plugins.KoboConnector import sync as sync_module

    attachments = [{"repeat": "actividades", "row": 0, "filename": "a.jpg"}]

    for_report, per_row = sync_module._split_attachments(attachments, "actividades", False)

    assert [row["filename"] for row in for_report] == ["a.jpg"]
    assert per_row == {}


# ---------------------------------------------------------------------------
# The credential boundary
# ---------------------------------------------------------------------------


@pytest.fixture()
def kobo_env(monkeypatch):
    monkeypatch.setenv("KOBO_HOST", "https://kobo.example.org")
    monkeypatch.setenv("KOBO_API_KEY", "a-token")
    monkeypatch.setenv("KOBO_ALLOWED_HOSTS", "")


def test_the_token_goes_to_the_configured_host(kobo_env):
    assert client.is_allowed("https://kobo.example.org/api/v2/assets/")


def test_the_token_does_not_go_to_a_host_the_response_named(kobo_env):
    """An attachment's download URL arrives inside the API response, which makes
    it data. Without this check a response naming any host at all would be
    handed the API token."""
    assert not client.is_allowed("https://attacker.example.net/media/1.jpg")
    assert not client.is_allowed("https://kobo.example.org.attacker.net/1.jpg")


def test_a_non_http_scheme_is_refused(kobo_env):
    assert not client.is_allowed("file:///etc/passwd")
    assert not client.is_allowed("ftp://kobo.example.org/x")


def test_an_extra_host_is_allowed_only_when_the_operator_names_it(monkeypatch, kobo_env):
    """Older deployments serve submissions from `kf.` and attachments from `kc.`."""
    assert not client.is_allowed("https://kc.kobotoolbox.org/media/1.jpg")

    monkeypatch.setenv("KOBO_ALLOWED_HOSTS", "kc.kobotoolbox.org")
    assert client.is_allowed("https://kc.kobotoolbox.org/media/1.jpg")


def test_an_allowlist_pasted_with_its_scheme_still_works(monkeypatch, kobo_env):
    """The likeliest way to fill this in wrongly, and it would otherwise produce
    an allowlist that matches nothing while looking correct."""
    monkeypatch.setenv("KOBO_ALLOWED_HOSTS", "https://kc.kobotoolbox.org/")

    assert client.is_allowed("https://kc.kobotoolbox.org/media/1.jpg")


def test_a_missing_credential_names_the_variable(monkeypatch):
    monkeypatch.delenv("KOBO_HOST", raising=False)
    monkeypatch.delenv("KOBO_API_KEY", raising=False)
    monkeypatch.setattr(client.config, "read_env_file", lambda slug: {})

    with pytest.raises(client.KoboNotConfigured) as caught:
        client.KoboClient()

    assert "KOBO_HOST" in str(caught.value)


def test_a_host_without_a_scheme_is_refused_rather_than_guessed(monkeypatch):
    """Guessing https for a bare hostname is a guess about where a credential
    travels, and http would send it in the clear."""
    monkeypatch.setenv("KOBO_HOST", "kobo.example.org")
    monkeypatch.setenv("KOBO_API_KEY", "a-token")

    with pytest.raises(client.KoboNotConfigured):
        client.KoboClient()


# ---------------------------------------------------------------------------
# The scheduling contract
# ---------------------------------------------------------------------------


def test_the_task_is_callable_with_no_arguments_at_all():
    """`worker/schedule.py` builds every beat entry with `"args": ()`.

    A schedulable task whose parameters are not all defaulted is accepted by the
    settings screen, scheduled, and then fails once a month with a TypeError
    nobody is watching for.
    """
    import inspect

    signature = inspect.signature(plugin.sync_submissions.run)

    assert all(
        parameter.default is not inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )


def test_the_scheduled_default_is_the_incremental_run():
    """A beat entry passes no arguments, so the default decides what a monthly
    run does. Defaulting to a full run re-reads every submission ever made."""
    import inspect

    assert inspect.signature(plugin.sync_submissions.run).parameters["full"].default is False


def test_the_task_name_is_the_one_the_scheduler_offers():
    """The scheduler lists what the workers registered and stores that string;
    the decorator is what resolves an arriving message. They must agree."""
    from archihub.worker.celery_app import celery_app

    assert plugin.TASK_SYNC == "KoboConnector.sync"
    assert plugin.TASK_SYNC in celery_app.tasks


def test_the_task_does_not_shadow_the_module_it_calls():
    """A module-level `def sync` would rebind the package attribute that the
    `sync` submodule import sets, so `KoboConnector.sync` becomes the task and
    `sync.run` resolves to its wrapped body — a different signature, failing at
    call time inside a background job where nothing is watching."""
    from types import ModuleType

    from archihub.plugins.KoboConnector import sync as submodule

    assert isinstance(submodule, ModuleType)
    assert callable(submodule.run)
    assert plugin.sync_submissions.name == plugin.TASK_SYNC


def test_the_plugin_declares_the_launch_screen_it_relies_on():
    """`settings_lunch` is what the launch screen reads; without `lunch` in
    `type` the plugin never appears there and the button does not exist."""
    assert "lunch" in plugin.plugin_info["type"]
    assert "settings_lunch" in plugin.plugin_info["settings"]


# ---------------------------------------------------------------------------
# Generating the ArchiHUB side
# ---------------------------------------------------------------------------


def _sample(answered=(), long=(), read=1):
    return scaffold.Sample(answered=set(answered), long=set(long), read=read)


def test_a_question_type_becomes_the_field_type_that_stores_it():
    """A date stored as text validates, imports, and then sorts alphabetically
    for the rest of the archive's life."""
    questions = {q["name"]: q for q in mapping.survey_questions(FORM)}
    empty = _sample(read=0)

    def kind(name):
        return scaffold.field_for(questions[name], "", empty, {})["type"]

    assert kind("fecha_inicio") == "simple-date"
    assert kind("anio_reporte") == "number"
    assert kind("nombre_persona") == "text"


def test_a_select_from_file_becomes_text_because_it_has_no_choices():
    """Its options live in a CSV uploaded beside the form, so a select would be
    an empty dropdown that stores nothing and gives no sign why."""
    question = {"name": "persona_sel", "path": "info_general/persona_sel",
                "type": "select_one_from_file", "label": "Persona", "repeat": "", "list": ""}

    assert scaffold.field_for(question, "", _sample(read=0), {})["type"] == "text"


def test_a_select_keeps_its_list_and_loses_the_type_without_one():
    question = {"name": "mes_reporte", "path": "info_general/mes_reporte",
                "type": "select_one", "label": "Mes", "repeat": "", "list": "mes"}

    with_list = scaffold.field_for(question, "", _sample(read=0), {"mes": "abc123"})
    assert with_list["type"] == "select"
    assert with_list["list"] == "abc123"

    without = scaffold.field_for(question, "", _sample(read=0), {})
    assert without["type"] == "text"
    assert "list" not in without


def test_only_the_title_is_generated_required():
    """Requiredness bites on publish, and it applies to the person filling the
    Kobo form in — not to years of submissions made under an earlier version.
    A generated required field refuses exactly the history being imported."""
    questions = mapping.survey_questions(FORM)
    title = "info_general/nombre_persona"
    fields = [scaffold.field_for(q, title, _sample(read=0), {})
              for q in questions if scaffold.is_generatable(q)]

    required = [f["destiny"] for f in fields if f.get("required")]
    assert required == ["metadata.firstLevel.title"]


def test_the_title_field_is_text_whatever_the_question_is():
    """The forms domain refuses any other type on that destiny, so a date chosen
    as the title would make the whole generated form unsavable."""
    question = {"name": "fecha_inicio", "path": "info_general/fecha_inicio",
                "type": "date", "label": "Fecha", "repeat": "", "list": ""}

    field = scaffold.field_for(question, "info_general/fecha_inicio", _sample(read=0), {})
    assert field["destiny"] == "metadata.firstLevel.title"
    assert field["type"] == "text"


def test_destinies_are_namespaced_by_the_kobo_question_name():
    """Two generated forms sharing a destiny with different types is a conflict
    the forms domain refuses across every form on the instance."""
    questions = {q["name"]: q for q in mapping.survey_questions(FORM)}

    assert scaffold.destiny_for(questions["fecha_inicio"], "") == "metadata.firstLevel.fecha_inicio"
    assert scaffold.destiny_for(questions["act_lugar"], "") == "metadata.firstLevel.act_lugar"


def test_a_file_question_does_not_become_a_metadata_field():
    """Its answer is a server-side storage name; the file itself is attached."""
    questions = mapping.survey_questions(FORM)
    names = {q["name"] for q in questions if scaffold.is_generatable(q)}

    assert "act_archivo" not in names
    assert "anexos_generales" not in names
    assert "act_lugar" in names


def test_a_form_gets_one_file_field_per_file_question():
    questions = mapping.survey_questions(FORM)

    fields = scaffold.file_fields_for(questions)
    assert [f["filetag"] for f in fields] == [
        q["name"] for q in questions if q["type"] in mapping.FILE_TYPES
    ]
    assert all(f["type"] == "file" for f in fields)
    assert all(f["filetag"] for f in fields), "the forms domain refuses an untagged file field"

    assert scaffold.file_fields_for(
        [q for q in questions if q["type"] not in mapping.FILE_TYPES]
    ) == []


def test_the_title_prefers_a_question_that_actually_carries_data():
    """A read-only `pulldata()` question is declared like any other and is never
    stored. Chosen as the title, every imported resource has an empty one — and
    a published resource with an empty title is refused outright."""
    questions = [q for q in mapping.survey_questions(FORM) if not q["repeat"]]

    chosen = scaffold.choose_title(questions, _sample(answered={"info_general/cedula"}))

    assert chosen == "info_general/cedula"


def test_an_operators_choice_of_title_wins_over_the_guess():
    questions = [q for q in mapping.survey_questions(FORM) if not q["repeat"]]

    chosen = scaffold.choose_title(
        questions, _sample(answered={"info_general/cedula"}), preferred="info_general/num_contrato"
    )

    assert chosen == "info_general/num_contrato"


def test_a_question_with_no_data_anywhere_is_left_out():
    """This form has six: read-only lookups and a calculated total, all declared
    like ordinary questions. Generating them makes a form of permanently blank
    fields labelled with raw variable names."""
    questions = mapping.survey_questions(FORM)
    sample = _sample(answered={"info_general/cedula", "info_general/nombre_persona"}, read=20)

    kept = {q["name"] for q in scaffold._generatable(questions, "", sample, keep_empty=False)}

    assert kept == {"cedula", "nombre_persona"}


def test_nothing_is_left_out_when_no_submissions_could_be_read():
    """With none — a form deployed but not yet used — "no data" says nothing,
    and generating an empty form would be the worse answer."""
    questions = mapping.survey_questions(FORM)

    kept = scaffold._generatable(questions, "", _sample(read=0), keep_empty=False)

    assert len(kept) > 2


def test_the_operator_can_ask_for_the_empty_questions_anyway():
    questions = mapping.survey_questions(FORM)

    kept = scaffold._generatable(questions, "", _sample(answered={"x"}, read=20), keep_empty=True)

    assert len(kept) > 2


def test_a_long_answer_makes_a_text_area():
    question = {"name": "objeto_contrato", "path": "info_general/objeto_contrato",
                "type": "text", "label": "Objeto", "repeat": "", "list": ""}

    sample = _sample(answered={"info_general/objeto_contrato"},
                     long={"info_general/objeto_contrato"})

    assert scaffold.field_for(question, "", sample, {})["type"] == "text-area"


def test_the_generated_form_passes_the_real_form_validator():
    """The definition is written by this plugin and validated by the forms
    domain, which has rules this module has to satisfy rather than restate: one
    text title, every destiny under metadata, a filetag on the file field."""
    from archihub.api.forms.services import validate_form

    questions = mapping.survey_questions(FORM)
    top = [q for q in questions if not q["repeat"] and scaffold.is_generatable(q)]
    title = scaffold.choose_title(top, _sample(read=0))

    fields = [scaffold.field_for(q, title, _sample(read=0), {}) for q in top]
    fields.extend(scaffold.file_fields_for(questions))

    validate_form({"name": "Kobo test", "fields": fields})


def test_sampling_reads_answers_inside_repeat_rows_too():
    """A repeat's answers are nested, so a walk that only reads the top level
    reports every activity question as never answered — and leaves them all out
    of the generated form."""
    sample = scaffold.Sample()
    scaffold._absorb(SUBMISSION, sample)

    assert "info_general/cedula" in sample.answered
    assert "actividades/act_lugar" in sample.answered


def test_a_choice_answer_is_stored_as_its_label():
    """A submission carries "03"; a catalogue entry saying "03" means nothing
    outside the form that produced it, and a dropdown built from the labels
    matches none of them."""
    choices = mapping.choice_labels(
        {"content": {"choices": [
            {"list_name": "mes", "name": "02", "label": ["Febrero"]},
            {"list_name": "mes", "name": "03", "label": ["Marzo"]},
        ]}}
    )
    questions = [{"name": "mes_reporte", "path": "info_general/mes_reporte",
                  "type": "select_one", "label": "Mes", "repeat": "", "list": "mes"}]

    body = mapping.build_body(
        {"info_general/mes_reporte": "03"},
        [{"question": "info_general/mes_reporte", "destiny": "metadata.firstLevel.month"}],
        [{"destiny": "metadata.firstLevel.month", "type": "text", "label": "Mes"}],
        "informe",
        "published",
        questions,
        choices,
    )

    assert body["metadata"]["firstLevel"]["month"] == "Marzo"


def test_a_choice_the_list_no_longer_declares_is_kept_as_it_is():
    """A choice removed from the form after somebody answered it is still what
    they answered; replacing it with nothing loses the answer."""
    questions = [{"name": "m", "path": "m", "type": "select_one", "label": "M", "repeat": "", "list": "mes"}]

    body = mapping.build_body(
        {"m": "99"},
        [{"question": "m", "destiny": "metadata.firstLevel.month"}],
        [{"destiny": "metadata.firstLevel.month", "type": "text", "label": "M"}],
        "informe", "published", questions, {"mes": {"03": "Marzo"}},
    )

    assert body["metadata"]["firstLevel"]["month"] == "99"


# ---------------------------------------------------------------------------
# The title a resource is published with
# ---------------------------------------------------------------------------


def test_a_resource_with_no_mapped_title_is_still_publishable():
    """The write path REFUSES a published resource whose title is empty when the
    form marks it required, which a generated form does. Without a fallback, a
    form whose title question is a read-only calculation fails every submission
    and the run reports every one of them as an error."""
    from archihub.plugins.KoboConnector import sync as sync_module

    body = {"metadata": {}}
    invented = sync_module._ensure_title(body, {"_id": 41, "_submission_time": "2026-03-25T21:12:00"})

    assert invented is True
    assert body["metadata"]["firstLevel"]["title"]
    assert "41" in body["metadata"]["firstLevel"]["title"]


def test_a_mapped_title_is_never_replaced():
    from archihub.plugins.KoboConnector import sync as sync_module

    body = {"metadata": {"firstLevel": {"title": "Ana Ruiz"}}}
    invented = sync_module._ensure_title(body, {"_id": 41})

    assert invented is False
    assert body["metadata"]["firstLevel"]["title"] == "Ana Ruiz"


# ---------------------------------------------------------------------------
# What the plugin publishes about itself
# ---------------------------------------------------------------------------


def test_the_manifest_is_a_pure_literal():
    """The plugins listing parses this file with `ast` and never executes it, so
    a name reference or an unpacking resolves for the settings route and reads as
    ABSENT for the listing — one plugin giving two different answers about
    itself, with nothing reporting the difference."""
    import ast
    import pathlib

    source = pathlib.Path(plugin.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", "") == "plugin_info" for target in node.targets
        ):
            assert ast.literal_eval(node.value) == plugin.plugin_info
            return
    raise AssertionError("plugin_info was not found")


def test_every_settings_field_publishes_a_name_as_well_as_an_id():
    """The plugin screens read a field's key from `name` in some places and from
    `id` in others, so a field carrying only one of them loses its value in
    whichever screen reads the other — silently, as an unsaved setting."""
    declared = [
        entry
        for group in plugin.plugin_info["settings"].values()
        for entry in group
        if entry.get("id")
    ]
    assert declared, "the fixture would otherwise prove nothing"

    payload, _status = plugin._with_field_names((declared, 200))

    for entry in payload:
        assert entry.get("name") == entry["id"], entry


def test_the_launch_screen_offers_every_action_the_plugin_declares():
    """An action the route handles but the screen does not offer is unreachable
    — nothing renders a button for it and nothing reports its absence."""
    actions = plugin.plugin_info["settings"]["settings_lunch"][1]
    declared = {
        value for name, value in vars(plugin).items()
        if name.startswith("ACTION_") and isinstance(value, str)
    }

    assert actions["id"] == "action"
    assert declared
    assert {option["value"] for option in actions["options"]} == declared


# ---------------------------------------------------------------------------
# What the settings screen can and cannot answer for the operator
# ---------------------------------------------------------------------------


def _select_entries(payload):
    return [e for e in payload if isinstance(e, dict) and e.get("type") == "select"]


def _rendered(monkeypatch, stored, kobo_form=None, types=(("informe", "Informe"),), fields=()):
    """Render the settings screen without a Kobo instance or a database."""
    from archihub.plugins.KoboConnector import client as kobo_client

    built = plugin.KoboConnector(plugin.SLUG, plugin.plugin_info)
    monkeypatch.setattr(built, "get_plugin_settings", lambda: stored)
    monkeypatch.setattr(plugin, "_kobo_form", lambda uid: kobo_form or {})
    monkeypatch.setattr(plugin, "_available_forms", lambda: ([{"value": "aX", "label": "F"}], ""))
    monkeypatch.setattr(
        plugin, "_content_types", lambda: [{"value": s, "label": l} for s, l in types]
    )
    monkeypatch.setattr(plugin, "_form_fields", lambda slug: list(fields))
    assert kobo_client  # imported for the patch target to be meaningful
    payload, _status = built.settings_payload("settings")
    return payload


def test_every_select_offers_a_value_it_can_already_be(monkeypatch):
    """The interface's select control CHANGES a value that is not among its
    options to the first option, and reports that as the operator's choice.

    So a picker with no empty entry answers its own question: an optional
    content type nobody chose is saved, acted on, and blamed on the person who
    opened the screen.
    """
    payload = _rendered(monkeypatch, {}, types=(("carpeta", "Carpeta"), ("informe", "Informe")))

    for entry in _select_entries(payload):
        values = {option["value"] for option in entry["options"]}
        assert entry.get("default", "") in values, entry["id"]


def test_an_optional_picker_does_not_default_to_the_first_content_type(monkeypatch):
    payload = _rendered(monkeypatch, {}, types=(("carpeta", "Carpeta"), ("informe", "Informe")))

    activity = next(e for e in _select_entries(payload) if e["id"] == "activity_post_type")
    assert activity["default"] == ""
    assert activity["options"][0]["value"] == ""


def test_a_declared_default_survives_being_given_its_options(monkeypatch):
    """Overwriting it would present a field as unanswered when it is not."""
    payload = _rendered(monkeypatch, {})

    status = next(e for e in _select_entries(payload) if e["id"] == "resource_status")
    assert status["default"] == "published"


def test_a_stored_value_that_is_no_longer_offered_is_kept(monkeypatch):
    """A content type renamed here would otherwise drop out of the picker, and
    the control would substitute the first option - silently repointing the sync
    at a different content type."""
    payload = _rendered(monkeypatch, {"post_type": "borrado"})

    post_type = next(e for e in _select_entries(payload) if e["id"] == "post_type")
    assert post_type["default"] == "borrado"
    assert "borrado" in {option["value"] for option in post_type["options"]}


def test_the_repeat_picker_lists_groups_declared_by_the_form(monkeypatch):
    """By its own label. The groups come from the form's structural rows, which
    the question list drops - so deriving them from questions leaves the picker
    empty for a form whose repeat questions were all filtered out."""
    payload = _rendered(monkeypatch, {"form_uid": "aX"}, kobo_form=FORM)

    repeat = next(e for e in _select_entries(payload) if e["id"] == "repeat_group")
    assert {o["value"] for o in repeat["options"]} == {"", "actividades"}
    assert next(o for o in repeat["options"] if o["value"] == "actividades")["label"] == (
        "Actividades del mes"
    )


def test_saving_does_not_refuse_on_fields_that_cannot_be_answered_yet():
    """The pickers a cross-field rule would check are filled from the Kobo form
    named in the settings being saved. On the save that first names a form they
    are all still empty, so such a rule refuses the one save that would let them
    be populated - and the screen can never be saved at all.
    """
    built = plugin.KoboConnector(plugin.SLUG, plugin.plugin_info)
    stored = {}
    built.set_plugin_settings = stored.update

    payload, status = built.save_settings(
        {"form_uid": "aX", "post_type": "informe", "activity_post_type": "carpeta",
         "repeat_group": ""}
    )

    assert status == 200, payload
    assert stored["form_uid"] == "aX"


# ---------------------------------------------------------------------------
# Resolving the repeat group at run time
# ---------------------------------------------------------------------------


def test_a_form_with_one_repeat_group_needs_no_setting():
    """The setting only has to be answered when the answer is ambiguous."""
    from archihub.plugins.KoboConnector import sync as sync_module

    report = sync_module.RunReport()
    resolved = sync_module.resolve_repeat({}, FORM, "actividad", report)

    assert resolved == "actividades"
    assert report.errors == []


def test_an_activity_type_with_no_repeat_anywhere_is_reported():
    """It creates no child resources at all, and a run finishing "imported 40"
    while forty reports lost their activities is the failure this module is
    arranged to avoid."""
    from archihub.plugins.KoboConnector import sync as sync_module

    report = sync_module.RunReport()
    resolved = sync_module.resolve_repeat({}, {"content": {"survey": []}}, "actividad", report)

    assert resolved == ""
    assert report.errors, "a configuration that cannot produce anything must say so"


def test_several_repeat_groups_ask_the_operator_to_choose():
    from archihub.plugins.KoboConnector import sync as sync_module

    two = {"content": {"survey": [
        {"type": "begin_repeat", "name": "a", "label": ["A"]},
        {"type": "text", "name": "x", "label": ["X"]},
        {"type": "end_repeat", "name": ""},
        {"type": "begin_repeat", "name": "b", "label": ["B"]},
        {"type": "text", "name": "y", "label": ["Y"]},
        {"type": "end_repeat", "name": ""},
    ]}}
    report = sync_module.RunReport()

    assert sync_module.resolve_repeat({}, two, "actividad", report) == ""
    assert report.errors


def test_the_operators_choice_is_never_overridden():
    from archihub.plugins.KoboConnector import sync as sync_module

    report = sync_module.RunReport()
    resolved = sync_module.resolve_repeat({"repeat_group": "actividades"}, FORM, "actividad", report)

    assert resolved == "actividades"


def test_a_chosen_repeat_the_form_does_not_have_is_reported():
    from archihub.plugins.KoboConnector import sync as sync_module

    report = sync_module.RunReport()
    sync_module.resolve_repeat({"repeat_group": "inexistente"}, FORM, "actividad", report)

    assert report.errors


def test_no_activity_type_means_no_repeat_and_no_complaint():
    """Not wanting child resources is a legitimate choice, not a misconfiguration."""
    from archihub.plugins.KoboConnector import sync as sync_module

    report = sync_module.RunReport()

    assert sync_module.resolve_repeat({}, FORM, "", report) == ""
    assert report.errors == []


def test_the_row_pickers_describe_the_only_repeat_before_one_is_chosen(monkeypatch):
    """Otherwise they describe the TOP level, offering questions no row has —
    a mapping that looks filled in and produces nothing on every activity."""
    payload = _rendered(monkeypatch, {"form_uid": "aX"}, kobo_form=FORM)

    picker = next(e for e in _select_entries(payload) if e["id"] == "activity_title_question")
    offered = {o["value"] for o in picker["options"] if o["value"]}

    assert offered
    assert all(value.startswith("actividades/") for value in offered)


def test_the_row_pickers_stay_empty_when_the_repeat_is_ambiguous(monkeypatch):
    """With two groups, describing either would describe the wrong rows."""
    two = {"content": {"survey": [
        {"type": "begin_repeat", "name": "a", "label": ["A"]},
        {"type": "text", "name": "x", "label": ["X"]},
        {"type": "end_repeat", "name": ""},
        {"type": "begin_repeat", "name": "b", "label": ["B"]},
        {"type": "text", "name": "y", "label": ["Y"]},
        {"type": "end_repeat", "name": ""},
    ]}}
    payload = _rendered(monkeypatch, {"form_uid": "aX"}, kobo_form=two)

    picker = next(e for e in _select_entries(payload) if e["id"] == "activity_title_question")
    assert {o["value"] for o in picker["options"]} == {""}


# ---------------------------------------------------------------------------
# Long labels
# ---------------------------------------------------------------------------

#: A real Kobo label, hint and all, on one question. Kobo has no separate hint
#: column here — the guidance is part of the label — so this arrives as a single
#: string with the form's own line break in it.
LONG = (
    "Documentos anexos adicionales\n"
    "Si tiene documentos generales que no correspondan a una actividad "
    "concreta, adjúntelos aquí"
)

LONG_FORM = {
    "content": {
        "survey": [
            {"type": "text", "name": "docs_extra", "label": [LONG]},
            _q("text", "corto", "Nombre"),
        ]
    }
}


def test_a_long_label_becomes_one_short_line():
    short = mapping.short_label(LONG)

    assert "\n" not in short
    assert len(short) <= mapping.LABEL_LIMIT + 1  # the ellipsis
    assert short.endswith("…")
    assert " ".join(LONG.split()).startswith(short[:-1].rstrip())


def test_a_label_is_cut_between_words_not_inside_one():
    """A word split in half reads as a typo rather than as a truncation."""
    short = mapping.short_label("a" + " palabra" * 20)

    assert short.rstrip("…").rstrip().endswith("palabra")


def test_a_label_that_already_fits_is_returned_unchanged():
    assert mapping.short_label("Nombre") == "Nombre"
    assert mapping.short_label("") == ""


def test_a_label_of_one_unbroken_word_is_still_cut():
    """There is no word boundary to cut on, and the row still has to fit."""
    short = mapping.short_label("x" * 200)

    assert len(short) <= mapping.LABEL_LIMIT + 1


def test_a_picker_shortens_the_label_and_never_the_value(monkeypatch):
    """The label is what a person reads; the path is what the sync resolves."""
    payload = _rendered(monkeypatch, {"form_uid": "aX"}, kobo_form=LONG_FORM)

    picker = next(e for e in _select_entries(payload) if e["id"] == "title_question")
    options = [o for o in picker["options"] if o["value"]]

    assert {o["value"] for o in options} == {"docs_extra", "corto"}
    assert all(len(o["label"]) <= mapping.LABEL_LIMIT + 40 for o in options)
    assert all("\n" not in o["label"] for o in options)


def test_a_mapping_row_shortens_both_of_its_pickers(monkeypatch):
    """The row is two selects side by side, so either one can overflow it."""
    long_field = {"destiny": "metadata.firstLevel.x", "type": "text", "label": LONG}
    payload = _rendered(
        monkeypatch,
        {"form_uid": "aX", "post_type": "informe"},
        kobo_form=LONG_FORM,
        fields=[long_field],
    )

    table = next(
        e for e in payload if isinstance(e, dict) and e.get("id") == "field_map"
    )
    for field in table["fields"]:
        for option in field["options"]:
            assert "\n" not in option["label"]
            assert len(option["label"]) <= mapping.LABEL_LIMIT + 40

    destiny = next(f for f in table["fields"] if f["id"] == "destiny")
    assert [o["value"] for o in destiny["options"]] == ["metadata.firstLevel.x"]


def test_a_generated_field_keeps_the_full_question_as_its_instructions():
    """Shortening is for the label only — the guidance must not be thrown away."""
    question = mapping.survey_questions(LONG_FORM)[0]
    field = scaffold.field_for(question, "corto", _sample(read=1), {})

    assert field["label"] == mapping.short_label(LONG)
    assert field["instructions"] == LONG


def test_a_field_whose_label_already_fits_carries_no_instructions():
    """An instructions icon on a field that explains nothing is noise."""
    question = mapping.survey_questions(LONG_FORM)[1]
    field = scaffold.field_for(question, "otro", _sample(read=1), {})

    assert field["label"] == "Nombre"
    assert "instructions" not in field


def test_a_suggestion_matches_a_field_generated_from_a_long_label():
    """The generated field's label is the SHORTENED one, so the two strings
    differ — a suggestion comparing only full labels would offer nothing for
    exactly the questions whose labels are hardest to match by eye."""
    question = mapping.survey_questions(LONG_FORM)[0]
    field = scaffold.field_for(question, "corto", _sample(read=1), {})

    assert mapping.suggest_mapping([question], [field]) == [
        {"question": "docs_extra", "destiny": field["destiny"]}
    ]


# ---------------------------------------------------------------------------
# Which file field an attachment lands under
# ---------------------------------------------------------------------------


def _file_field(**over):
    field = {"type": "file", "destiny": "", "label": "Archivos", "filetag": "Evidencias"}
    field.update(over)
    return field


def test_the_form_file_field_names_the_tag():
    assert mapping.file_tag([_file_field(), {"type": "text", "destiny": "metadata.x"}]) == "Evidencias"


def test_a_file_field_with_no_tag_is_addressed_by_its_label():
    """The interface matches a file's tag against the tag, the label and the
    destiny, so a field carrying only a label is still addressable."""
    assert mapping.file_tag([_file_field(filetag="", label="Documentos anexos")]) == "Documentos anexos"


def test_a_form_with_no_file_field_names_no_tag():
    assert mapping.file_tag([{"type": "text", "destiny": "metadata.x"}]) == ""
    assert mapping.file_tag([]) == ""


def test_the_first_file_field_wins():
    assert mapping.file_tag([_file_field(filetag="Fotos"), _file_field(filetag="Actas")]) == "Fotos"


def _q(name, path=None, label="", kind="image"):
    return {"name": name, "path": path or name, "type": kind, "label": label, "repeat": ""}


def test_a_file_question_goes_to_the_field_that_names_it():
    filing = mapping.filing(
        [_file_field(filetag="act_archivo"), _file_field(filetag="act_imagen")],
        [_q("act_archivo", "actividades/act_archivo", kind="file"), _q("act_imagen", "actividades/act_imagen")],
    )

    assert filing.tag_for("actividades/act_imagen") == "act_imagen"
    assert filing.tag_for("actividades/act_archivo") == "act_archivo"


def test_a_field_names_its_question_ignoring_case_accents_and_underscores():
    """Both halves are typed by hand, in a settings screen and a Kobo form, so
    the two spellings of one name must not decide where a file is shown."""
    filing = mapping.filing(
        [_file_field(filetag="otro"), _file_field(filetag="Act Fotografía")],
        [_q("act_fotografia", "actividades/act_fotografia")],
    )

    # Second, so the fallback to the first field cannot supply the answer.
    assert filing.tag_for("actividades/act_fotografia") == "Act Fotografía"


def test_a_field_is_matched_by_its_label_or_destiny_too():
    by_label = mapping.filing(
        [_file_field(filetag="", label="anexos_generales")], [_q("anexos_generales", kind="file")]
    )
    by_destiny = mapping.filing(
        [_file_field(filetag="", label="", destiny="anexos_generales")],
        [_q("anexos_generales", kind="file")],
    )

    assert by_label.tag_for("anexos_generales") == "anexos_generales"
    assert by_destiny.tag_for("anexos_generales") == "anexos_generales"


def test_a_question_no_field_claims_falls_back_to_the_first_file_field():
    """A partly configured form still shows every file. Dropping the fallback
    would tag them with nothing and attach them under no field at all."""
    filing = mapping.filing(
        [_file_field(filetag="act_archivo"), _file_field(filetag="act_imagen")],
        [_q("act_video", "actividades/act_video", kind="video")],
    )

    assert filing.tag_for("actividades/act_video") == "act_archivo"
    assert filing.tag_for("") == "act_archivo"


def test_a_file_field_no_question_matched_is_named(monkeypatch):
    """A tag with a typo in it is the failure this reports: every file quietly
    goes to the other field and the misspelt one stays empty forever."""
    filing = mapping.filing(
        [_file_field(filetag="act_archivo"), _file_field(filetag="act_aimagen")],
        [_q("act_archivo", kind="file"), _q("act_imagen")],
    )

    assert filing.unclaimed == ("act_aimagen",)
    assert filing.tags == frozenset({"act_archivo", "act_aimagen"})


def test_a_lone_file_field_is_never_reported_as_unmatched():
    """Everything reaches it by fallback, so its name misdirects nothing and
    saying otherwise trains an operator to ignore the line that does matter."""
    filing = mapping.filing([_file_field(filetag="kobo_anexos")], [_q("anexos", kind="file")])

    assert filing.tag_for("anexos") == "kobo_anexos"
    assert filing.unclaimed == ()


def test_a_run_reports_a_file_field_no_question_matched():
    report = sync.RunReport(imported=1, unclaimed_fields=["act_aimagen"])

    assert "act_aimagen" in report.as_message()
    assert "act_aimagen" not in sync.RunReport(imported=1).as_message()


def test_each_downloaded_file_is_tagged_for_the_field_that_shows_it(tmp_path, monkeypatch):
    from archihub.core import files as filestore

    monkeypatch.setattr(filestore, "dated_directory", lambda root: tmp_path)

    class _Api:
        def download_attachment(self, url, destination):
            destination.write_bytes(b"x")

    filing = mapping.filing(
        [_file_field(filetag="act_archivo"), _file_field(filetag="act_imagen")],
        [
            _q("act_archivo", "actividades/act_archivo", kind="file"),
            _q("act_imagen", "actividades/act_imagen"),
        ],
    )
    attachments = [
        {"filename": "a.pdf", "url": "https://k/a.pdf", "question": "actividades/act_archivo"},
        {"filename": "b.jpg", "url": "https://k/b.jpg", "question": "actividades/act_imagen"},
    ]

    incoming, skipped, unfiled = sync._download(_Api(), attachments, filing)

    assert [f.tag for f in incoming] == ["act_archivo", "act_imagen"]
    assert (skipped, unfiled) == (0, 0)


def test_refiling_leaves_a_file_the_form_already_shows_alone(monkeypatch):
    """Refiling has no Kobo question to match on, so it must not collapse files
    that an import already filed per question onto the form's first field."""
    resources = {
        RID: {
            "post_type": "kobo",
            "filesObj": [{"id": "1", "tag": "act_imagen"}, {"id": "2", "tag": "otro"}],
        }
    }
    fake = _refiling(
        monkeypatch,
        resources,
        [_file_field(filetag="act_archivo"), _file_field(filetag="act_imagen")],
    )

    report = sync.refile({"form_uid": "aX", "post_type": "kobo"})

    assert resources[RID]["filesObj"] == [
        {"id": "1", "tag": "act_imagen"},
        {"id": "2", "tag": "act_archivo"},
    ]
    assert (report.resources, report.files) == (1, 1)
    assert fake.writes == [RID]


PARENT_ID = "6a8efafdd2da3fa88b85fc75"


def test_a_child_resource_names_its_parent_in_the_shape_the_write_path_reads(monkeypatch):
    """The write path reads `parent` as a list of entries carrying an id. A
    bare id is not refused: it is iterated as a sequence of characters, none of
    which carries one, so the whole set reads as absent - and the activity is
    created, answered 201, and filed under nothing."""
    from archihub.api.resources import hierarchy, write

    created = []

    def _create(body, user, incoming_files=None):
        created.append(body)
        return {"id": "6a8efafdd2da3fa88b85fc99"}, 201

    monkeypatch.setattr(write, "create", _create)

    activity = sync.Target(
        "kobo_activities",
        [{"type": "text", "destiny": "metadata.firstLevel.title"}],
        mapping.filing([], []),
    )
    ids = sync._import_activities(
        object(),
        {"_uuid": "u1", "actividades": [{"actividades/act_nombre": "Taller"}]},
        mapping.survey_questions(FORM),
        {},
        {
            "repeat_group": "actividades",
            "resource_status": "published",
            "activity_map": [
                {"question": "actividades/act_nombre", "destiny": "metadata.firstLevel.title"}
            ],
        },
        activity,
        PARENT_ID,
        {},
        "alice",
        sync.RunReport(),
    )

    assert len(ids) == 1

    monkeypatch.setattr(hierarchy, "ancestors", lambda resource_id: [])
    monkeypatch.setattr(hierarchy, "_post_type_of", lambda resource_id: "kobo")
    monkeypatch.setattr(hierarchy, "_check_parent_is_acceptable", lambda child, parent: None)

    resolved = hierarchy.validate_parent(dict(created[0]))

    assert [entry["id"] for entry in resolved["parent"]] == [PARENT_ID]
    assert [entry["id"] for entry in resolved["parents"]] == [PARENT_ID]


def test_the_parent_chosen_in_the_settings_is_named_the_same_way(monkeypatch):
    """The same shape and the same silent failure one level up: a report filed
    under a parent chosen in the settings reaches the write path unreadable."""
    from archihub.api.resources import hierarchy, write

    created = []

    def _create(body, user, incoming_files=None):
        created.append(body)
        return {"id": "6a8efafdd2da3fa88b85fc99"}, 201

    monkeypatch.setattr(write, "create", _create)
    monkeypatch.setattr(sync, "_stamp", lambda *a, **k: None)
    monkeypatch.setattr(sync, "record_import", lambda *a, **k: None)

    target = sync.Target(
        "kobo", [{"type": "text", "destiny": "metadata.firstLevel.title"}], mapping.filing([], [])
    )
    sync._import_one(
        object(),
        {"_uuid": "u1", "info_general/persona_sel": "Ana"},
        mapping.survey_questions(FORM),
        {},
        {
            "parent": PARENT_ID,
            "resource_status": "published",
            "field_map": [
                {"question": "info_general/persona_sel", "destiny": "metadata.firstLevel.title"}
            ],
        },
        target,
        sync.Target("", [], mapping.filing([], [])),
        "alice",
        sync.RunReport(),
    )

    monkeypatch.setattr(hierarchy, "ancestors", lambda resource_id: [])
    monkeypatch.setattr(hierarchy, "_post_type_of", lambda resource_id: "carpeta")
    monkeypatch.setattr(hierarchy, "_check_parent_is_acceptable", lambda child, parent: None)

    resolved = hierarchy.validate_parent(dict(created[0]))

    assert [entry["id"] for entry in resolved["parent"]] == [PARENT_ID]


#: Both content types declare a field named for the same repeat question, which
#: is what an operator gets by copying a form. Only the activity may claim it.
REPORT_FIELDS = [_file_field(filetag="anexos_generales"), _file_field(filetag="act_imagen")]
ACTIVITY_FIELDS = [_file_field(filetag="act_archivo"), _file_field(filetag="act_imagen")]


def _split(**settings):
    report = sync.RunReport()
    target, activity = sync._targets(
        mapping.survey_questions(FORM),
        REPORT_FIELDS,
        "kobo",
        ACTIVITY_FIELDS,
        settings.pop("activity_post_type", "kobo_activities"),
        settings,
        report,
    )
    return target, activity, report


def test_a_repeat_groups_file_questions_are_filed_against_the_activity_form():
    target, activity, report = _split(repeat_group="actividades")

    assert activity.filing.tag_for("actividades/act_imagen") == "act_imagen"
    assert target.filing.tag_for("anexos_generales") == "anexos_generales"
    # The report's filing must not be offered the repeat's questions: its own
    # `act_imagen` field would claim them, and the row's resource owns them.
    assert target.filing.by_question == {"anexos_generales": "anexos_generales"}
    assert report.unclaimed_fields == ["act_imagen"]


def test_without_an_activity_type_every_file_question_is_filed_on_the_report():
    """`_split_attachments` sends everything to the report when there is no
    child resource to own it, so the report's filing has to cover it."""
    target, activity, _report = _split(repeat_group="actividades", activity_post_type="")

    assert target.filing.tag_for("actividades/act_imagen") == "act_imagen"
    assert target.filing.tag_for("actividades/act_archivo") == "anexos_generales"
    assert activity.filing.by_question == {}


def test_a_split_reports_the_activity_fields_no_question_matched():
    _target, _activity, report = _split(repeat_group="")

    assert sorted(report.unclaimed_fields) == ["act_archivo", "act_imagen"]


def test_a_generated_form_shows_every_file_it_imports():
    """The two halves have to agree. The scaffold declares each field's tag and
    the import writes the file's; a file whose tag no field claims is attached
    to the resource and rendered under no field at all."""
    questions = mapping.survey_questions(FORM)
    fields = scaffold.file_fields_for(questions)

    filing = mapping.filing(fields, questions)

    assert filing.unclaimed == ()
    for question in questions:
        if question["type"] in mapping.FILE_TYPES:
            assert filing.tag_for(question["path"]) == question["name"]


def test_a_file_the_form_cannot_show_is_attached_anyway_and_counted(tmp_path, monkeypatch):
    """Counted where it happens, not inferred from the settings: the count is
    the only sign an operator gets that a stored file appears nowhere."""
    from archihub.core import files as filestore

    monkeypatch.setattr(filestore, "dated_directory", lambda root: tmp_path)

    class _Api:
        def download_attachment(self, url, destination):
            destination.write_bytes(b"x")

    attachments = [{"filename": "a.pdf", "url": "https://k/a.pdf", "question": "anexos"}]

    incoming, skipped, unfiled = sync._download(_Api(), attachments, mapping.filing([], []))
    assert (len(incoming), skipped, unfiled) == (1, 0, 1)

    incoming, skipped, unfiled = sync._download(
        _Api(), attachments, mapping.filing([_file_field()], [])
    )
    assert (len(incoming), skipped, unfiled) == (1, 0, 0)
    # A form field's tag, never the Kobo question's: the interface groups on it,
    # so a file tagged with the question is stored and shown under no field.
    assert incoming[0].tag == "Evidencias"


def test_a_run_reports_the_files_no_field_can_show():
    report = sync.RunReport(imported=1, attachments=3, attachments_unfiled=3)

    assert "no file field" in report.as_message()


def test_a_run_that_filed_everything_says_nothing_about_it():
    report = sync.RunReport(imported=1, attachments=3)

    assert "no file field" not in report.as_message()


# ---------------------------------------------------------------------------
# Refiling what is already imported
# ---------------------------------------------------------------------------


class _FakeMongo:
    """Just enough of the wrapper for the refiling walk, counting its writes."""

    def __init__(self, resources, rows=None):
        self.resources = resources
        self.rows = rows or [{"resourceId": rid, "activityIds": []} for rid in resources]
        self.writes = []

    def get_all_records(self, collection, filters=None, fields=None, **kwargs):
        return list(self.rows)

    def get_record(self, collection, filters=None, fields=None):
        return self.resources.get(str(filters["_id"]))

    def update_record_operator(self, collection, filters, operator, **kwargs):
        self.writes.append(str(filters["_id"]))
        self.resources[str(filters["_id"])].update(operator["$set"])


def _refiling(monkeypatch, resources, fields, rows=None):
    from archihub.api.types import services as type_services

    fake = _FakeMongo(resources, rows)
    monkeypatch.setattr(sync, "_mongo", lambda: fake)
    monkeypatch.setattr(type_services, "get_metadata", lambda slug: {"fields": fields})
    return fake


RID = "6a8efafdd2da3fa88b85fc75"


def test_refiling_moves_a_file_onto_the_field_that_can_show_it(monkeypatch):
    resources = {
        RID: {"post_type": "kobo", "filesObj": [{"id": "1", "tag": "act_imagen"}]}
    }
    fake = _refiling(monkeypatch, resources, [_file_field(filetag="kobo_anexos")])

    report = sync.refile({"form_uid": "aX", "post_type": "kobo"})

    assert resources[RID]["filesObj"] == [{"id": "1", "tag": "kobo_anexos"}]
    assert (report.resources, report.files, report.unfiled) == (1, 1, 0)
    assert fake.writes == [RID]


def test_refiling_twice_changes_nothing_the_second_time(monkeypatch):
    """The operator will press it again — it must not be a one-way door, and a
    second pass must not rewrite every resource in the archive for no change."""
    resources = {
        RID: {"post_type": "kobo", "filesObj": [{"id": "1", "tag": "act_imagen"}]}
    }
    fake = _refiling(monkeypatch, resources, [_file_field(filetag="kobo_anexos")])

    sync.refile({"form_uid": "aX", "post_type": "kobo"})
    second = sync.refile({"form_uid": "aX", "post_type": "kobo"})

    assert fake.writes == [RID], "the second pass wrote again"
    assert (second.resources, second.files) == (0, 0)


def test_refiling_reports_what_it_still_cannot_place(monkeypatch):
    """With no file field there is nowhere to put them, and saying so is the
    whole value — the files are attached and invisible either way."""
    resources = {
        RID: {"post_type": "kobo", "filesObj": [{"id": "1", "tag": "act_imagen"}]}
    }
    _refiling(monkeypatch, resources, [{"type": "text", "destiny": "metadata.x"}])

    report = sync.refile({"form_uid": "aX", "post_type": "kobo"})

    assert report.unfiled == 1
    assert "no field" in report.as_message()


def test_refiling_keeps_everything_else_on_the_file_entry(monkeypatch):
    """The entry carries the record id the resource is attached through."""
    resources = {
        RID: {"post_type": "kobo", "filesObj": [{"id": "abc", "tag": "act_imagen"}]}
    }
    _refiling(monkeypatch, resources, [_file_field(filetag="kobo_anexos")])

    sync.refile({"form_uid": "aX", "post_type": "kobo"})

    assert resources[RID]["filesObj"][0]["id"] == "abc"


RID2 = "6a8efafdd2da3fa88b85fc76"


class _FakeLedger:
    """Ledger rows and the resources they name, counting deletions."""

    def __init__(self, rows, resources):
        self.rows = rows
        self.resources = resources
        self.deleted = []

    def get_all_records(self, collection, filters=None, fields=None, **kwargs):
        return list(self.rows)

    def get_record(self, collection, filters=None, fields=None):
        return self.resources.get(str(filters["_id"]))

    def delete_record(self, collection, filters):
        self.deleted.append((collection, filters["uuid"]))
        self.rows = [r for r in self.rows if r["uuid"] != filters["uuid"]]


def _forgetting(monkeypatch, rows, resources):
    fake = _FakeLedger(rows, resources)
    monkeypatch.setattr(sync, "_mongo", lambda: fake)
    return fake


def test_a_submission_whose_resource_was_deleted_can_be_imported_again(monkeypatch):
    """The ledger is what makes a run idempotent, so a resource an operator
    removed leaves a row that skips its submission forever."""
    fake = _forgetting(
        monkeypatch,
        [{"uuid": "u1", "resourceId": RID}],
        {RID: {"status": "deleted"}},
    )

    report = sync.forget({"form_uid": "aX"})

    assert (report.forgotten, report.kept) == (1, 0)
    assert fake.deleted == [(sync.LEDGER, "u1")]


def test_the_recycle_bin_counts_as_deleted(monkeypatch):
    """Deleting a resource in the interface only moves it to the bin, so a
    check for the document's absence forgets nothing an operator actually did."""
    fake = _forgetting(
        monkeypatch, [{"uuid": "u1", "resourceId": RID}], {RID: {"status": "published"}}
    )
    kept = sync.forget({"form_uid": "aX"})

    fake.resources[RID]["status"] = "deleted"
    gone = sync.forget({"form_uid": "aX"})

    assert (kept.forgotten, kept.kept) == (0, 1)
    assert (gone.forgotten, gone.kept) == (1, 0)


def test_a_submission_still_catalogued_is_left_alone(monkeypatch):
    """Forgetting it would import a second copy of a resource that is still
    there, which is the one outcome this action must never produce."""
    fake = _forgetting(
        monkeypatch,
        [{"uuid": "u1", "resourceId": RID}, {"uuid": "u2", "resourceId": RID2}],
        {RID: {"status": "published"}, RID2: {"status": "deleted"}},
    )

    report = sync.forget({"form_uid": "aX"})

    assert (report.forgotten, report.kept) == (1, 1)
    assert fake.deleted == [(sync.LEDGER, "u2")]


@pytest.mark.parametrize("resource_id", ["", "not-an-object-id"])
def test_a_row_naming_no_real_resource_is_forgotten(monkeypatch, resource_id):
    """A run that failed part way leaves a row naming nothing importable. Kept,
    it would skip that submission for as long as the ledger lives."""
    fake = _forgetting(monkeypatch, [{"uuid": "u1", "resourceId": resource_id}], {})

    assert sync.forget({"form_uid": "aX"}).forgotten == 1
    assert fake.deleted == [(sync.LEDGER, "u1")]


def test_forgetting_deletes_no_resource(monkeypatch):
    """It is bookkeeping about a resource, never the resource: what an operator
    removed stays removed, and what they kept must survive being forgotten."""
    fake = _forgetting(
        monkeypatch, [{"uuid": "u1", "resourceId": RID}], {RID: {"status": "deleted"}}
    )

    sync.forget({"form_uid": "aX"})

    assert fake.resources == {RID: {"status": "deleted"}}
    assert [collection for collection, _uuid in fake.deleted] == [sync.LEDGER]


def test_forgetting_without_a_form_refuses_rather_than_emptying_the_ledger(monkeypatch):
    fake = _forgetting(monkeypatch, [{"uuid": "u1", "resourceId": RID}], {})

    with pytest.raises(client.KoboError):
        sync.forget({})

    assert fake.deleted == []


ACT_ID = "6a8efafdd2da3fa88b85fc77"


def _repairing(monkeypatch, resources):
    from archihub.api.resources import hierarchy

    monkeypatch.setattr(hierarchy, "ancestors", lambda resource_id: [])
    monkeypatch.setattr(hierarchy, "_post_type_of", lambda resource_id: "kobo")
    monkeypatch.setattr(hierarchy, "_check_parent_is_acceptable", lambda child, parent: None)
    return _refiling(
        monkeypatch,
        resources,
        [_file_field(filetag="kobo_anexos")],
        rows=[{"resourceId": RID, "activityIds": [ACT_ID]}],
    )


def test_an_activity_with_no_parent_is_filed_under_its_report(monkeypatch):
    """An activity imported before the hierarchy was configured is catalogued
    and reachable, but appears under nothing - a repair, not a re-import."""
    resources = {
        RID: {"post_type": "kobo", "filesObj": []},
        ACT_ID: {"post_type": "kobo_activities", "filesObj": [], "parent": []},
    }
    _repairing(monkeypatch, resources)

    report = sync.refile({"form_uid": "aX", "post_type": "kobo"})

    # `parents` is the transitive closure the listing and the tree filter on;
    # `parent` is what the operator chose. Both must be written.
    assert resources[ACT_ID]["parent"] == [{"id": RID}]
    assert resources[ACT_ID]["parents"] == [{"id": RID, "post_type": "kobo"}]
    assert report.reparented == 1
    assert "activities under their report" in report.as_message()


def test_an_activity_already_filed_somewhere_is_left_where_it_is(monkeypatch):
    """A cataloguer who moved a resource made a decision, and pressing repair
    must not quietly undo it."""
    moved = [{"id": "6a8efafdd2da3fa88b85fc78", "post_type": "carpeta"}]
    resources = {
        RID: {"post_type": "kobo", "filesObj": []},
        ACT_ID: {"post_type": "kobo_activities", "filesObj": [], "parent": moved},
    }
    _repairing(monkeypatch, resources)

    report = sync.refile({"form_uid": "aX", "post_type": "kobo"})

    assert resources[ACT_ID]["parent"] == moved
    assert report.reparented == 0
    assert "activities under their report" not in report.as_message()


def test_a_repair_that_moved_nothing_says_nothing_about_the_hierarchy(monkeypatch):
    resources = {RID: {"post_type": "kobo", "filesObj": []}}
    _refiling(monkeypatch, resources, [_file_field(filetag="kobo_anexos")])

    assert "activities" not in sync.refile({"form_uid": "aX", "post_type": "kobo"}).as_message()


def test_an_arrangement_the_content_model_refuses_loses_only_that_activity(monkeypatch):
    """The activity type may not list the report's type as a parent. That is a
    configuration answer, not a reason to abandon the rest of the run."""
    from archihub.api.resources import hierarchy
    from archihub.core.errors import ValidationError

    resources = {
        RID: {"post_type": "kobo", "filesObj": []},
        ACT_ID: {"post_type": "kobo_activities", "filesObj": [], "parent": []},
    }
    _repairing(monkeypatch, resources)

    def _refuse(child, parent):
        raise ValidationError("nope")

    monkeypatch.setattr(hierarchy, "_check_parent_is_acceptable", _refuse)

    report = sync.refile({"form_uid": "aX", "post_type": "kobo"})

    assert resources[ACT_ID]["parent"] == []
    assert report.reparented == 0


def test_refiling_without_a_content_type_refuses_rather_than_walking(monkeypatch):
    _refiling(monkeypatch, {}, [])

    with pytest.raises(client.KoboError):
        sync.refile({"form_uid": "aX"})
