import gcode_lib


def test_modal_parser_matches_exact_opcode_only():
    st = gcode_lib.ModalState(abs_xy=True, abs_e=False, ij_relative=True)

    # Similar prefixes should not be interpreted as modal toggles.
    gcode_lib.advance_state(st, gcode_lib.parse_line("M820"))
    assert st.abs_e is False

    gcode_lib.advance_state(st, gcode_lib.parse_line("G901"))
    assert st.abs_xy is True

    gcode_lib.advance_state(st, gcode_lib.parse_line("G911"))
    assert st.abs_xy is True

    # Exact modal opcodes still work.
    gcode_lib.advance_state(st, gcode_lib.parse_line("M82"))
    assert st.abs_e is True
    gcode_lib.advance_state(st, gcode_lib.parse_line("G91"))
    assert st.abs_xy is False


def test_modal_parser_handles_whitespace_and_comment():
    st = gcode_lib.ModalState(abs_xy=False, abs_e=False, ij_relative=True)
    gcode_lib.advance_state(st, gcode_lib.parse_line("G90   ; COMMENT"))
    assert st.abs_xy is True
    gcode_lib.advance_state(st, gcode_lib.parse_line("M82 ; COMMENT"))
    assert st.abs_e is True
