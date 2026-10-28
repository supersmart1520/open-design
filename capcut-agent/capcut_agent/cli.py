"""캡컷 에이전트 CLI.

    python3 -m capcut_agent.cli check               환경 + 저장소 점검
    python3 -m capcut_agent.cli cut 영상.mp4         무음 컷 (가로 그대로)
    python3 -m capcut_agent.cli caption 영상.mp4     컷 없이 자막만
    python3 -m capcut_agent.cli shorts 강의.mp4      전사 → 숏폼 후보 추천
    python3 -m capcut_agent.cli build <shorts.json>  승인한 후보 → 숏폼 드래프트

하위 호환: 서브커맨드 없이 파일이나 --inbox 를 주면 `cut` 으로 본다.
"""

from __future__ import annotations

import argparse
import json
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import storage
from .draft import (
    DEFAULT_TITLE_LAYOUT,
    TITLE_LAYOUTS,
    SPEECH_Y_PX,
    TITLE_FONT_SIZE,
    TITLE_SECONDS,
    TITLE_X_PX,
    TITLE_Y_PX,
    DraftBuilder,
    SubtitleStyle,
    default_media_mode,
    y_px_to_normalized,
)
from .env_check import DRAFT_CHECK_NAME, format_report, run_checks
from .framing import SHORTS
from .highlight import load_candidates, propose, save_candidates, selected
from .media import MediaInfo, detect_silence_raw, probe
from .paths import resolve_draft_root
from .silence import (
    Range,
    SilenceOptions,
    keeps_from_silence,
    keeps_from_words,
    keeps_hybrid,
    overlap_to_timeline,
)
from .subtitle import Word, auto_max_chars, cues_from_words, fill_gaps, to_srt
from .title import DEFAULT_MAX_CHARS as TITLE_MAX_CHARS
from .title import PALETTES, make_title
from .transcribe import BackendMissing, Transcript, pick_backend, transcribe

SUBCOMMANDS = ("check", "doctor", "cut", "caption", "shorts", "build", "serve",
                "app", "install-skill", "install-app")


def _fmt(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"


def blocking_checks(report, args, needs_draft: bool = True) -> list:
    """진행을 막아야 하는 점검 항목만 추린다.

    드래프트 폴더 자동 탐색은 두 경우에 무관하다.
      * --draft-dir 로 위치를 직접 줬을 때 (resolve_draft_root 가 검사한다)
      * --dry-run 처럼 드래프트를 아예 쓰지 않을 때
    """
    skip_draft = (not needs_draft) or bool(getattr(args, "draft_dir", None))
    return [
        check for check in report.blockers
        if not (skip_draft and check.name == DRAFT_CHECK_NAME)
    ]


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capcut-agent",
        description="강의 영상과 대본을 캡컷 숏폼 드래프트로.",
    )
    parser.add_argument("--workspace", help=f"작업 저장소 (기본: {storage.default_home()})")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("check", help="환경 + 저장소 점검")

    doctor = subparsers.add_parser(
        "doctor", help="whisper 가 실제로 도는지 끝까지 돌려 확인"
    )
    doctor.add_argument("--video", help="진단에 쓸 영상 (생략하면 inbox 첫 영상)")
    doctor.add_argument("--seconds", type=int, default=20,
                        help="잘라 쓸 길이 (기본 %(default)s초)")
    doctor.add_argument("--model", default="", help="whisper 모델")
    doctor.add_argument("--language", default="ko", help="전사 언어")

    cut = subparsers.add_parser("cut", help="무음 컷 드래프트 (가로 그대로)")
    cut.add_argument("video", nargs="?", help="입력 mp4 / mov")
    cut.add_argument("--inbox", action="store_true", help="inbox 안의 영상을 전부 처리")
    cut.add_argument("--name", help="드래프트 이름")
    cut.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")
    cut.add_argument("--keep-source", action="store_true",
                     help="원본을 inbox 에 그대로 둔다")
    cut.add_argument("--dry-run", action="store_true", help="컷 계획만 출력")
    for flag, help_text in (
        ("--noise-db", "무음 임계 dBFS. 조용한 녹음이면 -45 쪽으로."),
        ("--min-silence", "이보다 긴 무음만 자른다, 초"),
        ("--padding", "컷 앞뒤 여유, 초"),
        ("--min-keep", "이보다 짧은 조각은 버린다, 초"),
        ("--min-gap", "이보다 가까운 조각끼리 붙인다, 초"),
    ):
        cut.add_argument(flag, type=float, help=f"{help_text} (생략하면 config.json)")

    caption = subparsers.add_parser(
        "caption", help="컷 없이 자막만 (전체 영상 + 자막 트랙)"
    )
    caption.add_argument("video", nargs="?", help="영상 mp4 / mov")
    caption.add_argument("--inbox", action="store_true",
                         help="inbox 안의 영상을 전부 처리 (파일명을 안 적어도 된다)")
    caption.add_argument("--name", help="드래프트 이름")
    caption.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")
    caption.add_argument("--language", default="ko", help="전사 언어 (기본 ko)")
    caption.add_argument("--model", default="", help="whisper 모델 (생략하면 트랙 기본값)")
    caption.add_argument("--max-chars", type=int,
                         help="자막 한 줄 글자 수. 생략하면 폰트 크기에서 자동으로 구한다.")
    caption.add_argument("--font-size", type=float, default=12.0,
                         help="자막 글자 크기 (기본 %(default)s). 키우면 한 줄 글자 수가 준다.")
    caption.add_argument("--motion", choices=("none", "fade", "pop", "rise"),
                         default="none",
                         help="자막 등장 모션 (기본 %(default)s). 캡컷에서 "
                              "검증되기 전까지 꺼둡니다 — fade 가 가장 안전합니다.")
    caption.add_argument("--no-fill-gaps", action="store_true",
                         help="자막 사이 빈틈을 메우지 않는다 (기본은 메움)")
    caption.add_argument("--cut", action="store_true",
                         help="말 없는 구간도 같이 잘라낸다")
    caption.add_argument("--cut-by", choices=("hybrid", "speech", "silence"),
                         default="hybrid",
                         help="컷 기준. hybrid(기본)=음량으로 자를 자리를 정하고 "
                              "경계를 단어 경계로 밀어냄. speech=단어 사이 틈만. "
                              "silence=음량만(말이 잘릴 수 있음)")
    caption.add_argument("--speech-y", type=float, default=SPEECH_Y_PX,
                         help="말하는 자막 세로 위치 (캔버스 픽셀, 중앙 0, 위가 양수). "
                              "기본 %(default)s")
    caption.add_argument("--title",
                         help="제목 자막 문구. **주지 않으면 제목을 넣지 않는다.** "
                              "두 줄은 공백 또는 줄바꿈으로 가른다.")
    caption.add_argument("--title-layout", choices=sorted(TITLE_LAYOUTS),
                         default=DEFAULT_TITLE_LAYOUT,
                         help="제목 배치 (중앙=화면을 채움, 상단=얼굴 위 두 줄)")
    caption.add_argument("--title-y", type=float, default=None,
                         help="제목 세로 위치 (캔버스 픽셀, 중앙 0)")
    caption.add_argument("--no-title", action="store_true", help="제목 자막 없이")
    caption.add_argument("--title-max-chars", type=int, default=TITLE_MAX_CHARS,
                         help="제목 두 줄 합계 최대 글자 수 (기본 %(default)s)")
    caption.add_argument("--title-palette", choices=sorted(PALETTES),
                         default="green",
                         help="제목 2색 조합 (기본 %(default)s). 1행 강조색 + "
                              "2행 흰색, 외곽선은 검정.")
    caption.add_argument("--title-x", type=float, default=TITLE_X_PX,
                         help="제목 가로 위치 (캔버스 픽셀, 중앙 0)")
    caption.add_argument("--title-seconds", type=float, default=TITLE_SECONDS,
                         help="제목이 떠 있는 시간, 초 (기본 %(default)s)")
    caption.add_argument("--title-font-size", type=float, default=None,
                         help="제목 글자 크기 (기본 %(default)s). 말하는 자막보다 "
                              "훨씬 커야 썸네일에서 손가락이 멈춥니다.")
    caption.add_argument("--media-mode",
                         choices=("auto", "link", "copy", "none"),
                         help="미디어를 드래프트 폴더로 들이는 방식. macOS 는 "
                              "샌드박스 때문에 auto 가 기본이다.")
    caption.add_argument("--shorts", action="store_true",
                         help="9:16 세로로 크롭한다 (기본은 원본 비율 그대로)")
    caption.add_argument("--anchor", default="center",
                         choices=("center", "left", "right", "top", "bottom"),
                         help="--shorts 일 때 남길 쪽")
    caption.add_argument("--srt", action="store_true", help="SRT 도 같이 내보낸다")
    caption.add_argument("--preview", action="store_true",
                         help="미리보기 MP4 도 굽는다. 캡컷 없이 결과를 바로 본다.")
    caption.add_argument("--json", action="store_true",
                         help="마지막 줄에 결과를 JSON 한 줄로 출력한다 (대시보드용)")

    shorts = subparsers.add_parser("shorts", help="전사 → 숏폼 후보 추천")
    shorts.add_argument("video", help="강의 영상 mp4 / mov")
    shorts.add_argument("--language", default="ko", help="전사 언어 (기본 ko)")
    shorts.add_argument("--model", default="", help="whisper 모델 (생략하면 트랙 기본값)")
    shorts.add_argument("--min-seconds", type=float, default=18.0, help="후보 최소 길이")
    shorts.add_argument("--max-seconds", type=float, default=60.0, help="후보 최대 길이")
    shorts.add_argument("--limit", type=int, default=8, help="추천 개수")
    shorts.add_argument("--local-only", action="store_true",
                        help="모델을 쓰지 않고 규칙 기반으로만 고릅니다")
    shorts.add_argument("--llm-model", default="",
                        help="대본을 읽을 모델 (기본: 가장 좋은 모델)")
    shorts.add_argument("--auto", type=int, metavar="N",
                        help="승인 없이 점수 상위 N개를 바로 드래프트로 만든다. "
                             "한 영상에서 뽑을 숏폼 개수.")
    shorts.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")
    shorts.add_argument("--anchor", default="center",
                        choices=("center", "left", "right", "top", "bottom"),
                        help="세로 크롭에서 남길 쪽")
    shorts.add_argument("--motion", choices=("none", "fade", "pop", "rise"),
                        default="none", help="자막 등장 모션")
    shorts.add_argument("--font-size", type=float, default=12.0, help="자막 글자 크기")
    shorts.add_argument("--subtitle-y", type=float, default=-850.0,
                        help="자막 세로 위치 (캔버스 픽셀)")
    shorts.add_argument("--max-chars", type=int, help="자막 한 줄 글자 수")
    shorts.add_argument("--no-subtitle", action="store_true", help="자막 없이")
    shorts.add_argument("--srt", action="store_true", help="SRT 도 같이")
    shorts.add_argument("--json", action="store_true",
                        help="결과를 JSON 한 줄로 (대시보드용)")

    serve = subparsers.add_parser("serve", help="로컬 대시보드 실행")
    serve.add_argument("--host", default="127.0.0.1",
                       help="바인딩 주소 (기본 %(default)s — 바깥으로 열지 않는다)")
    serve.add_argument("--port", type=int, default=8765, help="포트 (기본 %(default)s)")
    serve.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")

    desktop = subparsers.add_parser("app", help="PC 앱으로 실행 (창 하나)")
    desktop.add_argument("--port", type=int, default=8765, help="포트 (기본 %(default)s)")
    desktop.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")

    install_app = subparsers.add_parser(
        "install-app", help="더블클릭으로 실행되는 앱 아이콘을 만든다"
    )
    install_app.add_argument("--python",
                             help="앱이 쓸 파이썬 (기본: 지금 이 파이썬)")
    install_app.add_argument("--dest", help="설치 위치 (기본: macOS ~/Applications, "
                                            "그 외 바탕화면)")

    skill = subparsers.add_parser(
        "install-skill", help="Claude Code 가 이 도구를 쓸 수 있게 스킬을 설치"
    )
    skill.add_argument("--dest", help="설치 위치 (기본 ~/.claude/skills/capcut-agent)")
    skill.add_argument("--copy", action="store_true",
                       help="심볼릭 링크 대신 복사한다 (저장소를 옮겨도 남는다)")

    build = subparsers.add_parser("build", help="승인한 후보 → 숏폼 드래프트")
    build.add_argument("plan", help="scripts/<이름>.shorts.json")
    build.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")
    build.add_argument("--anchor", default="center",
                       choices=("center", "left", "right", "top", "bottom"),
                       help="세로 크롭에서 남길 쪽 (기본 center)")
    build.add_argument("--max-chars", type=int,
                       help="자막 한 줄 글자 수. 생략하면 폰트 크기에서 자동으로 구한다.")
    build.add_argument("--font-size", type=float, default=12.0,
                       help="자막 글자 크기 (기본 %(default)s)")
    build.add_argument("--motion", choices=("none", "fade", "pop", "rise"),
                       default="none", help="자막 등장 모션 (기본 %(default)s)")
    build.add_argument("--subtitle-y", type=float, default=-0.72,
                       help="자막 세로 위치. 화면 중앙 0, 아래가 음수.")
    build.add_argument("--no-subtitle", action="store_true", help="자막 트랙 생략")
    build.add_argument("--srt", action="store_true", help="SRT 도 같이 내보낸다")
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    """하위 호환 — 서브커맨드가 없으면 cut 으로 본다."""
    if not argv:
        return argv
    if argv[0] == "--check":
        return ["check", *argv[1:]]
    if argv[0] in SUBCOMMANDS or argv[0] in ("-h", "--help"):
        return argv
    if argv[0] == "--workspace":
        # 전역 플래그가 먼저 온 경우. 값까지 넘기고 그 뒤를 다시 본다.
        return argv[:2] + normalize_argv(argv[2:])
    return ["cut", *argv]


# ------------------------------------------------------------------ cut (1단)


def resolve_options(workspace: storage.Workspace, args: argparse.Namespace) -> SilenceOptions:
    """config.json 을 바탕에 깔고, 명시한 플래그만 덮어쓴다."""
    options = storage.load_options(workspace)
    for name in ("noise_db", "min_silence", "padding", "min_keep", "min_gap"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(options, name, value)
    return options


def plan_cuts(media: MediaInfo, options: SilenceOptions) -> list[Range]:
    stderr = detect_silence_raw(media.path, options.noise_db, options.min_silence)
    return keeps_from_silence(stderr, media.duration, options)


def _cut_report(media, options, keeps, draft, source_before=None) -> dict:
    kept = sum(k.length for k in keeps)
    moved_from = (
        str(source_before)
        if source_before is not None and source_before != media.path
        else None
    )
    return {
        "모드": "cut",
        "생성시각": datetime.now().isoformat(timespec="seconds"),
        "원본": {
            "경로": str(media.path), "투입경로": moved_from,
            "해상도": f"{media.width}x{media.height}", "fps": media.fps,
            "길이초": round(media.duration, 3),
        },
        "옵션": options.to_dict(),
        "결과": {
            "보존조각": len(keeps), "컷수": max(len(keeps) - 1, 0),
            "출력길이초": round(kept, 3),
            "제거길이초": round(media.duration - kept, 3),
            "제거비율": round((1 - kept / media.duration) * 100, 1) if media.duration else 0,
        },
        "드래프트": str(draft) if draft else None,
        "보존구간": [{"시작": round(k.start, 3), "끝": round(k.end, 3)} for k in keeps],
    }


def process_one(video, workspace, options, args, draft_root) -> int:
    media = probe(video)
    print(f"입력      : {media.path.name}")
    print(f"해상도    : {media.width}x{media.height} @ {media.fps}fps")
    print(f"길이      : {_fmt(media.duration)}")
    if not media.has_audio:
        print("오디오 트랙이 없어 무음 컷을 할 수 없습니다.", file=sys.stderr)
        if not args.dry_run and not args.keep_source:
            storage.archive(workspace, video, ok=False)
        return 1

    keeps = plan_cuts(media, options)
    if not keeps:
        print("보존할 구간이 없습니다. --noise-db 를 낮춰 보세요 (예: -45).", file=sys.stderr)
        if not args.dry_run and not args.keep_source:
            storage.archive(workspace, video, ok=False)
        return 1

    kept = sum(k.length for k in keeps)
    print()
    print(f"보존      : {len(keeps)}조각 / {_fmt(kept)}")
    print(f"제거      : {_fmt(media.duration - kept)} "
          f"({(1 - kept / media.duration) * 100:.1f}%)")
    print(f"컷 수     : {max(len(keeps) - 1, 0)}")

    if args.dry_run:
        print()
        for i, keep in enumerate(keeps, 1):
            print(f"  {i:3d}. {_fmt(keep.start)} → {_fmt(keep.end)}  ({keep.length:5.2f}s)")
        return 0

    name = args.name or f"{media.path.stem}-{datetime.now():%m%d-%H%M%S}"

    # 원본을 드래프트보다 **먼저** 옮긴다. 드래프트에 절대경로가 박히므로,
    # 순서가 뒤집히면 캡컷이 열 때 미디어 재연결을 요구한다.
    source_before = media.path
    if not args.keep_source:
        moved = storage.archive(workspace, video, ok=True)
        if moved != source_before:
            media = replace(media, path=moved)
            print(f"원본 이동 : {moved}")

    builder = DraftBuilder(media=media, name=name)
    builder.add_video_cuts(keeps)
    assert draft_root is not None
    folder = builder.write(draft_root)
    report = storage.write_report(
        workspace, name, _cut_report(media, options, keeps, folder, source_before)
    )
    print(f"드래프트  : {folder}")
    print(f"기록      : {report}")
    return 0


def run_cut(args, workspace) -> int:
    report = run_checks(workspace)
    blockers = blocking_checks(report, args, needs_draft=not args.dry_run)
    if blockers:
        print(format_report(report), file=sys.stderr)
        return 1

    draft_root = None if args.dry_run else resolve_draft_root(args.draft_dir)
    options = resolve_options(workspace, args)

    if args.video:
        targets = [Path(args.video).expanduser()]
    elif args.inbox:
        targets = storage.list_inbox(workspace)
        if not targets:
            print(f"inbox 가 비어 있습니다: {workspace.inbox}")
            return 0
        print(f"inbox     : {len(targets)}개 처리")
    else:
        print("영상 경로나 --inbox 가 필요합니다.", file=sys.stderr)
        return 2

    failures = 0
    for i, target in enumerate(targets):
        if len(targets) > 1:
            print(f"\n--- [{i + 1}/{len(targets)}] {target.name} ---")
        try:
            if process_one(target, workspace, options, args, draft_root) != 0:
                failures += 1
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"실패: {target.name} — {exc}", file=sys.stderr)
            if not args.dry_run and not args.keep_source:
                storage.archive(workspace, target, ok=False)
            failures += 1

    if not args.dry_run and failures < len(targets):
        _print_verify_steps()
    return 1 if failures else 0


# ------------------------------------------------------------------- caption


def run_caption(args, workspace) -> int:
    resolve_title_layout(args)
    """컷 없이 자막만. 여러 개면 하나씩 돌린다."""
    report = run_checks(workspace)
    if blocking_checks(report, args):
        print(format_report(report), file=sys.stderr)
        return 1

    if args.video:
        targets = [Path(args.video).expanduser()]
    elif args.inbox:
        targets = storage.list_inbox(workspace)
        if not targets:
            print(f"inbox 가 비어 있습니다: {workspace.inbox}")
            return 0
        print(f"inbox     : {len(targets)}개 처리")
    else:
        print("영상 경로나 --inbox 가 필요합니다.", file=sys.stderr)
        return 2

    failures = 0
    for i, target in enumerate(targets):
        if len(targets) > 1:
            print(f"\n--- [{i + 1}/{len(targets)}] {target.name} ---")
        try:
            if caption_one(target, workspace, args) != 0:
                failures += 1
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"실패: {target.name} — {exc}", file=sys.stderr)
            failures += 1

    if failures < len(targets):
        _print_verify_steps()
    return 1 if failures else 0


def caption_one(video: Path, workspace, args) -> int:
    resolve_title_layout(args)
    """영상 하나에 자막을 얹는다. --cut 을 주면 무음도 같이 잘라낸다."""
    media = probe(video)
    shape = f"{media.width}x{media.height}"
    if media.rotation:
        shape += f" (회전 {media.rotation}°)"
    print(f"입력      : {media.path.name}")
    print(f"해상도    : {shape} @ {media.fps}fps")
    print(f"길이      : {_fmt(media.duration)}")
    if not media.has_audio:
        print("오디오가 없어 자막을 만들 수 없습니다.", file=sys.stderr)
        return 1

    backend = pick_backend(platform.system(), platform.machine())
    try:
        transcript = transcribe(
            media.path, workspace.asr_cache, backend,
            model=args.model, language=args.language,
            on_progress=lambda message: print(f"  … {message}"),
        )
    except BackendMissing as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    if not transcript.segments:
        print("전사 결과가 비었습니다. 오디오를 확인해 주세요.", file=sys.stderr)
        return 1

    # 컷을 먼저 정하고, 자막 시간을 그 결과 위로 옮긴다.
    keeps: list[Range] = []
    if args.cut:
        options = resolve_options(workspace, args)
        by = args.cut_by
        if by in ("hybrid", "speech") and not transcript.words:
            # 단어 타임스탬프가 없으면 경계를 밀어낼 기준이 없다.
            print("  … 단어 타임스탬프가 없어 음량 기준으로 자릅니다")
            by = "silence"
        if by == "speech":
            keeps = keeps_from_words(transcript.words, media.duration, options)
        elif by == "hybrid":
            stderr = detect_silence_raw(
                media.path, options.noise_db, options.min_silence
            )
            keeps = keeps_hybrid(stderr, transcript.words, media.duration, options)
        else:
            keeps = plan_cuts(media, options)
        if not keeps:
            hint = ("--min-silence 를 올려 보세요." if by == "speech"
                    else "--noise-db 를 낮춰 보세요.")
            print(f"보존할 구간이 없습니다. {hint}", file=sys.stderr)
            return 1
        kept = sum(k.length for k in keeps)
        label = {"speech": "대본 기준", "hybrid": "음량+단어경계",
                 "silence": "음량 기준"}[by]
        print(f"컷        : {len(keeps)}조각 / {_fmt(kept)} "
              f"({(1 - kept / media.duration) * 100:.1f}% 제거, {label})")

    words = transcript.words or [
        Word(s.text, s.start, s.end) for s in transcript.segments
    ]
    dropped = 0
    boundaries: list[float] = []
    if keeps:
        words, dropped = _shift_words(words, keeps)
        if dropped:
            print(f"  … 컷에 통째로 들어간 단어 {dropped}개는 자막에서 뺐습니다")
        boundaries = _cut_boundaries(keeps)
    max_chars = args.max_chars or auto_max_chars(args.font_size)
    cues = cues_from_words(words, max_chars=max_chars, boundaries=boundaries)
    if not cues:
        print("자막으로 만들 말이 없습니다.", file=sys.stderr)
        return 1

    name = args.name or f"{media.path.stem}-자막-{datetime.now():%m%d-%H%M%S}"
    canvas = SHORTS if args.shorts else None
    builder = DraftBuilder(
        media=media, name=name, canvas=canvas, crop_anchor=args.anchor
    )
    if keeps:
        builder.add_video_cuts(keeps)
    else:
        builder.add_clip(media, 0.0, media.duration)

    # 빈틈 메우기는 타임라인 길이를 알아야 하므로 클립을 깐 뒤에 한다.
    if not args.no_fill_gaps:
        cues = fill_gaps(
            cues, until=builder.timeline_us / 1_000_000, boundaries=boundaries
        )

    # 제목은 **직접 준 것만** 쓴다. 대본에서 뽑은 문구는 썸네일 카피로
    # 쓸 만하지 않았다 — 들은 말을 줄인 것이지 손가락을 멈추게 하는 문구가
    # 아니다. 레퍼런스 문법에 맞는 제목은 사람이 쓴다.
    title = None
    if args.title and not args.no_title:
        title = make_title(args.title, args.title_palette, args.title_max_chars)

    # 제목이 떠 있는 동안에는 말하는 자막을 띄우지 않는다. 두 덩어리가 겹치면
    # 화면이 무너지고, 썸네일로 쓸 첫 장면이 지저분해진다.
    title_span = None
    if title is not None:
        title_span = (
            0.0, min(args.title_seconds, builder.timeline_us / 1_000_000)
        )
        kept_cues = [
            cue for cue in cues
            if cue.end <= title_span[0] or cue.start >= title_span[1]
        ]
        if len(kept_cues) != len(cues):
            print(f"  … 제목 구간({title_span[1]:.1f}s)과 겹친 자막 "
                  f"{len(cues) - len(kept_cues)}장을 뺐습니다")
        cues = kept_cues

    height = builder.canvas.height
    width = builder.canvas.width
    speech_style = SubtitleStyle.from_pixels(
        args.speech_y, height, font_size=args.font_size, motion=args.motion
    )
    _warn_offscreen("말하는 자막", args.speech_y, height)
    builder.add_subtitles(cues, speech_style)

    if title is not None and title_span is not None:
        _warn_offscreen("제목 자막", args.title_y, height)
        builder.add_title(
            title,
            SubtitleStyle.from_pixels(
                args.title_y, height, args.title_x, width,
                font_size=args.title_font_size, motion=args.motion,
            ),
            start=title_span[0], end=title_span[1],
        )
        for note in title.warnings:
            print(f"  ⚠ 제목 {note}", file=sys.stderr)

    # 제목은 **직접 준 것만** 쓴다. 대본에서 뽑은 문구는 썸네일 카피로
    # 쓸 만하지 않았다 — 들은 말을 줄인 것이지 손가락을 멈추게 하는 문구가
    # 아니다. 레퍼런스 문법에 맞는 제목은 사람이 쓴다.
    title = None
    if args.title and not args.no_title:
        title = make_title(args.title, args.title_palette, args.title_max_chars)

    # 제목이 떠 있는 동안에는 말하는 자막을 띄우지 않는다. 두 덩어리가
    # 겹치면 화면이 무너지고, 썸네일로 쓸 첫 장면이 지저분해진다.
    title_span = None
    if title is not None:
        title_span = (0.0, min(args.title_seconds, builder.timeline_us / 1_000_000))
        cues = [c for c in cues if c.end <= title_span[0] or c.start >= title_span[1]]
        if not cues:
            print("  … 제목 구간에 가려 말하는 자막이 모두 빠졌습니다", file=sys.stderr)

    height = builder.canvas.height

    mode = args.media_mode or default_media_mode()
    folder = builder.write(resolve_draft_root(args.draft_dir), media_mode=mode)

    script_path = workspace.scripts / f"{media.path.stem}.대본.txt"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(transcript.script(), encoding="utf-8")

    post_path = write_post_text(workspace, media.path.stem, transcript)

    print()
    if post_path:
        print(f"게시문구  : {post_path}")
    print(f"캔버스    : {builder.canvas.width}x{builder.canvas.height}")
    print(f"문장       : {len(transcript.segments)}개 → 자막 {len(cues)}장 "
          f"(한 줄 {max_chars}자, 모션 {args.motion})")
    if title is not None:
        print(f"제목      : {title.line1} / {title.line2}"
              f"  ({args.title_palette}, 2행 {title.line2_scale*100:.0f}%)")
    if mode != "none":
        print(f"미디어     : 드래프트 폴더로 반입 ({mode}) — 샌드박스 대응")
    print(f"대본      : {script_path}")
    print(f"드래프트  : {folder}")

    if args.srt:
        srt_path = workspace.scripts / f"{name}.srt"
        srt_path.write_text(to_srt(cues), encoding="utf-8")
        print(f"SRT       : {srt_path}")

    preview_path = None
    if args.preview:
        # 드래프트와 **같은** 컷·자막 데이터로 굽는다. 따로 계산하면
        # 검수한 영상과 캡컷 프로젝트가 어긋난다.
        from .render import RenderPlan, render

        preview_path = workspace.previews / f"{name}.mp4"
        print("  … 미리보기 렌더 중")
        render(
            RenderPlan(
                media=media,
                keeps=keeps or [Range(0.0, media.duration)],
                cues=cues,
                canvas=builder.canvas,
                speech_style=speech_style,
                title=title,
                title_style=(
                    SubtitleStyle.from_pixels(
                        args.title_y, height, args.title_x, width,
                        font_size=args.title_font_size,
                    ) if title else None
                ),
                title_span=title_span,
            ),
            preview_path,
        )
        print(f"미리보기  : {preview_path}")

    report = {
        "모드": "caption",
        "생성시각": datetime.now().isoformat(timespec="seconds"),
        "원본": {
            "경로": str(media.path),
            "해상도": f"{media.width}x{media.height}",
            "회전": media.rotation,
            "길이초": round(media.duration, 3),
        },
        "전사": {
            "언어": transcript.language, "모델": transcript.model,
            "문장수": len(transcript.segments), "단어수": len(transcript.words),
        },
        "컷": {
            "적용": bool(keeps), "기준": args.cut_by if keeps else None,
            "보존조각": len(keeps),
            "출력길이초": round(sum(k.length for k in keeps), 3) if keeps
                        else round(media.duration, 3),
        },
        "자막": {
            "장수": len(cues), "한줄글자수": max_chars,
            "글자크기": args.font_size, "모션": args.motion,
            "빈틈메움": not args.no_fill_gaps,
            "컷으로버린단어": dropped,
            "말하는자막Ypx": args.speech_y,
            "제목": title.text if title else None,
            "제목1행": title.line1 if title else None,
            "제목2행": title.line2 if title else None,
            "제목팔레트": args.title_palette if title else None,
            "제목글자크기": args.title_font_size if title else None,
            "제목Ypx": args.title_y if title else None,
            "제목Xpx": args.title_x if title else None,
            "제목초": args.title_seconds if title else None,
        },
        "캔버스": f"{builder.canvas.width}x{builder.canvas.height}",
        "미디어반입": mode,
        "드래프트": str(folder),
        "대본파일": str(script_path),
        "미리보기": str(preview_path) if preview_path else None,
        "게시문구": str(post_path) if post_path else None,
    }
    storage.write_report(workspace, name, report)
    if getattr(args, "json", False):
        # 대시보드가 읽는 줄. 접두사로 찾으므로 사람이 읽는 출력과 섞여도 된다.
        print("__CAPCUT_JSON__ " + json.dumps(report, ensure_ascii=False))
    return 0


def _cut_boundaries(keeps: list[Range]) -> list[float]:
    """컷이 떨어지는 **타임라인** 시각들.

    자막 한 장이 이 지점을 넘으면 안 된다. 넘으면 멀리 떨어져 있던 두 말이
    한 장에 담긴다 — 컷으로 틈이 사라져 '쉼' 신호가 안 걸리기 때문이다.
    """
    edges: list[float] = []
    offset = 0.0
    for keep in keeps[:-1]:
        offset += keep.length
        edges.append(offset)
    return edges


def _shift_words(words: list[Word], keeps: list[Range]) -> tuple[list[Word], int]:
    """소스 시각의 단어들을 컷 적용 후 타임라인 시각으로 옮긴다.

    컷 경계에 걸친 단어는 **버리지 않고 겹치는 부분만** 쓴다. 무음 경계가
    단어 경계와 다르기 때문에, 버리면 말이 남아 있는데 자막만 없는 구간이
    생긴다. 소리가 통째로 사라진 단어만 버리고, 그 수를 같이 돌려준다.
    """
    shifted: list[Word] = []
    dropped = 0
    for word in words:
        span = overlap_to_timeline(keeps, word.start, word.end)
        if span is None:
            dropped += 1
            continue
        start, end = span
        if end <= start:
            end = start + 0.01
        shifted.append(Word(word.text, start, end))
    return shifted, dropped


#: 자막이 화면 가장자리에 붙지 않도록 남기는 여유 (정규화).
_EDGE_MARGIN = 0.92


def _warn_offscreen(label: str, y_px: float, canvas_height: int) -> None:
    """위치 값이 이 캔버스에서 화면을 벗어나는지 보고, 대안 수치를 알려준다.

    픽셀 값은 **그 값을 읽은 프로젝트의 캔버스**에 묶여 있다. 4K 세로(3840)
    프로젝트에서 읽은 y=1400 을 1080x1920 숏폼 캔버스에 그대로 쓰면 화면 밖이다.
    """
    normalized = y_px_to_normalized(y_px, canvas_height)
    half = canvas_height // 2
    if abs(normalized) <= 1.0:
        if abs(normalized) > _EDGE_MARGIN:
            print(
                f"  ⚠ {label} y={y_px:g}px 가 화면 가장자리에 거의 닿습니다 "
                f"(정규화 {normalized:+.2f}). 잘려 보일 수 있습니다.",
                file=sys.stderr,
            )
        return

    # 같은 '상대 위치' 를 이 캔버스에서 내려면 얼마여야 하는지 알려준다.
    suggested = int(round(_EDGE_MARGIN * half * (1 if y_px > 0 else -1)))
    print(
        f"  ⚠ {label} y={y_px:g}px 는 캔버스 세로 {canvas_height}px 에서 "
        f"화면 밖입니다 (정규화 {normalized:+.2f}).",
        file=sys.stderr,
    )
    print(
        f"     이 캔버스의 범위는 ±{half}px 입니다. "
        f"{suggested:+d} 정도를 쓰시거나, 원본 비율 그대로(--shorts 없이) 만드세요.",
        file=sys.stderr,
    )


# -------------------------------------------------------- shorts (모드 A 1단계)


#: 기승전결 네 단계. 표시 순서를 코드 여기저기서 다시 정하지 않는다.
ARC_STAGES = ("기", "승", "전", "결")


def format_arc(arc: dict) -> str:
    """왜 이 구간을 가져왔는지 한 줄로. 없는 단계는 가운뎃점으로 비워 둔다."""
    if not arc:
        return "기승전결: 찾지 못함"
    marks = "".join(stage if stage in arc else "·" for stage in ARC_STAGES)
    return f"기승전결 {marks}  " + " / ".join(
        f"{stage}: {_clip(arc[stage])}" for stage in ARC_STAGES if stage in arc
    )


def _clip(text: str, width: int = 22) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def write_post_text(workspace, name: str, transcript):
    """게시문구를 만들어 저장한다. 못 만들면 `None` — 편집은 계속된다.

    올리는 건 사람이 한다. 여기서는 복사해 붙일 글만 만든다.
    """
    from . import llm

    key = llm.load_key(workspace)
    if not key:
        return None
    try:
        post = llm.post_text(transcript, key)
    except llm.LLMUnavailable as exc:
        print(f"  … 게시문구를 만들지 못했습니다 — {exc}", file=sys.stderr)
        return None

    path = workspace.scripts / f"{name}.게시문구.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(llm.format_post(post), encoding="utf-8")
    return path


def choose_candidates(args, workspace, transcript):
    """숏폼 후보와 **어떻게 골랐는지**.

    대본을 읽고 고르는 쪽이 낫다. 규칙 기반은 낱말과 자리를 맞힐 뿐이다.
    다만 키가 없거나 인터넷이 끊겨 있을 수 있으므로, 그때는 규칙 기반으로
    떨어지고 그 사실을 **감추지 않는다** — 고른 방식이 다르면 결과도 다르다.
    """
    from . import llm

    model = getattr(args, "llm_model", "") or llm.DEFAULT_MODEL
    if not getattr(args, "local_only", False):
        key = llm.load_key(workspace)
        if key:
            try:
                print(f"  … 클로드가 대본을 읽는 중 ({model})")
                picked = llm.select(transcript, args.limit, key, model=model)
                return picked, f"클로드가 대본을 읽고 선정 ({model})"
            except llm.LLMUnavailable as exc:
                print(f"  … 모델을 쓰지 못했습니다 — {exc}", file=sys.stderr)
                print("  … 규칙 기반으로 고릅니다.", file=sys.stderr)
        else:
            print("  … API 키가 없어 규칙 기반으로 고릅니다.")

    picked = propose(
        transcript,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        limit=args.limit,
    )
    return picked, "규칙 기반 (낱말·자리)"


def run_shorts(args, workspace) -> int:
    report = run_checks(workspace)
    # 드래프트를 아직 쓰지 않으므로 캡컷이 없어도 된다.
    if blocking_checks(report, args, needs_draft=False):
        print(format_report(report), file=sys.stderr)
        return 1

    media = probe(args.video)
    print(f"입력      : {media.path.name}")
    print(f"길이      : {_fmt(media.duration)}")
    if not media.has_audio:
        print("오디오가 없어 전사할 수 없습니다.", file=sys.stderr)
        return 1

    backend = pick_backend(platform.system(), platform.machine())
    try:
        transcript = transcribe(
            media.path, workspace.asr_cache, backend,
            model=args.model, language=args.language,
            on_progress=lambda message: print(f"  … {message}"),
        )
    except BackendMissing as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    if not transcript.segments:
        print("전사 결과가 비었습니다. 오디오를 확인해 주세요.", file=sys.stderr)
        return 1

    stem = media.path.stem
    script_path = workspace.scripts / f"{stem}.대본.txt"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(transcript.script(), encoding="utf-8")

    transcript_path = workspace.scripts / f"{stem}.transcript.json"
    transcript_path.write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    candidates, picked_by = choose_candidates(args, workspace, transcript)
    plan_path = save_candidates(
        workspace.scripts / f"{stem}.shorts.json", media.path, transcript, candidates
    )

    print()
    print(f"대본      : {script_path}")
    print(f"전사       : {transcript_path}  ({len(transcript.segments)}문장, "
          f"{len(transcript.words)}단어)")
    print(f"후보       : {plan_path}")
    print()
    if not candidates:
        print("추천할 후보가 없습니다. --min-seconds 를 낮춰 보세요.")
        return 0

    print(f"고른 방식   : {picked_by}")
    print()
    for candidate in candidates:
        print(f"  #{candidate.index}  {_fmt(candidate.start)} → {_fmt(candidate.end)}  "
              f"({candidate.duration:4.1f}s)  점수 {candidate.score:4.1f}")
        print(f"      {candidate.title}")
        print(f"      {format_arc(candidate.arc)}")
        for reason in candidate.reasons[:3]:
            print(f"      · {reason}")
    if args.auto:
        return _auto_build(args, workspace, plan_path, candidates)

    print()
    print("다음 — 고르는 건 사람이 합니다:")
    print(f"  1. {plan_path} 를 열어 쓸 후보의 \"선택\" 을 true 로 바꾸세요")
    print("     (시작·끝 초를 직접 고쳐도 됩니다)")
    print(f"  2. python3 -m capcut_agent.cli build \"{plan_path}\"")
    return 0


def _auto_build(args, workspace, plan_path: Path, candidates) -> int:
    """승인을 건너뛰고 점수 상위 N개를 바로 만든다.

    사람이 고르는 쪽이 늘 낫다. 다만 한 영상에서 N개를 뽑아야 하는 작업량이면
    매번 고르고 앉아 있을 수 없다. 그래서 고르는 자리를 옵션으로 연다.
    승인 자체는 대시보드의 '검수 대기' 에서 여전히 사람이 한다.
    """
    wanted = max(1, args.auto)
    chosen = sorted(candidates, key=lambda c: -c.score)[:wanted]
    for candidate in chosen:
        candidate.selected = True
    save_candidates(plan_path, Path(args.video).expanduser(),
                    Transcript(language="", text=""), candidates)

    print()
    print(f"자동 선택  : 점수 상위 {len(chosen)}개")
    build_args = argparse.Namespace(
        plan=str(plan_path), draft_dir=args.draft_dir, anchor=args.anchor,
        max_chars=args.max_chars, font_size=args.font_size,
        motion=args.motion, subtitle_y=args.subtitle_y,
        no_subtitle=args.no_subtitle, srt=args.srt,
        json=getattr(args, "json", False),
    )
    return run_build(build_args, workspace)


# --------------------------------------------------------- build (모드 A 2단계)


def _window_cues(transcript: Transcript, start: float, end: float, max_chars: int):
    """구간 안의 단어만 추려 타임라인(0 기준) 시간으로 옮긴 자막 큐."""
    inside = [
        Word(w.text, w.start - start, w.end - start)
        for w in transcript.words
        if w.start >= start - 1e-6 and w.end <= end + 1e-6
    ]
    if not inside:
        # 단어 타임스탬프가 없으면 문장 단위로 떨어뜨린다.
        inside = [
            Word(s.text, max(s.start - start, 0.0), min(s.end - start, end - start))
            for s in transcript.segments
            if s.end > start and s.start < end
        ]
    return cues_from_words(inside, max_chars=max_chars)


def run_build(args, workspace) -> int:
    report = run_checks(workspace)
    if blocking_checks(report, args):
        print(format_report(report), file=sys.stderr)
        return 1

    plan_path = Path(args.plan).expanduser()
    if not plan_path.is_file():
        print(f"후보 파일이 없습니다: {plan_path}", file=sys.stderr)
        return 1

    source, candidates = load_candidates(plan_path)
    chosen = selected(candidates)
    if not chosen:
        print(f"\"선택\": true 로 표시된 후보가 없습니다.")
        print(f"  {plan_path} 를 열어 쓸 후보를 켜 주세요.")
        return 1

    media = probe(source)
    transcript_path = plan_path.with_name(plan_path.name.replace(".shorts.json", ".transcript.json"))
    transcript = None
    if transcript_path.is_file():
        transcript = Transcript.from_dict(
            json.loads(transcript_path.read_text(encoding="utf-8"))
        )
    elif not args.no_subtitle:
        print(f"전사 파일이 없어 자막을 만들 수 없습니다: {transcript_path}",
              file=sys.stderr)
        print("  --no-subtitle 로 자막 없이 만들거나, shorts 를 다시 돌리세요.",
              file=sys.stderr)
        return 1

    draft_root = resolve_draft_root(args.draft_dir)
    max_chars = args.max_chars or auto_max_chars(args.font_size)
    style = SubtitleStyle(
        transform_y=args.subtitle_y, font_size=args.font_size, motion=args.motion
    )
    stamp = f"{datetime.now():%m%d-%H%M%S}"
    print(f"원본      : {media.path}")
    print(f"선택       : {len(chosen)}개")

    made = []
    for candidate in chosen:
        name = f"{media.path.stem}-숏폼{candidate.index:02d}-{stamp}"
        builder = DraftBuilder(
            media=media, name=name, canvas=SHORTS, crop_anchor=args.anchor
        )
        builder.add_clip(media, candidate.start, candidate.duration)

        cues = []
        if transcript is not None and not args.no_subtitle:
            cues = _window_cues(transcript, candidate.start, candidate.end, max_chars)
            builder.add_subtitles(cues, style)

        folder = builder.write(draft_root)
        made.append((candidate, folder, cues))
        print(f"\n  #{candidate.index} {candidate.title}")
        print(f"      {_fmt(candidate.start)} → {_fmt(candidate.end)} "
              f"({candidate.duration:.1f}s), 자막 {len(cues)}장")
        print(f"      {folder}")

        if args.srt and cues:
            srt_path = workspace.scripts / f"{name}.srt"
            srt_path.write_text(to_srt(cues), encoding="utf-8")
            print(f"      {srt_path}")

    report = {
        "모드": "shorts",
        "생성시각": datetime.now().isoformat(timespec="seconds"),
        "원본": str(media.path),
        "후보파일": str(plan_path),
        "캔버스": f"{SHORTS.width}x{SHORTS.height}",
        "크롭앵커": args.anchor,
        "만든것": [
            {
                "번호": c.index, "제목": c.title,
                "시작": round(c.start, 2), "끝": round(c.end, 2),
                "길이": round(c.duration, 1), "자막장수": len(cues),
                "드래프트": str(folder),
            }
            for c, folder, cues in made
        ],
        # 대시보드 요약이 caption 과 같은 키를 읽을 수 있게 맞춰 둔다.
        "드래프트": str(made[0][1]) if made else None,
        "자막": {"장수": sum(len(cues) for _, _, cues in made)},
        "컷": {"적용": True, "보존조각": len(made)},
        "원본": {"경로": str(media.path),
                 "해상도": f"{media.width}x{media.height}",
                 "길이초": round(media.duration, 3)},
        "캔버스": f"{SHORTS.width}x{SHORTS.height}",
    }
    storage.write_report(workspace, f"{media.path.stem}-숏폼-{stamp}", report)
    if getattr(args, "json", False):
        print("__CAPCUT_JSON__ " + json.dumps(report, ensure_ascii=False))
    _print_verify_steps()
    return 0


def run_app(args, workspace) -> int:
    """PC 앱. 서버를 띄우고 창을 연다."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("앱 실행에는 추가 패키지가 필요합니다:", file=sys.stderr)
        print(f"  {install_hint()}", file=sys.stderr)
        return 1

    from .desktop import run as run_desktop

    return run_desktop(workspace.home, args.draft_dir, args.port)


_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>캡컷 에이전트</string>
  <key>CFBundleDisplayName</key><string>캡컷 에이전트</string>
  <key>CFBundleIdentifier</key><string>com.capcutagent.launcher</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIconFile</key><string>appicon</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


#: 앱이 남기는 기록. .app 안에서는 터미널 출력이 아무 데도 안 보인다.
APP_LOG = "~/CapCutAgent/app.log"

#: 네이티브 창은 **선택**이다. `pywebview` 는 `pyobjc` 를 소스에서 빌드하는데,
#: Xcode 가 들고 오는 파이썬 3.9 에서는 컴파일이 깨진다. 이걸 필수 패키지와
#: 같은 줄에 적으면 pip 가 하나만 실패해도 **전부** 설치하지 않아서, 꼭 필요한
#: fastapi/uvicorn 까지 같이 날아간다. 그래서 줄을 나눈다.
def window_note(python: str = "python3") -> str:
    """창은 선택. venv 로 깔았으면 `python3` 이 아니라 그 파이썬이어야 한다."""
    return f"앱 자체 창으로 띄우려면 (선택):  {python} -m pip install pywebview"
WINDOW_FALLBACK = "  — 없어도 됩니다. 크롬 계열이 있으면 탭 없는 창으로 열립니다."


def install_hint(python: str = "python3") -> str:
    """앱과 대시보드에 반드시 필요한 것만."""
    return f"{python} -m pip install fastapi 'uvicorn[standard]'"


def _mac_launcher(package_dir: Path, python: str) -> str:
    """더블클릭했을 때 실행되는 스크립트.

    `.app` 으로 실행하면 stdout/stderr 가 어디에도 보이지 않는다. 그래서
    실패하면 **아무 일도 일어나지 않은 것처럼** 보인다. 기록을 파일로 남기고,
    실패하면 네이티브 창으로 알린다.

    알림 문구는 osascript 에 **인자로** 넘긴다. 문자열에 끼워 넣으면 따옴표나
    줄바꿈이 섞이는 순간 스크립트가 깨진다 — 오류를 알리려다 오류가 난다.
    """
    return f"""#!/bin/bash
APPDIR={shlex.quote(str(package_dir))}
PY={shlex.quote(python)}
LOG="$HOME/CapCutAgent/app.log"
mkdir -p "$(dirname "$LOG")"

alert() {{
  /usr/bin/osascript -e 'on run argv' -e 'display alert "캡컷 에이전트" message (item 1 of argv) as critical' -e 'end run' -- "$1" >/dev/null 2>&1
}}

if [ ! -d "$APPDIR" ]; then
  alert "설치된 폴더를 찾지 못했습니다.

$APPDIR

저장소를 옮기셨다면 install-app 을 다시 실행하세요."
  exit 1
fi

cd "$APPDIR" || exit 1
{{ echo; echo "=== $(date) ==="; }} >>"$LOG"

# Xcode 가 들고 오는 파이썬은 universal2 라 arm64 로도 x86_64 로도 뜬다.
# 그런데 확장 모듈(`_pydantic_core.so`)은 pip 를 돌린 쪽 하나만 깔려 있다.
# 터미널에서는 되고 `.app` 에서는 안 되는 일이 여기서 갈린다.
#
# 인터프리터가 뜨는지만 봐서는 소용없다. **import 가 되는 쪽**을 찾는다.
PYRUN=""
FOUND=no
for CAND in "/usr/bin/arch -arm64" "/usr/bin/arch -x86_64" ""; do
  if $CAND "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    PYRUN="$CAND"
    FOUND=yes
    echo "파이썬: $CAND $PY" >>"$LOG"
    break
  fi
done

if [ "$FOUND" = no ]; then
  # 어느 아키텍처로도 못 읽는다. 진짜 오류를 기록에 남겨 둔다.
  "$PY" -c "import fastapi, uvicorn" >>"$LOG" 2>&1
  alert "필요한 패키지를 읽지 못했습니다.

$PY

아래를 터미널에 붙여 넣고 다시 열어 보세요:
  $PY -m pip install --force-reinstall fastapi 'uvicorn[standard]'

기록: $LOG"
  exit 1
fi

START=$(date +%s)
$PYRUN "$PY" -m capcut_agent.cli app >>"$LOG" 2>&1
STATUS=$?
ELAPSED=$(( $(date +%s) - START ))
echo "끝남: 코드 $STATUS, $ELAPSED 초" >>"$LOG"

if [ $STATUS -ne 0 ]; then
  alert "실행에 실패했습니다 (코드 $STATUS)

$(tail -n 10 "$LOG")

기록: $LOG"
elif [ $ELAPSED -lt 5 ] && ! tail -n 40 "$LOG" | grep -q __CAPCUT_APP_UP__; then
  # 성공 코드로 바로 끝났고, 서버가 떴다는 표식도 없다. 아이콘만 깜빡이고
  # 사라진 것처럼 보이는데 코드가 0 이라 지금까지는 아무 말도 하지 않았다.
  # 표식이 있으면 떴다가 닫힌 것이다 — 그건 알릴 일이 아니다.
  alert "앱이 바로 종료되었습니다 ($ELAPSED 초).

$(tail -n 10 "$LOG")

기록: $LOG"
fi
exit $STATUS
"""


#: 런처가 시도하는 순서와 같다. 빈 문자열은 "그냥 실행".
ARCH_CANDIDATES = (("/usr/bin/arch", "-arm64"), ("/usr/bin/arch", "-x86_64"), ())


def has_module(python: str, arch: tuple[str, ...], name: str) -> bool:
    """굳힐 파이썬이 그 모듈을 읽을 수 있나. 런처와 같은 방식으로 묻는다."""
    try:
        done = subprocess.run(
            [*arch, python, "-c", f"import {name}"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def working_arch(python: str) -> tuple[str, ...] | None:
    """`fastapi`/`uvicorn` 을 **읽을 수 있는** 아키텍처를 찾는다.

    이 프로세스에서 `find_spec` 로 확인하면 안 된다. universal2 파이썬은
    arm64 로도 x86_64 로도 뜨는데 확장 모듈은 한쪽만 깔려 있고, 터미널에서
    되는 것이 `.app` 안에서 된다는 보장이 없다. 굳혀 넣을 파이썬을 **실제로
    실행해서** 확인한다. 못 찾으면 `None`.
    """
    for prefix in ARCH_CANDIDATES:
        try:
            done = subprocess.run(
                [*prefix, python, "-c", "import fastapi, uvicorn"],
                capture_output=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return prefix
    return None


def _clear_quarantine(path: Path) -> None:
    """서명이 없는 번들은 격리 속성 때문에 macOS 가 막는다. 직접 만든 것이니 뗀다."""
    try:
        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", str(path)],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def run_install_app(args) -> int:
    """더블클릭으로 실행되는 아이콘을 만든다.

    파이썬 경로와 패키지 위치를 지금 값으로 굳혀 넣는다. 저장소를 옮기면
    다시 돌려야 한다 — 그 사실을 출력에 적어 둔다.
    """
    from . import __version__

    package_dir = Path(__file__).resolve().parent.parent
    python = args.python or sys.executable
    system = platform.system()

    if system == "Darwin":
        root = Path(args.dest).expanduser() if args.dest else (
            Path.home() / "Applications"
        )
        bundle = root / "캡컷 에이전트.app"
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True, exist_ok=True)
        (bundle / "Contents" / "Info.plist").write_text(
            _PLIST.format(version=__version__), encoding="utf-8"
        )
        icon = Path(__file__).resolve().parent / "assets" / "appicon.icns"
        if icon.is_file():
            resources = bundle / "Contents" / "Resources"
            resources.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon, resources / "appicon.icns")

        launcher = macos / "launch"
        launcher.write_text(_mac_launcher(package_dir, python), encoding="utf-8")
        launcher.chmod(0o755)
        _clear_quarantine(bundle)
        target = bundle
    else:
        root = Path(args.dest).expanduser() if args.dest else (
            Path.home() / "Desktop"
        )
        root.mkdir(parents=True, exist_ok=True)
        if system == "Windows":
            target = root / "캡컷 에이전트.cmd"
            target.write_text(
                "@echo off\r\n"
                f'cd /d "{package_dir}"\r\n'
                f'"{python}" -m capcut_agent.cli app\r\n',
                encoding="utf-8",
            )
        else:
            target = root / "캡컷 에이전트.sh"
            target.write_text(
                "#!/bin/bash\n"
                f'cd "{package_dir}" || exit 1\n'
                f'exec "{python}" -m capcut_agent.cli app\n',
                encoding="utf-8",
            )
            target.chmod(0o755)

    arch = working_arch(python)

    print(f"앱 설치   : {target}")
    print(f"파이썬    : {python}")
    print(f"패키지    : {package_dir}")
    print(f"기록      : {APP_LOG}")
    print()
    if arch is None:
        # 이대로 더블클릭하면 창이 안 뜨고 끝난다. 설치 때 말해 준다.
        print("⚠ 아직 실행되지 않습니다 — fastapi/uvicorn 을 읽지 못했습니다.")
        print(f"  {install_hint(python)}")
        print()
        print("다른 파이썬을 쓰려면:  install-app --python <경로>")
    else:
        print("더블클릭하면 창이 열립니다.")
        if arch:
            print(f"  ({' '.join(arch)} 로 띄웁니다 — 패키지가 그쪽에 깔려 있습니다)")
        print()
        if has_module(python, arch, "webview"):
            print("앱 자체 창으로 뜹니다 (pywebview).")
        else:
            print(window_note(python))
            print(WINDOW_FALLBACK)
    print()
    print("열리지 않으면 터미널에서 직접 실행해 오류를 보세요:")
    print(f'  "{target}/Contents/MacOS/launch"'
          if str(target).endswith(".app") else f'  "{target}"')
    print()
    print("저장소를 다른 폴더로 옮기면 경로가 굳어 있어 다시 설치해야 합니다.")
    return 0


def run_install_skill(args) -> int:
    """Claude Code 가 찾는 자리에 스킬을 놓는다.

    기본은 심볼릭 링크다 — 저장소를 `git pull` 하면 스킬도 같이 최신이 된다.
    저장소를 옮기거나 지울 거면 `--copy`.
    """
    source = Path(__file__).resolve().parent.parent / "SKILL.md"
    if not source.is_file():
        print(f"스킬 파일이 없습니다: {source}", file=sys.stderr)
        return 1

    destination = Path(args.dest).expanduser() if args.dest else (
        Path.home() / ".claude" / "skills" / "capcut-agent"
    )
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "SKILL.md"

    if target.exists() or target.is_symlink():
        target.unlink()
    if args.copy:
        shutil.copy2(source, target)
        how = "복사"
    else:
        try:
            target.symlink_to(source)
            how = "링크"
        except OSError:
            shutil.copy2(source, target)
            how = "복사 (링크 실패)"

    print(f"설치 완료 : {target}  ({how})")
    print(f"원본      : {source}")
    print()
    print("Claude Code 를 새로 시작하면 인식합니다. 이렇게 말해 보세요:")
    print('  "inbox 영상에 자막 달고 무음 잘라줘"')
    return 0


def run_serve(args, workspace) -> int:
    """로컬 대시보드. 바깥으로 열지 않는다."""
    try:
        import uvicorn
    except ImportError:
        print("대시보드에는 추가 패키지가 필요합니다:", file=sys.stderr)
        print(f"  {install_hint()}", file=sys.stderr)
        return 1

    from .web.app import create_app

    print(f"저장소    : {workspace.home}")
    print(f"대시보드  : http://{args.host}:{args.port}")
    print("종료하려면 Ctrl+C")
    uvicorn.run(
        create_app(workspace.home, args.draft_dir),
        host=args.host, port=args.port, log_level="warning",
    )
    return 0


def _print_verify_steps() -> None:
    print()
    print("검증 — 여기서부터는 사람이 합니다:")
    print("  1. 캡컷이 실행 중이면 완전히 종료 후 다시 켜세요 (목록 갱신이 안 됩니다)")
    print("  2. 홈 화면에서 방금 만든 프로젝트를 여세요")
    print("  3. 처음부터 끝까지 재생해 컷·자막이 말과 맞는지 확인하세요")


# ----------------------------------------------------------------------- main


def resolve_title_layout(args) -> None:
    """제목 배치를 위치·크기로 푼다. 직접 준 값이 있으면 그게 이긴다.

    배치는 묶음일 뿐이고 최종 권한은 사람에게 있다. `--title-layout 상단`
    을 고르고도 `--title-y` 를 따로 주면 그 값을 쓴다.
    """
    layout = TITLE_LAYOUTS.get(getattr(args, "title_layout", "") or "")
    if layout is None:
        return
    if getattr(args, "title_y", None) is None:
        args.title_y = layout["y_px"]
    if getattr(args, "title_font_size", None) is None:
        args.title_font_size = layout["font_size"]


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(normalize_argv(raw))
    resolve_title_layout(args)
    workspace = storage.resolve_workspace(args.workspace)

    if args.command == "doctor":
        from .doctor import format_diagnosis
        from .doctor import run as run_doctor

        print(format_diagnosis(run_doctor(
            workspace, args.video, args.seconds, args.model, args.language
        )))
        return 0
    if args.command == "check" or not args.command:
        report = run_checks(workspace)
        print(format_report(report))
        if not args.command:
            print()
            parser.print_help()
        return 0 if report.ok else 1
    if args.command == "cut":
        return run_cut(args, workspace)
    if args.command == "caption":
        return run_caption(args, workspace)
    if args.command == "shorts":
        return run_shorts(args, workspace)
    if args.command == "build":
        return run_build(args, workspace)
    if args.command == "serve":
        return run_serve(args, workspace)
    if args.command == "install-skill":
        return run_install_skill(args)
    if args.command == "app":
        return run_app(args, workspace)
    if args.command == "install-app":
        return run_install_app(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
