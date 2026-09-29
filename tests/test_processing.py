"""本地验证规则与 Streaming 协议；不冒充 Hadoop 集成测试。"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from processing.quality import assessment
from processing.rules import parse, reference_ids
from processing.worker import mapper, reducer

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / "config/rules.json").read_text(encoding="utf-8"))


def run_worker(data, mode):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for table in ("users", "movies"):
            (root / f"{table}.dat").write_text(
                "\n".join(data.get(table, [])) + ("\n" if data.get(table) else ""),
                encoding="latin-1",
            )
        refs = {
            table: reference_ids(root / f"{table}.dat", table, RULES)
            for table in ("users", "movies")
        }
        source = io.StringIO(
            "".join(
                json.dumps({"table": table, "line": number, "raw": raw}) + "\n"
                for table, rows in data.items()
                for number, raw in enumerate(rows, 1)
            )
        )
        mapped, output = io.StringIO(), io.StringIO()
        mapper(source, mapped, RULES)
        reducer(
            iter(sorted(mapped.getvalue().splitlines(keepends=True))), output, RULES, refs, mode
        )
        return [
            (line.split("\t", 1)[0], json.loads(line.split("\t", 1)[1]))
            for line in output.getvalue().splitlines()
        ]


class ProcessingTests(unittest.TestCase):
    def test_normalization_preserves_zip_and_latin1(self):
        user = parse("users", " 01::f::018::00::00123 ", RULES)
        self.assertEqual(user.normalized, "1::F::18::0::00123")
        self.assertFalse(user.errors)
        movie = parse("movies", "1::Amélie (2001)::drama|Comedy|Drama", RULES)
        self.assertEqual(movie.normalized, "1::Amélie (2001)::Comedy|Drama")
        self.assertFalse(movie.errors)

    def test_invalid_values_are_not_guessed(self):
        for raw, reason in (
            ("1::1::6::978307199", "rating_domain"),
            ("1::1::4::978307199000", "timestamp_range"),
            ("1::1::4.0::978307199", "integer_field_2"),
            ("1::1::N/A::978307199", "missing_field"),
            ("UserID::MovieID::Rating::Timestamp", "integer_field_0"),
            ("1::1::4", "field_count"),
        ):
            self.assertIn(reason, parse("ratings", raw, RULES).errors)

    def test_missing_and_bad_field_count(self):
        self.assertEqual(parse("ratings", "1:: ::NULL::978307199", RULES).present, 2)
        self.assertEqual(parse("ratings", "1::1::5", RULES).present, 3)
        self.assertIn("field_count", parse("ratings", "1::1::5::978307199::extra", RULES).errors)

    def test_conflicts_duplicates_references_and_boundaries(self):
        data = {
            "users": [
                "1::F::18::0::00123",
                "1::F::18::0::00123",
                "2::M::25::1::12345",
                "2::F::25::1::12345",
            ],
            "movies": ["1::Film (2000)::Drama", "2::Other (2000)::Comedy"],
            "ratings": [
                "1::1::4::978307199",
                "1::1::4::978307199",
                "1::1::5::978307200",
                "1::2::4::1009843199",
                "1::2::4::1009843200",
                "2::1::5::978307199",
                "1::3::5::978307199",
                "1::1::5::978307199",
            ],
        }
        result = run_worker(data, "clean")
        before = next(value for kind, value in result if kind == "assessment")
        clean = {table: [] for table in data}
        audit = []
        for kind, value in result:
            if kind == "clean":
                clean[value["table"]].append(value["raw"])
            elif kind == "audit":
                audit.append(value)
        disposition = next(value for kind, value in result if kind == "disposition")
        self.assertEqual(len(clean["users"]), 1)
        self.assertEqual(before["counts"]["rows"], sum(len(rows) for rows in data.values()))
        self.assertEqual(len(clean["ratings"]), 3)
        self.assertEqual(
            disposition["tables"]["users"], {"unchanged": 1, "deduplicated": 1, "quarantined": 2}
        )
        self.assertEqual(disposition["tables"]["ratings"]["quarantined"], 5)
        self.assertTrue(any("user_reference_unavailable" in row["reasons"] for row in audit))
        self.assertTrue(any("movie_reference_unavailable" in row["reasons"] for row in audit))
        after = next(value for kind, value in run_worker(clean, "evaluate") if kind == "assessment")
        self.assertEqual(after["scores"]["Consistent"], 100)
        self.assertEqual(after["counts"].get("train", 0), 0)
        self.assertEqual(after["counts"]["validation"], 2)
        self.assertEqual(after["counts"]["test"], 1)
        for table, rows in data.items():
            self.assertEqual(sum(disposition["tables"][table].values()), len(rows))

    def test_same_event_dedup_but_different_time_preserved(self):
        data = {
            "users": ["1::F::18::0::00123"],
            "movies": ["1::Film (2000)::Drama"],
            "ratings": ["1::1::4::978307199", "1::1::4::978307199", "1::1::5::978307200"],
        }
        before = next(value for kind, value in run_worker(data, "evaluate") if kind == "assessment")
        self.assertEqual(before["scores"]["Unique"], 80)
        result = run_worker(data, "clean")
        self.assertEqual(len([v for k, v in result if k == "clean" and v["table"] == "ratings"]), 2)

    def test_all_five_scores_use_declared_denominators(self):
        data = {
            "users": ["1::F::18::0::00123"],
            "movies": ["1::Film (2000)::Drama"],
            "ratings": ["1::1::4::978307199", "1::1::4::1030000000", "1::1::6::1030000001"],
        }
        before = next(value for kind, value in run_worker(data, "evaluate") if kind == "assessment")
        self.assertEqual(
            before["scores"],
            {
                "Accurate": 80,
                "Complete": 100,
                "Unique": 100,
                "Up-to-date": 66.6667,
                "Consistent": 80,
            },
        )
        self.assertIsNone(before["tables"]["users"]["scores"]["Up-to-date"])

    def test_empty_data_is_not_perfect(self):
        self.assertTrue(all(score is None for score in assessment({})["scores"].values()))
        result = run_worker({"users": [], "movies": [], "ratings": []}, "evaluate")
        self.assertTrue(all(score is None for score in result[0][1]["scores"].values()))

    def test_unverified_metadata_is_preserved(self):
        user = parse("users", "1::F::18::0::K1A0B1", RULES)
        movie = parse("movies", "1::Unknown year::Drama", RULES)
        self.assertFalse(user.errors)
        self.assertIn("zip_unverified", user.warnings)
        self.assertFalse(movie.errors)
        self.assertIn("title_year_unverified", movie.warnings)

    def test_cleaning_is_idempotent(self):
        data = {
            "users": [" 01::f::018::00::00123 "],
            "movies": ["1:: Amélie (2001) ::drama|Comedy|Drama"],
            "ratings": ["01::01::04::978307199"],
        }
        clean = {table: [] for table in data}
        for kind, item in run_worker(data, "clean"):
            if kind == "clean":
                clean[item["table"]].append(item["raw"])
        actions = next(value for kind, value in run_worker(clean, "clean") if kind == "disposition")
        for values in actions["tables"].values():
            self.assertEqual(values, {"unchanged": 1})


if __name__ == "__main__":
    unittest.main()
