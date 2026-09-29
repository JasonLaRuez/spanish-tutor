"""Project-wide settings, read from the environment (.env supported)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

MODEL = os.getenv("TUTOR_MODEL", "claude-opus-5-5")

SQL_DIR = PROJECT_ROOT / "sql"
DATA_DIR = PROJECT_ROOT / "data"
PRIVATE_DIR = PROJECT_ROOT / "private"  # copyrighted input; gitignored

DB_PATH = Path(os.getenv("TUTOR_DB_PATH", DATA_DIR / "processed" / "word_bank.db"))
CHROMA_DIR = Path(os.getenv("TUTOR_CHROMA_DIR", DATA_DIR / "processed" / "chroma"))
