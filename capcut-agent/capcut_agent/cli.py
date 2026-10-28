"""캡컷 에이전트 CLI.

    python3 -m capcut_agent.cli check               환경 + 저장소 점검
    python3 -m capcut_agent.cli cut 영상.mp4         무음 컷 (1단, 가로 그대로)
    python3 -m capcut_agent.cli shorts 강의.mp4      전사 → 숏폼 후보 추천
    python3 -m capcut_agent.cli build <shorts.json>  승인한 후보 → 숏폼 드래프트

하위 호환: 서브커맨드 없이 파일이나 --inbox 를 주면 `cut` 으로 본다.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import storage
from .draft import DraftBuilder, SubtitleStyle
from .env_check import DRAFT_CHECK_NAME, format_report, run_checks
from .framing import SHORTS
from .highlight import load_candidates, propose, save_candidates, selected
from .media import MediaInfo, detect_silence_raw, probe
from .paths import resolve_draft_root
from .silence import Range, SilenceOptions, keeps_from_silence
from .subtitle import DEFAULT_MAX_CHARS, Word, cues_from_words, to_srt
from .transcribe import BackendMissing, Transcript, pick_backend, transcribe

SUBCOMMANDS = ("check", "cut", "shorts", "build")


def _fmt(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capcut-agent",
        description="강의 영상과 대본을 캡컷 숏폼 드래프트로.",
    )
    parser.add_argument("--workspace", help=f"작업 저장소 (기본: {storage.default_home()})")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("check", help="환경 + 저장소 점검")

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

    shorts = subparsers.add_parser("shorts", help="전사 → 숏폼 후보 추천")
    shorts.add_argument("video", help="강의 영상 mp4 / mov")
    shorts.add_argument("--language", default="ko", help="전사 언어 (기본 ko)")
    shorts.add_argument("--model", default="", help="whisper 모델 (생략하면 트랙 기본값)")
    shorts.add_argument("--min-seconds", type=float, default=18.0, help="후보 최소 길이")
    shorts.add_argument("--max-seconds", type=float, default=60.0, help="후보 최대 길이")
    shorts.add_argument("--limit", type=int, default=8, help="추천 개수")

    build = subparsers.add_parser("build", help="승인한 후보 → 숏폼 드래프트")
    build.add_argument("plan", help="scripts/<이름>.shorts.json")
    build.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")
    build.add_argument("--anchor", default="center",
                       choices=("center", "left", "right", "top", "bottom"),
                       help="세로 크롭에서 남길 쪽 (기본 center)")
    build.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                       help="자막 한 줄 글자 수")
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
    blockers = [
        c for c in report.blockers
        if not (args.dry_run and c.name == DRAFT_CHECK_NAME)
    ]
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


# -------------------------------------------------------- shorts (모드 A 1단계)


def run_shorts(args, workspace) -> int:
    report = run_checks(workspace)
    # 드래프트를 아직 쓰지 않으므로 캡컷이 없어도 된다.
    blockers = [c for c in report.blockers if c.name != DRAFT_CHECK_NAME]
    if blockers:
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

    candidates = propose(
        transcript,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        limit=args.limit,
    )
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

    for candidate in candidates:
        print(f"  #{candidate.index}  {_fmt(candidate.start)} → {_fmt(candidate.end)}  "
              f"({candidate.duration:4.1f}s)  점수 {candidate.score:4.1f}")
        print(f"      {candidate.title}")
        for reason in candidate.reasons[:3]:
            print(f"      · {reason}")
    print()
    print("다음 — 고르는 건 사람이 합니다:")
    print(f"  1. {plan_path} 를 열어 쓸 후보의 \"선택\" 을 true 로 바꾸세요")
    print("     (시작·끝 초를 직접 고쳐도 됩니다)")
    print(f"  2. python3 -m capcut_agent.cli build \"{plan_path}\"")
    return 0


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
    if report.blockers:
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
    style = SubtitleStyle(transform_y=args.subtitle_y)
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
            cues = _window_cues(transcript, candidate.start, candidate.end, args.max_chars)
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

    storage.write_report(workspace, f"{media.path.stem}-숏폼-{stamp}", {
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
    })
    _print_verify_steps()
    return 0


def _print_verify_steps() -> None:
    print()
    print("검증 — 여기서부터는 사람이 합니다:")
    print("  1. 캡컷이 실행 중이면 완전히 종료 후 다시 켜세요 (목록 갱신이 안 됩니다)")
    print("  2. 홈 화면에서 방금 만든 프로젝트를 여세요")
    print("  3. 처음부터 끝까지 재생해 컷·자막이 말과 맞는지 확인하세요")


# ----------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(normalize_argv(raw))
    workspace = storage.resolve_workspace(args.workspace)

    if args.command == "check" or not args.command:
        report = run_checks(workspace)
        print(format_report(report))
        if not args.command:
            print()
            parser.print_help()
        return 0 if report.ok else 1
    if args.command == "cut":
        return run_cut(args, workspace)
    if args.command == "shorts":
        return run_shorts(args, workspace)
    if args.command == "build":
        return run_build(args, workspace)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
