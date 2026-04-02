# Eternal Return Log Analyzer

`Player.log`를 읽어 오류 구간을 추출하고, 로컬 규칙 기반 분석 또는 AI 분석을 보여주는 Windows용 도구입니다.

## 실행

```powershell
cd C:\Users\user\eternal-return-log-analyzer
python main.py
```

## 분석 모드

앱에는 2가지 모드가 있습니다.

- `로컬 분석만`: 로그를 외부로 보내지 않고 앱 내부 규칙만 사용
- `이 PC에서 직접 AI 분석`: 이 PC의 `OPENAI_API_KEY`를 사용해 직접 AI 호출

AI 분석을 사용할 때도 전체 로그를 보내지 않고 `추출된 오류 블록`, `파일 경로`, `줄 수`, `인코딩` 같은 메타정보 중심으로 분석합니다.

## 클라이언트 배포본

- `dist\PlayerLogAIAnalyzerClient.exe`

클라이언트 exe는 기본적으로 로컬 분석 모드에서 시작합니다. AI를 쓰려면 이 PC에 `OPENAI_API_KEY`를 설정한 뒤 앱에서 `이 PC에서 직접 AI 분석`을 선택하면 됩니다.

## 빌드

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
powershell -ExecutionPolicy Bypass -File .\build_server_exe.ps1
```

## 핵심 기능

- 현재 사용자 기준 기본 `Player.log` 경로 자동 계산
- 사용자 지정 경로 저장/복원
- 최근 N줄 기반 로그 읽기
- 오류/예외 블록 추출 및 병합
- 민감 문자열 마스킹
- 로컬 규칙 기반 원인 추정
- 이 PC에서 직접 AI 호출
- 유사 오류 히스토리 누적 저장 및 비교
- 결과와 근거 로그 복사

## Slack bot

회사용 Slack 채널에서 봇을 멘션하면, 봇이 `.log` 또는 `.txt` 파일을 찾아 같은 메시지의 스레드에 분석 결과를 답글로 남기도록 구성할 수 있습니다.

빠른 시작:

1. `run_analysis_server.ps1`로 AI 분석 서버를 실행합니다.
2. Slack App에 `https://<public-host>/slack/events`를 Event Request URL로 등록합니다.
3. `run_slack_bot.ps1`로 bot 프로세스를 실행합니다.
4. bot이 초대된 채널에서 파일과 함께 봇을 멘션하거나, 파일 업로드 메시지의 스레드에서 봇을 멘션합니다.
5. bot이 같은 메시지 스레드에 원인 요약과 권장 조치를 남깁니다.

Slack App 설정 순서:

1. Slack API에서 `From scratch`로 새 앱을 만들거나, 저장소의 `slack-app-manifest.yaml`을 import합니다.
2. `OAuth & Permissions`에서 bot token scope를 추가합니다.
3. 앱 생성 후 `Event Subscriptions`를 켜고 Request URL을 `https://<public-host>/slack/events`로 지정합니다.
4. `app_mention` bot event를 추가합니다.
5. 앱을 워크스페이스에 설치한 뒤 `Bot User OAuth Token`과 `Signing Secret`을 복사합니다.
6. 아래 환경 변수를 넣고 bot 프로세스를 실행합니다.

권장 bot token scope:
- 공통: `app_mentions.read`, `chat:write`, `files:read`
- 공개 채널용: `channels:history`, `channels:read`
- 비공개 채널용: `groups:history`, `groups:read`
- DM용: `im:history`, `im:read`
- 그룹 DM용: `mpim:history`, `mpim:read`

권장 bot event:
- 채널 멘션 기반 호출: `app_mention`

주의:
- Slack의 Request URL은 공개 HTTPS 주소여야 합니다. 로컬 개발 중이면 `ngrok`, `cloudflared` 같은 터널을 붙여 `8780` 포트를 외부로 노출해야 합니다.
- bot이 읽을 채널에 초대되어 있어야 파일 공유 이벤트를 받습니다.
- `files.info` 재조회 fallback이 들어가 있어서, 일부 공유 채널/Slack Connect 업로드처럼 파일 URL이 바로 오지 않는 경우도 처리합니다.
- 자동 업로드 감지는 사용하지 않습니다. 아래 둘 중 하나로 호출해야 합니다.
- 파일 업로드 메시지에 봇 멘션을 함께 넣기
- 파일을 올린 뒤 그 메시지 스레드에서 봇을 멘션하기

실행:

```powershell
cd C:\Users\user\eternal-return-log-analyzer
$env:SLACK_BOT_TOKEN="xoxb-..."
$env:SLACK_SIGNING_SECRET="..."
$env:SLACK_ALLOWED_CHANNELS="C01234567,C07654321"
$env:SLACK_ANALYSIS_MODE="server"
$env:SLACK_ANALYSIS_SERVER_URL="http://127.0.0.1:8765"
python slack_bot.py
```

PowerShell 시작 스크립트:

```powershell
cd C:\Users\user\eternal-return-log-analyzer
$env:OPENAI_API_KEY="sk-..."
.\run_analysis_server.ps1
```

```powershell
cd C:\Users\user\eternal-return-log-analyzer
$env:SLACK_BOT_TOKEN="xoxb-..."
$env:SLACK_SIGNING_SECRET="..."
$env:SLACK_ALLOWED_CHANNELS="C01234567"
.\run_slack_bot.ps1
```

기본 엔드포인트:
- `POST /slack/events`
- `GET /health`

운영 보강:
- Slack 재전송 이벤트는 내부 dedupe 캐시로 한 번만 처리합니다.
- 긴 분석 결과는 여러 개의 thread reply로 자동 분할 전송합니다.
- 일부 공유 채널에서는 `files.info` 재조회로 실제 다운로드 URL을 다시 확인합니다.

Slack bot 분석 모드:
- `local`: 로컬 규칙 기반 분석만 사용
- `direct`: Slack bot 서버가 직접 OpenAI API 호출
- `server`: 별도 분석 서버에 AI 요청 위임

Slack App 기본 권한:
- `app_mentions.read`
- `chat:write`
- `files:read`
- `channels:history`
- `channels:read`

권장 OpenAI 설정 예시:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_MODEL="gpt-5-mini"
```

분석 서버까지 함께 실행하는 예시:

```powershell
cd C:\Users\user\eternal-return-log-analyzer
$env:OPENAI_API_KEY="sk-..."
python analysis_server.py
```

로컬 검증:

```powershell
cd C:\Users\user\eternal-return-log-analyzer
python -m unittest discover -s tests -v
```

Slack bot 빌드:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_slack_bot_exe.ps1
```

빌드 결과:
- `dist\PlayerLogAIServer\PlayerLogAIServer.exe`
- `dist\PlayerLogSlackBot\PlayerLogSlackBot.exe`

주의:
- PyInstaller `onedir` 배포이므로 exe 옆의 `_internal` 폴더를 함께 유지해야 합니다.
