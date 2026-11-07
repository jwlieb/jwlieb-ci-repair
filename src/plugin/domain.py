"""Repair workflow configuration while application code and checks stay immutable."""

import json
import os
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
SETUP = "astral-sh/setup-uv@d0d8abe699bfb85fec6de9f7adb5ae17292296ff"
SYNC = "uv sync --frozen --extra dev"
COMMANDS = {
    "uv run --frozen pytest tests -q": "test",
    "uv run --frozen ruff check src": "lint",
    "uv build --offline --no-build-isolation": "build",
}
STARTER = {
    ".github/workflows/ci.yml": f"""name: package checks
on: [push, pull_request]
jobs:
  quality:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: packages/metrics
    steps:
      - uses: {CHECKOUT}
      - uses: {SETUP}
      - run: {SYNC}
      - run: uv run --frozen pytest test -q
      - run: uv run --frozen ruff check src
      - run: uv build --offline --no-build-isolation
"""
}
RESULT_FILES = ["results/ci-log.json"]
SPEC = f"""Repair only .github/workflows/ci.yml. The immutable repository has its Python package at package/ (not packages/metrics), source package/src/metrics, and tests package/tests. Both push and pull_request triggers and real test, lint and wheel-build checks are required. The source itself already works and cannot be edited.
The local runner supports a documented GitHub Actions subset: name, on (list), jobs; each job has runs-on: ubuntu-latest, optional defaults.run.working-directory, and steps with uses or run and optional name/working-directory. Actions are pinned {CHECKOUT} and {SETUP}. Each job must checkout, set up uv, then install using exactly '{SYNC}' from package/. Required commands (may be in one or several jobs): {", ".join(repr(c) for c in COMMANDS)}. Each command must use package/ as working directory (set at step or job level). No shell operators, if guards, continue-on-error, external actions, test filters or unsupported commands.
The executor will copy a fresh immutable repository for each job, execute frozen uv sync, pytest, ruff and the build backend, require both tests to be collected, and inspect the actual wheel. It will rerun the same test/lint steps against intentionally broken source to prove failures propagate. Any valid job names/grouping or cwd placement within this subset are acceptable. This will not emulate all GitHub-hosted runner features. Submit the final assessment exactly."""


def fixture(scenario, seed):
    values = [] if scenario == "empty" else ([0, 0, 1] if scenario == "boundary" else [seed + 1, seed + 2, seed + 3])
    return {"scenario": scenario, "seed": seed, "values": values}


def policy(source):
    document = yaml.safe_load(source)
    if not isinstance(document, dict) or not set(document) <= {"name", "on", True, "jobs"}:
        raise ValueError("unsupported workflow fields")
    triggers = document.get("on", document.get(True))
    if not isinstance(triggers, list) or set(triggers) != {"push", "pull_request"}:
        raise ValueError("both triggers must remain enabled")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs or len(jobs) > 4:
        raise ValueError("one through four jobs are required")
    schedule = []
    for name, job in jobs.items():
        if (
            not isinstance(job, dict)
            or not set(job) <= {"name", "runs-on", "defaults", "steps"}
            or job.get("runs-on") != "ubuntu-latest"
        ):
            raise ValueError("unsupported job or disabled check")
        defaults = job.get("defaults", {})
        if (
            not isinstance(defaults, dict)
            or set(defaults) - {"run"}
            or set(defaults.get("run", {})) - {"working-directory"}
        ):
            raise ValueError("unsupported defaults")
        default_dir = defaults.get("run", {}).get("working-directory", "")
        ready = set()
        commands = []
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise ValueError("missing steps")
        for step in steps:
            if (
                not isinstance(step, dict)
                or set(step) - {"name", "uses", "run", "working-directory"}
                or ("run" in step) == ("uses" in step)
            ):
                raise ValueError("unsupported or disabled step")
            if "uses" in step:
                if step["uses"] not in (CHECKOUT, SETUP):
                    raise ValueError("action must be pinned")
                ready.add(step["uses"])
            else:
                command = step["run"]
                if not isinstance(command, str) or command not in {SYNC, *COMMANDS}:
                    raise ValueError("unsupported command, filter, or swallowed failure")
                if not {CHECKOUT, SETUP} <= ready or step.get("working-directory", default_dir) not in (
                    "package",
                    "./package",
                ):
                    raise ValueError("run commands need checkout, uv, and package/ cwd")
                if command != SYNC and SYNC not in ready:
                    raise ValueError("frozen dependency installation must precede checks")
                ready.add(command)
                commands.append(command)
        schedule.append((str(name), commands))
    kinds = [COMMANDS[c] for _, commands in schedule for c in commands if c in COMMANDS]
    if set(kinds) != {"test", "lint", "build"}:
        raise ValueError("required check was removed")
    return schedule


def measure(files, raw, workspace):
    schedule = policy(files[".github/workflows/ci.yml"])
    root = Path(workspace) / "ci-execution"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    template = Path(__file__).parent / "ci_project"
    log = []
    seen = {}
    collected = []
    wheel_created = True
    negatives = {}
    for index, (name, commands) in enumerate(schedule):
        checkout = root / f"job-{index}" / "package"
        shutil.copytree(template, checkout)
        test_file = checkout / "tests" / "test_metrics.py"
        test_file.write_text(
            (checkout / "tests" / "test_metrics.py.in")
            .read_text()
            .replace("__VALUES__", repr(raw["values"]))
            .replace("__TOTAL__", str(sum(raw["values"])))
        )
        environment = {**os.environ, "PYTHONPATH": "src", "UV_NO_PROGRESS": "1", "UV_OFFLINE": "1"}
        environment.pop("UV_NO_SYNC", None)
        for command in commands:
            argv = shlex.split(command)
            kind = COMMANDS.get(command, "install")
            if kind == "test":
                argv += ["--junitxml=pytest.xml"]
            completed = subprocess.run(argv, cwd=checkout, env=environment, capture_output=True, text=True, timeout=25)
            log.append(
                {
                    "job": name,
                    "command": command,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
            if completed.returncode:
                raise ValueError(f"{name}/{kind} failed: {completed.stderr[-800:]} {completed.stdout[-800:]}")
            if kind != "install":
                seen[kind] = completed.returncode
            if kind == "test":
                tree = ET.parse(checkout / "pytest.xml")
                collected.append(sum(int(s.attrib.get("tests", 0)) for s in tree.getroot().iter("testsuite")))
            if kind == "build":
                wheels = list((checkout / "dist").glob("*.whl"))
                wheel_created = wheel_created and len(wheels) == 1
                if wheels:
                    with zipfile.ZipFile(wheels[0]) as archive:
                        wheel_created = (
                            wheel_created
                            and archive.read("metrics/__init__.py").decode()
                            == (checkout / "src/metrics/__init__.py").read_text()
                        )
            if kind in ("test", "lint"):
                path = checkout / "src/metrics/__init__.py"
                original = path.read_text()
                path.write_text(
                    original.replace('"total": sum(values)', '"total": sum(values) + 1')
                    if kind == "test"
                    else "import os\n" + original
                )
                mutant = subprocess.run(argv, cwd=checkout, env=environment, capture_output=True, text=True, timeout=15)
                negatives[kind] = mutant.returncode != 0
                log.append(
                    {
                        "job": name,
                        "command": command,
                        "negative_control": kind,
                        "exit_code": mutant.returncode,
                        "stdout": mutant.stdout,
                        "stderr": mutant.stderr,
                    }
                )
                path.write_text(original)
    results = Path(workspace) / "results"
    results.mkdir(exist_ok=True)
    (results / "ci-log.json").write_text(json.dumps(log, indent=2))
    return {
        "workflow_policy": True,
        "jobs": seen,
        "tests_collected": collected,
        "test_mutation_rejected": negatives.get("test", False),
        "lint_mutation_rejected": negatives.get("lint", False),
        "wheel_created": wheel_created,
    }
