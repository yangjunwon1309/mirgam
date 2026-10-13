# 배포판 `client.json` 설정

`Mirgam.exe`와 같은 폴더의 `client.json`에서 접속 주소와 선택적 로컬 설정을 관리합니다.

```json
{
  "MIRGAM_API_BASE_URL": "https://mirgam.onrender.com",
  "MIRGAM_SMS_ENCRYPTION_KEY": "",
  "api_base_url": "https://mirgam.onrender.com",
  "default_server_id": "render",
  "servers": [
    { "id": "render", "name": "클라우드 서버 (Render)", "url": "https://mirgam.onrender.com" },
    { "id": "local", "name": "이 PC의 로컬 서버", "url": "http://127.0.0.1:5100" }
  ]
}
```

- `MIRGAM_API_BASE_URL`을 비워 두면 `servers` 목록과 로그인 화면의 서버 선택을 사용합니다. 주소를 넣으면 그 주소 하나로 고정 연결합니다.
- `MIRGAM_SMS_ENCRYPTION_KEY`는 배포판 안에 실제 키를 넣지 않는 것을 권장합니다. 설정하면 Mirgam 실행기가 같은 PC에서 띄우는 로컬 API 프로세스에만 전달합니다. Render API의 복호화 키는 Render 환경변수에 별도로 설정해야 합니다. 클라이언트 JSON의 값은 Render로 전송되지 않습니다.
- 로컬 API를 SQLite Cloud에 연결하려면 `MIRGAM_DATABASE_URL`도 필요합니다. 현재 이 값은 `client.json`에서 읽지 않으며, Windows 환경변수로 설정해야 합니다.
- 환경변수로 지정한 값은 JSON 값보다 우선합니다.

`MIRGAM_SMS_ENCRYPTION_KEY`는 평문 비밀값입니다. 이 값을 적은 `client.json`을 다른 사람에게 배포하면 수신자가 키를 읽을 수 있으므로, 그 키로 보호되는 SMS 연동 정보를 신뢰할 수 없는 사용자에게 노출하지 마세요.

`MirgamServer.exe`를 별도로 실행하는 경우에도 같은 배포 폴더의 `Mirgam/client.json`에서 `MIRGAM_SMS_ENCRYPTION_KEY`를 읽습니다. 실행 중인 서버에는 설정 변경이 자동 적용되지 않으므로, 설정을 바꾼 뒤에는 로컬 API를 재시작해야 합니다. `MIRGAM_DATABASE_URL`은 계속 서버 실행 환경변수로 설정해야 합니다.
