-- Step 3: Remove the email column (after making it nullable)
ALTER TABLE users DROP COLUMN email;

-- Step 3b: Rename created_at to signed_up_at
ALTER TABLE users RENAME COLUMN created_at TO signed_up_at;

-- Step 3c: Add phone as required
ALTER TABLE users ADD COLUMN phone VARCHAR(20) NOT NULL;

-- Step 3d: Drop old logs table
DROP TABLE old_logs;

-- Step 3e: Change id to BIGINT
ALTER TABLE users ALTER COLUMN id TYPE BIGINT;
