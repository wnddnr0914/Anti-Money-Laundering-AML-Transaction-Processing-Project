# SAML-D 사용법

**DB 파일을 이미 받았다면 1번부터 사용하면 됩니다.** 모든 명령은 저장소 최상위 폴더에서 실행합니다.

## 1. 거래 확인하기

`saml.sqlite3`를 `aml_database/data/`에 넣습니다.

```bash
# 거래 5건 보기
python aml_database/saml_db.py list --limit 5

# 거래 ID로 1건 찾기
python aml_database/saml_db.py get --tx-id TX_SAML_D_V1_0000000003

# 저장된 거래 수와 기간 확인하기
python aml_database/saml_db.py stats
```

## 2. 대시보드 코드에서 사용하기

저장소 루트에서 실행하는 Python 코드에서는 다음처럼 거래를 가져올 수 있습니다.

```python
from aml_database.saml_db import get_transaction

transaction = get_transaction(
    "aml_database/data/saml.sqlite3",
    "TX_SAML_D_V1_0000000003",
)
```

결과는 거래 정보를 담은 딕셔너리입니다. 해당 ID가 없으면 `None`을 반환합니다.
GNN 점수와 XAI 결과는 별도로 받아 **같은 거래 ID**로 연결합니다.

## 3. 거래 ID의 의미

`TX_SAML_D_V1_0000000003`은 SAML-D 원본 CSV에서 헤더를 제외한 세 번째 거래입니다.
팀원 모두 이 ID를 그대로 사용해야 같은 거래를 찾을 수 있습니다.
원본을 정렬하거나 일부 행을 삭제한 뒤 새 번호를 붙이지 않습니다.

## 4. DB를 직접 만들어야 할 때만

DB를 받지 못했다면 원본 ZIP에서 만들 수 있습니다. 아래 경로를 본인의 파일 위치로 바꾸세요.

```bash
python aml_database/saml_db.py import --source "C:/data/SAML-D.csv.zip"
```

압축을 미리 풀 필요는 없습니다. 완료되면 `aml_database/data/saml.sqlite3`가 생깁니다.
같은 원본과 같은 데이터셋 이름을 쓰면 거래 ID도 같습니다. 같은 파일을 다시 실행해도 중복 저장하지 않습니다.

## 사용할 때 기억할 점

- `Is_laundering`, `Laundering_type`은 학습·평가용 **정답**입니다. 별도 테이블에 있으며 기본 거래 조회에서는 제외됩니다.
- 은행 소재 국가는 은행 ID가 아닙니다. 금액의 통화 의미와 시간대는 원본 설명을 확인해 보고서에 연결해야 합니다.
- DB에는 거래 정보와 정답이 들어 있습니다. GNN 점수나 XAI 결과는 들어 있지 않습니다.
- DB를 공유할 때는 저장 작업을 마치고 프로그램을 정상 종료한 뒤 복사합니다.

원본 출처와 이용 안내: [SAML-D 공식 저장소](https://github.com/BOztasUK/Anti_Money_Laundering_Transaction_Data_SAML-D).

테스트가 필요하면 다음 명령을 실행합니다.

```bash
python -m unittest discover -s aml_database -p "test_*db.py" -v
```
