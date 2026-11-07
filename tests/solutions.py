GOLD = {
    ".github/workflows/ci.yml": "name: package checks\non: [push, pull_request]\njobs:\n  quality:\n    runs-on: ubuntu-latest\n    defaults:\n      run:\n        working-directory: package\n    steps:\n      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n      - uses: astral-sh/setup-uv@d0d8abe699bfb85fec6de9f7adb5ae17292296ff\n      - run: uv sync --frozen --extra dev\n      - run: uv run --frozen pytest tests -q\n      - run: uv run --frozen ruff check src\n      - run: uv build --offline --no-build-isolation\n"
}


def solution(scenario="standard", seed=7):
    return dict(GOLD)
