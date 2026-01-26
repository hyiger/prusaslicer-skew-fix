import math
import pytest

def test_apply_skew_abs_with_y_ref(load_module):
    m = load_module
    k = math.tan(math.radians(-0.15))
    # If y == y_ref, x should not change
    x2, y2 = m.apply_skew_abs(10.0, 100.0, k, y_ref=100.0)
    assert y2 == pytest.approx(100.0)
    assert x2 == pytest.approx(10.0)

    # Symmetry around y_ref: y_ref +/- d should shift x by +/- d*k
    x_plus, _ = m.apply_skew_abs(10.0, 110.0, k, y_ref=100.0)
    x_minus, _ = m.apply_skew_abs(10.0,  90.0, k, y_ref=100.0)
    assert (x_plus - 10.0) == pytest.approx( (110.0-100.0)*k )
    assert (x_minus - 10.0) == pytest.approx( ( 90.0-100.0)*k )

def test_fmt_axis_rounding(load_module):
    m = load_module
    # X/Y default to xy_places, others to other_places
    assert m.fmt_axis("X", 1.23456, 3, 5) == "1.235"
    assert m.fmt_axis("Y", 1.2, 3, 5) == "1.2"
    assert m.fmt_axis("E", 1.23456, 3, 5) == "1.23456"
