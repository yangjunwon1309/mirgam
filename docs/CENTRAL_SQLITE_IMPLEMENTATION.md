# 중앙 SQLite 개발 버전 실행 안내

반영일: 2026-10-07. 기존 설계는 [작업 명세서](CENTRAL_SQLITE_WORK_SPEC.md)를 참고한다.

## 이번에 반영한 구조

```text
브라우저 → 로컬 Flask 클라이언트(5000)
                    ↓ HTTP /api/v1 + 인증 토큰
           별도 중앙 Flask API(5100)
                    ↓ SQLAlchemy / Alembic
           var/server/mirgam.sqlite3
```

클라이언트는 고객·상품·주문·로그인을 CSV에서 조회하지 않는다. SQLite 파일은 중앙 서버만 열며, 다른 PC에는 API 주소만 설정한다. SQLite 파일을 네트워크 공유 폴더에 두지 않는다. 개발 실행에도 Waitress를 사용한다.

기존 녹색/베이지 화면, 탭 순서, 고객 검색·신규 등록 팝업, 고객별 주문 입력, 전체 적용 창, 날짜 캘린더, CSV 저장 창을 유지했다. 고객/주문 목록은 페이지당 50건이고 검색·정렬·날짜 필터는 서버에서 처리한다. 다운로드는 현재 필터의 **모든 페이지**를 포함한다.

## 현재 데이터 이전 결과

사용자가 지정한 `apps/static`의 현재 CSV로 새 개발 DB를 초기화했다. `dist/Mirgam/data`는 사용하지 않았다.

| 계정 | 고객 원본 행 | DB 고객 | DB 주문 |
| --- | ---: | ---: | ---: |
| user | 415 | 399 | 0 |
| 준이네 농장 | 826 | 826 | 1,061 |
| admin | 0 | 0 | 0 |

상품 8개는 기존처럼 계정 간 공용이다. 고객 기준 파일은 `customer_upload_<계정>.csv`다. `user`의 `customer_user.csv`와 기준 파일의 차이는 보고만 했고 자동 병합하지 않았다. 완전히 동일한 이름·전화번호·주소의 고객 16행만 중복 제거했다. 주소가 다른 고객은 별도 고정 ID다.

주문은 중복처럼 보여도 원본 1행당 DB 1행으로 모두 유지한다. 주문일을 해석할 수 없는 1건도 삭제/추측 수정하지 않았다. 해당 주문은 전체 내역/다운로드에 포함되지만 날짜 캘린더에서는 선택되지 않는다. 전화번호·수량·가격·주문일 원문은 문자열로 보존한다.

원본 CSV의 SHA-256을 이전 기록에 보관하고 동일한 파일을 `var/server-source-backup/<해시>/`에 복사했다. 원본 CSV는 수정하지 않았으며 변경 여부를 실제 HTTP 검증 CLI에서 다시 확인한다. **이전 후 사이트의 변경은 DB에만 반영된다. 원본 CSV를 계속 수정해도 자동 반영되지 않는다.**

## 실행

프로젝트 루트에서 Python 3.10 이상으로 실행한다.

```powershell
python -m pip install -r requirements-server.txt -r requirements-client.txt

# 다른 환경에서 처음 설치할 때: 먼저 확인, 확인 후 일회성 이전
python scripts/migrate_csv_to_sqlite.py --source apps/static --data-dir var/server
python scripts/migrate_csv_to_sqlite.py --source apps/static --data-dir var/server --apply

# API와 클라이언트 두 프로세스를 함께 실행하고 브라우저 열기
python scripts/run_local.py
```

이미 이 노트북의 `var/server`에는 이전을 완료했다. 같은 원본으로 `--apply`를 반복해도 추가하지 않는다. 이전 원본이 달라졌거나 다른 계정 데이터가 있는 DB에는 자동 재이전하지 않고 중단한다. `Start-Mirgam-Local.cmd`로도 함께 실행할 수 있다. 종료는 터미널에서 Ctrl+C를 사용한다. 그러면 이 실행 도구가 시작한 두 프로세스만 종료한다. 터미널을 강제로 닫으면 자식 프로세스가 남을 수 있다. 같은 포트의 기존 프로그램은 강제 종료하지 않는다.

각각 별도 터미널로 실행하려면:

```powershell
python scripts/run_api_server.py --data-dir var/server --port 5100
python scripts/run_client.py --api-url http://127.0.0.1:5100 --port 5000
```

사이트: <http://127.0.0.1:5000/>. API 상태: <http://127.0.0.1:5100/health>. 기존 농장 이름/비밀번호로 로그인한다. 비밀번호는 DB에서 해시로 보관하며 서버가 자동 계정을 생성하지 않는다.

다른 API에 연결하려면 `--api-url` 또는 환경변수 `MIRGAM_API_BASE_URL`을 변경한다. 루트(EXE는 EXE 옆)의 `client.json`도 지원하며 형식은 `client.example.json`과 같다. 우선순위는 명령행 > 환경변수 > JSON > 로컬 기본 주소다. 토큰은 클라이언트 프로세스 메모리에만 보관한다. 재시작하면 다시 로그인한다.

서버 설정: `--host`, `--port`, `--data-dir` 또는 `MIRGAM_SERVER_HOST`, `MIRGAM_SERVER_PORT`, `MIRGAM_SERVER_DATA_DIR`. 기본은 loopback 전용이다. 외부 호스팅 전에는 HTTPS 리버스 프록시, 접근 제한, 강한 비밀번호, 외부 백업을 구성해야 한다. 현재 HTTP 주소를 그대로 공개하지 않는다.

## 고객 파일 가져오기

1. 상품·고객 파일 관리에서 `.xlsx` 또는 `.csv` 선택.
2. 엑셀 시트, 제목 행(없음 가능), CSV 인코딩/구분자를 확인.
3. 미리보기 예시를 보고 이름·전화번호·주소에 사용할 서로 다른 열 3개 선택.
4. 누락·숫자형 전화번호·수식·중복 등을 확인. 오류가 있으면 파일을 고쳐 다시 선택.
5. 새로 추가/기존과 중복/삭제 상태에서 복구되는 고객 수를 확인하고 승인.

파일은 해당 계정의 기존 고객에 **추가**한다. 기존에 활성 상태인 고객과 이름·전화번호·주소가 모두 같은 행, 그리고 파일 안의 중복 행은 건너뛴다. 삭제된 고객과 정확히 일치하면 원래 ID로 복구한다. 파일에 없는 고객은 건드리지 않으며 과거 주문도 유지한다. 미리보기 자체로 고객 DB가 바뀌지 않는다. 미리보기 후 다른 PC가 고객을 변경하면 다시 미리보기를 요구한다. 업로드 파일은 10MB, 고객 20,000행, 100열 제한이며 24시간 후 정리한다. 새 업로드의 기본 옵션은 제목 행 1번째 줄, UTF-8, 쉼표다. CP949 파일 등은 직접 옵션을 변경할 수 있다. 표본은 원본 10행/검증 후 최대 50행이다.

고객 정보 수정과 상품 가격 수정도 API에 연결했다. 고객 이름/주소를 바꿔도 과거 주문의 수령인/배송 정보는 변경하지 않는다. 수량 정렬은 이전 명세대로 현재 고객의 이름·전화번호·주소와 같은 주문의 수량 합계 기준이다. 신규 주문 수량은 양수이며 최대 1조까지 검증한다.

## 주문 저장 및 백업

주문 저장 시 로컬 저장 대화상자에서 폴더/파일명을 고른다. 기본 폴더는 Downloads, 기본 이름은 한국 시간의 `YYYY_MM_DD.csv`다. 취소하거나 폴더 쓰기가 불가능하면 새 주문을 저장하지 않고 입력을 유지한다.

여러 주문은 하나의 DB 트랜잭션으로 저장한다. 요청 ID를 재사용하여 더블클릭·응답 유실·재시도에 따른 중복 추가를 막는다. DB 저장 후 CSV 저장이 실패하면 화면에 별도 안내하고 CSV만 재저장한다. 저장 결과가 불확실할 때는 같은 요청으로 결과를 확인한다. 실패 후 재시도 전 화면 새로고침/프로그램 종료는 피한다. 새 화면의 주문 입력은 새로운 요청이므로 사람이 같은 주문을 다시 입력하는 것까지 자동 중복 제거하지 않는다.

서버가 시작될 때와 이후 24시간 간격으로 SQLite 온라인 백업을 만든다. 최근 일일 백업 30개를 유지하고 고객 추가·최초 이전 전의 백업은 별도 보존한다. 폴더는 `var/server-backups/`다.

```powershell
python scripts/backup_server.py --data-dir var/server
```

백업 파일에는 개인정보가 있다. 원본 CSV 백업과 함께 접근을 제한한다. 운영용 복구 자동화와 외부 백업 보관 정책은 후속 배포 단계에서 검증해야 한다. 실행 중인 DB 파일 하나만 복사하지 말고 온라인 백업 파일을 사용한다.

## 검증

```powershell
# 임시 데이터만 사용: 계정 격리, CRUD, 동시 등록, 원자성, XLSX/CSV 매핑,
# 응답 유실/CSV 실패 재시도, 두 클라이언트 공유 등
python -m unittest tests.test_central_api -v
node scripts/verify_order_bulk.cjs

# API/클라이언트 실행 중 실제 이전 데이터의 조회·필터 내보내기 확인
python scripts/verify_live_sqlite.py
```

실데이터 CLI는 고객/주문/상품을 변경하지 않는다. 검증용 로그인 세션만 생성/해제하고 비밀번호·토큰·고객 값은 출력하지 않는다. 기존 `scripts/run_mirgam.py --verify-only`도 새 격리 테스트를 실행한다. 과거 `verify_customer_identity.py`, `verify_quick_customer.py`, `verify_order_save_dialog.py`의 CSV 백엔드 검증은 구버전 전용이다.

2026-10-07 검증 결과: 격리 API/클라이언트 테스트 37건 통과, 주문 화면 JavaScript 회귀 검사 통과. 실제 이전 DB의 고객/주문 전체 필드·주문 중복 행·상품 가격 원문 보존, 필터 다운로드, 주요 페이지 HTTP 200, 원본 CSV 해시 불변을 확인했다. 온라인 백업의 무결성/외래키 검사를 통과했다. 함께 실행 CLI도 대체 포트에서 시작과 Ctrl+C 종료를 확인했다.

이번 환경에는 연결 가능한 Browser가 없어 시각적 브라우저 검증은 하지 못했다. 실제 API/클라이언트 HTTP 응답과 화면 JavaScript 회귀 테스트로 확인했다. 배포 전 실제 브라우저/네이티브 저장 창과 EXE 실행 검증이 필요하다.

## EXE와 후속 작업

**기존 `dist/Mirgam` EXE와 데이터는 이번에 교체하지 않았다.** 이번 버전은 먼저 소스 실행으로 검증한다. 기존 EXE는 여전히 구 CSV 버전이다. `Update-Mirgam.cmd`도 구버전 업데이트 패키지용이므로 이번 SQLite 버전 반영에 사용하지 않는다.

서버/클라이언트 분리 빌드 스크립트는 준비했다:

```powershell
python -m pip install pyinstaller
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_desktop.ps1
```

출력은 `build/sqlite-release/Mirgam`와 `build/sqlite-release/MirgamServer`이며, `apps/static`·비밀번호·실제 DB를 포함하지 않는다. 클라이언트에는 템플릿만, 서버에는 Alembic 스키마만 포함한다. 서버 EXE는 기본적으로 자신의 옆 `data`를 사용하므로 개발 DB를 쓰려면 명시적으로 `--data-dir`을 지정한다. 새 서버의 빈 DB에 자동 계정 생성은 없다.

이 빌드 경로는 기존 `dist`를 삭제/덮어쓰지 않는다. **이번 작업에서 EXE 빌드·이관·복구 검증은 아직 수행하지 않았다.** 이후 새 패키지의 시작, 저장 창, 서버 중단 안내, 백업 복구, 배포 업데이트 경로를 검증하고 배포한다. 현재는 로컬 분리 서버 구현 단계이며 실제 외부 호스팅은 하지 않았다.
