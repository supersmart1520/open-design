"""inbox 자동 감지 — 파일을 넣기만 하면 작업으로 등록된다.

`watchdog` 이 있으면 OS 이벤트로, 없으면 주기적으로 훑는다. 둘 다 같은
`scan` 을 부르므로 동작은 같다 — 반응 속도만 다르다.

**복사가 끝날 때까지 기다린다.** 큰 영상이 복사되는 도중에 등록하면 반쪽짜리
파일을 편집하려다 실패한다. 크기가 두 번 연속 같을 때만 등록한다.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .. import storage

#: 폴링 간격 (초). watchdog 이 없을 때만 쓴다.
POLL_SECONDS = 3.0

#: 파일 크기가 이만큼 연속으로 같으면 복사가 끝났다고 본다.
STABLE_CHECKS = 2


class InboxWatcher:
    """inbox 를 지켜보다 안정된 파일을 등록한다."""

    def __init__(self, workspace, on_new, poll_seconds: float = POLL_SECONDS):
        self.workspace = workspace
        self.on_new = on_new
        self.poll_seconds = poll_seconds
        self._sizes: dict[str, tuple[int, int]] = {}
        self._seen: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ 수명주기

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="capcut-watcher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # --------------------------------------------------------------- 루프

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweep()
            except OSError:
                # 폴더가 잠깐 사라져도 감시는 계속한다.
                pass
            self._stop.wait(self.poll_seconds)

    def sweep(self) -> list[Path]:
        """한 번 훑는다. 이번에 새로 안정된 파일들을 돌려준다."""
        settled: list[Path] = []
        current = {str(path): path for path in storage.list_inbox(self.workspace)}

        # 사라진 파일은 기억에서 지운다. 다시 넣으면 새 파일로 본다.
        for key in list(self._sizes):
            if key not in current:
                self._sizes.pop(key, None)
                self._seen.discard(key)

        for key, path in current.items():
            if key in self._seen:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            previous_size, stable = self._sizes.get(key, (-1, 0))
            if size == previous_size and size > 0:
                stable += 1
            else:
                stable = 0
            self._sizes[key] = (size, stable)
            if stable >= STABLE_CHECKS:
                self._seen.add(key)
                settled.append(path)

        if settled:
            self.on_new(settled)
        return settled
