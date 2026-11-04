"""Keep personal-workspace references out of the extracted engine and its tests.

The engine was extracted from a personal repository. Its code and tests must stay synthetic: no
private file layouts, owner names, real week labels or local paths. The guard scans the engine
package, its tests and its shared fixtures. This file is deliberately outside the scanned paths so
that the patterns below never flag themselves.
"""

import re
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCANNED_PATHS = (
    REPO_ROOT / "src" / "athlete_platform" / "domain" / "engine",
    REPO_ROOT / "tests" / "unit" / "engine",
    REPO_ROOT / "tests" / "engine_fixtures.py",
)

SCANNED_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".csv", ".toml"}

# Specific on purpose: generic domain words (squat, pull-up, athlete) stay allowed.
FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "session log layout": r"log/sessions",
        "raw log layout": r"log/raw",
        "wellness export": r"wellness\.csv",
        "body reports": r"body-reports",
        "constraints profile path": r"profile/constraints",
        "external coach plans": r"external/oly",
        "test registry": r"tests/registry\.yaml",
        "real week label": r"2026-W4",
        "owner surname": r"\bCastrucci",  # \b spares the mcastrucci account in repository URLs
        # The product repository is mcastrucci/athlete-platform; only the personal one is forbidden.
        "personal repository": r"mcastrucci/athlete(?!-platform)",
        "local checkout path": r"/home/user/athlete",
        "scratch directory": r"scratchpad",
    }.items()
}


def find_forbidden(text: str) -> list[str]:
    """Return the names of the forbidden references found in ``text``."""
    return [name for name, pattern in FORBIDDEN_PATTERNS.items() if pattern.search(text)]


def scanned_files(paths: Iterable[Path] = SCANNED_PATHS) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                p
                for p in sorted(path.rglob("*"))
                if p.is_file() and p.suffix in SCANNED_SUFFIXES and "__pycache__" not in p.parts
            )
    return files


def scan(paths: Iterable[Path] = SCANNED_PATHS) -> dict[str, list[str]]:
    """Map each offending file (relative to the repository) to the references it contains."""
    offenders: dict[str, list[str]] = {}
    for file in scanned_files(paths):
        found = find_forbidden(file.read_text(encoding="utf-8"))
        if found:
            offenders[file.relative_to(REPO_ROOT).as_posix()] = found
    return offenders


def test_guard_flags_a_seeded_forbidden_sample() -> None:
    sample = "\n".join(
        [
            "open('log/sessions/day.yaml')",
            "read('log/raw/notes.txt')",
            "load('wellness.csv')",
            "see body-reports/",
            "profile/constraints.yaml",
            "external/oly/week.yaml",
            "tests/registry.yaml",
            "week 2026-W44",
            "Max Castrucci",
            "https://github.com/mcastrucci/athlete/commit/abc",
            "cd /home/user/athlete",
            "scratchpad/engine",
        ]
    )
    assert set(find_forbidden(sample)) == set(FORBIDDEN_PATTERNS)


def test_guard_flags_a_seeded_file(tmp_path: Path) -> None:
    (tmp_path / "leak.py").write_text("PATH = 'log/sessions/2026-W44.yaml'\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("PATH = 'synthetic'\n", encoding="utf-8")
    found = {
        file.name: find_forbidden(file.read_text("utf-8")) for file in scanned_files([tmp_path])
    }
    assert found == {"leak.py": ["session log layout", "real week label"], "clean.py": []}


def test_guard_does_not_flag_the_product_repository_or_ordinary_domain_words() -> None:
    clean = (
        "https://github.com/mcastrucci/athlete-platform\n"
        "pull_up, squat, athlete, weighted pull-up, 2030-W10, log/ is a word\n"
        "athlete@3f5897c core.py::session_pulls\n"
    )
    assert find_forbidden(clean) == []


def test_guard_does_not_flag_itself() -> None:
    assert Path(__file__) not in scanned_files()
    assert find_forbidden(Path(__file__).read_text(encoding="utf-8")) != []  # it lists the patterns


def test_real_engine_corpus_is_clean() -> None:
    files = scanned_files()
    assert any(f.name == "pulls.py" for f in files)
    assert any(f.name.startswith("test_") for f in files)
    assert scan() == {}
