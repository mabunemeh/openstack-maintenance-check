import json
import runpy
from pathlib import Path

import pytest

compare = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "compare_inventory.py"))[
    "main"
]


@pytest.mark.parametrize(
    ("ids", "complete", "expected"),
    [
        (["server-demo-01"], True, 0),
        ([], True, 1),
        (["another-demo"], True, 1),
        (["server-demo-01"], False, 1),
    ],
)
def test_inventory_comparison(inventory, tmp_path, ids, complete, expected):
    inventory["servers_complete"] = complete
    tool = tmp_path / "tool.json"
    reference = tmp_path / "cli.json"
    tool.write_text(json.dumps(inventory))
    reference.write_text(json.dumps([{"ID": server_id} for server_id in ids]))
    assert compare([str(tool), str(reference)]) == expected


@pytest.mark.parametrize(
    "data",
    [
        {},
        [None],
        [{"id": "wrong-case"}],
        [{"ID": ""}],
        [{"ID": "same"}, {"ID": "same"}],
        [{"ID": 42}],
    ],
)
def test_invalid_reference_never_counts_as_match(inventory, tmp_path, data, capsys):
    tool = tmp_path / "tool.json"
    reference = tmp_path / "cli.json"
    tool.write_text(json.dumps(inventory))
    reference.write_text(json.dumps(data))
    with pytest.raises(SystemExit) as exc:
        compare([str(tool), str(reference)])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""
