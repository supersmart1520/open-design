"""1단 엔트리 — 무음 컷 + 캡컷 드래프트 생성. UI 없음.

    python3 -m capcut_agent.cli --check              환경 + 저장소 점검
    python3 -m capcut_agent.cli --inbox              저장소 inbox 를 전부 처리
    python3 -m capcut_agent.cli 영상.mp4              파일 하나 처리
    python3 -m capcut_agent.cli 영상.mp4 --dry-run    컷 계획만 출력
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import storage
from .draft import DraftBuilder
from .env_check import DRAFT_CHECK_NAME, format_report, run_checks
from .media import MediaInfo, detect_silence_raw, probe
from .paths import resolve_draft_root
from .silence import Range, SilenceOptions, keeps_from_silence


def _fmt(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="capcut-agent",
        description="영상에서 무음을 걷어내고 캡컷 드래프트를 만든다.",
    )
    p.add_argument("video", nargs="?", help="입력 mp4 / mov. 생략하고 --inbox 를 쓸 수 있다.")
    p.add_argument("--check", action="store_true", help="환경 + 저장소 점검만 하고 종료")
    p.add_argument("--inbox", action="store_true", help="저장소 inbox 안의 영상을 전부 처리")
    p.add_argument("--workspace", help=f"작업 저장소 위치 (기본: {storage.default_home()})")
    p.add_argument("--name", help="드래프트 이름 (기본: 파일명 + 시각)")
    p.add_argument("--draft-dir", help="캡컷 드래프트 루트 직접 지정")
    p.add_argument("--keep-source", action="store_true",
                   help="처리한 원본을 inbox 에 그대로 둔다 (기본은 done/ 으로 이동)")
    p.add_argument("--dry-run", action="store_true", help="컷 계획만 출력하고 쓰지 않는다")

    # 아래 값들은 생략하면 저장소의 config.json 을 따른다.
    tune = p.add_argument_group("컷 옵션 (생략하면 config.json 값)")
    tune.add_argument("--noise-db", type=float,
                      help="무음 임계 dBFS. 조용한 녹음이면 -45 쪽으로.")
    tune.add_argument("--min-silence", type=float, help="이보다 긴 무음만 자른다, 초")
    tune.add_argument("--padding", type=float, help="컷 앞뒤 여유, 초")
    tune.add_argument("--min-keep", type=float, help="이보다 짧은 조각은 버린다, 초")
    tune.add_argument("--min-gap", type=float, help="이보다 가까운 조각끼리 붙인다, 초")
    return p


def resolve_options(workspace: storage.Workspace, args: argparse.Namespace) -> SilenceOptions:
    """config.json 을 바탕에 깔고, 명시한 플래그만 덮어쓴다."""
    options = storage.load_options(workspace)
    for field_name in ("noise_db", "min_silence", "padding", "min_keep", "min_gap"):
        value = getattr(args, field_name)
        if value is not None:
            setattr(options, field_name, value)
    return options


def plan_cuts(media: MediaInfo, options: SilenceOptions) -> list[Range]:
    stderr = detect_silence_raw(media.path, options.noise_db, options.min_silence)
    return keeps_from_silence(stderr, media.duration, options)


def _report_payload(
    media: MediaInfo,
    options: SilenceOptions,
    keeps: list[Range],
    draft: Path | None,
    source_before: Path | None = None,
) -> dict:
    kept = sum(k.length for k in keeps)
    moved_from = (
        str(source_before)
        if source_before is not None and source_before != media.path
        else None
    )
    return {
        "생성시각": datetime.now().isoformat(timespec="seconds"),
        "원본": {
            "경로": str(media.path),
            "투입경로": moved_from,
            "해상도": f"{media.width}x{media.height}",
            "fps": media.fps,
            "길이초": round(media.duration, 3),
        },
        "옵션": options.to_dict(),
        "결과": {
            "보존조각": len(keeps),
            "컷수": max(len(keeps) - 1, 0),
            "출력길이초": round(kept, 3),
            "제거길이초": round(media.duration - kept, 3),
            "제거비율": round((1 - kept / media.duration) * 100, 1) if media.duration else 0,
        },
        "드래프트": str(draft) if draft else None,
        "보존구간": [{"시작": round(k.start, 3), "끝": round(k.end, 3)} for k in keeps],
    }


def process_one(
    video: Path,
    workspace: storage.Workspace,
    options: SilenceOptions,
    args: argparse.Namespace,
    draft_root: Path | None,
) -> int:
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

    # 원본을 드래프트보다 **먼저** 옮긴다.
    # 드래프트는 미디어의 절대경로를 박아 넣으므로, 쓴 다음에 원본을 옮기면
    # 캡컷이 프로젝트를 열 때 "미디어를 찾을 수 없다"며 재연결을 요구한다.
    source_before = media.path
    if not args.keep_source:
        moved = storage.archive(workspace, video, ok=True)
        if moved != source_before:
            media = replace(media, path=moved)
            print(f"원본 이동 : {moved}")

    builder = DraftBuilder(media=media, name=name)
    builder.add_video_cuts(keeps)
    assert draft_root is not None  # --dry-run 이 아니면 호출부에서 이미 해결했다.
    folder = builder.write(draft_root)

    report = storage.write_report(
        workspace, name, _report_payload(media, options, keeps, folder, source_before)
    )
    print(f"드래프트  : {folder}")
    print(f"기록      : {report}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = storage.resolve_workspace(args.workspace)
    report = run_checks(workspace)

    if args.check:
        print(format_report(report))
        return 0 if report.ok else 1

    if not args.video and not args.inbox:
        build_parser().print_help()
        print()
        print(storage.describe(workspace))
        return 2

    # --dry-run 은 드래프트를 쓰지 않으므로 캡컷이 없어도 돌아간다.
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
    else:
        targets = storage.list_inbox(workspace)
        if not targets:
            print(f"inbox 가 비어 있습니다: {workspace.inbox}")
            print("여기에 mp4 / mov 를 넣고 다시 실행하세요.")
            return 0
        print(f"inbox    : {len(targets)}개 처리")

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
        print()
        print("검증 — 여기서부터는 사람이 합니다:")
        print("  1. 캡컷이 실행 중이면 완전히 종료 후 다시 켜세요 (목록 갱신이 안 됩니다)")
        print("  2. 홈 화면에서 방금 만든 프로젝트를 여세요")
        print("  3. 처음부터 끝까지 재생해 컷이 말을 잘라먹지 않는지 확인하세요")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
