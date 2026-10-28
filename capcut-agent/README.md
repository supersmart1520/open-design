# 캡컷 에이전트

영상을 캡컷 숏폼 드래프트로. 두 가지 모드로 돈다.

| 모드 | 입력 | 하는 일 |
|---|---|---|
| **A · 강의 하이라이트** | 긴 강의 영상 | 전사 → 대본 → 숏폼 후보 추천 → **사람이 승인** → 9:16 드래프트 + 자막 |
| **B · AI 보이스** | 대본 + B롤 클립 | 대본 → ElevenLabs 나레이션 → B롤 깔기 → 9:16 드래프트 + 자막 |
| **자막만** | 아무 영상 | 컷 없이 전체 영상 + 자막 트랙 (`caption`) |
| (보조) **cut** | 토킹 영상 | 무음만 걷어낸 가로 점프컷 드래프트 |

캡컷을 열면 컷·자막이 이미 잡힌 타임라인이 있고, 거기서부터는 사람이 손봅니다.

> **검증 기준: 빌드 성공은 검증이 아닙니다.**
> 캡컷에서 직접 처음부터 끝까지 재생해 보는 것만 검증입니다.

> **'유효한 부분'은 사람이 고릅니다.**
> 모드 A 는 후보를 **추천만** 하고, 왜 추천했는지 근거를 같이 적습니다.
> `"선택": true` 로 켠 것만 드래프트가 됩니다.

---

## 파일 저장소

기본 위치는 **`~/CapCutAgent/`** 입니다. 처음 실행할 때 자동으로 만들어집니다.

```
~/CapCutAgent/
├── inbox/        ← 원본 mp4 / mov 를 여기 던진다
├── broll/        ← 모드 B 가 화면에 깔 B롤 클립 (이동하지 않음)
├── done/         ← 처리에 성공한 원본이 자동으로 옮겨진다
├── failed/       ← 처리에 실패한 원본이 옮겨진다
├── scripts/      ← 대본 · 전사 · 숏폼 후보 교환 파일 (사람이 열어 고치는 곳)
├── voice/        ← 합성된 나레이션 mp3 (문장 해시로 캐시)
├── reports/      ← 무엇을 왜 잘랐는지의 기록
├── cache/asr/    ← 전사 캐시 (키 = 파일 내용 해시)
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

> ### ⚠️ `done/` 은 미디어의 영구 주소입니다
> 드래프트에는 영상의 **절대경로가 박혀 들어갑니다.** 그래서 원본을 `done/` 으로
> 먼저 옮기고, 드래프트가 그 최종 위치를 가리키게 합니다.
> **`done/` 안의 파일을 지우거나 옮기면 이미 만든 캡컷 프로젝트가 미디어를 잃습니다.**
> 원본을 다른 곳에서 관리하고 싶으면 `--keep-source` 로 inbox 에 남겨두거나,
> inbox 밖에 둔 파일을 경로로 직접 지정하세요 — inbox 밖의 파일은 이동시키지 않습니다.

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
python -m capcut_agent.cli check
```

OS를 감지해 트랙을 판정하고, 저장소 경로와 각 폴더의 파일 수를 같이 보여줍니다.

| OS / 아키텍처 | 트랙 | ASR 백엔드 | 설치 |
|---|---|---|---|
| Darwin arm64 | **A** — Mac M칩 | `mlx-whisper` | `pip install mlx-whisper` |
| Windows AMD64 | **C** — Windows | `faster-whisper` | `pip install faster-whisper` |
| 그 외 | fallback | `faster-whisper` | `pip install faster-whisper` |

| 항목 | Mac | Windows |
|---|---|---|
| Python 3.9+ | macOS 기본 파이썬으로 충분 (Xcode 번들 3.9) | [python.org](https://www.python.org/downloads/) |
| ffmpeg / ffprobe | `brew install ffmpeg` | `winget install --id Gyan.FFmpeg -e` |
| 캡컷 드래프트 폴더 | 캡컷 설치 후 **1회 실행**해 프로젝트 1개 생성 | 동일 |

---

### 모드 A — 강의 영상에서 숏폼 뽑기

**1단계. 전사하고 후보를 받는다**

```bash
python -m capcut_agent.cli shorts ~/CapCutAgent/inbox/강의.mp4
```

```
입력      : 강의.mp4
길이      : 48:12.40
  … 오디오 추출
  … 전사 (mlx-whisper / mlx-community/whisper-large-v3-turbo)

대본      : ~/CapCutAgent/scripts/강의.대본.txt
전사       : ~/CapCutAgent/scripts/강의.transcript.json  (312문장, 2841단어)
후보       : ~/CapCutAgent/scripts/강의.shorts.json

  #1  04:12.30 → 04:43.10  (30.8s)  점수 10.0
      왜 대부분이 이 단계에서 실패하는지 아시나요?
      · 첫 문장에 후킹 표현: 왜, 아시나요
      · 질문으로 시작
      · 완결된 문장으로 끝남
  #2  11:35.80 → 11:55.10  (19.3s)  점수  5.5
      여기서 가장 중요한 핵심은 순서입니다.
      ...
```

전사는 **파일 내용 해시로 캐시**됩니다. 같은 영상을 다시 넣으면 전사를 안 돌립니다.

**2단계. 쓸 것만 켠다**

`scripts/강의.shorts.json` 을 열어 쓸 후보의 `"선택"` 을 `true` 로 바꿉니다.
`"시작"` / `"끝"` 초를 직접 고쳐도 됩니다.

```json
{
  "번호": 1,
  "선택": true,
  "시작": 252.3,
  "끝": 283.1,
  "제목": "왜 대부분이 이 단계에서 실패하는지 아시나요?",
  "점수": 10.0,
  "근거": ["첫 문장에 후킹 표현: 왜, 아시나요", "질문으로 시작"],
  "대본": "왜 대부분이 이 단계에서 실패하는지 아시나요? 이유는 세 가지입니다. ..."
}
```

**3단계. 드래프트로 만든다**

```bash
python -m capcut_agent.cli build ~/CapCutAgent/scripts/강의.shorts.json
```

승인한 개수만큼 **9:16 세로 드래프트**가 생기고, 자막 트랙이 함께 들어갑니다.

| 옵션 | 쓸 때 |
|---|---|
| `--anchor top` | 인물이 화면 아래쪽에 잡힌 가로 화면녹화 |
| `--anchor left` / `right` | 인물이 한쪽에 치우쳐 있을 때 |
| `--subtitle-y -0.55` | 자막을 더 위로 (하단 UI가 가릴 때) |
| `--max-chars 14` | 자막 한 줄을 더 짧게 |
| `--no-subtitle` | 자막 없이 (캡컷 자동 캡션을 쓸 때) |
| `--srt` | SRT 도 같이 내보내기 |

---

### 자막만 달기 — `caption`

컷을 하나도 하지 않고, 전체 영상에 자막 트랙만 얹습니다. **원본을 옮기지도 않습니다.**

```bash
python -m capcut_agent.cli caption --inbox          # inbox 전체 (파일명 안 적어도 됨)
python -m capcut_agent.cli caption --inbox --cut    # 무음 컷까지 같이
python -m capcut_agent.cli caption 영상.mp4          # 파일 하나
```

자막은 두 트랙으로 나옵니다.

| 트랙 | 내용 | 기본 세로 위치 |
|---|---|---|
| 말하는 자막 | 전사한 말, 여러 장 | `y = -850px` |
| 제목 | 영상당 **1장, 1줄** | `y = +1400px` |

가로는 둘 다 `x = 0`(가운데) 고정입니다. 제목은 생략하면 대본에서 뽑고,
`--title "문구"` 로 직접 지정할 수 있습니다. `--no-title` 로 끌 수 있습니다.

> ### ⚠️ 위치 픽셀 값은 캔버스 높이에 묶여 있습니다
> 캡컷 UI 가 보여주는 px 는 **그 프로젝트 캔버스 기준**입니다. 드래프트 JSON 은
> 정규화 값(절반 높이 = 1)을 쓰므로, 이 도구가 캔버스 높이로 환산합니다.
>
> 아이폰 4K 세로(2160×3840) 원본을 **원본 비율 그대로** 쓰면 `-850` / `+1400` 이
> 의도한 자리에 떨어집니다. 하지만 `--shorts`(1080×1920)로 만들면 범위가
> ±960px 라 `+1400` 은 **화면 밖**입니다. 그런 경우 실행 중에 경고와 함께
> 이 캔버스에 맞는 대체 수치를 알려줍니다.

```
입력      : 영상.mp4
해상도    : 1920x1080 @ 30.0fps
길이      : 00:25.00
  … 전사 캐시에서 꺼냄

문장       : 4개 → 자막 4장
대본      : ~/CapCutAgent/scripts/영상.대본.txt
드래프트  : …/com.lveditor.draft/영상-자막-1028-042258
```

| 옵션 | 쓸 때 |
|---|---|
| `--shorts` | 9:16 세로로 크롭까지 (기본은 원본 비율 그대로) |
| `--anchor top` | `--shorts` 일 때 인물이 아래쪽에 잡혀 있으면 |
| `--max-chars 14` | 자막 한 줄을 더 짧게 |
| `--subtitle-y -0.55` | 자막을 더 위로 (하단 UI가 가릴 때) |
| `--srt` | SRT 도 같이 내보내기 |
| `--inbox` | inbox 안의 영상을 전부 처리 |
| `--cut` | 무음 구간도 같이 잘라낸다 |
| `--title "문구"` | 제목 직접 지정 (생략하면 대본에서 뽑음) |
| `--no-title` | 제목 자막 없이 |
| `--speech-y` / `--title-y` | 세로 위치 (캔버스 px, 중앙 0, 위가 양수) |
| `--media-mode` | 미디어 반입 방식 (`auto`/`link`/`copy`/`none`) |

`shorts` / `build` 와 **전사 캐시를 공유**합니다. 같은 영상이면 전사를 다시 돌리지 않습니다.

---

### 모드 B — AI 보이스로 숏폼 만들기

아직 구현 중입니다. `broll/` 과 `voice/` 폴더는 미리 만들어져 있습니다.

---

### 보조 — 무음 컷만 (가로 그대로)

```bash
python -m capcut_agent.cli cut 영상.mp4 --dry-run   # 컷 계획만
python -m capcut_agent.cli cut --inbox              # inbox 전체
python -m capcut_agent.cli 영상.mp4                  # 서브커맨드 생략 = cut
```

`--inbox` 는 이름 순으로 전부 돌면서 성공분은 `done/`, 실패분은 `failed/` 로 옮깁니다.
원본을 그대로 두려면 `--keep-source`.

### 캡컷에서 확인 — 이게 진짜 검증입니다

1. **캡컷이 켜져 있으면 완전히 종료 후 재실행** (실행 중엔 목록이 안 갱신됩니다)
2. 홈 화면에서 방금 만든 프로젝트 열기
3. 처음부터 끝까지 재생하며 확인:
   - 말 첫 음절이 잘려 나가지 않는가
   - 자막이 말과 맞고, 하단 UI에 가리지 않는가
   - 세로 크롭에 인물이 제대로 들어왔는가 (아니면 `--anchor` 를 바꿔 다시)
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
| 1단 | `silence_detect` + `build_draft` → 점프컷 드래프트 | **완료 · 캡컷 재생 검증 대기** |
| 1.5단 | 파일 저장소 (inbox / done / failed / scripts / reports / config) | **완료** |
| 1.6단 | 미디어 경로 버그 수정 — 드래프트가 이동 후 경로를 가리키게 | **완료** |
| 2단 | 숏폼 기반 — 9:16 프레이밍, 한국어 자막, 오디오 트랙 | **완료** |
| 3단 | **모드 A** — 전사 → 대본 → 후보 추천 → 승인 → 숏폼 드래프트 | **완료 · 캡컷 재생 검증 대기** |
| 3.5단 | **자막만** (`caption`) — 컷 없이 전체 영상 + 자막 | **완료 · 캡컷 재생 검증 대기** |
| 4단 | **모드 B** — ElevenLabs 나레이션 + B롤 → 숏폼 드래프트 | 다음 |
| 5단 | FastAPI + 정적 HTML 1장 (drag/drop + SSE stepper) | 예정 |
| 6단 | 영상 프리뷰 + 보존 구간 사용자 마킹 (`[` / `]` 단축키) | 예정 |

각 단은 **캡컷에서 실제 재생 검증을 통과한 뒤에만** 다음으로 넘어갑니다.

---

## 구조

```
capcut-agent/
├── capcut_agent/
│   ├── env_check.py    # Step 0 — OS 감지, 트랙 판정, 도구 + 저장소 점검
│   ├── storage.py      # 파일 저장소 (inbox / broll / scripts / voice / …)
│   ├── paths.py        # 캡컷 드래프트 폴더 탐색 (Mac 샌드박스 포함)
│   ├── media.py        # ffprobe / ffmpeg 래퍼
│   ├── silence.py      # 무음 → 보존 구간 (순수 로직)
│   ├── framing.py      # 9:16 세로 크롭 계산 (순수 로직)
│   ├── subtitle.py     # 한국어 줄바꿈 · 큐 묶기 · SRT (순수 로직)
│   ├── eojeol.py       # whisper 토막 → 한국어 어절 복원 (순수 로직)
│   ├── transcribe.py   # 로컬 whisper + 내용 해시 캐시 + ASR 락
│   ├── highlight.py    # 대본 → 숏폼 후보 추천 + 승인 교환 파일
│   ├── draft.py        # 캡컷 드래프트 빌더 (video / audio / text 트랙)
│   └── cli.py          # check / cut / caption / shorts / build
└── tests/              # pytest 188개 — ffmpeg·whisper 없이도 도는 테스트
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

6-1. **macOS 캡컷은 샌드박스 안에서 돕니다.**
   컨테이너 밖 경로(`~/CapCutAgent/inbox/` 등)를 못 읽어 *"파일에 액세스할 수 없음 /
   Unsupported Media"* 로 뜹니다. UI 로 가져오면 macOS 가 파일 선택 창을 통해 권한을
   주지만, 손으로 쓴 드래프트에는 그게 없습니다. 그래서 미디어를 **드래프트 폴더 안**
   (`materials/`) 으로 들여놓고 그 경로를 가리킵니다. 같은 볼륨이면 하드링크라
   용량이 늘지 않습니다.

6-2. **아이폰 세로 영상은 회전 메타데이터를 씁니다.**
   `.MOV` 가 `1920x1080 + rotate 90°` 로 저장돼, 회전을 무시하면 세로 영상을
   가로 캔버스에 넣어 비율이 통째로 뒤집힙니다. ffprobe 는 두 가지 방식으로
   알려줍니다 — 예전 `tags.rotate`, 요즘 `side_data_list[].rotation`(Display Matrix,
   반시계 음수). 둘 다 읽습니다.

7. **원본 이동은 드래프트 생성보다 먼저.**
   드래프트는 미디어의 절대경로를 박아 넣습니다. 드래프트를 쓴 **뒤에** 원본을
   `done/` 으로 옮기면, 캡컷이 프로젝트를 열 때 *"미디어를 찾을 수 없다"* 며
   재연결을 요구합니다. 실사용에서 실제로 터진 버그이고,
   `tests/test_cli.py::test_draft_points_at_a_file_that_actually_exists` 가 막고 있습니다.

3단부터 붙을 것들 (아직 미구현):

8. **ASR 은 `asyncio.Lock` 으로 직렬화.** numba 가 스레드 안전하지 않아 동시 호출 시 segfault.
9. **ASR 캐시 키는 content hash.** mtime 기반은 업로드마다 miss 납니다.

9-0. **선행 공백이 하나도 없으면 어절 병합을 멈춰야 합니다.**
   공백을 떼고 주는 백엔드에 병합을 그대로 돌리면 문장 전체가 한 어절로 뭉칩니다.

9-1. **whisper 는 한국어 어절을 쪼갠다.** `"순서" + "가"`, `"중요합니" + "다"` 처럼
   갈라져 나오면 자막 줄바꿈이 전부 어긋납니다. 복원 단서는 whisper BPE 의
   **선행 공백**(`' '` / SentencePiece `'▁'`) — 새 어절을 시작하는 토막에만 붙습니다.
   그래서 전사 결과를 **strip 하기 전에** `eojeol.merge_eojeol()` 로 합쳐야 합니다.
   (이 접근은 [dawn-cut](https://github.com/kwakseongjae/dawn-cut) 에서 가져왔습니다 — `NOTICE` 참고)
10. **자막 줄바꿈은 의존명사를 앞 어절에 붙여야 합니다** (`것`, `수`, `때`, `거` …).
11. **자막 세로 위치는 `transform_y`.** 캡컷 좌표계는 화면 중앙이 0.

---

## 참고 프로젝트

**[dawn-cut](https://github.com/kwakseongjae/dawn-cut)** (MIT) — 100% 로컬에서 도는
오픈소스 영상 편집기. 텍스트 기반 컷, 무음 제거, 한국어 자동 자막, 9:16 리프레이밍,
SRT·챕터 내보내기를 전부 로컬 whisper.cpp + FFmpeg 로 처리합니다.

`eojeol.py` 의 한국어 어절 복원 접근을 여기서 가져왔습니다 (`NOTICE` 참고).

**겹치지 않는 부분은 출력입니다.**

| | dawn-cut | 캡컷 에이전트 |
|---|---|---|
| 출력 | MP4 · SRT · `.dawn` 프로젝트 | **캡컷 드래프트** |
| 이후 편집 | dawn-cut 자체 타임라인 | **캡컷 안에서 사람이 이어서** |
| 전사 | whisper.cpp (번들) | faster-whisper / mlx-whisper (설치) |
| 완성도 | 편집기 한 벌 | 캡컷으로 넘기는 전처리기 |

캡컷을 안 쓰실 거면 dawn-cut 쪽이 더 완결된 선택입니다. 캡컷에서 마무리하실 거면
이 도구가 그 앞단을 맡습니다.
