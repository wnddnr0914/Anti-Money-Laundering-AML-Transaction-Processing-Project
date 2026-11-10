"""IBM AML CSV → SQLite. Python 표준 라이브러리만 사용한다.

CSV 레코드 순번을 ID로 사용하므로 원본을 정렬/필터링하기 전에 적재해야 한다.
같은 데이터셋 ID와 동일 파일은 중복 적재하지 않는다. 변경 파일은 새 데이터셋 버전으로 넣는다.
원본 CSV 삭제, 기존 데이터 덮어쓰기, 모델 실행, 외부 서버 접속은 수행하지 않는다.
"""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "aml.sqlite3"
IBM_HEADER = ["Timestamp", "From Bank", "Account", "To Bank", "Account",
              "Amount Received", "Receiving Currency", "Amount Paid", "Payment Currency",
              "Payment Format", "Is Laundering"]
TRANSACTION_COLUMNS = ["transaction_id", "dataset_id", "source_row", "timestamp", "timestamp_raw",
                       "sender_bank_id", "sender_account_number", "sender_account_id",
                       "receiver_bank_id", "receiver_account_number", "receiver_account_id",
                       "amount_received", "receiving_currency", "amount_paid", "payment_currency", "payment_format"]


def check_dataset_id(dataset_id):
    if not isinstance(dataset_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", dataset_id):
        raise ValueError("dataset_id는 대문자로 시작하는 영문 대문자·숫자·밑줄 1~40자로 지정하세요. 예: HI_SMALL_V1")


def transaction_id(dataset_id, source_row):
    check_dataset_id(dataset_id)
    if type(source_row) is not int or source_row < 1:
        raise ValueError("source_row는 헤더를 제외한 1부터 시작하는 원본 CSV 레코드 번호입니다.")
    return f"TX_{dataset_id}_{source_row:010d}"


def account_id(dataset_id, bank_id, account_number):
    # 은행/계좌의 구분자도 인코딩해 ('A:B','C')와 ('A','B:C')가 충돌하지 않도록 한다.
    return f"ACC_{dataset_id}:{quote(bank_id, safe='')}:{quote(account_number, safe='')}"


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_header(header):
    # csv.DictReader는 이름이 같은 Account 열을 덮어쓸 수 있으므로 위치로 읽는다.
    actual = [value.strip() for value in header]
    if len(actual) == 11 and actual[4] == "Account.1":
        actual[4] = "Account"
    if actual != IBM_HEADER:
        raise ValueError("IBM AML 거래 CSV의 11개 컬럼과 순서가 다릅니다. Account가 두 번 나오는 원본 또는 Account.1 형식을 사용하세요.")


def parse_timestamp(value):
    parsed = None
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
            break
        except ValueError:
            pass
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("Timestamp가 지원되는 날짜/시간 형식이 아닙니다.") from None
    if parsed.utcoffset() is not None:
        raise ValueError("이 IBM 적재기는 시간대 미지정 거래 시각을 사용합니다. 시간대가 있는 파일은 별도 정책이 필요합니다.")
    # ISO 날짜만을 임의의 자정 거래로 해석하지 않는다.
    if ":" not in value:
        raise ValueError("Timestamp에는 날짜와 거래 시간이 모두 필요합니다.")
    return parsed.isoformat(timespec="microseconds" if parsed.microsecond else "seconds")


def parse_amount(value, field):
    # 쉼표/과학 표기 등 형식 변경을 자동 추정하지 않는다. 실제 IBM CSV의 십진수 표기를 보존한다.
    if len(value) > 100 or not re.fullmatch(r"\+?\d+(?:\.\d+)?", value):
        raise ValueError(f"{field}는 0 이상의 유한한 십진수여야 합니다.")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{field} 금액을 해석할 수 없습니다.") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} 금액이 유효하지 않습니다.")
    return format(amount, "f")


def parse_record(row, dataset_id, row_number):
    if len(row) != 11:
        raise ValueError("컬럼 수가 11개가 아닙니다. 빈 행도 조용히 건너뛰지 않습니다.")
    values = [cell.strip() for cell in row]
    if any(not cell for cell in values):
        raise ValueError("필수 컬럼에 빈 값이 있습니다.")
    timestamp, from_bank, sender, to_bank, receiver, received, receive_ccy, paid, pay_ccy, fmt, label = values
    if label not in {"0", "1"}:
        raise ValueError("Is Laundering은 0 또는 1이어야 합니다.")
    tx_id = transaction_id(dataset_id, row_number)
    record = (tx_id, dataset_id, row_number, parse_timestamp(timestamp), timestamp,
              from_bank, sender, account_id(dataset_id, from_bank, sender),
              to_bank, receiver, account_id(dataset_id, to_bank, receiver),
              parse_amount(received, "Amount Received"), receive_ccy,
              parse_amount(paid, "Amount Paid"), pay_ccy, fmt)
    return record, (tx_id, int(label))


def ensure_schema(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if not tables and version == 0:
        sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
        connection.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
    elif version != 1 or not {"aml_datasets", "transactions", "transaction_labels"} <= tables:
        raise ValueError("이 도구의 AML DB 형식이 아닙니다. 새 DB 경로를 지정하세요.")


def readonly_connection(db_path):
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError("DB 파일이 없습니다. 먼저 import를 실행하세요.")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def import_csv(csv_path, db_path=DEFAULT_DB, *, dataset_id, batch_size=10000, progress=None):
    """스트리밍 읽기 + 묶음 INSERT. 실패하면 이번 파일의 전체 적재를 롤백한다."""
    check_dataset_id(dataset_id)
    if type(batch_size) is not int or not 1 <= batch_size <= 100000:
        raise ValueError("batch_size는 1~100000 범위의 정수여야 합니다.")
    source, db = Path(csv_path).resolve(), Path(db_path).resolve()
    if source == db:
        raise ValueError("원본 CSV와 DB 경로가 같을 수 없습니다.")
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        validate_header(next(reader, []))
    before = source.stat()
    checksum = file_hash(source)
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, timeout=30)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute("SELECT source_sha256, row_count FROM aml_datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise ValueError("동일 dataset_id에 다른 파일이 등록되어 있습니다. 원본을 덮어쓰지 말고 새 버전 ID를 지정하세요.")
                connection.rollback()
                return {"status": "already_imported", "dataset_id": dataset_id, "row_count": existing[1],
                        "source_sha256": checksum, "db_path": str(db)}
            alias = connection.execute("SELECT dataset_id FROM aml_datasets WHERE source_sha256=?", (checksum,)).fetchone()
            if alias:
                raise ValueError(f"동일 파일이 이미 {alias[0]} 데이터셋으로 등록되어 있습니다. 기존 ID를 사용하세요.")
            connection.execute("INSERT INTO aml_datasets VALUES (?,?,?,?,?,?,?)",
                               (dataset_id, source.name, checksum, before.st_size, 0, "unspecified",
                                datetime.now(timezone.utc).isoformat()))
            transaction_batch, label_batch, count = [], [], 0
            insert_sql = "INSERT INTO transactions (" + ",".join(TRANSACTION_COLUMNS) + ") VALUES (" + ",".join("?" for _ in TRANSACTION_COLUMNS) + ")"

            def flush():
                connection.executemany(insert_sql, transaction_batch)
                connection.executemany("INSERT INTO transaction_labels VALUES (?,?)", label_batch)
                transaction_batch.clear()
                label_batch.clear()

            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, strict=True)
                validate_header(next(reader, []))
                while True:
                    try:
                        row = next(reader)
                    except StopIteration:
                        break
                    except csv.Error:
                        raise ValueError(f"원본 CSV 레코드 {count + 1}의 CSV 구문 오류") from None
                    count += 1
                    try:
                        transaction, label = parse_record(row, dataset_id, count)
                    except ValueError as exc:
                        raise ValueError(f"원본 CSV 레코드 {count}: {exc}") from None
                    transaction_batch.append(transaction)
                    label_batch.append(label)
                    if len(transaction_batch) >= batch_size:
                        flush()
                        if progress:
                            progress(count)
                flush()
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("적재 중 원본 파일이 변경되었습니다. 변경하지 않은 원본으로 다시 실행하세요.")
            if count == 0:
                raise ValueError("헤더만 있고 거래 레코드가 없습니다.")
            connection.execute("UPDATE aml_datasets SET row_count=? WHERE dataset_id=?", (count, dataset_id))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"status": "imported", "dataset_id": dataset_id, "row_count": count,
            "source_sha256": checksum, "first_transaction_id": transaction_id(dataset_id, 1),
            "last_transaction_id": transaction_id(dataset_id, count), "db_path": str(db)}


def get_transaction(db_path, tx_id):
    """원본 거래 조회. 정답 라벨/GNN 점수/XAI 결과를 만들어 넣지 않는다."""
    with closing(readonly_connection(db_path)) as connection:
        row = connection.execute("SELECT * FROM transactions WHERE transaction_id=?", (tx_id,)).fetchone()
        return dict(row) if row else None


def list_transactions(db_path, *, dataset_id, limit=10, start=None, end=None):
    check_dataset_id(dataset_id)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit은 1~1000 범위의 정수여야 합니다.")
    clauses, params = ["dataset_id=?"], [dataset_id]
    if start:
        start = parse_timestamp(start)
        clauses.append("timestamp>=?")
        params.append(start)
    if end:
        end = parse_timestamp(end)
        clauses.append("timestamp<?")
        params.append(end)
    if start and end and start >= end:
        raise ValueError("조회 시작 시각은 종료 시각보다 빨라야 합니다.")
    params.append(limit)
    with closing(readonly_connection(db_path)) as connection:
        rows = connection.execute("SELECT * FROM transactions WHERE " + " AND ".join(clauses)
                                  + " ORDER BY timestamp, transaction_id LIMIT ?", params)
        return [dict(row) for row in rows]


def dataset_stats(db_path):
    with closing(readonly_connection(db_path)) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM aml_datasets ORDER BY dataset_id")]


def export_id_map(db_path, dataset_id, output_path):
    """GNN 팀이 원본 행에 결합할 매핑. 라벨/보고서 생성은 포함하지 않는다."""
    check_dataset_id(dataset_id)
    destination = Path(output_path).resolve()
    if destination == Path(db_path).resolve():
        raise ValueError("출력 파일로 DB를 덮어쓸 수 없습니다.")
    if destination.exists():
        raise FileExistsError("매핑 출력 파일이 이미 존재합니다. 다른 이름을 지정하세요.")
    with closing(readonly_connection(db_path)) as connection:
        meta = connection.execute("SELECT source_sha256 FROM aml_datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        if not meta:
            raise ValueError("등록되지 않은 dataset_id입니다.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["source_sha256", "source_row", "transaction_id", "sender_account_id", "receiver_account_id"])
            for row in connection.execute("SELECT source_row, transaction_id, sender_account_id, receiver_account_id FROM transactions WHERE dataset_id=? ORDER BY source_row", (dataset_id,)):
                writer.writerow([meta[0], *row])
    return str(destination)


def main():
    parser = argparse.ArgumentParser(description="IBM AML CSV를 고정 거래 ID와 함께 SQLite에 저장/조회합니다.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 파일 경로")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import", help="원본 11컬럼 CSV를 스트리밍 적재")
    load.add_argument("--csv", type=Path, required=True)
    load.add_argument("--dataset-id", required=True)
    load.add_argument("--batch-size", type=int, default=10000)
    commands.add_parser("stats", help="적재 데이터셋·거래 수 확인")
    get = commands.add_parser("get", help="거래 ID로 조회")
    get.add_argument("--tx-id", required=True)
    ls = commands.add_parser("list", help="기간 내 거래 조회: 시작 포함/종료 제외")
    ls.add_argument("--dataset-id", required=True)
    ls.add_argument("--limit", type=int, default=10)
    ls.add_argument("--start")
    ls.add_argument("--end")
    export = commands.add_parser("export-map", help="원본 행 번호 ↔ 거래/계좌 ID 매핑 CSV 생성")
    export.add_argument("--dataset-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "import":
            result = import_csv(args.csv, args.db, dataset_id=args.dataset_id, batch_size=args.batch_size,
                                progress=lambda n: print(f"{n:,}개 행 처리 중(최종 완료 시 전체 커밋)", flush=True))
        elif args.command == "stats":
            result = dataset_stats(args.db)
        elif args.command == "get":
            result = get_transaction(args.db, args.tx_id)
        elif args.command == "list":
            result = list_transactions(args.db, dataset_id=args.dataset_id, limit=args.limit, start=args.start, end=args.end)
        else:
            result = {"output": export_id_map(args.db, args.dataset_id, args.output)}
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
