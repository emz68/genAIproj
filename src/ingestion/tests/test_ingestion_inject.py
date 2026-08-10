"""Messiness injector tests (E5): determinism, scale math, round-trip."""

import hashlib
import json
import os

from src.ingestion import inject, readers, run

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_hashes(d):
    return {
        name: _sha(os.path.join(d, name))
        for name in sorted(os.listdir(d))
    }


def _seed_dir(tmp_path, seed_dir=None):
    """A mini seed dir: one CSV row set + a one-feature GeoJSON + a report."""
    src = seed_dir or (tmp_path / "seed")
    src.mkdir(exist_ok=True)
    a = src / "ev_charging_sessions_cary.csv.gz"
    with a.open("w", encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write(
            "start_date;station_name;charging_time_hh_mm_ss;energy_kwh;address_1;address_2;city;state_province;zip_postal_code\r\n"
            "2023-01-03T17:58:04+00:00;TOWN OF CARY / TOWN HALL-PWH;00:54:50;3.976;228 Ambassador Loop;Page-Walker Hotel;Cary;North Carolina;27513\r\n"
            "2023-01-03T17:29:44+00:00;TOWN OF CARY / DT DECK P2 (2);01:22:28;8.463;113 Walnut St;;Cary;North Carolina;27511\r\n"
        )
    b = src / "afdc_stations_nc_elec.geojson.gz"
    with b.open("w", encoding="utf-8") as fh:
        fh.write(
            '{"type": "FeatureCollection", "features": [{"type": "Feature", '
            '"geometry": {"type": "Point", "coordinates": [-78.6, 35.7]}, '
            '"properties": {"id": 1, "station_name": "Seed Station", '
            '"street_address": "1 Seed Way", "city": "Cary", "state": "NC", '
            '"zip": "27513", "status_code": "E", "open_date": 1287100800000, '
            '"ev_network": "ChargePoint Network", "ev_charging_units": '
            '"[{\\"network\\": \\"ChargePoint Network\\", \\"connectors\\": '
            '{\\"J1772\\": {\\"power_kw\\": 6.5, \\"port_count\\": 1}}, '
            '\\"port_count\\": 1, \\"charging_level\\": \\"2\\"}]"}}]}'
        )
    return src


class TestDeterminism:
    def test_same_seed_identical_output(self, tmp_path):
        src = _seed_dir(tmp_path)
        out1 = tmp_path / "o1"
        out2 = tmp_path / "o2"
        inject.inject(str(src), str(out1), seed=7, scale=2)
        inject.inject(str(src), str(out2), seed=7, scale=2)
        assert sorted(os.listdir(out1)) == sorted(os.listdir(out2))
        assert _tree_hashes(out1) == _tree_hashes(out2)

    def test_different_seed_different_output(self, tmp_path):
        src = _seed_dir(tmp_path)
        out1 = tmp_path / "o1"
        out2 = tmp_path / "o2"
        inject.inject(str(src), str(out1), seed=7, scale=2)
        inject.inject(str(src), str(out2), seed=8, scale=2)
        assert _tree_hashes(out1) != _tree_hashes(out2)


class TestScaleMath:
    def test_scale_1_no_megafiles(self, tmp_path):
        src = _seed_dir(tmp_path)
        out = tmp_path / "o"
        summary = inject.inject(str(src), str(out), seed=1, scale=1)
        assert summary["source_c"]["files"] == 24
        assert summary["source_c"]["events"] == 24
        assert not [f for f in os.listdir(out) if "mega" in f]

    def test_scale_50_megafiles(self, tmp_path):
        src = _seed_dir(tmp_path)
        out = tmp_path / "o"
        summary = inject.inject(str(src), str(out), seed=1, scale=50)
        # scale // 25 = 2 megafiles of 1000 lines each
        assert summary["source_c"]["events"] == 24 + 2 * 1000
        megas = [f for f in os.listdir(out) if "mega" in f]
        assert len(megas) == 2

    def test_late_batch_default_and_optout(self, tmp_path):
        src = _seed_dir(tmp_path)
        out1 = tmp_path / "o1"
        inject.inject(str(src), str(out1), seed=3, scale=1)
        assert any("_late.csv" in f for f in os.listdir(out1))
        out2 = tmp_path / "o2"
        inject.inject(str(src), str(out2), seed=3, scale=1, late=False)
        assert not any("_late.csv" in f for f in os.listdir(out2))


class TestRoundTrip:
    def test_injected_output_ingests_cleanly(self, tmp_path):
        """Inject -> run ingestion --no-llm -> every line parses to a model."""
        src = _seed_dir(tmp_path)
        out = tmp_path / "o"
        summary = inject.inject(str(src), str(out), seed=7, scale=2)
        assert summary["source_a"]["rows"] > 0
        assert summary["source_b"]["features"] >= 1
        assert summary["source_c"]["events"] > 0

        canonical = tmp_path / "canonical.jsonl"
        metrics = run.run(str(out), str(canonical), no_llm=True)
        assert metrics["records_out"] > 0
        with open(canonical, encoding="utf-8") as fh:
            kinds = {}
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                kinds[rec["record_type"]] = kinds.get(rec["record_type"], 0) + 1
                assert rec["provenance"]["source"] in ("cary", "afdc", "contractor")
                assert "quality" in rec
        assert kinds.get("maintenance", 0) > 0
        assert kinds.get("session", 0) > 0
        assert kinds.get("charger", 0) >= 1

    def test_station_name_never_blanked_in_degraded_a(self, tmp_path):
        src = _seed_dir(tmp_path)
        out = tmp_path / "o"
        inject.inject(str(src), str(out), seed=5, scale=1)
        messy = os.path.join(out, "ev_charging_sessions_cary_messy.csv.gz")
        names = [row["station_name"] for row, _ in readers.iter_csv_records(messy)]
        assert names and all(n for n in names)
