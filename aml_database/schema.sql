-- SQLite 로컬 적재용. 거래 시각의 시간대는 원본에 없으므로 추정하지 않는다.
-- 금액은 TEXT로 보존하고 Python Decimal로 계산한다. SQLite SUM으로 금액을 합산하지 않는다.
CREATE TABLE aml_datasets (
    dataset_id TEXT PRIMARY KEY NOT NULL,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL UNIQUE,
    source_size_bytes INTEGER NOT NULL CHECK (source_size_bytes >= 0),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    timestamp_timezone TEXT NOT NULL DEFAULT 'unspecified',
    imported_at TEXT NOT NULL
);

CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY NOT NULL,
    dataset_id TEXT NOT NULL REFERENCES aml_datasets(dataset_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    timestamp TEXT NOT NULL,
    timestamp_raw TEXT NOT NULL,
    sender_bank_id TEXT NOT NULL,
    sender_account_number TEXT NOT NULL,
    sender_account_id TEXT NOT NULL,
    receiver_bank_id TEXT NOT NULL,
    receiver_account_number TEXT NOT NULL,
    receiver_account_id TEXT NOT NULL,
    amount_received TEXT NOT NULL,
    receiving_currency TEXT NOT NULL,
    amount_paid TEXT NOT NULL,
    payment_currency TEXT NOT NULL,
    payment_format TEXT NOT NULL,
    UNIQUE (dataset_id, source_row)
);

-- 학습/평가용 정답. 기본 거래 조회와 ID 매핑 파일에는 포함하지 않는다.
CREATE TABLE transaction_labels (
    transaction_id TEXT PRIMARY KEY NOT NULL REFERENCES transactions(transaction_id),
    is_laundering INTEGER NOT NULL CHECK (is_laundering IN (0, 1))
);

CREATE INDEX idx_transactions_dataset_time ON transactions(dataset_id, timestamp, transaction_id);
CREATE INDEX idx_transactions_sender_time ON transactions(sender_account_id, timestamp, transaction_id);
CREATE INDEX idx_transactions_receiver_time ON transactions(receiver_account_id, timestamp, transaction_id);
PRAGMA user_version = 1;
