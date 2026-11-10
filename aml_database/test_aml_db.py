"""적재 무결성 검증. 실행: python -m unittest discover -s aml_database -p test_aml_db.py -v"""
import csv
import importlib.util
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("aml_db_test_target", MODULE_ROOT / "aml_db.py")
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "demo.csv"
        shutil.copyfile(MODULE_ROOT / "demo_transactions.csv", self.source)
        self.database = self.root / "aml.sqlite3"

    def load(self, **kwargs):
        return db.import_csv(self.source, self.database, dataset_id="DEMO_V1", **kwargs)

    def mutate(self, function):
        with self.source.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        function(rows)
        with self.source.open("w", encoding="utf-8", newline="") as file:
            csv.writer(file).writerows(rows)

    def test_duplicate_headers_rows_and_leading_zeros_preserved(self):
        result = self.load(batch_size=2)
        self.assertEqual(result["row_count"], 5)
        first = db.get_transaction(self.database, "TX_DEMO_V1_0000000001")
        self.assertEqual(first["sender_account_number"], "A")
        self.assertEqual(first["receiver_account_number"], "B")
        self.assertEqual(first["sender_bank_id"], "001")
        third = db.get_transaction(self.database, "TX_DEMO_V1_0000000003")
        fourth = db.get_transaction(self.database, "TX_DEMO_V1_0000000004")
        self.assertNotEqual(third["transaction_id"], fourth["transaction_id"])
        self.assertEqual(third["amount_paid"], fourth["amount_paid"])
        fifth = db.get_transaction(self.database, "TX_DEMO_V1_0000000005")
        self.assertEqual(fifth["sender_account_number"], "000123")
        self.assertEqual(fifth["receiver_account_number"], "000123")
        self.assertNotEqual(fifth["sender_account_id"], fifth["receiver_account_id"])

    def test_paid_received_currency_distinction_and_label_exclusion(self):
        self.load()
        record = db.get_transaction(self.database, "TX_DEMO_V1_0000000005")
        self.assertEqual((record["amount_paid"], record["payment_currency"]), ("100.00", "US Dollar"))
        self.assertEqual((record["amount_received"], record["receiving_currency"]), ("90.00", "Euro"))
        self.assertNotIn("is_laundering", record)
        self.assertNotIn("is_laundering", json.dumps(db.list_transactions(self.database, dataset_id="DEMO_V1")))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT is_laundering FROM transaction_labels WHERE transaction_id=?", ("TX_DEMO_V1_0000000003",)).fetchone()[0], 1)

    def test_reimport_does_not_duplicate_or_change_ids(self):
        first = self.load()
        records = db.list_transactions(self.database, dataset_id="DEMO_V1")
        second = self.load()
        self.assertEqual(second["status"], "already_imported")
        self.assertEqual(first["source_sha256"], second["source_sha256"])
        self.assertEqual(records, db.list_transactions(self.database, dataset_id="DEMO_V1"))

    def test_changed_file_cannot_reuse_dataset_name(self):
        self.load()
        self.mutate(lambda rows: rows[1].__setitem__(7, "999.00"))
        with self.assertRaisesRegex(ValueError, "다른 파일"):
            self.load()
        self.assertEqual(db.get_transaction(self.database, "TX_DEMO_V1_0000000001")["amount_paid"], "1000.00")

    def test_duplicate_file_under_another_dataset_is_rejected(self):
        self.load()
        with self.assertRaisesRegex(ValueError, "기존 ID"):
            db.import_csv(self.source, self.database, dataset_id="OTHER_V1")

    def test_ids_stable_in_another_database(self):
        self.load()
        other = self.root / "other.sqlite3"
        db.import_csv(self.source, other, dataset_id="DEMO_V1", batch_size=3)
        self.assertEqual(db.list_transactions(self.database, dataset_id="DEMO_V1"), db.list_transactions(other, dataset_id="DEMO_V1"))

    def test_late_bad_row_rolls_back_all_batches(self):
        self.mutate(lambda rows: rows[-1].__setitem__(10, "bad-label"))
        with self.assertRaisesRegex(ValueError, "레코드 5"):
            self.load(batch_size=2)
        with closing(sqlite3.connect(self.database)) as connection:
            for table in ("aml_datasets", "transactions", "transaction_labels"):
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)

    def test_existing_dataset_survives_other_import_failure(self):
        self.load()
        self.mutate(lambda rows: rows[-1].__setitem__(7, "NaN"))
        with self.assertRaises(ValueError):
            db.import_csv(self.source, self.database, dataset_id="NEW_V1", batch_size=2)
        self.assertEqual(len(db.dataset_stats(self.database)), 1)
        self.assertEqual(len(db.list_transactions(self.database, dataset_id="DEMO_V1")), 5)

    def test_malformed_header_fails_before_creating_database(self):
        self.mutate(lambda rows: rows[0].__setitem__(0, "Unknown"))
        with self.assertRaises(ValueError):
            self.load()
        self.assertFalse(self.database.exists())

    def test_pandas_account_dot_one_header_accepted(self):
        self.mutate(lambda rows: rows[0].__setitem__(4, "Account.1"))
        self.assertEqual(self.load()["row_count"], 5)

    def test_time_naive_and_date_range_half_open(self):
        self.load()
        records = db.list_transactions(self.database, dataset_id="DEMO_V1", start="2022-11-01T10:03:00", end="2022-11-01T10:07:00")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["timestamp"], "2022-11-01T10:03:00")
        self.assertEqual(db.dataset_stats(self.database)[0]["timestamp_timezone"], "unspecified")
        with self.assertRaises(ValueError):
            db.parse_timestamp("2022-11-01T10:03:00Z")
        with self.assertRaises(ValueError):
            db.parse_timestamp("2022-11-01")

    def test_decimal_precision_zero_and_invalid_amounts(self):
        value = "12345678901234567890.123456789"
        self.mutate(lambda rows: rows[1].__setitem__(7, value))
        self.assertEqual(self.load()["row_count"], 5)
        self.assertEqual(db.get_transaction(self.database, "TX_DEMO_V1_0000000001")["amount_paid"], value)
        self.assertEqual(db.parse_amount("0.00", "Amount Paid"), "0.00")
        for invalid in ("NaN", "Infinity", "-1", "", "1,000", "1e10"):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    db.parse_amount(invalid, "Amount Paid")

    def test_identifiers_and_sql_parameters(self):
        self.assertNotEqual(db.account_id("D", "A:B", "C"), db.account_id("D", "A", "B:C"))
        self.assertNotEqual(db.account_id("D", "A", "B"), db.account_id("E", "A", "B"))
        self.load()
        self.assertIsNone(db.get_transaction(self.database, "' OR 1=1 --"))
        self.assertEqual(len(db.list_transactions(self.database, dataset_id="DEMO_V1")), 5)

    def test_export_is_streamed_and_traceable(self):
        self.load()
        output = self.root / "ids.csv"
        db.export_id_map(self.database, "DEMO_V1", output)
        with output.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[2]["source_row"], "3")
        self.assertEqual(rows[2]["transaction_id"], "TX_DEMO_V1_0000000003")
        self.assertEqual(rows[2]["source_sha256"], db.file_hash(self.source))
        self.assertNotIn("is_laundering", rows[0])
        with self.assertRaises(FileExistsError):
            db.export_id_map(self.database, "DEMO_V1", output)

    def test_unrelated_database_is_not_modified(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
        with self.assertRaises(ValueError):
            self.load()
        with closing(sqlite3.connect(self.database)) as connection:
            names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertEqual(names, ["unrelated"])

    def test_read_does_not_create_empty_database(self):
        with self.assertRaises(FileNotFoundError):
            db.get_transaction(self.database, "TX_UNKNOWN")
        self.assertFalse(self.database.exists())


if __name__ == "__main__":
    unittest.main()
