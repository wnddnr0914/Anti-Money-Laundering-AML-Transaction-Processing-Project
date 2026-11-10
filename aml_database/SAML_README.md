# SAML-D 실제 데이터 저장

사용자가 제공한 `C:/Users/lyh30/Desktop/SAML-D.csv.zip`은 **SAML-D**다.
기존 계획서의 IBM AML과 다른 데이터셋이므로 팀의 데이터셋 명칭/선정 사유도 실제 사용 파일에 맞춰 확인해야 한다.
작성자 안내: [SAML-D 공식 저장소](https://github.com/BOztasUK/Anti_Money_Laundering_Transaction_Data_SAML-D).

## 지금 사용할 파일

- `saml_db.py`: 실제 SAML-D ZIP/CSV 저장·조회. Python 표준 라이브러리만 사용.
- `saml_schema.sql`: SAML-D 테이블 구조.
- `data/saml.sqlite3`: 로컬에 적재되는 실제 DB. Git 업로드 대상 아님.
- `test_saml_db.py`: 작은 사례로 적재 무결성을 검증하는 자동 테스트.
- `aml_db.py`의 일반 유틸리티를 재사용하므로 이 파일도 같은 폴더에 둔다.

기존 `aml_db.py`, `schema.sql`, `demo_transactions.csv`, `data/demo.sqlite3`는 IBM 형식용 구현/예제다.
두 데이터셋을 임의 변환하거나 하나의 데이터셋처럼 합치지 않는다.

## 거래 PK와 계좌 ID

거래 PK는 **`TX_SAML_D_V1_0000000003`**처럼 데이터셋 이름과 원본 CSV의 레코드 순번으로 만든다.
헤더를 제외한 세 번째 행이라는 뜻이다. 거래 내용을 해시하여 중복 제거하지 않으므로 같은 내용의
반복 거래도 보존된다. 전처리 전에 ID를 붙이고 이후 GNN/XAI/보고서에서 같은 ID를 유지한다.

같은 원본 CSV를 다시 넣으면 건너뛴다. ZIP의 압축 방식이나 내부 파일명만 바뀌어도 CSV 내용이 같으면
같은 파일로 인식한다. 다른 내용을 같은 `dataset_id`로 덮어쓰지는 않는다.
새 버전 데이터는 팀이 새 `dataset_id`를 정해야 한다.

계좌는 원본 계좌번호를 문자열로 보존하고 조회 결과에 `ACC_SAML_D_V1:계좌번호`를 함께 반환한다.
SAML-D에는 은행 ID가 없으므로 **은행 소재 국가를 은행 ID로 대체하지 않는다.**

## 컬럼과 주의할 차이

| 원본 | 저장 내용 |
| --- | --- |
| Date + Time | 조회용 timestamp, 원본 date_raw/time_raw |
| Sender_account / Receiver_account | 송신/수신 계좌 문자열, 선행 0 보존 |
| Amount | 원본 단일 금액. Decimal로 검증해 문자열 저장 |
| Payment_currency / Received_currency | 각각 지급/수취 통화 정보로 보존 |
| Sender_bank_location / Receiver_bank_location | 은행 소재 국가로 보존 |
| Payment_type | 결제 유형 |
| Is_laundering / Laundering_type | **별도 transaction_labels 테이블에만 보존** |

`Amount`와 통화 컬럼의 의미를 확정하지 않은 상태에서 Amount를 두 통화의 금액으로 복제하거나
`amount_paid=amount_received`로 만들지 않는다. 현재 적재기는 금액 단위와 시간대를 파일에서
추정하지 않으며, metadata에 각각 `raw_amount_unit_not_inferred`, `unspecified_in_file`로 명시한다.
팀이 원 저자의 데이터 설명을 확인한 뒤 보고서 공통 JSON의 amount/currency 매핑을 확정해야 한다.

`Laundering_type`은 모델이 예측한 유형이 아니라 **데이터에 붙어 있는 정답**이다.
이 값을 보고서 입력의 `typology_analysis.predicted_type`으로 넣으면 정답 누출이 된다.
기본 거래 조회와 매핑 내보내기에는 두 정답 컬럼을 모두 제외했다.
별도 테이블은 권한 격리 장치가 아니므로 DB를 직접 읽는 학습 코드에서도 입력 컬럼을 명시해야 한다.

## 실행 — 레포지토리 최상위 폴더

```bash
python aml_database/saml_db.py import --source "C:/Users/lyh30/Desktop/SAML-D.csv.zip"
python aml_database/saml_db.py stats
python aml_database/saml_db.py list --limit 3
python aml_database/saml_db.py get --tx-id TX_SAML_D_V1_0000000003
```

기본 DB는 `aml_database/data/saml.sqlite3`다. 다른 경로는 서브명령 앞에 `--db 경로`를 지정한다.
ZIP을 별도 파일로 압축 해제하지 않고 내부 CSV를 스트리밍으로 읽는다.
기본 20,000행 단위로 처리하며 **전체 파일 적재가 성공해야 커밋**한다. 오류가 나면 이번 파일의
적재 전체를 롤백한다. 원본 ZIP은 변경하지 않는다. 전체 적재 중에는 진행 행 수가 출력된다.

```bash
python aml_database/saml_db.py list --start "2022-10-07T00:00:00" --end "2022-10-08T00:00:00" --limit 20
python aml_database/saml_db.py export-map --output aml_database/data/saml_id_map.csv
```

기간 조회는 시작 포함/종료 제외다. 매핑에는 CSV 해시·원본 행 번호·거래 ID가 들어간다.
약 950만 행의 전체 매핑 CSV도 크므로 필요할 때만 내보낸다. 기존 출력 파일을 덮어쓰지 않는다.

## 대시보드/백엔드에서 조회

```python
from aml_database.saml_db import get_transaction

transaction = get_transaction(
    "aml_database/data/saml.sqlite3",
    "TX_SAML_D_V1_0000000003",
)
```

이 반환값은 원본 거래 정보다. GNN 점수, XAI 결과, 주변 거래망은 각 모듈에서 얻어 같은 거래 ID로
결합해야 한다. 거래 DB를 만들었다고 실제 GNN/XAI 결과가 자동 생성되는 것은 아니다.
팀 공통 JSON을 받는 기존 STR 생성 코드는 그대로 유지했다.

테스트:

```bash
python -m unittest discover -s aml_database -p "test_*db.py" -v
```

`data/`에는 DB·원본·대규모 매핑을 두고 GitHub에는 코드/스키마/사용법만 올린다.
실제 데이터의 사용·재배포는 원 데이터의 이용 조건을 따른다.
