# AML 거래 데이터 저장

**현재 사용자가 제공한 실제 파일은 `SAML-D.csv.zip`이다.** SAML-D의 실행 방법과 컬럼 설명은
[SAML_README.md](SAML_README.md)를 먼저 읽는다. 아래 `aml_db.py` 설명은 IBM 11컬럼용이다.
두 데이터셋의 금액/계좌/라벨 구조가 달라 적재기와 DB를 구분했다.

IBM AML 원본 CSV의 각 거래에 고유 ID를 부여해 SQLite DB에 저장하는 로컬 개발용 모듈이다.
Python 3.12 표준 라이브러리만 사용하므로 추가 패키지/API 키/DB 서버 설치가 필요 없다.
팀의 공유 PostgreSQL/MySQL 서버가 확정되면 이 ID 정책과 컬럼 매핑을 유지해 이식할 수 있다.
현재 코드는 SQLite용이며 해당 서버에 연결하는 기능은 포함하지 않는다.

## 왜 거래 ID가 필요한가?

CSV의 시각·금액·계좌 조합은 고유하다고 보장할 수 없다. 같은 시각에 같은 금액의 거래가 반복될 수 있다.
각 CSV 레코드에 ID를 한 번 부여하고, DB/GNN/XAI/보고서가 그 ID를 공통으로 사용한다.
이 작업은 'ID가 없어서 반드시 DB를 써야 한다'는 의미는 아니다. DB를 사용하면 거래 ID로 필요한
거래를 찾고 계좌·기간별로 조회하기 쉬워진다.

## ID 정책 v1 — 팀과 공유할 내용

- 거래 PK: `TX_{dataset_id}_{원본 레코드 번호 10자리}`
- 예: `TX_HI_SMALL_V1_0000000003` = HI_SMALL_V1 원본 CSV의 세 번째 거래.
- 행 번호는 헤더를 제외하고 1부터 센 **CSV 레코드 순번**이다. 정렬/필터/샘플링 전에 부여한다.
- PK는 같은 거래를 참조하는 식별자이며 숫자 부분으로 거래 시간 순서를 추론하면 안 된다.
- 원본에서 내용이 완전히 같은 두 행도 서로 다른 ID를 갖는다. `drop_duplicates()`를 적용하지 않는다.
- 같은 데이터셋 ID와 같은 원본 파일이면 재적재 시 건너뛰고 ID를 유지한다.
- 원본 파일 해시가 바뀌면 같은 데이터셋 ID로 덮어쓰지 않는다. 팀이 새 버전 ID를 정해야 한다.
- 계좌 ID: `ACC_{dataset_id}:{은행 ID}:{계좌번호}`. 각 구성요소의 구분 문자는 URL 인코딩한다.
  같은 계좌번호라도 은행이나 데이터셋이 다르면 구분한다. 선행 0도 보존한다.
- 지금 보고서 예시의 `TX000003`, `B`, `D`는 Mock ID다. 실제 데이터로 전환할 때 GNN/XAI/대시보드가
  모두 이 DB의 ID 또는 내보낸 매핑을 사용해야 한다. 이름이 비슷하다고 임의 대응하지 않는다.

여러 팀원이 각각 DB를 만들어도 **같은 원본 바이트 + 같은 dataset_id + 같은 적재 코드**라면 같은 ID가 생성된다.
내용 해시만을 거래 PK로 삼지 않으므로 반복 거래가 사라지지 않는다.

## 컬럼 매핑

| 원본 컬럼 | DB 컬럼 | 처리 |
| --- | --- | --- |
| Timestamp | timestamp, timestamp_raw | 조회용 ISO 시각과 원본 문자열. 시간대는 미지정 |
| From Bank | sender_bank_id | 문자열, 선행 0 보존 |
| 첫 번째 Account | sender_account_number | 송신 계좌 |
| To Bank | receiver_bank_id | 문자열, 선행 0 보존 |
| 두 번째 Account / Account.1 | receiver_account_number | 수신 계좌 |
| Amount Received | amount_received | 수취액, 십진수 문자열 |
| Receiving Currency | receiving_currency | 수취 통화, 원본 명칭 유지 |
| Amount Paid | amount_paid | 지급액, 십진수 문자열 |
| Payment Currency | payment_currency | 지급 통화, 원본 명칭 유지 |
| Payment Format | payment_format | 결제 방식 |
| Is Laundering | transaction_labels.is_laundering | 별도 학습·평가용 정답 |

`Account`라는 이름이 원본에 두 번 나오므로 `csv.reader`로 위치를 구분한다.
금액은 SQLite의 부동소수점 변환을 피하기 위해 TEXT로 저장하고 Python `Decimal`로 계산한다.
두 통화/금액을 합치거나 `US Dollar`를 임의로 `USD`로 변경하지 않는다. 0원 거래도 저장한다.
현재 STR 입력의 amount는 양수만 허용하므로 0원 거래의 보고서 처리 정책은 별도로 정해야 한다.

테이블은 `aml_datasets`(원본/해시/건수), `transactions`(거래), `transaction_labels`(정답) 세 개다.
라벨 분리는 우발적 입력 누출 방지용이다. SQLite 파일 접근 권한을 가진 사람이 라벨을 읽지 못하도록
차단하는 권한 체계는 아니다. 기본 거래 조회/ID 매핑에는 정답 라벨을 넣지 않는다.

## 실행

모든 명령은 레포지토리 최상위 폴더에서 실행한다. `--db`는 서브명령 앞에 넣는다.

### 예제 5건으로 먼저 실행

`demo_transactions.csv`는 이 기능을 검증하려고 작성한 **개발용 가상 데이터**다.
IBM 데이터에서 추출한 샘플이 아니며, 마지막 두 행은 반복 거래/통화 차이를 검사한다.
이 예제에 붙인 라벨은 모델 성능 평가에 사용할 정답이 아니다.

```bash
python aml_database/aml_db.py --db aml_database/data/demo.sqlite3 import --csv aml_database/demo_transactions.csv --dataset-id DEMO_V1
python aml_database/aml_db.py --db aml_database/data/demo.sqlite3 stats
python aml_database/aml_db.py --db aml_database/data/demo.sqlite3 get --tx-id TX_DEMO_V1_0000000003
```

### 실제 IBM 원본 CSV 적재

압축 파일이면 먼저 필요한 원본 거래 CSV를 압축 해제한다. `--csv`에는 실제 경로를 넣는다.
HI-Small 등의 파일명만으로 실제 버전/해시가 같다고 가정하지 말고 팀이 원본 파일을 맞춘다.

```bash
python aml_database/aml_db.py import --csv "C:/데이터경로/HI-Small_Trans.csv" --dataset-id HI_SMALL_V1
python aml_database/aml_db.py stats
python aml_database/aml_db.py list --dataset-id HI_SMALL_V1 --limit 5
python aml_database/aml_db.py get --tx-id TX_HI_SMALL_V1_0000000003
```

기본 저장 위치는 `aml_database/data/aml.sqlite3`다. 원본 CSV는 수정하지 않는다.
기본 10,000행씩 처리하고 전체 CSV를 메모리에 올리지 않는다. 파일 전체 적재가 성공해야 커밋한다.
실패하면 해당 파일에서 이미 처리한 배치도 롤백한다. 원본 CSV와 충분한 DB/저널 저장 공간이 필요하다.
대용량 파일 처리 시간은 실제 적재로 확인해야 하며 예제 5건 테스트로 수백만 건 성능을 보증하지 않는다.
날짜 오류·결측·잘못된 라벨 등이 있으면 해당 레코드 번호를 알려주고 중단한다. 임의 삭제하지 않는다.

기간 조회는 시작 시각 포함, 종료 시각 제외이다.

```bash
python aml_database/aml_db.py list --dataset-id HI_SMALL_V1 --start "2022-09-01T00:00:00" --end "2022-09-02T00:00:00" --limit 20
```

위 날짜는 명령 형식 예시다. 실제 적재 파일의 기간에 맞게 지정한다.

## GNN/XAI 담당자에게 전달할 ID 매핑

```bash
python aml_database/aml_db.py export-map --dataset-id HI_SMALL_V1 --output aml_database/data/hi_small_id_map.csv
```

파일에는 원본 SHA-256, 원본 레코드 번호, 거래 ID, 송신/수신 계좌 ID가 들어간다.
GNN 전처리에서 원본 CSV에 `source_row=1..N`을 먼저 붙인 뒤 매핑과 결합한다.
그다음 정렬/필터/학습 분할을 해도 거래 ID를 계속 보존한다. XAI 에지 인덱스에도 원본 거래 ID를 보관한다.
명령을 다시 실행할 때 기존 매핑 파일을 덮어쓰지 않도록 다른 출력 이름을 지정한다.

## 보고서 코드와의 관계

```python
from aml_database.aml_db import get_transaction

record = get_transaction("aml_database/data/aml.sqlite3", "TX_HI_SMALL_V1_0000000003")
```

이 함수는 DB 원본 거래만 반환한다. GNN 점수나 XAI 중요도를 만들어 주지 않는다.
대시보드/백엔드 담당자는 이 거래 조회 결과와 실제 GNN/XAI 결과를 같은 ID로 결합해 팀 공통 JSON을
구성한다. `amount/currency`에 지급액을 사용할지 수취액을 사용할지 팀이 일관되게 정해야 한다.
현재 DB는 둘 다 보존한다. 공통 JSON에 하나만 넣더라도 원본 두 금액을 같다고 가정하면 안 된다.

## 검증과 소스 관리

```bash
python -m unittest discover -s aml_database -p test_aml_db.py -v
```

반복 거래 보존, 같은 파일의 재적재, 원본 변경 거부, 실패 시 롤백, 금액 정밀도, 거래 조회 시 정답
제외 등을 검사한다. DB·원본 CSV·대규모 매핑은 `data/`에 두고 GitHub에는 코드/스키마/작은 예제만 올린다.
실제 데이터 이용/재배포는 [IBM 데이터 안내](https://github.com/IBM/AML-Data)의 데이터 라이선스를 따른다.
기술 참고: [SQLite 데이터 타입](https://www.sqlite.org/datatype3.html),
[외래 키](https://www.sqlite.org/foreignkeys.html).
