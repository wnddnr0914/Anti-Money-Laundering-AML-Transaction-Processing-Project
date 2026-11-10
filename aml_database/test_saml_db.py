"""SAML-D 적재, ZIP, 라벨 분리 검증. 작은 테스트 데이터만 사용한다."""
import csv
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import saml_db as saml


class SamlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / "SAML-D.csv"
        self.zip = self.root / "SAML-D.csv.zip"
        self.db = self.root / "saml.sqlite3"
        self.rows = [
            ["10:35:19", "2022-10-07", "0001", "0002", "1459.15", "UK pounds", "UK pounds", "UK", "UK", "Cash Deposit", "0", "Normal_Cash_Deposits"],
            ["10:35:20", "2022-10-07", "0001", "0003", "6019.64", "UK pounds", "Dirham", "UK", "UAE", "Cross-border", "0", "Normal_Fan_Out"],
            ["10:35:20", "2022-10-07", "0001", "0003", "6019.64", "UK pounds", "Dirham", "UK", "UAE", "Cross-border", "1", "Fan_Out"],
        ]
        self.write_files()

    def write_files(self):
        with self.csv.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(saml.HEADER)
            writer.writerows(self.rows)
        with zipfile.ZipFile(self.zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(self.csv, "folder/SAML-D.csv")

    def load(self, source=None, **kwargs):
        return saml.import_saml(source or self.zip, self.db, **kwargs)

    def test_zip_all_fields_and_labels_separated(self):
        result = self.load(batch_size=2)
        self.assertEqual(result["row_count"], 3)
        row = saml.get_transaction(self.db, "TX_SAML_D_V1_0000000002")
        self.assertEqual(row["sender_account"], "0001")
        self.assertEqual(row["sender_account_id"], "ACC_SAML_D_V1:0001")
        self.assertEqual(row["timestamp"], "2022-10-07T10:35:20")
        self.assertEqual((row["payment_currency"], row["received_currency"]), ("UK pounds", "Dirham"))
        self.assertEqual(row["receiver_bank_location"], "UAE")
        self.assertNotIn("amount_received", row)
        self.assertNotIn("is_laundering", row)
        self.assertNotIn("laundering_type", row)
        self.assertNotIn("receiver_bank_id", row)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT is_laundering,laundering_type FROM transaction_labels WHERE transaction_id=?", ("TX_SAML_D_V1_0000000003",)).fetchone(), (1, "Fan_Out"))

    def test_csv_zip_and_repacked_zip_have_identical_identity(self):
        initial = self.load()
        self.assertEqual(self.load(source=self.csv)["status"], "already_imported")
        with zipfile.ZipFile(self.zip, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.write(self.csv, "renamed.csv")
        self.assertEqual(self.load()["csv_sha256"], initial["csv_sha256"])

    def test_duplicate_rows_retained(self):
        self.rows[2] = self.rows[1].copy()
        self.write_files()
        self.load()
        rows = saml.list_transactions(self.db)
        self.assertEqual(len(rows), 3)
        self.assertNotEqual(rows[1]["transaction_id"], rows[2]["transaction_id"])

    def test_label_distribution_and_time_bounds(self):
        self.load()
        summary = saml.stats(self.db)[0]["summary"]
        self.assertEqual(summary["label_counts"], {"0": 2, "1": 1})
        self.assertEqual(summary["min_timestamp"], "2022-10-07T10:35:19")
        self.assertEqual(summary["max_timestamp"], "2022-10-07T10:35:20")
        self.assertEqual(len(saml.list_transactions(self.db, end="2022-10-07T10:35:20")), 1)

    def test_bad_late_row_rolls_back(self):
        self.rows[2][10] = "BAD"
        self.write_files()
        with self.assertRaisesRegex(ValueError, "레코드 3"):
            self.load(batch_size=1)
        with closing(sqlite3.connect(self.db)) as connection:
            for table in ("transactions", "transaction_labels", "saml_datasets"):
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)

    def test_changed_dataset_is_rejected_and_original_is_preserved(self):
        self.load()
        self.rows[0][4] = "2.00"
        self.write_files()
        with self.assertRaisesRegex(ValueError, "원본 CSV"):
            self.load()
        self.assertEqual(saml.get_transaction(self.db, "TX_SAML_D_V1_0000000001")["amount"], "1459.15")

    def test_alias_is_rejected(self):
        self.load()
        with self.assertRaisesRegex(ValueError, "이미"):
            self.load(dataset_id="OTHER")

    def test_foreign_keys_and_parameterized_lookup(self):
        self.load()
        self.assertIsNone(saml.get_transaction(self.db, "' OR 1=1 --"))
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO transaction_labels VALUES (?,?,?)", ("missing", 1, "Fan_Out"))

    def test_zip_multiple_csv_requires_member(self):
        with zipfile.ZipFile(self.zip, "a") as archive:
            archive.write(self.csv, "other.csv")
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.load(member="other.csv")["row_count"], 3)

    def test_export_mapping_and_overwrite_guard(self):
        self.load()
        output = self.root / "map.csv"
        saml.export_map(self.db, output)
        with output.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["transaction_id"], "TX_SAML_D_V1_0000000003")
        self.assertNotIn("laundering_type", rows[0])
        with self.assertRaises(ValueError):
            saml.export_map(self.db, self.db)

    def test_invalid_date_and_unknown_timezone_not_fabricated(self):
        for date, time in [("2022-02-31", "10:00:00"), ("2022-10-07", "25:00:00"), ("2022-10-07", "10:00:00Z")]:
            row = self.rows[0].copy()
            row[0], row[1] = time, date
            with self.assertRaises(ValueError):
                saml.parse_row(row, "SAML_D_V1", 1)


if __name__ == "__main__":
    unittest.main()
