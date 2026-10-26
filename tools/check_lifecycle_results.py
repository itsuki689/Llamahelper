"""Reject missing, skipped or unsuccessful lifecycle coverage before packaging."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


REQUIRED_TESTS = {
    "test_server_integration": {
        "test_server_start_logs_stop", "test_server_unexpected_exit_no_crash",
        "test_start_with_occupied_port_fails", "test_fake_server_can_exit_cleanly",
        "test_benchmark_one_round",
    },
    "test_e2e": {
        "test_full_start_stop_via_mainwindow",
        "test_restart_reaps_old_process_and_freezes_new_parameters",
        "test_stop_during_start_leaves_no_process_or_running_state",
        "test_health_timeout_reaps_process_and_can_retry",
        "test_early_exit_restores_controls_and_logs_reason",
        "test_unexecutable_server_restores_controls_and_can_retry",
        "test_close_with_health_request_inflight_reaps_everything",
    },
    "test_shutdown": {
        "test_stale_health_result_cannot_change_new_launch",
        "test_old_process_signals_cannot_retire_or_kill_new_process",
        "test_explicit_stop_cancels_pending_restart",
        "test_close_server_reaps_process_and_threads",
    },
    "test_api_health": {
        "test_stalled_health_is_bounded_and_cancellable",
        "test_cancel_before_start_produces_no_result",
    },
    "test_asr_benchmark": {
        "test_benchmark_asr_one_round", "test_benchmark_asr_empty_dir",
    },
}


def check_report(path: Path) -> list[str]:
    cases = list(ET.parse(path).iter("testcase"))
    errors = []
    for module, required in REQUIRED_TESTS.items():
        selected = [case for case in cases
                    if case.get("classname", "").split(".")[-1] == module]
        found = {case.get("name", "").split("[")[0] for case in selected}
        for missing in sorted(required - found):
            errors.append(f"Missing required test: {module}::{missing}")
        for case in selected:
            for outcome in ("skipped", "failure", "error"):
                if case.find(outcome) is not None:
                    errors.append(f"{outcome}: {module}::{case.get('name')}")
        print(f"{module}: {len(selected)} executed cases, "
              f"{sum(case.find('skipped') is not None for case in selected)} skipped")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/check_lifecycle_results.py <junit.xml>", file=sys.stderr)
        return 1
    try:
        errors = check_report(Path(sys.argv[1]))
    except (OSError, ET.ParseError) as exc:
        print(f"Cannot read test results: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Required process and main-window tests passed without skips.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
