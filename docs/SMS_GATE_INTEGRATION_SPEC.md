# Mirgam × SMS Gateway for Android 연동 작업 명세서

작성일: 2026-10-08  
상태: 설계안. 앱 설치, 실제 문자 송수신, DB 변경 및 배포는 아직 수행하지 않음.

> 코드 구현 및 배포·DB 마이그레이션 절차는 [SMS_GATE_SETUP.md](SMS_GATE_SETUP.md)를 참고하세요. 클라우드 DB 변경과 실기기 송수신 검증은 운영자가 진행해야 합니다.

## 1. 목표와 1차 범위

Mirgam 계정에 Android 휴대폰을 연결한다. 휴대폰에 온 **주문 문자**를 Mirgam의 `문자 주문` 화면에서 검토하고, 담당자가 확정하면 기존 주문 DB에 저장한다. 저장된 SMS 주문에 한해 발신자에게 `주문 접수 완료` 문자를 자동 발신하고 결과를 추적한다. 기존 주문 입력·고객 관리·CSV 내보내기는 유지한다.

현재 `accounts`는 사람별 계정이 아니라 농장별 로그인 계정이다. 1차 버전의 `Mirgam user별 휴대폰`은 **계정(`accounts.id`)당 활성 기기 1대**를 뜻한다. 한 농장의 여러 직원이 각자 폰을 연결해야 한다면 사람별 사용자·권한 테이블을 먼저 추가해야 한다. DB 구조는 나중에 기기 여러 대로 확장할 수 있게 만든다.

1차 버전은 SMS 텍스트만 지원한다. MMS, RCS, 문자 발송 캠페인, 고객에게 보내는 광고/홍보 문자, 완전 자동 주문 확정은 범위 밖이다. 명확한 문구는 주문 초안으로 자동 해석할 수 있지만, **주문 생성과 완료 문자는 담당자의 확정 이후**에만 진행한다.

## 2. 선택한 연결 방식

초기 검증에는 SMS Gateway 앱의 **Cloud Server 모드**를 사용한다. 폰이 모바일 데이터망이나 다른 Wi-Fi에 있어도 SMS Gateway API로 발신 요청을 전달할 수 있고, 앱이 수신 SMS를 Mirgam의 공개 HTTPS 웹훅으로 보낼 수 있다. Mirgam 데스크톱 EXE는 기존처럼 Render의 중앙 Flask API만 호출한다. SMS Gateway 계정 비밀번호·서명 키는 EXE나 브라우저에 저장하지 않는다. [Cloud 모드 안내](https://docs.sms-gate.app/getting-started/public-cloud-server/), [웹훅 안내](https://docs.sms-gate.app/features/webhooks/).

```text
고객 문자 → 농장 Android 폰(SMS Gateway 앱)
          → HTTPS 웹훅 → Mirgam 중앙 Flask API(Render)
          → SQLite Cloud: 수신함/주문 초안
          → Mirgam UI에서 검토·확정
          → 기존 order_batches + orders 저장 / 발신 대기 기록 생성
          → 발신 작업자 → SMS Gateway API → 같은 농장 폰의 SIM → 문자 발신자
          ← 전송·배달·실패 웹훅으로 결과 반영
```

SMS Gateway 앱과 Cloud 모드는 공식 가격 문서상 무료다. 실제 SIM의 문자 발송 요금·한도는 사용자 통신 요금제에서 확인한다. Cloud 모드는 인터넷 연결이 필요하다. [가격 안내](https://docs.sms-gate.app/pricing/), [Cloud 모드 요구사항](https://docs.sms-gate.app/getting-started/public-cloud-server/).

### 개인 휴대폰의 개인정보 경계

현재 공식 웹훅 등록 문서에서 확인되는 제한은 **이벤트 종류**와 **기기 ID**뿐이다. 발신번호·본문 키워드로 폰에서 미리 거르는 옵션은 문서에 없다. 따라서 `sms:received` 웹훅을 등록하면 주문 외 일반 SMS도 Mirgam 서버에 전달된다고 전제한다. 서버에서 `[주문]` 접두어나 등록된 주문 발신자/형식을 검사해 원문 저장을 막을 수는 있지만, 서버에 도착하기 전 전송 자체는 막지 못한다. 연결 화면에서 이 사실을 분명히 알리고 사용자가 연결을 활성화하도록 한다. 주문이 아닌 SMS의 본문·전화번호는 Mirgam DB와 로그에 남기지 않는다. [웹훅 등록 필드](https://docs.sms-gate.app/features/webhooks/), [기기 설정 항목](https://docs.sms-gate.app/features/settings-management/).

Cloud 모드의 개인정보 흐름은 공식 저장소 설명에도 서로 다른 문구가 있다. 한 곳에는 수신 메시지가 Cloud 서버에 자동 업로드된다고 적혀 있고, 다른 곳에는 웹훅은 기기에서 직접 보내며 서비스 제공자가 수신 메시지에 접근하지 못한다고 적혀 있다. 그러므로 개인 폰을 연결하기 전에 실제 앱 버전의 Cloud 모드 데이터 흐름·보관 정책을 확인해야 한다. 운영에는 주문 전용 폰/SIM을 우선 권장한다. **개인 폰에서 주문 문자만 폰 밖으로 나가야 한다면**, SMS Gateway 기본 웹훅 대신 기기 내부에서 발신번호·내용을 거른 후 전달하는 별도 앱 또는 앱 수정이 필요하다. Local 모드로 바꾸는 것만으로 필터가 생기지는 않는다. [공식 저장소 Cloud/웹훅 설명](https://github.com/capcom6/android-sms-gateway/blob/master/README.md).

## 3. 휴대폰 연결·해제 흐름

1. 사용자가 공식 APK를 Android 폰에 설치하고 SMS 송수신 권한을 허용한다. Cloud Server를 켜고 Online 상태로 만든다. Android 5.0 이상이 필요하며, 배터리 최적화·백그라운드 제한·RCS 설정은 수신 테스트에서 확인한다. [설치 안내](https://docs.sms-gate.app/installation/), [웹훅 FAQ](https://docs.sms-gate.app/faq/webhooks/).
2. Mirgam `내 정보 > 문자 연결`에서 휴대폰의 SMS Gateway 사용자 이름·비밀번호를 입력한다. 중앙 서버가 `GET /3rdparty/v1/devices`로 기기 목록을 조회해 사용자가 자기 폰의 `deviceId`를 선택하게 한다. 발신에 사용할 SIM과 본인 전화번호는 별도로 확인한다. 웹훅의 수신 `recipient`는 `null`일 수 있으므로 계정 매칭 기준으로 삼지 않는다. [기기 API](https://docs.sms-gate.app/features/multi-device/), [웹훅 필드](https://docs.sms-gate.app/features/webhooks/).
3. 앱 `Settings > Webhooks > Signing Key`의 키를 Mirgam 연결 화면에 입력하고 테스트 웹훅으로 검증한다. 중앙 서버가 **선택한 device ID에 한정해** `sms:received`, `sms:sent`, `sms:delivered`, `sms:failed` 웹훅을 각각 등록하고 웹훅 ID를 저장한다. 웹훅은 이벤트별로 따로 등록한다. [웹훅 등록](https://docs.sms-gate.app/features/webhooks/).
4. `연결 테스트`는 내 폰으로 시험 SMS 1건을 보내거나 다른 시험 폰에서 내 폰으로 보내 수신함 표시를 확인한다. 본문·대상 번호·예상 요금을 보여준 후 시험 발신을 요청한다. 실제 주문이나 고객 데이터는 사용하지 않는다.
5. `연결 끊기`는 등록한 웹훅을 해제하고 암호화된 인증정보·서명 키를 폐기한다. 과거 주문·수신함 기록의 삭제 여부는 별도 데이터 보존 정책으로 처리한다.

휴대폰 비밀번호와 서명 키는 중앙 API에서 암호화해 저장한다. 복호화 마스터 키는 Render 환경변수/비밀 저장소에만 둔다. 화면에서는 재표시하지 않고, 로그·CSV·EXE·Git에는 포함하지 않는다. 기기 ID는 농장 간 중복 연결을 막는다.

## 4. 수신 SMS → 주문 초안

웹훅 주소 예: `POST /api/v1/sms-gate/webhooks/<랜덤 연결 ID>`. 이 엔드포인트만 기존 Bearer 로그인 검사에서 제외하고, 별도 HMAC 검증을 필수로 한다. **원본 요청 본문 + `X-Timestamp`**로 HMAC-SHA256을 계산해 `X-Signature`와 상수 시간 비교한다. 허용 시각 차이는 ±5분을 기본으로 하고, 연결 ID·`deviceId`·등록된 `webhookId`·이벤트 타입도 확인한다. `recipient` 번호만으로 농장을 결정하지 않는다. [공식 서명 방식](https://docs.sms-gate.app/features/webhooks/).

서명 검증 이후 본문 크기·필드·전화번호·타임스탬프를 검사한다. `sms:received` 이벤트의 최상위 `id`를 웹훅 재전송 방지 키로 사용한다. 수신 `messageId`는 내용 기반이며 유일성이 보장되지 않으므로 단독 PK로 쓰지 않는다. 과거 문자 다시 가져오기 때는 `deviceId + sender + receivedAt + simNumber + 본문 해시`로 이미 처리한 문자인지 추가 확인한다. 데이터베이스에 기록한 뒤 30초 안에 2xx로 답한다. 저장에 실패했을 때만 5xx를 반환해 앱의 재시도를 받는다. [수신 ID 주의사항](https://docs.sms-gate.app/features/reading-messages/), [재시도 정책](https://docs.sms-gate.app/features/webhooks/).

처음에는 아래처럼 정해진 형식만 자동 필드 추출한다. 자유 문장은 수신함에 `확인 필요`로 두고 담당자가 주문 항목을 채운다.

```text
[주문]
받는 분: 홍길동
전화: 010-0000-0000
주소: 서울시 ...
상품: 사과 2상자
```

`[주문]`은 대소문자/공백 변형을 허용하되 은행·인증·광고 문자와 혼동하지 않게 다른 접두어를 허용하지 않는다. 상품은 현재 `products.item`과 정확히 매칭되면 제안하고, 수량은 기존 양수 검증을 사용한다. 주문일은 문자의 `receivedAt`을 한국 시간으로 변환한 날짜를 기본값으로 제안하되 수정 가능하게 한다. 고객 선택은 기존 `name + ph + address` 구분을 유지한다. 발신자 번호는 **주문 요청자**이며 배송 수령인의 `customers.ph`와 같다고 가정하지 않는다. 같은 번호나 이름의 고객이 여럿이면 자동으로 한 명을 확정하지 않는다. 신규 수령인은 기존 `새 고객 등록` 창을 사용한다.

수신함 상태: `새 문자` → `초안/확인 필요` → `주문 확정` 또는 `무시`. 원문과 수정된 주문안, 확정자, 확정 시각, 생성된 `order_batch_id`를 연결해 추적한다. 한 SMS에서 여러 상품을 주문하면 기존 `order_batches` 1개에 `orders` 여러 행을 생성한다. 기존 주문 저장의 `request_id` 중복 방지 규칙을 재사용한다. 주문 생성은 확정 버튼을 누를 때 한 트랜잭션으로 처리한다.

## 5. 주문 접수 완료 문자

확정 트랜잭션에서 주문과 **발신 대기(outbox)** 기록을 함께 저장한다. 메시지 수신 대상은 주문의 배송 수령인 번호가 아니라 **원래 주문 SMS 발신자**다. 계정별 `자동 접수 문자` 스위치는 최초 연결 시 꺼져 있으며, 사용자가 문구 미리보기와 시험 발신을 확인한 뒤 켤 수 있다.

기본 문구 예: `미르감: 주문이 접수되었습니다. 사과 2상자. 문의는 이 번호로 연락해 주세요.` `접수 완료`는 Mirgam 저장 성공을 뜻한다. 실제 배송 완료나 문자 배달 성공을 뜻하지 않는다. 문자에 전체 배송주소·불필요한 개인정보는 넣지 않는다. 발신 길이가 길어지면 한글 SMS가 여러 건으로 나뉠 수 있다. [메시지 분할 및 상태](https://docs.sms-gate.app/features/webhooks/).

별도 발신 작업자가 중앙 DB의 outbox를 읽어 `POST https://api.sms-gate.app/3rdparty/v1/messages`에 `deviceId`, `phoneNumbers`, `textMessage.text`, `simNumber`, `ttl`, 고정 `id`를 보낸다. `id`에는 outbox의 고유 ID를 사용한다. API가 메시지를 접수한 상태와 폰이 전송·상대방에게 배달된 상태를 구분한다. `sms:sent`, `sms:delivered`, `sms:failed` 웹훅으로 상태를 갱신하고 실패는 UI에 표시한다. 동일 주문에 대한 outbox 행은 한 개로 제한한다. [발신 API](https://docs.sms-gate.app/features/sending-messages/), [상태 추적](https://docs.sms-gate.app/features/status-tracking/).

네트워크 시간 초과 등으로 발신 결과가 불명확하면 즉시 같은 문자를 다시 보내지 않는다. 저장한 제공업체 메시지 ID 또는 고정 요청 ID로 상태를 조회하고, 확인되지 않으면 `확인 필요`로 둔다. 고정 `id`를 다시 보낼 때 제공업체가 실제로 중복을 막는지는 실기기 시험으로 검증한다. 작업자는 Render 웹 프로세스 안의 임시 스레드가 아닌 독립 실행 프로세스로 둔다. 개발 시 노트북에서 실행하고, 운영 배포 시 계속 실행 가능한 작업자/스케줄러를 확보한다. 작업자가 꺼져 있어도 주문과 발신 대기 기록은 DB에 남는다.

## 6. DB 변경 초안 (`0002_sms_gate`)

기존 `customers`, `orders`, `order_batches`의 CSV 호환 컬럼은 유지한다. 아래 테이블은 모두 중앙 SQLite Cloud DB에 만든다.

| 테이블 | 주요 컬럼·제약 | 용도 |
| --- | --- | --- |
| `sms_gate_connections` | `id`, `account_id`, `device_id UNIQUE`, `phone_e164`, `sim_number`, `username`, `password_ciphertext`, `signing_key_ciphertext`, `webhook_ids_json`, `enabled`, `auto_ack_enabled`, 시각 | 농장별 기기 연결 |
| `sms_webhook_events` | `id`, `connection_id`, `provider_event_id`, `event_type`, `received_at`, `UNIQUE(connection_id, provider_event_id)` | 재전송 중복 제거·상태 이벤트 기록 |
| `sms_inbound` | `id`, `account_id`, `connection_id`, `event_id`, `sender_e164`, `recipient_e164 NULL`, `sim_number`, `received_at`, `body_ciphertext`, `body_hash`, `state`, 시각 | 주문으로 분류된 문자 보관 |
| `sms_order_candidates` | `id`, `inbound_id UNIQUE`, `draft_json`, `state`, `order_batch_id UNIQUE NULL`, `reviewed_by_account_id`, `reviewed_at` | 검토/확정·주문 연결 |
| `sms_outbox` | `id`, `account_id`, `connection_id`, `inbound_id`, `order_batch_id`, `kind`, `recipient_e164`, `body_ciphertext`, `provider_message_id`, `state`, `attempt_count`, `last_error_code`, 시각, `UNIQUE(inbound_id, kind)` | 자동 확인 문자 및 발신 결과 |

테이블마다 외래키와 계정별 조회 인덱스를 둔다. `sms_gate_connections`에는 계정당 활성 연결 1개를 보장하는 부분 유니크 인덱스를 둔다. API는 기존 `g.account_id`로 항상 계정 범위를 검사한다. 번호는 원본과 표준화 값을 구분하며, 화면 표시에는 일부를 가릴 수 있다. 주문이 아닌 개인 SMS의 본문은 저장하지 않는다. 수신 원문과 발신 기록의 보존 기간·삭제 UI는 구현 전에 설정값으로 결정한다.

현재 `server/db.py`는 원격 DB에 Alembic을 자동 적용하지 않고 스키마 버전을 정확히 `0001`로 검사한다. 따라서 `0002` 마이그레이션 실행 CLI, 버전 검사 변경, SQLite Cloud 백업, Render 배포 순서를 함께 구현해야 한다. 기존 `0001` 데이터와 로그인 정보가 보존되는지 확인한다.

## 7. 중앙 API와 클라이언트 화면

| 위치 | 추가 기능 |
| --- | --- |
| 중앙 API `POST /api/v1/sms-gate/webhooks/<connection-id>` | 서명 검증, 수신 SMS 저장, 전송 상태 반영. Mirgam 로그인 토큰 대신 웹훅 서명 사용 |
| 중앙 API `/api/v1/sms-gate/connections` | 로그인한 농장의 연결/시험/해제/문자 설정. 타 농장 기기 접근 차단 |
| 중앙 API `/api/v1/sms/inbound` 및 `/<id>/confirm` | 수신함·초안 조회, 주문 확정. 확정 시 기존 주문 저장 검증 재사용 |
| 중앙 API `/api/v1/sms/outbox` | 발신 대기·전송·배달·실패 상태 조회와 명시적 재확인 |
| Mirgam `내 정보 > 문자 연결` | 쉬운 연결 안내, 기기·번호·SIM 표시, 시험, 자동 문자 스위치, 연결 해제 |
| Mirgam `문자 주문` | `새 문자/확인 필요/확정됨` 구분, 원문과 주문 초안 나란히 보기, 기존 고객/상품 검색, 주문 확정, 발신 결과 |

화면의 번호·기기 표시가 실제 저장소 역할을 오해하게 하지 않도록 `연결된 휴대폰`, `주문 문자`, `주문 접수 문자`라는 용어를 쓴다. 사이트가 열려 있지 않아도 중앙 서버가 수신할 수 있으며, EXE에는 SMS Gateway 비밀정보를 넣지 않는다.

## 8. 구현 순서와 완료 기준

1. **실행 환경 확인:** 실제 사용하는 EXE가 중앙 API 클라이언트인지 `/status`와 패키지 `client.json`으로 확인한다. 과거 `dist/Mirgam/Mirgam.exe`의 CSV 저장형 버전은 SMS 기능을 사용할 수 없다. Render API가 목표 SQLite Cloud DB를 사용하는지도 확인한다.
2. **DB/보안 기반:** `0002` 마이그레이션, 비밀정보 암호화, 계정별 기기 소유권, 서명 검증, 웹훅 중복 방지, 개인정보 비저장 규칙을 구현한다.
3. **수신/화면:** SMS 연결 화면과 수신함, 형식 파서, 고객·상품 매핑, 검토 후 기존 주문 저장 로직 재사용을 구현한다. 자동 발신은 아직 비활성으로 둔다.
4. **발신/상태:** outbox 작업자, 접수 문자 설정, SMS Gateway 발신 API, 전송 상태 웹훅과 오류 화면을 구현한다.
5. **실기기 검증:** Android 폰 1대에 공식 앱 설치 → Cloud 모드 연결 → 실제 시험 SMS 수신 → Mirgam 주문 확정 → 발신·상태 확인 → 다른 PC의 Mirgam에서 같은 주문 확인. 확인 뒤 농장별로 확대한다.

인수 시험에는 다음을 포함한다: 서명 오류/재생 공격 거절; 동일 웹훅 2회에 주문 1건; 주문 형식 아닌 개인 SMS의 비저장; 같은 전화번호를 가진 다른 고객의 잘못된 자동 선택 방지; 주문 저장 실패 시 문자 미발신; SMS API 실패·타임아웃·폰 오프라인 상태 표시; 한 주문에 자동 확인 문자 중복 발신 방지; 계정 A가 계정 B의 SMS/기기를 보거나 사용하지 못함; RCS/백그라운드 제한/Render 응답 지연 확인. 웹훅 응답은 공식 요구인 30초 안의 2xx를 목표로 한다. [웹훅 재시도·응답 요구](https://docs.sms-gate.app/features/webhooks/), [RCS 제한](https://docs.sms-gate.app/faq/webhooks/).

## 9. 착수 전 확인할 값

- 실기기 종류: SMS 가능한 Android 폰, 사용할 SIM 번호, 시험 문자 수신·발신이 가능한 두 번째 번호.
- 현재 `accounts`를 농장별로 쓸지, 직원별 개별 로그인부터 도입할지. 1차 설계는 농장별 계정이다.
- 개인 폰으로 시험할지 주문 전용 폰/SIM을 쓸지. 개인 폰이라면 일반 SMS도 Mirgam 서버에 도착할 수 있고 Cloud 모드의 제공업체 보관 범위도 확인해야 한다. 이 노출이 허용되지 않으면 기기 내부 사전 필터를 지원하는 별도 수신 방식으로 설계를 바꾼다.
- 접수 완료 문구, 발신 시간대, 하루 발신 한도, 수신 원문 보존 기간.

## 공식 문서

- [SMS Gateway 설치](https://docs.sms-gate.app/installation/)
- [Cloud Server 모드](https://docs.sms-gate.app/getting-started/public-cloud-server/)
- [웹훅 등록·서명·재시도](https://docs.sms-gate.app/features/webhooks/)
- [메시지 발신 API](https://docs.sms-gate.app/features/sending-messages/)
- [발신 상태 추적](https://docs.sms-gate.app/features/status-tracking/)
- [수신 메시지 ID 주의사항](https://docs.sms-gate.app/features/reading-messages/)
- [가격](https://docs.sms-gate.app/pricing/)
