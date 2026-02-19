def test_arc_center_respects_g90_1_and_g91_1(load_module):
    m = load_module
    st = m.State(x=10.0, y=20.0, ij_relative=True)
    words = {"I": 1.0, "J": 2.0}

    assert m._arc_center(st, words) == (11.0, 22.0)

    assert m._handle_modal_state_line(st, "G90.1")
    assert st.ij_relative is False
    assert m._arc_center(st, words) == (1.0, 2.0)

    assert m._handle_modal_state_line(st, "G91.1")
    assert st.ij_relative is True
    assert m._arc_center(st, words) == (11.0, 22.0)
