import gcode_lib


def test_arc_center_respects_g90_1_and_g91_1():
    st = gcode_lib.ModalState(x=10.0, y=20.0, ij_relative=True)
    words = {"I": 1.0, "J": 2.0}

    assert gcode_lib._arc_center(st, words) == (11.0, 22.0)

    gcode_lib.advance_state(st, gcode_lib.parse_line("G90.1"))
    assert st.ij_relative is False
    assert gcode_lib._arc_center(st, words) == (1.0, 2.0)

    gcode_lib.advance_state(st, gcode_lib.parse_line("G91.1"))
    assert st.ij_relative is True
    assert gcode_lib._arc_center(st, words) == (11.0, 22.0)
