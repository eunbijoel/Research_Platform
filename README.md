**한국어** | [English](README_EN.md)

# Research Memory Platform

**An Organizational Research Intelligence Platform**

과거 자료 기반의 지식베이스-> AI가 보관-이해-활용할 수 있도록 하는 시스템
![image](https://github.com/user-attachments/assets/00fa17b6-f8d6-4049-87e0-087053dd9051)

### Goals

1. Enable evidence-based reuse of organizational research assets
2. Preserve and operationalize institutional research knowledge

지원 형식 (문서): PDF, DOCX, TXT/MD, CSV, XLSX, HWP/HWPX  
회의록 녹음: MP3, WAV, M4A, WEBM, OGG, FLAC (연구 기록 · 회의록 모드, 선택적 STT)

검색: Ollama 임베딩(`nomic-embed-text`) + TF-IDF를 **hybrid(RRF)** 로 결합.  
임베딩이 안 되면 TF-IDF만 사용.

---



## UI tabs


| Tab         | 하는 일                                                                                                                                                                                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **홈**       | Research Memory 대시보드. 프로젝트·검색·업로드·최근 문서, 문서 상세·역할(연구문서/참고자료)·Document Insight                                                                                                                                                        |
| **일정 관리**   | 과제별 회의·제출·작업·마일스톤을 월간 캘린더로 등록·조회(알림 및 외부 캘린더 연동은 [myown](https://github.com/sumin-ma-1/myown)을 참고)                                                                                                                                   |
| **채팅**      | Memory에 질문. 답변마다 출처(파일·위치)와 `[연구문서]`/`[참고규정]` 구분을 붙입니다. 근거가 없으면 거절합니다                                                                                                                                                                |
| **연구 기록**   | 연구노트·회의록 초안. Memory + 추가자료(회의록은 녹음/트랜스크립트) 참고, 표 미리보기·DOCX/HWPX 다운로드·Memory 저장                                                                                                                                                       |
| **제안서**     | RFP/공고문을 넣고, **연구문서 + 참고규정(운영요령)** 근거로 센터 파트 초안·준수 포인트를 만듭니다. 전체 제안서 자동완성이 아닙니다                                                                                                                                                      |
| **유사도 검토**  | 새 문서 ↔ Memory(또는 문서끼리) 문장·페이지·이미지를 비교합니다. MiniLM + pHash, 표/페이지 PNG로 중복·재사용 검토                                                                                                                                                       |
| **코딩 에이전트** | 로컬 Ollama + deepagents-code 코딩 워크벤치. 채팅·파일 탐색기·코드 에디터·터미널·스레드·Excel/CSV 첨부·inspect/analyze/transform. 워크스페이스는 `data/coding_agent/`에 저장. Excel 연동: `[coding_agent/docs/excel_integration.md](coding_agent/docs/excel_integration.md)` |


---



## Run

### 웹 (Research Platform)

팀 서버(Docker)를 쓰는 경우 브라우저만 접속하면 됩니다.

- 주소: [http://bigsoft.iptime.org:51100/](http://bigsoft.iptime.org:51100/)
- 첫 화면에서 웹 비밀번호(`RM_APP_PASSWORD`) 입력

로컬에서 직접 띄울 때:

```bash
cd /mnt/data/eunbi/research-memory
./run_app.sh
# 브라우저: http://127.0.0.1:8505
```

(최초 1회: Python 3.12+ venv + `pip install -r requirements.txt`, 루트 `.env`에 `RM_APP_PASSWORD` 설정)

### Telegram Bot

웹과 **별도 프로세스**입니다. Bot이 켜져 있어야 DM에 응답합니다.  
등록된 Telegram User ID만 **개인 DM**에서 Memory를 질의할 수 있습니다(`/today`·`/week`·자연어). 그룹 채팅에는 응답하지 않습니다.

**1) 터미널에서 Bot 실행**

```bash
cd /mnt/data/eunbi/research-memory
./run_telegram.sh
```

(최초 1회: `pip install -r telegram_memory/requirements.txt`, `telegram_memory/.env`에 `TELEGRAM_BOT_TOKEN`  
선택: 두 번째 봇은 `TELEGRAM_BOT_TOKEN_SECONDARY` — 같은 Memory·허용 사용자로 `./run_telegram.sh` 한 번이면 둘 다 polling)

**2) 웹에서 사용자 등록**

1. [http://bigsoft.iptime.org:51100/](http://bigsoft.iptime.org:51100/) 접속 · 웹 비밀번호 로그인  
2. 사이드바 **고급 → Telegram 사용자 관리**  
3. 관리자 비밀번호(`RM_TELEGRAM_ADMIN_PASSWORD`, 웹 비번과 다름) 입력  
4. **Telegram User ID** + 표시 이름 등록

**User ID 확인 방법**

1. Telegram에서 `@userinfobot` 검색 → **Start**  
2. 봇이 알려주는 숫자 ID(약 10자리)를 복사  
3. 위 관리 화면에 붙여 넣고 등록  

등록된 사용자는 Research Bot과의 **개인 DM**에서 바로 질문하면 됩니다.

### 데이터 저장


| 경로                                  | 내용                                              |
| ----------------------------------- | ----------------------------------------------- |
| `data/raw/`                         | 업로드 원본 파일                                       |
| `data/kb/memory.sqlite3`            | 문서·프로젝트·일정·인덱스 메타데이터                            |
| `data/kb/*.pkl`                     | 검색 인덱스 (TF-IDF, vector)                         |
| `data/coding_agent/`                | 코딩 에이전트 스레드·체크포인트·워크스페이스                        |
| `telegram_memory/data/chat.sqlite3` | 텔레그램 질문·답 로그 + 허용 User ID (봇 전용, Memory KB와 분리) |


---



## Project layout

```
app.py                 UI
research_memory/
  pipeline/            문서 파싱 · 메타/Fact · 인제스트
  kb/                  Knowledge Base
  engine/              Chat · Similarity · Proposal · Schedule · Research Note
                 docsim/  (유사도: MiniLM · 파서 · pHash)
coding_agent/          코딩 에이전트 워크벤치 (deepagents-code · Ollama · Excel)
                       docs/excel_integration.md · tests/ (Excel·HITL·bridge)
telegram_memory/       Research Memory Bot (Telegram, 읽기 전용)
```

---



## Credits

일정 캘린더 UI와 알림·외부 캘린더 연동은 [myown](https://github.com/sumin-ma-1/myown)을 참고했습니다.