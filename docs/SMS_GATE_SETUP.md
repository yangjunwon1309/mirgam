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

기존 스키마 `0001`에 문자 관련 테이블만 추가하는 `0002` 마이그레이션입니다. 기존 accounts/customers/orders 행을 다시 쓰지 않습니다. 이미 `0002`이면 아무 작업도 하지 않습니다. Render API는 `0001`과 `0002` 모두 시작할 수 있지만, 문자 연결은 `0002`가 적용되기 전까지 사용할 수 없습니다.

## 3. 배포 및 농장 연결

1. `requirements-server.txt`가 반영된 서버를 배포하고 Render 로그에서 정상 시작을 확인합니다.
2. Mirgam 클라이언트에서 **상품·고객 파일 관리 → 문자 주문 연결**로 갑니다.
3. SMS Gateway 앱의 Cloud Server 주소, 계정 사용자명/비밀번호, Device ID, Webhooks Signing Key를 입력하고 휴대폰 번호와 발신 SIM을 지정합니다.
4. SMS 수신 웹훅 등록 알림이 휴대폰에 뜨는지 확인합니다. 연결은 device-specific webhook으로 등록됩니다.
5. 주문 전용 테스트 번호에서 `[주문] 사과 2상자 보내주세요` 같은 문자를 보냅니다. **문자 주문**에서 확인한 뒤 주문으로 저장합니다.
6. 실기기 수신을 확인한 다음에만 자동 접수 문자 옵션을 켭니다.

Cloud 모드의 웹훅 등록은 앱과 동기화되는 데 시간이 걸릴 수 있습니다. 새 웹훅이 SMS Gateway 앱의 Settings → Webhooks → Registered webhooks 목록에 나타나는지 확인하세요. 연결 해제 시 Mirgam은 등록한 웹훅 삭제를 시도합니다.

## 4. 발신 dispatcher

Flask 웹 프로세스는 발신 대기열을 저장하지만 문자를 직접 보내지 않습니다. 한 개의 독립 작업자를 계속 실행해야 자동 접수 문자가 발신됩니다. 먼저 동일한 Python 환경과 서버 DB 접근이 가능한 호스트에서 실행합니다.

```powershell
$env:MIRGAM_DATABASE_URL = "sqlitecloud://..."
$env:MIRGAM_SMS_ENCRYPTION_KEY = "Render에 설정한 동일한 키"
python scripts/run_sms_dispatcher.py
```

운영 시에는 Render의 별도 Background Worker 또는 항상 실행되는 서버에 작업자를 하나만 둡니다. 무료 웹 서비스 프로세스 내부에 임시 스레드를 띄우는 방식은 사용하지 않습니다. 여러 dispatcher를 동시에 실행하지 마세요. 타임아웃으로 결과가 불명확한 발신은 중복 문자를 막기 위해 `결과 확인 필요`로 남기며 자동 재시도하지 않습니다.

## 현재 범위와 주의사항

- SMS Gateway 수신 웹훅에는 발신번호/본문 필터가 없습니다. 휴대폰으로 오는 이벤트는 먼저 중앙 API에 도착합니다. Mirgam은 주문 의도를 나타내는 `[주문]`, `주문`, `보내주세요` 등 명시 표현이 없는 문자를 DB에 저장하지 않습니다. 제품명 언급만으로는 보관하지 않습니다.
- 필터는 보안·개인정보 경계가 아니며, 주문 표현이 들어간 개인 문자라면 검토함에 남을 수 있습니다. 개인용 폰을 연결할 때는 이 점을 감수해야 하며 주문 전용 SIM/폰이 가장 안전합니다.
- 현재 초안 제안은 상품명·수량의 단순 추출뿐입니다. 주문 대상 고객, 상품, 수량, 날짜를 반드시 확인하고 확정하세요. 한 문자에서 여러 상품 주문은 아직 지원하지 않습니다.
- 앱/웹훅 연결 자격 정보와 본문은 Fernet으로 암호화합니다. 웹훅 중복은 provider event ID로 제거합니다. `sms:sent`는 휴대폰 발신 완료이며 `sms:delivered`와 같지 않습니다.
- 현재 `sms:received`를 통해 도착한 일반 문자는 Mirgam 서버에 전달되지만, 주문 분류 조건을 통과하지 않는 본문은 SQLite에 남지 않습니다. SMS Gateway Cloud 측 전송·보관 정책은 별도로 확인하세요.
