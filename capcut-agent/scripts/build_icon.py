"""`appicon.png` → `appicon.icns`.

macOS 의 `iconutil` 은 맥에서만 돈다. `.icns` 는 사실 단순한 컨테이너라
직접 쓴다 — 매직 + 길이, 그 뒤로 (4바이트 타입, 길이, PNG) 가 이어진다.
그래야 맥이 아닌 곳에서도 아이콘을 만들어 저장소에 넣어 둘 수 있고,
사용자 쪽에는 이미지 도구가 하나도 필요 없다.

    python3 scripts/build_icon.py

ffmpeg 으로 크기를 줄인다. 이미 쓰고 있는 도구라 새 의존이 늘지 않는다.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "capcut_agent" / "assets" / "appicon.png"
TARGET = ROOT / "capcut_agent" / "assets" / "appicon.icns"

#: (타입, 한 변의 픽셀). PNG 를 받는 타입만 쓴다.
#: ic11/ic12/ic13/ic14 는 레티나 변종이라 같은 크기가 겹쳐 나온다.
ENTRIES = (
    (b"ic11", 32),    # 16@2x
    (b"ic12", 64),    # 32@2x
    (b"ic07", 128),
    (b"ic13", 256),   # 128@2x
    (b"ic08", 256),
    (b"ic14", 512),   # 256@2x
    (b"ic09", 512),
    (b"ic10", 1024),  # 512@2x
)


def scaled(source: Path, size: int, workdir: Path) -> bytes:
    out = workdir / f"{size}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source),
         "-vf", f"scale={size}:{size}:flags=lanczos", str(out)],
        check=True,
    )
    return out.read_bytes()


def build(source: Path = SOURCE, target: Path = TARGET) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        cache: dict[int, bytes] = {}
        chunks = []
        for kind, size in ENTRIES:
            if size not in cache:
                cache[size] = scaled(source, size, workdir)
            payload = cache[size]
            chunks.append(kind + struct.pack(">I", len(payload) + 8) + payload)

    body = b"".join(chunks)
    target.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
    return target


if __name__ == "__main__":
    written = build()
    print(f"{written}  ({written.stat().st_size:,} 바이트)")
    sys.exit(0)
