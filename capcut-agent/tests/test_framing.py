"""세로 프레이밍 크롭 계산."""

from __future__ import annotations

import pytest

from capcut_agent.framing import SHORTS, Canvas, crop_ratio_label, crop_window


def test_landscape_source_crops_the_sides():
    window = crop_window(1920, 1080, SHORTS)
    assert window.top == 0.0 and window.bottom == 1.0
    # 1080 높이에 9:16 이면 가로 607.5px 만 남는다.
    assert (window.right - window.left) * 1920 == pytest.approx(607.5)


def test_landscape_crop_is_centred_by_default():
    window = crop_window(1920, 1080, SHORTS)
    assert window.left == pytest.approx(1 - window.right)


def test_anchor_left_keeps_the_left_edge():
    assert crop_window(1920, 1080, SHORTS, anchor="left").left == 0.0


def test_anchor_right_keeps_the_right_edge():
    assert crop_window(1920, 1080, SHORTS, anchor="right").right == pytest.approx(1.0)


def test_matching_aspect_is_a_full_frame():
    assert crop_window(1080, 1920, SHORTS).is_full_frame
    assert crop_window(540, 960, SHORTS).is_full_frame


def test_taller_than_target_crops_top_and_bottom():
    # 1:2 소스는 9:16 보다 더 길다.
    window = crop_window(1000, 2000, SHORTS)
    assert window.left == 0.0 and window.right == 1.0
    assert window.top > 0.0 and window.bottom < 1.0


def test_anchor_top_keeps_the_top_edge():
    # 인물이 아래쪽에 있을 때 쓰는 앵커.
    assert crop_window(1000, 2000, SHORTS, anchor="top").top == 0.0


def test_anchor_bottom_keeps_the_bottom_edge():
    assert crop_window(1000, 2000, SHORTS, anchor="bottom").bottom == pytest.approx(1.0)


def test_capcut_shape_has_four_corners_that_agree():
    corners = crop_window(1920, 1080, SHORTS).to_capcut()
    assert corners["upper_left_x"] == corners["lower_left_x"]
    assert corners["upper_right_x"] == corners["lower_right_x"]
    assert corners["upper_left_y"] == corners["upper_right_y"]
    assert corners["lower_left_y"] == corners["lower_right_y"]


def test_zero_sized_source_is_rejected():
    with pytest.raises(ValueError):
        crop_window(0, 1080, SHORTS)


def test_ratio_labels_name_the_common_shapes():
    assert crop_ratio_label(SHORTS) == "9:16"
    assert crop_ratio_label(Canvas(1920, 1080)) == "16:9"
    assert crop_ratio_label(Canvas(1080, 1080)) == "1:1"
    assert crop_ratio_label(Canvas(1000, 999)) == "free"
