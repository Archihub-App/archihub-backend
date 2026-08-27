"""The storage report.

Two things carry the weight here. The catalogue total is SUMMED from the sizes
records already carry rather than walked on disk, which is what makes it
answerable on request; and every one of its three sources fails independently,
because a report that will not render is worse than one whose disk figures read
zero.
"""

from __future__ import annotations

import pytest

from archihub.api.system import storage


class FakeMongo:
    def __init__(self, records=(), db_stats=None):
        self.records = list(records)
        self.db_stats = db_stats if db_stats is not None else {"dataSize": 1000}
        self.db = self

    def aggregate(self, collection, pipeline):
        match = pipeline[0]["$match"]
        rows = [
            r for r in self.records
            if r.get("status") != match["status"]["$ne"]
        ]
        if not rows:
            return []
        return [{
            "_id": None,
            # `$sum` treats a missing or non-numeric field as 0, which is the
            # behaviour the report has to survive.
            "bytes": sum(r["size"] for r in rows if isinstance(r.get("size"), int)),
            "files": len(rows),
        }]

    def command(self, name):
        if self.db_stats is None:
            raise RuntimeError("dbStats unavailable")
        return self.db_stats


@pytest.fixture
def mongo(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(storage, "_mongo", lambda: fake)
    return fake


@pytest.fixture
def a_disk(monkeypatch):
    import collections

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _p: usage(1000, 400, 600))

    class Settings:
        original_files_path = "/anywhere"

    import archihub.core.settings as settings_module
    monkeypatch.setattr(settings_module, "get_settings", lambda: Settings())


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (2048, "2.0 KB"),
        (1536 * 1024, "1.5 MB"),
        # The figure from the specification this was written against, so the
        # units are pinned as binary: a "GB" here is 1024³, which is what the
        # operating system reports and therefore what `df` agrees with.
        (153291489280, "142.8 GB"),
        (5 * 1024 ** 4, "5.0 TB"),
    ],
)
def test_sizes_read_the_way_an_operator_expects(size, expected):
    assert storage.format_bytes(size) == expected


def test_a_size_beyond_the_largest_unit_does_not_fall_off_the_scale():
    assert storage.format_bytes(9000 * 1024 ** 4).endswith(" TB")


# ---------------------------------------------------------------------------
# The figures
# ---------------------------------------------------------------------------


def test_the_total_is_the_sum_of_its_breakdown(mongo, a_disk):
    mongo.records = [{"size": 100, "status": "uploaded"}, {"size": 250, "status": "uploaded"}]
    mongo.db_stats = {"dataSize": 50}

    report = storage.storage_report()

    assert report["breakdown"] == {
        "multimedia_files_bytes": 350,
        "database_metadata_bytes": 50,
    }
    assert report["total_cataloged_bytes"] == 400
    assert report["files_count"] == 2


def test_the_recycle_bin_is_not_counted(mongo, a_disk):
    """A file whose record was deleted is not part of what is held."""
    mongo.records = [{"size": 100, "status": "uploaded"}, {"size": 900, "status": "deleted"}]

    assert storage.storage_report()["breakdown"]["multimedia_files_bytes"] == 100


def test_a_record_stored_without_a_size_does_not_break_the_report(mongo, a_disk):
    mongo.records = [{"size": 100, "status": "uploaded"}, {"status": "uploaded"}]

    report = storage.storage_report()

    assert report["breakdown"]["multimedia_files_bytes"] == 100
    assert report["files_count"] == 2


def test_an_empty_archive_reports_zero_rather_than_failing(mongo, a_disk):
    mongo.records = []
    mongo.db_stats = {"dataSize": 0}

    report = storage.storage_report()

    assert report["total_cataloged_bytes"] == 0
    assert report["files_count"] == 0
    assert report["total_cataloged_formatted"] == "0 B"


def test_the_gigabyte_figure_is_binary(mongo, a_disk):
    mongo.records = [{"size": 153291489280, "status": "uploaded"}]
    mongo.db_stats = {"dataSize": 0}

    assert storage.storage_report()["total_cataloged_gb"] == 142.76


# ---------------------------------------------------------------------------
# Failing independently
# ---------------------------------------------------------------------------


def test_a_database_that_cannot_report_its_size_does_not_lose_the_file_total(mongo, a_disk):
    mongo.records = [{"size": 100, "status": "uploaded"}]
    mongo.db_stats = None

    report = storage.storage_report()

    assert report["breakdown"]["database_metadata_bytes"] == 0
    assert report["breakdown"]["multimedia_files_bytes"] == 100


def test_an_unreadable_volume_does_not_lose_the_rest_of_the_report(mongo, monkeypatch):
    """An unmounted volume is itself the thing worth reporting."""
    def explode(_path):
        raise OSError("not mounted")

    monkeypatch.setattr(storage.shutil, "disk_usage", explode)

    class Settings:
        original_files_path = "/gone"

    import archihub.core.settings as settings_module
    monkeypatch.setattr(settings_module, "get_settings", lambda: Settings())
    mongo.records = [{"size": 100, "status": "uploaded"}]

    report = storage.storage_report()

    assert report["disk_capacity"] == {
        "total_bytes": 0, "available_bytes": 0, "used_percentage": 0.0
    }
    assert report["breakdown"]["multimedia_files_bytes"] == 100


def test_an_instance_with_no_storage_root_configured_reports_zero(mongo, monkeypatch):
    class Settings:
        original_files_path = ""

    import archihub.core.settings as settings_module
    monkeypatch.setattr(settings_module, "get_settings", lambda: Settings())

    assert storage.disk_capacity()["total_bytes"] == 0


def test_used_percentage_is_of_the_whole_volume(mongo, a_disk):
    # total 1000, free 600 -> 40% used.
    assert storage.storage_report()["disk_capacity"]["used_percentage"] == 40.0


def test_the_report_states_every_field_the_contract_promises(mongo, a_disk):
    report = storage.storage_report()

    assert set(report) == {
        "total_cataloged_bytes", "total_cataloged_gb", "total_cataloged_formatted",
        "files_count", "breakdown", "disk_capacity",
    }
    assert set(report["breakdown"]) == {"multimedia_files_bytes", "database_metadata_bytes"}
    assert set(report["disk_capacity"]) == {
        "total_bytes", "available_bytes", "used_percentage"
    }


def test_the_report_is_declared_as_depending_on_records():
    """The cached half is invalidated by writing records, not by a clock.

    Depositing a file makes the figure wrong immediately; a TTL alone would
    leave it wrong for the rest of its window.
    """
    assert storage.catalogued_files.cache_collections == ("records",)
