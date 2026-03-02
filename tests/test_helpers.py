import gcode_lib


def test_choose_translation_selects_interval_edge_by_magnitude(load_module):
    m = load_module
    # Zero outside interval on positive side -> choose lo.
    assert m._choose_translation(2.0, 5.0, "clamp") == 2.0
    # Zero outside interval on negative side -> choose hi.
    assert m._choose_translation(-5.0, -2.0, "clamp") == -2.0


def test_split_comment_and_replace_or_append_edges():
    code, comment = gcode_lib.split_comment("G1 X1 ; hi")
    assert code == "G1 X1"
    assert comment == "; hi"

    code2, comment2 = gcode_lib.split_comment("G1 X1")
    assert code2 == "G1 X1"
    assert comment2 == ""

    # Case-insensitive replace of first axis occurrence.
    replaced = gcode_lib.replace_or_append("G1 x1 Y2", "X", 3.0, 3, 5)
    assert "X3" in replaced
    assert "Y2" in replaced

    # Append missing axis.
    appended = gcode_lib.replace_or_append("G1 Y2", "X", 4.0, 3, 5)
    assert appended.endswith(" X4")
