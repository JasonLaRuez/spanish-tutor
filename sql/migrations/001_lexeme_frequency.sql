-- Migration 1: add word frequency to lexemes.
-- New databases get this column from schema.sql directly; this brings databases
-- created before it to the same shape. (lexeme_reviews needs no migration: it is a
-- new table, and schema.sql's CREATE TABLE IF NOT EXISTS adds it.)
ALTER TABLE lexemes
    ADD COLUMN frequency_per_million REAL CHECK (frequency_per_million >= 0);
