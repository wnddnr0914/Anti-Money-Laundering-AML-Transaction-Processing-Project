-- SAML-D용. IBM 11컬럼 DB와 구분한다. 라벨은 별도 테이블에 보존한다.
CREATE TABLE saml_datasets (
    dataset_id TEXT PRIMARY KEY NOT NULL,
    source_filename TEXT NOT NULL,
    source_member TEXT,
    csv_sha256 TEXT NOT NULL UNIQUE,
    csv_size_bytes INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    timestamp_timezone TEXT NOT NULL,
    amount_unit_policy TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY NOT NULL,
    dataset_id TEXT NOT NULL REFERENCES saml_datasets(dataset_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    timestamp TEXT NOT NULL,
    date_raw TEXT NOT NULL,
    time_raw TEXT NOT NULL,
    sender_account TEXT NOT NULL,
    receiver_account TEXT NOT NULL,
    amount TEXT NOT NULL,
    payment_currency TEXT NOT NULL,
    received_currency TEXT NOT NULL,
    sender_bank_location TEXT NOT NULL,
    receiver_bank_location TEXT NOT NULL,
    payment_type TEXT NOT NULL,
    UNIQUE (dataset_id, source_row)
);
CREATE TABLE transaction_labels (
    transaction_id TEXT PRIMARY KEY NOT NULL REFERENCES transactions(transaction_id),
    is_laundering INTEGER NOT NULL CHECK (is_laundering IN (0, 1)),
    laundering_type TEXT NOT NULL
);
CREATE INDEX idx_saml_time ON transactions(dataset_id, timestamp, transaction_id);
CREATE INDEX idx_saml_sender_time ON transactions(dataset_id, sender_account, timestamp);
CREATE INDEX idx_saml_receiver_time ON transactions(dataset_id, receiver_account, timestamp);
PRAGMA user_version=101;
