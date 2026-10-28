# 캡컷 에이전트

한국어 토킹 영상 자동 편집기. **inbox 폴더에 영상을 던져 넣으면 캡컷 드래프트가 나옵니다.**
캡컷을 열면 컷이 이미 잡힌 타임라인이 있고, 거기서부터는 사람이 손봅니다.

> **검증 기준: 빌드 성공은 검증이 아닙니다.**
> 캡컷에서 직접 처음부터 끝까지 재생해 보는 것만 검증입니다.

---

## 파일 저장소

기본 위치는 **`~/CapCutAgent/`** 입니다. 처음 실행할 때 자동으로 만들어집니다.

```
~/CapCutAgent/
├── inbox/        ← 원본 mp4 / mov 를 여기 던진다
├── done/         ← 처리에 성공한 원본이 자동으로 옮겨진다
├── failed/       ← 처리에 실패한 원본이 옮겨진다
├── reports/      ← 컷 계획 JSON (무엇을 왜 잘랐는지의 기록)
└── config.json   ← 기본 컷 옵션. 매번 플래그 치지 않아도 되게.
```

| OS | 실제 경로 |
|---|---|
| Windows | `C:\Users\<사용자>\CapCutAgent\` |
| macOS | `/Users/<사용자>/CapCutAgent/` |

위치를 바꾸려면:

```powershell
# 한 번만
python -m capcut_agent.cli --inbox --workspace "D:\영상작업"

# 계속 쓸 거면 환경변수로
setx CAPCUT_AGENT_HOME "D:\영상작업"
```

**완성된 드래프트는 이 저장소에 두지 않습니다.** 캡컷이 읽어야 하므로 캡컷 자신의
드래프트 폴더에 직접 씁니다. 저장소는 입력과 기록만 맡습니다.

```
# Windows — 캡컷 드래프트 폴더
%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft

# macOS — 최신 캡컷은 샌드박스 컨테이너 안에 있습니다
~/Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Projects/com.lveditor.draft
~/Movies/CapCut/User Data/Projects/com.lveditor.draft
```

다른 곳에 있으면 `CAPCUT_DRAFT_DIR` 환경변수나 `--draft-dir` 로 지정하세요.

---

## 쓰는 법

### 0. 환경 + 저장소 점검

```bash
python -m capcut_agent.cli --check
```

OS를 감지해 트랙을 판정하고, 저장소 경로와 각 폴더의 파일 수를 같이 보여줍니다.

| OS / 아키텍처 | 트랙 | ASR 백엔드 (3단에서 사용) |
|---|---|---|
| Darwin arm64 | **A** — Mac M칩 | `mlx-whisper` |
| Windows AMD64 | **C** — Windows (MP4 export 보너스) | `faster-whisper` |
| 그 외 | fallback | `faster-whisper` |

MISS 가 뜨면 옆에 설치 명령이 함께 나옵니다.

| 항목 | Mac | Windows |
|---|---|---|
| Python 3.10+ | `brew install python@3.11` | [python.org](https://www.python.org/downloads/) |
| ffmpeg / ffprobe | `brew install ffmpeg` | `winget install --id Gyan.FFmpeg -e` |
| 캡컷 드래프트 폴더 | 캡컷 설치 후 **1회 실행**해 프로젝트 1개 생성 | 동일 |

### 1. 컷 계획만 먼저 보기 (아무것도 쓰지 않습니다)

```bash
python -m capcut_agent.cli 영상.mp4 --dry-run
```

```
입력      : 영상.mp4
해상도    : 1920x1080 @ 30.0fps
길이      : 12:04.30

보존      : 87조각 / 08:51.20
제거      : 03:13.10 (26.7%)
컷 수     : 86

    1. 00:00.00 → 00:04.62  ( 4.62s)
    2. 00:05.71 → 00:09.08  ( 3.37s)
    ...
```

토킹 영상은 보통 제거 비율 **20~30%** 가 정상입니다. 50%를 넘으면 `--noise-db` 를 낮추세요.

### 2. inbox 째로 처리

```bash
# 영상들을 ~/CapCutAgent/inbox/ 에 넣고
python -m capcut_agent.cli --inbox
```

이름 순으로 전부 돌면서, 성공한 원본은 `done/` 으로, 실패한 건 `failed/` 로 옮깁니다.
원본을 그대로 두고 싶으면 `--keep-source`.

### 3. 파일 하나만

```bash
python -m capcut_agent.cli "C:\Users\yeonj\Videos\내영상.mp4"
```

inbox 밖의 파일은 이동시키지 않습니다.

### 4. 캡컷에서 확인 — 이게 진짜 검증입니다

1. **캡컷이 켜져 있으면 완전히 종료 후 재실행** (실행 중엔 목록이 안 갱신됩니다)
2. 홈 화면에서 방금 만든 프로젝트 열기
3. 처음부터 끝까지 재생하며 확인:
   - 말 첫 음절이 잘려 나가지 않는가
   - 컷이 너무 촘촘해 숨이 안 쉬어지지 않는가
   - 홈 화면 길이가 `00:00` 이면 드래프트 생성 실패입니다

---

## 튜닝

`config.json` 을 고치면 기본값이 바뀌고, CLI 플래그를 주면 그쪽이 이깁니다.

| 증상 | 옵션 | 기본값 |
|---|---|---|
| 말 첫·끝 음절이 잘린다 | `--padding 0.15` | 0.08 |
| 조용히 녹음해서 말까지 잘린다 | `--noise-db -45` | -34 |
| 에어컨·노이즈 때문에 아무것도 안 잘린다 | `--noise-db -28` | -34 |
| 컷이 딸꾹질처럼 잘다 | `--min-gap 0.3 --min-keep 0.5` | 0.12 / 0.30 |
| 짧은 숨소리까지 다 자른다 | `--min-silence 0.6` | 0.35 |

전체 옵션은 `python -m capcut_agent.cli --help`.

---

## 빌드 적층

| 단 | 내용 | 상태 |
|---|---|---|
| **1단** | `silence_detect` + `build_draft` → 점프컷 드래프트 (UI 없음) | **완료 · 캡컷 재생 검증 대기** |
| **1.5단** | 파일 저장소 (inbox / done / failed / reports / config) | **완료** |
| 2단 | FastAPI + 정적 HTML 1장 (drag/drop + SSE stepper) | 예정 |
| 3단 | whisper Transcript(세그먼트+단어) + 세그먼트 단위 자막 | 예정 |
| 4단 | `filler_ng` + `cuts` (잔말/NG 통합 컷) + 결과 카드 transcript 노출 | 예정 |
| 5단 | 영상 프리뷰 + 보존 구간 사용자 마킹 (`[` / `]` 단축키) | 예정 |

각 단은 **캡컷에서 실제 재생 검증을 통과한 뒤에만** 다음으로 넘어갑니다.

---

## 구조

```
capcut-agent/
├── capcut_agent/
│   ├── env_check.py   # Step 0 — OS 감지, 트랙 판정, 도구 + 저장소 점검
│   ├── storage.py     # 파일 저장소 (inbox / done / failed / reports / config)
│   ├── paths.py       # 캡컷 드래프트 폴더 탐색 (Mac 샌드박스 포함)
│   ├── media.py       # ffprobe / ffmpeg 래퍼
│   ├── silence.py     # 무음 → 보존 구간 (순수 로직, 단위테스트 대상)
│   ├── draft.py       # 캡컷 드래프트 빌더
│   └── cli.py         # 1단 엔트리
└── tests/             # pytest 38개 — ffmpeg 없이도 도는 순수 로직 테스트
```

```bash
python -m pytest tests -q
```

---

## 알려진 지뢰

코드에 주석으로도 박아두었지만, 여기 모아둡니다.

1. **타임라인 파일 이름이 버전마다 다릅니다.**
   Windows/구버전은 `draft_content.json`, macOS 최신판은 `draft_info.json` 을 읽습니다.
   어느 쪽인지 미리 알 수 없어 **같은 내용을 두 이름으로** 씁니다.

2. **`draft_meta_info.json` 의 `tm_duration`(마이크로초).**
   빠지거나 0이면 캡컷 홈 화면에 `00:00` 으로 뜨고, 열어도 빈 프로젝트가 됩니다.

3. **세그먼트의 `extra_material_refs` 4종.**
   `speed` / `canvas` / `sound_channel_mapping` / `vocal_separation` 이 다 없으면
   클립이 검은 화면으로 그려지거나 캡컷이 죽습니다. 세그먼트마다 새로 만들어 붙입니다.

4. **macOS 샌드박스.**
   `~/Movies/CapCut/...` 만 보고 "드래프트 폴더 없음" 으로 판단하면 안 됩니다.

5. **경로는 절대경로.** Windows 에서는 역슬래시로 저장됩니다.

6. **캡컷 실행 중에는 목록이 갱신되지 않습니다.** 재시작해야 새 드래프트가 보입니다.

3단부터 붙을 것들 (아직 미구현):

7. **ASR 은 `asyncio.Lock` 으로 직렬화.** numba 가 스레드 안전하지 않아 동시 호출 시 segfault.
8. **ASR 캐시 키는 content hash.** mtime 기반은 업로드마다 miss 납니다.
9. **자막 줄바꿈은 의존명사를 앞 어절에 붙여야 합니다** (`것`, `수`, `때`, `거` …).
10. **자막 세로 위치는 `transform_y`.** 캡컷 좌표계는 화면 중앙이 0.
