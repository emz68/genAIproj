"""Reader tests: BOM/CRLF/delimiter robustness, .gz transparency, autodetect."""

import gzip
import json

import pytest

from src.ingestion import readers

FIXTURES = readers.os.path.join(readers.os.path.dirname(__file__), "..", "fixtures")


def _fix(*parts):
    return readers.os.path.join(FIXTURES, *parts)


class TestCsvReader:
    def test_bom_crlf_semicolon_sample(self):
        """Source A sample: UTF-8 BOM, CRLF, ';' delimiter — 3 rows, verbatim."""
        rows = list(readers.iter_csv_records(_fix("source_a_sample.csv")))
        assert len(rows) == 3
        row, issues = rows[0]
        assert issues == []
        assert row["station_name"] == "TOWN OF CARY / TOWN HALL-PWH"
        assert row["energy_kwh"] == "3.976"  # string verbatim
        assert row["start_date"] == "2023-01-03T17:58:04+00:00"
        assert row["address_2"] == "Page-Walker Hotel"

    def test_empty_cells_become_none(self):
        rows = list(readers.iter_csv_records(_fix("source_a_sample.csv")))
        row, _ = rows[1]
        assert row["address_2"] is None  # "113 Walnut St;;Cary" -> empty cell

    def test_malformed_short_row_flagged(self):
        rows = list(readers.iter_csv_records(_fix("source_a_malformed.csv")))
        short = [(r, issues) for r, issues in rows if "malformed_row" in issues]
        assert len(short) == 1
        row, issues = short[0]
        assert row["energy_kwh"] is None  # missing cell padded with None

    def test_long_row_preserves_extra_columns(self):
        rows = list(readers.iter_csv_records(_fix("source_a_malformed.csv")))
        long = [(r, issues) for r, issues in rows if "unmapped_columns" in issues]
        assert len(long) == 1
        row, issues = long[0]
        assert row["_extra_columns"] == ["EXTRA", "COLUMNS"]

    def test_comma_and_tab_delimited(self, tmp_path):
        for delim, text in [
            (",", "a,b,c\n1,2,3\n"),
            ("\t", "a\tb\tc\n4\t5\t6\n"),
        ]:
            p = tmp_path / f"f_{delim}.csv"
            p.write_text(text, encoding="utf-8")
            rows = list(readers.iter_csv_records(str(p)))
            assert rows[0][0]["c"] == {",": "3", "\t": "6"}[delim]

    def test_gz_transparency(self, tmp_path):
        """Readers must handle .gz transparently (E2) — sniffed by magic bytes."""
        src = _fix("source_a_sample.csv")
        p = tmp_path / "zipped.csv.gz"
        with open(src, "rb") as fh, gzip.open(p, "wb") as out:
            out.write(fh.read())
        rows = list(readers.iter_csv_records(str(p)))
        assert len(rows) == 3
        # magic-bytes detection: gzipped file WITHOUT a .gz extension
        p2 = tmp_path / "disguised.csv"
        with open(src, "rb") as fh, gzip.open(p2, "wb") as out:
            out.write(fh.read())
        assert readers._is_gzip(str(p2))
        assert len(list(readers.iter_csv_records(str(p2)))) == 3


class TestGeoJsonReader:
    def test_streams_features(self):
        feats = list(readers.iter_geojson_features(_fix("source_b_sample.geojson")))
        assert len(feats) == 3
        assert feats[0]["properties"]["station_name"] == "Fixture Station One"

    def test_geojson_gz(self, tmp_path):
        src = _fix("source_b_sample.geojson")
        p = tmp_path / "stations.geojson.gz"
        with open(src, "rb") as fh, gzip.open(p, "wb") as out:
            out.write(fh.read())
        feats = list(readers.iter_geojson_features(str(p)))
        assert len(feats) == 3


class TestDetection:
    def test_by_extension(self):
        assert readers.detect_format("x.csv") == "csv"
        assert readers.detect_format("x.csv.gz") == "csv"
        assert readers.detect_format("x.geojson") == "geojson"
        assert readers.detect_format("x.geojson.gz") == "geojson"
        assert readers.detect_format("x.txt") == "text"

    def test_json_sniffed_as_geojson(self, tmp_path):
        p = tmp_path / "dump.json"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        assert readers.detect_format(str(p)) == "geojson"

    def test_unknown_extension_raises(self, tmp_path):
        p = tmp_path / "blob.dat"
        p.write_text("hello world")
        with pytest.raises(ValueError):
            readers.detect_format(str(p))

    def test_json_extension_with_csv_content_sniffs_to_csv(self, tmp_path):
        """Ambiguous .json whose content is delimited text sniffs as csv."""
        p = tmp_path / "mystery.json"
        p.write_text("a;b;c\n1;2;3\n")
        assert readers.detect_format(str(p)) == "csv"

    def test_open_maybe_gz_plain_file(self, tmp_path):
        p = tmp_path / "plain.txt"
        p.write_text("hello", encoding="utf-8")
        with readers.open_maybe_gz(str(p)) as fh:
            assert fh.read() == "hello"
