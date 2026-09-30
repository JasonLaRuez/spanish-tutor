"""Guards the project's copyright rule: private material must never be committable."""

import subprocess

import pytest

from spanish_tutor.config import PROJECT_ROOT


def is_git_ignored(relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", relative_path],
        cwd=PROJECT_ROOT,
        check=False,  # non-zero just means "not ignored"
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "private/lyrics/some_song.txt",
        "private/books/some_book/chapter_01.txt",
        ".env",
        ".claude/settings.local.json",
        "data/processed/word_bank.db",
        # Derived from NC-SA / BY-SA / BY sources, and personal: never committed.
        "data/processed/seed_candidates.csv",
        "data/processed/seed_words.sql",
        "data/processed/tatoeba_analyzed.jsonl",
        "data/raw/SUBTLEX-ESP.xlsx",
        "data/raw/wiktionary_es.jsonl",
    ],
)
def test_private_and_generated_files_are_ignored(path):
    assert is_git_ignored(path), f"{path} would be committable"


@pytest.mark.parametrize("path", ["sql/schema.sql", "src/spanish_tutor/config.py"])
def test_source_files_are_not_ignored(path):
    assert not is_git_ignored(path), f"{path} is unexpectedly gitignored"
