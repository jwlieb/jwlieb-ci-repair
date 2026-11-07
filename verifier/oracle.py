"""Required CI outcomes; no workflow parsing or execution implementation is shared."""


def expected(raw):
    return {
        "workflow_policy": True,
        "jobs": {"test": 0, "lint": 0, "build": 0},
        "tests_collected": [2],
        "test_mutation_rejected": True,
        "lint_mutation_rejected": True,
        "wheel_created": True,
    }
