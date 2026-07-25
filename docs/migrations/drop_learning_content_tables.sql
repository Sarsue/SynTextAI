-- Drops the flashcards, quiz_questions, and key_concepts tables.
--
-- NOT applied automatically. Review and run manually against the target
-- database (e.g. via psql or your normal migration process) once you're
-- ready — this is a destructive, irreversible schema change.
--
-- Context: this is the last step of removing the flashcard/quiz/key-concept
-- feature (vestigial from an earlier EdTech pivot, not used by the current
-- product). The API routes and the frontend side panel that used these
-- tables have already been removed from the codebase. This script is not
-- wired into Alembic because the repo's migration history currently has
-- multiple divergent heads and no tracked alembic.ini was found — resolve
-- that separately before trying to author a real Alembic migration for this,
-- rather than guessing a down_revision against an already-inconsistent chain.
--
-- Recommended before running:
--   1. Take a database backup / snapshot.
--   2. Confirm no other code path still reads these tables (grep the repo
--      for "flashcards", "quiz_questions", "key_concepts" one more time).
--   3. Run this against staging/dev first if you have it.

BEGIN;

DROP TABLE IF EXISTS flashcards;
DROP TABLE IF EXISTS quiz_questions;
DROP TABLE IF EXISTS key_concepts;

COMMIT;
