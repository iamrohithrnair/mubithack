-- Facts as each decision saw them, and as each answer was given. The case row
-- (harness_cases) is latest-wins; a decision and an answer are not. Remembering
-- drafts from the answered decision's own facts, and replay reads each past
-- answer's own facts, so a later arrival can never rewrite either.
ALTER TABLE harness_decisions ADD COLUMN facts TEXT NOT NULL DEFAULT '{}';
ALTER TABLE harness_answers ADD COLUMN facts TEXT NOT NULL DEFAULT '{}';
