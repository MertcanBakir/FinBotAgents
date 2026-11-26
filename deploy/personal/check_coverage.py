"""Enforce independent minimum line and branch coverage on a coverage.py JSON report."""

import json
import sys
from pathlib import Path

MINIMUM_PERCENT = 85


def main(path: str) -> int:
    try:
        totals = json.loads(Path(path).read_text(encoding="utf-8"))["totals"]
        results = []
        for label, covered_key, total_key in (
            ("Line", "covered_lines", "num_statements"),
            ("Branch", "covered_branches", "num_branches"),
        ):
            covered, total = totals[covered_key], totals[total_key]
            if type(covered) is not int or type(total) is not int or total <= 0 or not 0 <= covered <= total:
                raise ValueError(f"invalid {label.lower()} coverage counts")
            passed = covered * 100 >= MINIMUM_PERCENT * total
            results.append(passed)
            print(f"{label} coverage: {covered / total * 100:.2f}% ({covered}/{total}); "
                  f"minimum {MINIMUM_PERCENT}%: {'PASS' if passed else 'FAIL'}")
        return 0 if all(results) else 1
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Invalid or missing coverage report: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) == 2 else 2)
