def test_modal_parser_matches_exact_opcode_only(load_module):
    m = load_module
    st = m.State(abs_xy=True, abs_e=False, ij_relative=True)

    # Similar prefixes should not be interpreted as modal toggles.
    assert m._handle_modal_state_line(st, "M820") is False
    assert st.abs_e is False

    assert m._handle_modal_state_line(st, "G901") is False
    assert st.abs_xy is True

    assert m._handle_modal_state_line(st, "G911") is False
    assert st.abs_xy is True

    # Exact modal opcodes still work.
    assert m._handle_modal_state_line(st, "M82") is True
    assert st.abs_e is True
    assert m._handle_modal_state_line(st, "G91") is True
    assert st.abs_xy is False
