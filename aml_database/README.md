# 거래 데이터 저장·조회

거래마다 고유한 ID를 붙여 SQLite DB에 저장하고, 필요한 거래를 찾아오는 코드입니다.
현재 만들어 둔 DB는 **SAML-D**입니다. IBM AML은 별도 데이터셋이며 아직 원본을 저장하지 않았습니다.

## 팀원이 시작하는 방법

1. GitHub에서 이 저장소의 최신 코드를 받습니다.
2. 팀에서 공유받은 `saml.sqlite3`를 `aml_database/data/` 폴더에 넣습니다. `data` 폴더가 없으면 만듭니다.
3. 저장소 최상위 폴더에서 아래 명령을 실행합니다.

```bash
python aml_database/saml_db.py list --limit 5
```

거래 5건이 출력되면 준비 완료입니다. Python 3.12에서 확인했으며, 이 DB 코드에는 추가 패키지나 API 키가 필요 없습니다.
DB를 이미 받았다면 원본 CSV를 다시 저장할 필요가 없습니다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `saml_db.py` | SAML-D 데이터 저장·조회 |
| `aml_db.py` | IBM AML 데이터 저장·조회 및 공통 함수. SAML 코드도 사용하므로 함께 둡니다. |
| `saml_schema.sql`, `schema.sql` | 각 데이터셋의 DB 테이블 구조 |
| `test_saml_db.py`, `test_aml_db.py` | 저장·조회가 올바른지 확인하는 테스트 |
| `demo_transactions.csv` | 테스트용 가상 거래 5건. 실제 IBM 데이터가 아닙니다. |
| `data/saml.sqlite3` | 실제 거래가 들어 있는 DB. 별도로 공유합니다. |

## 팀에서 맞출 것

- GNN·XAI·보고서는 **같은 거래 ID**를 사용합니다. 예: `TX_SAML_D_V1_0000000003`.
- DB 파일은 GitHub에 올리지 않고 별도로 전달합니다. 받은 파일은 각자 컴퓨터에 저장해 사용합니다.
- 다운로드한 DB는 각자의 사본이므로 한 사람의 수정이 다른 사람에게 자동 반영되지는 않습니다.
- 데이터의 정답 라벨을 모델 입력이나 LLM의 거래 근거로 넣지 않습니다.

거래 조회, 원본 파일 저장, 대시보드 연결 방법은 [SAML-D 사용법](SAML_README.md)을 참고하세요.

<details>
<summary>IBM AML을 사용하게 된다면</summary>

팀에서 같은 원본 CSV와 데이터셋 이름을 정한 뒤 실행합니다. 아래 경로는 실제 파일 위치로 바꿉니다.

```bash
python aml_database/aml_db.py import --csv "C:/data/HI-Small_Trans.csv" --dataset-id HI_SMALL_V1
python aml_database/aml_db.py list --dataset-id HI_SMALL_V1 --limit 5
```

IBM 데이터는 `data/aml.sqlite3`에 저장됩니다. SAML-D와는 은행·금액 컬럼 구조가 달라 구분해서 사용합니다.
원본 이용 조건은 [IBM 데이터 안내](https://github.com/IBM/AML-Data)를 확인하세요.

</details>
