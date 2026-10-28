"""SSE 브로커 — 진행 상황을 열려 있는 모든 대시보드로 흘린다.

구독자마다 큐를 하나씩 준다. 느린 구독자가 워커를 붙잡지 않도록 큐가 차면
가장 오래된 것을 버린다. 진행 표시는 놓쳐도 되는 정보다.
"""

from __future__ import annotations

import json
import queue
from threading import Lock

#: 구독자당 큐 길이. 넘치면 오래된 것부터 버린다.
QUEUE_SIZE = 64


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = Lock()

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            if channel in self._subscribers:
                self._subscribers.remove(channel)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, kind: str, payload: dict) -> None:
        message = json.dumps({"kind": kind, **payload}, ensure_ascii=False)
        with self._lock:
            channels = list(self._subscribers)
        for channel in channels:
            try:
                channel.put_nowait(message)
            except queue.Full:
                # 느린 구독자 때문에 워커가 멈추면 안 된다. 하나 버리고 넣는다.
                try:
                    channel.get_nowait()
                    channel.put_nowait(message)
                except (queue.Empty, queue.Full):
                    pass


def sse(message: str) -> str:
    """SSE 한 덩어리. 줄바꿈이 들어가도 깨지지 않게 줄마다 접두사를 붙인다."""
    body = "\n".join(f"data: {line}" for line in message.split("\n"))
    return f"{body}\n\n"
