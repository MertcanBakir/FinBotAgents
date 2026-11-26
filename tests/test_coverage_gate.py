import json
import subprocess
import sys
from pathlib import Path

import pytest

from deploy.personal.check_coverage import main

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/personal/check_coverage.py"


@pytest.mark.parametrize(
    ("lines", "branches", "expected"),
    [(85, 85, 0), (84, 90, 1), (90, 84, 1), (84, 84, 1)],
)
def test_separate_line_and_branch_thresholds(tmp_path, capsys, lines, branches, expected):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {
        "num_statements": 100, "covered_lines": lines,
        "num_branches": 100, "covered_branches": branches,
    }}))
    assert main(str(report)) == expected
    output = capsys.readouterr().out.lower()
    assert "line" in output and "branch" in output


@pytest.mark.parametrize("totals", [{}, {"num_statements": 0, "covered_lines": 0,
                                        "num_branches": 0, "covered_branches": 0}])
def test_missing_or_empty_coverage_fails(tmp_path, totals):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": totals}))
    assert main(str(report)) != 0


def test_rounding_does_not_hide_a_below_threshold_result(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {
        "num_statements": 10000, "covered_lines": 8499,
        "num_branches": 100, "covered_branches": 85,
    }}))
    assert main(str(report)) == 1


def test_cli_exit_status_blocks_below_threshold(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {
        "num_statements": 100, "covered_lines": 85,
        "num_branches": 100, "covered_branches": 84,
    }}))
    result = subprocess.run([sys.executable, str(SCRIPT), str(report)], capture_output=True, text=True)
    assert result.returncode == 1
