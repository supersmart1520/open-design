"""세로 프레이밍 — 가로 소스를 9:16 숏폼 화면에 맞추는 크롭 계산.

캡컷의 crop 필드는 **소스 기준 정규화 좌표**(0~1)로 네 꼭짓점을 받는다.
scale 로 억지로 키우는 대신 crop 으로 창을 잘라내면, 캡컷 안에서 사람이
다시 손볼 때도 좌표가 직관적으로 남는다.

좌표계
    x: 0 = 소스 왼쪽,  1 = 소스 오른쪽
    y: 0 = 소스 위쪽,  1 = 소스 아래쪽
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: 숏폼 기본 캔버스. 인스타 릴스 / 유튜브 쇼츠 공통.
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920

Anchor = Literal["center", "left", "right", "top", "bottom"]


@dataclass(frozen=True)
class Canvas:
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height


#: 세로 숏폼 캔버스.
SHORTS = Canvas(SHORTS_WIDTH, SHORTS_HEIGHT)


@dataclass(frozen=True)
class CropWindow:
    """소스에서 잘라낼 창. 정규화 좌표."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def is_full_frame(self) -> bool:
        return (
            abs(self.left) < 1e-6
            and abs(self.top) < 1e-6
            and abs(self.right - 1.0) < 1e-6
            and abs(self.bottom - 1.0) < 1e-6
        )

    def to_capcut(self) -> dict:
        """캡컷이 쓰는 네 꼭짓점 형태로."""
        return {
            "upper_left_x": self.left, "upper_left_y": self.top,
            "upper_right_x": self.right, "upper_right_y": self.top,
            "lower_left_x": self.left, "lower_left_y": self.bottom,
            "lower_right_x": self.right, "lower_right_y": self.bottom,
        }


def crop_window(
    source_width: int,
    source_height: int,
    canvas: Canvas = SHORTS,
    anchor: Anchor = "center",
) -> CropWindow:
    """소스를 캔버스 비율로 꽉 채우는 크롭 창.

    소스가 캔버스보다 가로로 넓으면 좌우를 잘라내고, 세로로 길면 위아래를
    잘라낸다. 비율이 이미 같으면 전체 프레임을 그대로 돌려준다.

    anchor 는 **잘라낼 때 무엇을 남길지**를 정한다. 인물이 화면 아래쪽에
    잡힌 가로 화면녹화라면 anchor="top" 으로 위쪽을 남긴다.
    """
    if source_width <= 0 or source_height <= 0:
        raise ValueError(f"소스 크기가 잘못됐습니다: {source_width}x{source_height}")

    source_aspect = source_width / source_height
    target_aspect = canvas.aspect

    if abs(source_aspect - target_aspect) < 1e-6:
        return CropWindow(0.0, 0.0, 1.0, 1.0)

    if source_aspect > target_aspect:
        # 소스가 더 넓다 → 좌우를 잘라낸다.
        keep = target_aspect / source_aspect  # 남길 가로 비율
        slack = 1.0 - keep
        if anchor == "left":
            left = 0.0
        elif anchor == "right":
            left = slack
        else:  # center, 그리고 세로 앵커를 가로 크롭에 준 경우
            left = slack / 2
        return CropWindow(left, 0.0, left + keep, 1.0)

    # 소스가 더 길다 → 위아래를 잘라낸다.
    keep = source_aspect / target_aspect  # 남길 세로 비율
    slack = 1.0 - keep
    if anchor == "top":
        top = 0.0
    elif anchor == "bottom":
        top = slack
    else:
        top = slack / 2
    return CropWindow(0.0, top, 1.0, top + keep)


def crop_ratio_label(canvas: Canvas) -> str:
    """캡컷의 crop_ratio 문자열. 9:16 / 16:9 / 1:1 은 이름을 알아본다."""
    known = {
        (9, 16): "9:16", (16, 9): "16:9", (1, 1): "1:1",
        (4, 5): "4:5", (3, 4): "3:4", (4, 3): "4:3",
    }
    from math import gcd

    divisor = gcd(canvas.width, canvas.height)
    key = (canvas.width // divisor, canvas.height // divisor)
    return known.get(key, "free")
