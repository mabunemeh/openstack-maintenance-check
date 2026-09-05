import json
import subprocess
import sys
from importlib.resources import files

import pytest

from openstack_maintenance_check.checks import evaluate
from openstack_maintenance_check.cli import main
from openstack_maintenance_check.reporting import render_json, render_text
from openstack_maintenance_check.snapshot import parse_snapshot


@pytest.mark.parametrize(
    ("scenario", "code", "outcome", "summary"),
    [
        ("clear", 0, "no_known_blockers", {"blocker": 0, "warning": 0, "unknown": 0}),
        ("blocked", 1, "blocked", {"blocker": 2, "warning": 1, "unknown": 0}),
        ("incomplete", 1, "incomplete", {"blocker": 0, "warning": 0, "unknown": 3}),
    ],
)
def test_demo_contract(scenario, code, outcome, summary, capsys):
    assert main(["demo", "--scenario", scenario, "--format", "json"]) == code
    output = capsys.readouterr()
    assert output.err == ""
    data = json.loads(output.out)
    assert data["report_schema_version"] == 1
    assert data["analysis_mode"] == "snapshot"
    assert data["outcome"] == outcome
    assert data["summary"] == summary
    assert len(data["checks_run"]) == 4
    assert len(data["limitations"]) == 5
    for finding in data["findings"]:
        assert isinstance(finding["evidence"], dict)
        assert finding["resource_id"]
        assert finding["next_check"]
        assert finding["reason"]


def test_demo_defaults_to_blocked_text(capsys):
    assert main(["demo"]) == 1
    text = capsys.readouterr().out
    assert "Outcome: blocked" in text
    assert "Evidence:" in text
    assert "Next check:" in text
    assert "Limitations:" in text


def test_file_check(inventory, tmp_path, capsys):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")
    assert main(["check", "--snapshot", str(path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["captured_at"] == "2026-09-05T08:00:00+00:00"
    assert data["inventory"]["servers"][0]["task_state_known"] is True


@pytest.mark.parametrize("payload", ["{", '{"schema_version": 2}', "DO-NOT-ECHO"])
def test_input_error_is_stderr_only_exit_three(tmp_path, capsys, payload):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["check", "--snapshot", str(path), "--format", "json"])
    assert exc.value.code == 3
    output = capsys.readouterr()
    assert output.out == ""
    assert "maintenance-check:" in output.err
    assert "DO-NOT-ECHO" not in output.err
    assert "Traceback" not in output.err


def test_missing_input_exit_three(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", "--snapshot", str(tmp_path / "missing")])
    assert exc.value.code == 3
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["check"],
        ["demo", "--format", "html"],
        ["demo", "--scenario", "missing"],
        ["check", "--cloud", "demo"],
    ],
)
def test_invalid_cli_exit_two(args):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "args", [["--help"], ["check", "--help"], ["demo", "--help"], ["--version"]]
)
def test_help_and_version(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 0
    assert "maintenance-check" in capsys.readouterr().out


def test_output_escapes_untrusted_terminal_content(inventory):
    inventory["host"]["name"] = "compute-\x1b[2J\nFAKE-HEADING\u202e"
    server = inventory["servers"][0]
    server["host"] = inventory["host"]["name"]
    server["id"] = "vm-\x1b[31m\nFAKE"
    server["task_state"] = "\x1b]0;window-title\x07"
    report = evaluate(parse_snapshot(json.dumps(inventory)))
    text = render_text(report)
    assert "\x1b" not in text and "\u202e" not in text and "\x07" not in text
    assert "\nFAKE" not in text
    assert "\\u001b" in text
    assert json.loads(render_json(report))["host"]["name"] == inventory["host"]["name"]


def test_json_order_is_deterministic_across_input_permutations(inventory):
    first = inventory["servers"][0]
    first["status"] = "ERROR"
    inventory["servers"].append({**first, "id": "aaa-demo", "task_state": "migrating"})
    before = render_json(evaluate(parse_snapshot(json.dumps(inventory))))
    inventory["servers"].reverse()
    assert render_json(evaluate(parse_snapshot(json.dumps(inventory)))) == before


def test_no_findings_text_retains_scope(inventory):
    text = render_text(evaluate(parse_snapshot(json.dumps(inventory))))
    assert "No known blockers in the implemented source checks." in text
    assert "migration success is unknown" in text
    assert "freshness not checked" in text


def test_demo_and_file_use_same_pipeline(tmp_path, capsys):
    source = files("openstack_maintenance_check").joinpath("data", "blocked.json")
    path = tmp_path / "demo.json"
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["demo", "--format", "json"]) == 1
    demo = capsys.readouterr().out
    assert main(["check", "--snapshot", str(path), "--format", "json"]) == 1
    assert capsys.readouterr().out == demo


def test_module_entrypoint_outside_repository(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openstack_maintenance_check",
            "demo",
            "--scenario",
            "clear",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["outcome"] == "no_known_blockers"
