# Browser walkthroughs

End-to-end checks of the web app in a real browser (headless Microsoft Edge, through
`playwright-core`, so no browser download is needed). Each script walks one skill the way a
learner would, prints what it sees, and exits with an error if a check fails. They were
written to verify Phase 4's slices and are meant to be re-run after changes to those flows.

| Script | What it walks | Model calls |
|---|---|---|
| `reading.mjs` | What next? → start the suggested book → study chapter 1's words → read, look a word up → Finished (not "Talk about it") → the book moves to chapter 2; the Books page | none |
| `lyrics.mjs` | Songs & poems → Rima XXIII → study → translate line 1 → Compare → Finished; the library shows it finished | one translation and one comparison, ~2¢ |

## Running them

**Always against a copy of the word bank**, never your real one: the walkthroughs study
words and finish items, which writes to the learning log.

```sh
# 1. A copy of the word bank (any path), and the built UI
uv run python -c "import sqlite3; s = sqlite3.connect('data/processed/word_bank.db'); d = sqlite3.connect('copy.db'); s.backup(d)"
cd web && npm install && npm run build && cd ..

# 2. The server on the copy (a second terminal; ~20 s to load)
TUTOR_DB_PATH=copy.db uv run python -c "from spanish_tutor.api.app import create_app; import uvicorn; uvicorn.run(create_app(), port=8765)"

# 3. A walkthrough (screenshots go to the folder given, here web/e2e/out)
cd web && node e2e/reading.mjs e2e/out
```

`E2E_BASE` points them at another address (default `http://127.0.0.1:8765`).

They expect the content as it was when they were written: *An Elementary Spanish Reader*
as the suggested book (chapter 1 with 14 new words) and Bécquer's Rimas added (Rima XXIII
with 2 new words), on a fresh copy where nothing has been read yet. Run them on a fresh
copy each time.
