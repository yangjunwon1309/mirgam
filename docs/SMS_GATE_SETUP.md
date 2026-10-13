# SMS Gateway 연동 운영 절차

이 기능은 휴대폰에서 받은 문자를 Mirgam 중앙 API로 전달하고, 주문으로 보이는 문자만 암호화해 중앙 SQLite에 보관합니다. 주문은 사용자가 확인하고 저장해야 기존 `orders`에 들어갑니다. 접수 완료 문자 자동 발신은 기본 꺼짐이며, 켠 경우 별도 dispatcher가 처리합니다.

## 1. Render 환경 변수

중앙 API 서비스에 다음 값을 설정합니다.

| 변수 | 값 |
| --- | --- |
| `MIRGAM_PUBLIC_BASE_URL` | 배포한 API의 HTTPS 기본 주소 (예: `https://mirgam.onrender.com`) |
| `MIRGAM_SMS_ENCRYPTION_KEY` | 아래 명령으로 생성한 Fernet 키 |

PowerShell에서 키 생성:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

키는 Render 환경 변수에만 넣고 저장소, CSV, 로그에 올리지 않습니다. 이 키가 바뀌거나 사라지면 저장된 SMS Gateway 비밀번호, Signing Key, 문자 원문을 복호화할 수 없습니다. Render API와 dispatcher에는 반드시 동일한 키를 설정합니다.

## 2. 클라우드 DB 백업 및 스키마 업그레이드

SQLite Cloud 대시보드에서 대상 DB를 먼저 백업하거나 내려받습니다. 다음 명령은 원격 DB를 실제로 변경하며, 반드시 올바른 DB URL을 확인한 뒤 실행합니다.

```powershell
$env:MIRGAM_DATABASE_URL = "sqlitecloud://..."
python scripts/migrate_sms_schema.py --yes
Remove-Item Env:MIRGAM_DATABASE_URL
```

현재 마이그레이션은 `0001` 또는 `0002`에서 `0003`으로 올립니다. SMS 동의 기록과 예약 캠페인 테이블을 추가하며, 기존 고객의 동의 값은 `미확인(NULL)`으로 둡니다. 동의 확인은 실제로 받은 사전 동의가 있을 때만 진행해야 합니다. Render API는 `0003`이 적용되기 전에는 시작되지 않으므로 DB 백업 후 마이그레이션을 먼저 실행하고 서버를 배포하세요.

마이그레이션 명령 뒤 `Remove-Item`을 실행하면 해당 PowerShell 세션의 `MIRGAM_DATABASE_URL`이 지워집니다. **로컬 EXE를 실행할 때도** 같은 PC에서 URL을 다시 설정해야 합니다. 로컬 API 시작에는 최대 90초가 걸릴 수 있고, 시작 실패 상세는 `%LOCALAPPDATA%\Mirgam\logs\local-api.log`에서 확인합니다. 로그를 공유하기 전 연결 주소나 키가 포함되지 않았는지 확인하세요.

```powershell
$env:MIRGAM_DATABASE_URL = "sqlitecloud://..."
& .\build\sqlite-sms-mirgam-render\Mirgam\Mirgam.exe
```

## 3. 배포 및 농장 연결

1. `requirements-server.txt`가 반영된 서버를 배포하고 Render 로그에서 정상 시작을 확인합니다.
2. Mirgam 클라이언트에서 **설정 → 문자 설정**으로 갑니다.
3. SMS Gateway 앱의 Cloud Server 주소, 계정 사용자명/비밀번호, Device ID, Webhooks Signing Key를 입력하고 휴대폰 번호와 발신 SIM을 지정합니다.
4. SMS 수신 웹훅 등록 알림이 휴대폰에 뜨는지 확인합니다. 연결은 device-specific webhook으로 등록됩니다.
5. 주문 전용 테스트 번호에서 `[주문] 사과 2상자 보내주세요` 같은 문자를 보냅니다. **문자 주문**에서 확인한 뒤 주문으로 저장합니다.
6. 실기기 수신을 확인한 다음에만 자동 접수 문자 옵션을 켭니다.

Cloud 모드의 웹훅 등록은 앱과 동기화되는 데 시간이 걸릴 수 있습니다. 새 웹훅이 SMS Gateway 앱의 Settings → Webhooks → Registered webhooks 목록에 나타나는지 확인하세요. 연결 해제 시 Mirgam은 등록한 웹훅 삭제를 시도합니다.

## 4. 문자 일괄 발송 예약

문자 주문 화면에서 고객을 이름으로 검색하고 발송 대상을 고릅니다. 기본으로 선택되는 대상은 광고 수신 동의가 기록된 고객뿐입니다. 기존 고객의 사전 동의를 실제로 받았다는 확인과 동의 날짜를 등록해도 수신 거부 고객은 다시 동의 처리되지 않습니다. SMS 수신 거부 회신은 해당 고객의 발송 동의를 해제합니다.

광고 문자에는 발신자 표시와 연락처, 무료 수신거부 방법이 필요합니다. 따라서 농장 발신번호와 080 무료 수신거부 번호, 광고 내용 검토 확인이 있어야 예약할 수 있습니다. 발송 시간은 08:00~21:00 안에서 설정되며, 기본은 09:00~18:00입니다. 하루 발송 상한과 기간을 정하면 대상자와 시간대를 무작위로 나누어 예약합니다. 통신사별 허용량을 자동 확인하는 기능은 아니므로 보유한 회선의 한도에 맞춰 설정하세요. 예약은 실제 문자를 보낼 수 있는 작업이므로 대상·문구·요금을 확인하고 제출하고, 진행 중에도 남은 문자를 취소할 수 있습니다.

## 5. 발신 dispatcher

Flask 웹 프로세스에는 예약된 접수/캠페인 문자를 처리하는 dispatcher가 포함되어 있습니다. Render 설정은 한 개의 Gunicorn worker를 유지하세요. 여러 서버 프로세스나 dispatcher를 동시에 실행하지 마세요. 아래 명령은 별도 운영 방식을 쓸 때만 사용합니다.

```powershell
$env:MIRGAM_DATABASE_URL = "sqlitecloud://..."
$env:MIRGAM_SMS_ENCRYPTION_KEY = "Render에 설정한 동일한 키"
python scripts/run_sms_dispatcher.py
```

별도 dispatcher 방식을 선택하면 작업자를 하나만 둡니다. 타임아웃으로 결과가 불명확한 발신은 중복 문자를 막기 위해 `결과 확인 필요`로 남기며 자동 재시도하지 않습니다.

## 현재 범위와 주의사항

- SMS Gateway 수신 웹훅에는 발신번호/본문 필터가 없습니다. 휴대폰으로 오는 이벤트는 먼저 중앙 API에 도착합니다. Mirgam은 주문 의도를 나타내는 `[주문]`, `주문`, `보내주세요` 등 명시 표현이 없는 문자를 DB에 저장하지 않습니다. 제품명 언급만으로는 보관하지 않습니다.
- 필터는 보안·개인정보 경계가 아니며, 주문 표현이 들어간 개인 문자라면 검토함에 남을 수 있습니다. 개인용 폰을 연결할 때는 이 점을 감수해야 하며 주문 전용 SIM/폰이 가장 안전합니다.
- 현재 초안 제안은 상품명·수량의 단순 추출뿐입니다. 주문 대상 고객, 상품, 수량, 날짜를 반드시 확인하고 확정하세요. 한 문자에서 여러 상품 주문은 아직 지원하지 않습니다.
- 앱/웹훅 연결 자격 정보와 본문은 Fernet으로 암호화합니다. 웹훅 중복은 provider event ID로 제거합니다. `sms:sent`는 휴대폰 발신 완료이며 `sms:delivered`와 같지 않습니다.
- 현재 `sms:received`를 통해 도착한 일반 문자는 Mirgam 서버에 전달되지만, 주문 분류 조건을 통과하지 않는 본문은 SQLite에 남지 않습니다. SMS Gateway Cloud 측 전송·보관 정책은 별도로 확인하세요.
