"""SAML-D의 CSV 또는 ZIP → SQLite. 원본을 추출/변경하지 않고 스트리밍 저장한다.

실행: python aml_database/saml_db.py import --source "C:/.../SAML-D.csv.zip"
은행 소재 국가를 은행 ID로 간주하지 않는다. Amount의 단위나 지급액/수취액을 임의 추정하지 않는다.
"""
import argparse
import csv
import hashlib
import io
import json
import sqlite3
import zipfile
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import quote

if __package__:
    from .aml_db import check_dataset_id, parse_amount, parse_timestamp, readonly_connection
else:
    from aml_db import check_dataset_id, parse_amount, parse_timestamp, readonly_connection

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "saml.sqlite3"
HEADER = ["Time", "Date", "Sender_account", "Receiver_account", "Amount", "Payment_currency",
          "Received_currency", "Sender_bank_location", "Receiver_bank_location", "Payment_type",
          "Is_laundering", "Laundering_type"]


@contextmanager
def open_source(path, member=None):
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir() and entry.filename.lower().endswith(".csv")]
            if member is None:
                if len(entries) != 1:
                    raise ValueError("ZIP 안에 CSV가 여러 개이거나 없습니다. --member로 정확한 파일명을 지정하세요.")
                entry = entries[0]
            else:
                matches = [entry for entry in entries if entry.filename == member]
                if len(matches) != 1:
                    raise ValueError("ZIP 내부 CSV 파일명을 확인하세요.")
                entry = matches[0]
            with archive.open(entry) as stream:
                yield stream, entry.filename, entry.file_size
    else:
        if member is not None:
            raise ValueError("--member는 ZIP에만 사용할 수 있습니다.")
        with path.open("rb") as stream:
            yield stream, None, path.stat().st_size


def fingerprint(path, member=None):
    checksum = hashlib.sha256()
    with open_source(path, member) as (stream, selected, size):
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest(), selected, size


def check_header(header):
    if [x.strip() for x in header] != HEADER:
        raise ValueError("SAML-D의 12컬럼 헤더와 다릅니다. IBM 데이터는 aml_db.py 적재기를 사용하세요.")


def check_schema(connection):
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if not tables and version == 0:
        connection.executescript("BEGIN;\n" + (ROOT / "saml_schema.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
    elif version != 101 or not {"saml_datasets", "transactions", "transaction_labels"} <= tables:
        raise ValueError("SAML-D DB 형식이 아닙니다. IBM/다른 DB와 다른 저장 경로를 지정하세요.")


def parse_row(row, dataset_id, number):
    if len(row) != 12:
        raise ValueError("컬럼 수가 12개가 아닙니다.")
    values = [cell.strip() for cell in row]
    if any(not value for value in values):
        raise ValueError("필수 값이 비어 있습니다.")
    time, date, sender, receiver, amount, pay_ccy, receive_ccy, sender_loc, receiver_loc, payment_type, label, typology = values
    if label not in {"0", "1"}:
        raise ValueError("Is_laundering은 0 또는 1이어야 합니다.")
    # SAML-D는 YYYY-MM-DD와 HH:MM:SS. 시각에 원본에 없는 Z/UTC 표기를 추가하지 않는다.
    try:
        dt = datetime.fromisoformat(date + "T" + time)
        if dt.utcoffset() is not None or len(date) != 10 or ":" not in time:
            raise ValueError
    except ValueError:
        raise ValueError("Date/Time 형식이 유효하지 않거나 시간대가 포함되어 있습니다.") from None
    timestamp = dt.isoformat(timespec="microseconds" if dt.microsecond else "seconds")
    tx_id = f"TX_{dataset_id}_{number:010d}"
    record = (tx_id, dataset_id, number, timestamp, date, time, sender, receiver,
              parse_amount(amount, "Amount"), pay_ccy, receive_ccy, sender_loc, receiver_loc, payment_type)
    return record, (tx_id, int(label), typology)


def import_saml(source_path, db_path=DEFAULT_DB, *, dataset_id="SAML_D_V1", member=None, batch_size=20000, progress=None):
    check_dataset_id(dataset_id)
    if type(batch_size) is not int or not 1 <= batch_size <= 100000:
        raise ValueError("batch_size는 1~100000 사이 정수여야 합니다.")
    started = perf_counter()
    source, db = Path(source_path).resolve(), Path(db_path).resolve()
    if source == db:
        raise ValueError("원본과 DB의 경로가 같습니다.")
    before = source.stat()
    with open_source(source, member) as (stream, selected, _):
        check_header(next(csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""), strict=True), []))
    checksum, selected, size = fingerprint(source, selected)
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, timeout=30)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA cache_size=-65536")
        check_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute("SELECT csv_sha256, row_count, summary_json FROM saml_datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise ValueError("동일 dataset_id의 원본 CSV가 달라졌습니다. 새 버전 ID를 지정하세요.")
                connection.rollback()
                return {"status": "already_imported", "dataset_id": dataset_id, "row_count": existing[1],
                        "summary": json.loads(existing[2]), "csv_sha256": checksum, "db_path": str(db)}
            alias = connection.execute("SELECT dataset_id FROM saml_datasets WHERE csv_sha256=?", (checksum,)).fetchone()
            if alias:
                raise ValueError(f"동일 CSV가 이미 {alias[0]}로 저장되어 있습니다. 기존 ID를 사용하세요.")
            connection.execute("INSERT INTO saml_datasets VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (dataset_id, source.name, selected, checksum, size, 0, "unspecified_in_file",
                                "raw_amount_unit_not_inferred", datetime.now(timezone.utc).isoformat(), "{}"))
            records, labels, counts, typologies = [], [], Counter(), Counter()
            count, minimum, maximum = 0, None, None

            def flush():
                connection.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", records)
                connection.executemany("INSERT INTO transaction_labels VALUES (?,?,?)", labels)
                records.clear()
                labels.clear()

            with open_source(source, selected) as (stream, _, _):
                reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""), strict=True)
                check_header(next(reader, []))
                while True:
                    try:
                        raw = next(reader)
                    except StopIteration:
                        break
                    except csv.Error:
                        raise ValueError(f"CSV 레코드 {count + 1}의 구문 오류") from None
                    count += 1
                    try:
                        record, label = parse_row(raw, dataset_id, count)
                    except ValueError as exc:
                        raise ValueError(f"CSV 레코드 {count}: {exc}") from None
                    records.append(record)
                    labels.append(label)
                    counts[str(label[1])] += 1
                    typologies[label[2]] += 1
                    minimum = min(minimum, record[3]) if minimum else record[3]
                    maximum = max(maximum, record[3]) if maximum else record[3]
                    if len(records) >= batch_size:
                        flush()
                        if progress:
                            progress(count, perf_counter() - started)
                flush()
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("적재 중 원본 파일이 변경되었습니다.")
            if count == 0:
                raise ValueError("거래가 없는 CSV입니다.")
            summary = {"label_counts": dict(counts), "typology_counts": dict(typologies),
                       "min_timestamp": minimum, "max_timestamp": maximum,
                       "elapsed_seconds": round(perf_counter() - started, 3)}
            connection.execute("UPDATE saml_datasets SET row_count=?, summary_json=? WHERE dataset_id=?",
                               (count, json.dumps(summary, ensure_ascii=False), dataset_id))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"status": "imported", "dataset_id": dataset_id, "row_count": count, "summary": summary,
            "csv_sha256": checksum, "first_transaction_id": f"TX_{dataset_id}_0000000001",
            "last_transaction_id": f"TX_{dataset_id}_{count:010d}", "db_path": str(db)}


def with_account_ids(row):
    if row is None:
        return None
    result = dict(row)
    # 국가를 은행 ID처럼 붙이지 않는다. 원본 계좌 ID에 데이터셋 네임스페이스만 붙인다.
    result["sender_account_id"] = f"ACC_{result['dataset_id']}:{quote(result['sender_account'], safe='')}"
    result["receiver_account_id"] = f"ACC_{result['dataset_id']}:{quote(result['receiver_account'], safe='')}"
    return result


def get_transaction(db_path, tx_id):
    with closing(readonly_connection(db_path)) as connection:
        return with_account_ids(connection.execute("SELECT * FROM transactions WHERE transaction_id=?", (tx_id,)).fetchone())


def list_transactions(db_path, *, dataset_id="SAML_D_V1", limit=10, start=None, end=None):
    check_dataset_id(dataset_id)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit은 1~1000 범위 정수여야 합니다.")
    conditions, params = ["dataset_id=?"], [dataset_id]
    if start:
        start = parse_timestamp(start)
        conditions.append("timestamp>=?")
        params.append(start)
    if end:
        end = parse_timestamp(end)
        conditions.append("timestamp<?")
        params.append(end)
    if start and end and start >= end:
        raise ValueError("시작 시각은 종료 시각보다 빨라야 합니다.")
    with closing(readonly_connection(db_path)) as connection:
        rows = connection.execute("SELECT * FROM transactions WHERE " + " AND ".join(conditions)
                                  + " ORDER BY timestamp,transaction_id LIMIT ?", [*params, limit])
        return [with_account_ids(row) for row in rows]


def stats(db_path):
    with closing(readonly_connection(db_path)) as connection:
        result = [dict(row) for row in connection.execute("SELECT * FROM saml_datasets ORDER BY dataset_id")]
    for row in result:
        row["summary"] = json.loads(row.pop("summary_json"))
    return result


def export_map(db_path, output_path, dataset_id="SAML_D_V1"):
    check_dataset_id(dataset_id)
    output = Path(output_path).resolve()
    if output == Path(db_path).resolve() or output.exists():
        raise ValueError("출력 경로가 DB이거나 이미 존재합니다. 다른 파일명을 사용하세요.")
    with closing(readonly_connection(db_path)) as connection:
        metadata = connection.execute("SELECT csv_sha256 FROM saml_datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        if not metadata:
            raise ValueError("데이터셋 ID가 없습니다.")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["csv_sha256", "source_row", "transaction_id"])
            for row in connection.execute("SELECT source_row, transaction_id FROM transactions WHERE dataset_id=? ORDER BY source_row", (dataset_id,)):
                writer.writerow([metadata[0], *row])
    return str(output)


def main():
    parser = argparse.ArgumentParser(description="SAML-D 원본 ZIP/CSV 전체 적재 및 조회")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import")
    load.add_argument("--source", type=Path, required=True)
    load.add_argument("--member")
    load.add_argument("--dataset-id", default="SAML_D_V1")
    load.add_argument("--batch-size", type=int, default=20000)
    commands.add_parser("stats")
    get = commands.add_parser("get")
    get.add_argument("--tx-id", required=True)
    ls = commands.add_parser("list")
    ls.add_argument("--dataset-id", default="SAML_D_V1")
    ls.add_argument("--limit", type=int, default=10)
    ls.add_argument("--start")
    ls.add_argument("--end")
    export = commands.add_parser("export-map")
    export.add_argument("--dataset-id", default="SAML_D_V1")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "import":
            print("원본 컬럼 및 CSV SHA-256 확인 중...", flush=True)
            result = import_saml(args.source, args.db, dataset_id=args.dataset_id, member=args.member,
                                 batch_size=args.batch_size,
                                 progress=lambda n, seconds: print(f"진행: {n:,}개 / {seconds:.1f}초 (전체 성공 후 커밋)", flush=True))
        elif args.command == "stats":
            result = stats(args.db)
        elif args.command == "get":
            result = get_transaction(args.db, args.tx_id)
        elif args.command == "list":
            result = list_transactions(args.db, dataset_id=args.dataset_id, limit=args.limit, start=args.start, end=args.end)
        else:
            result = {"output": export_map(args.db, args.output, args.dataset_id)}
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
