from factory_runtime.failure_classification import classify_terminal_failure


def test_missing_terminal_report_is_validator_harness_without_log_leakage() -> None:
    capsule = classify_terminal_failure(
        final=None,
        caller_returncode=1,
        caller_stdout="private test assertion: target switched",
        caller_stderr="Traceback: hidden fixture detail",
        validator_result_present=False,
        coder_receipt_present=False,
        tester_receipt_present=False,
    )

    assert capsule.owner == "validator-harness"
    assert capsule.code == "caller-missing-terminal-report"
    assert "assertion" not in capsule.summary
    assert "fixture" not in capsule.summary


def test_missing_acceptance_after_complete_author_outputs_is_validator_harness() -> None:
    capsule = classify_terminal_failure(
        final={"status": "runtime-terminal", "passed": False},
        caller_returncode=0,
        caller_stdout="",
        caller_stderr="",
        validator_result_present=False,
        coder_receipt_present=True,
        tester_receipt_present=True,
    )

    assert capsule.owner == "validator-harness"
    assert capsule.code == "validator-acceptance-not-recorded"


def test_completed_acceptance_failure_routes_to_coder() -> None:
    capsule = classify_terminal_failure(
        final={"status": "runtime-terminal", "passed": False},
        caller_returncode=0,
        caller_stdout="",
        caller_stderr="",
        validator_result_present=True,
        coder_receipt_present=True,
        tester_receipt_present=True,
    )

    assert capsule.owner == "coder"
    assert capsule.code == "candidate-failed-acceptance"


def test_required_lifecycle_before_behavior_routes_to_validator_harness() -> None:
    capsule = classify_terminal_failure(
        final={"status": "runtime-terminal", "passed": False},
        caller_returncode=0,
        caller_stdout="private assertion output",
        caller_stderr="private fixture output",
        validator_result_present=True,
        coder_receipt_present=True,
        tester_receipt_present=True,
        acceptance_lifecycle_required=True,
        acceptance_phase="tester-connected",
    )

    assert capsule.owner == "validator-harness"
    assert capsule.code == "acceptance-setup-not-reached"


def test_required_lifecycle_after_behavior_routes_to_coder() -> None:
    capsule = classify_terminal_failure(
        final={"status": "runtime-terminal", "passed": False},
        caller_returncode=0,
        caller_stdout="private assertion output",
        caller_stderr="private fixture output",
        validator_result_present=True,
        coder_receipt_present=True,
        tester_receipt_present=True,
        acceptance_lifecycle_required=True,
        acceptance_phase="behavior-started",
    )

    assert capsule.owner == "coder"
    assert capsule.code == "candidate-failed-acceptance"


def test_invocation_timeout_precedes_missing_terminal_report() -> None:
    capsule = classify_terminal_failure(
        final=None,
        caller_returncode=124,
        caller_stdout="private output",
        caller_stderr="private output",
        validator_result_present=False,
        coder_receipt_present=False,
        tester_receipt_present=False,
        invocation_termination_reason="idle-limit",
    )

    assert capsule.owner == "validator-harness"
    assert capsule.code == "runner-invocation-timeout"


def test_model_output_cannot_spoof_host_prerequisite_ownership() -> None:
    capsule = classify_terminal_failure(
        final={"status": "runtime-exception"},
        caller_returncode=1,
        caller_stdout="direnv coding_agent launch environment unavailable",
        caller_stderr="model-controlled text",
        validator_result_present=False,
        coder_receipt_present=False,
        tester_receipt_present=False,
        invocation_termination_reason="exit-nonzero",
    )

    assert capsule.owner == "validator-harness"
    assert capsule.code == "validator-caller-exception"
