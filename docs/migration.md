# OpenRecall Legacy Database Migration Guide

This guide explains how to audit and migrate an older OpenRecall database to the format required by OpenRecall v0.9.0.

---

## Overview

As part of the OpenRecall overhaul, the underlying database format was updated to support fast full-text search, multi-platform window tracking, and storage management.

OpenRecall is designed with user safety in mind:
- **Zero Screenshot Changes**: Your existing screenshot files are not moved, renamed, copied, or deleted during migration.
- **Automatic Backup Copy**: OpenRecall automatically creates a verified backup copy (`recall-legacy.db`) before updating your database.
- **Explicit User Control**: OpenRecall does **not** automatically alter older databases during normal application launch. You must run the migration command explicitly.

---

## Do I Need to Migrate?

You need to run the migration command if:
- You are upgrading from an older OpenRecall installation (created prior to version 0.9.0).
- OpenRecall stops during startup and displays a message explaining that an older database was detected.

If you are a new user starting with a fresh installation, OpenRecall automatically creates a database in the current format on first launch and no migration is required.

---

## Before You Begin

Follow these simple safety steps before migrating:

### 1. Stop OpenRecall

Make sure OpenRecall is completely closed before starting the migration.

Do not start the migration while OpenRecall is still running.

### 2. Make a Manual Backup
**Strongly Recommended**: Before running migration, manually copy your complete OpenRecall storage folder (containing `recall.db` and the `screenshots/` subfolder) to a separate backup location (such as an external drive or backup folder).

> **Note**: Although OpenRecall creates its own automatic `recall-legacy.db` copy during migration, making your own manual backup gives you an extra layer of protection for your digital memory.

### 3. Check Free Disk Space
Ensure your hard drive has enough free space for the automatic backup copy (`recall-legacy.db`), which requires about the same amount of space as your existing `recall.db` file.

---

## Step 1 — Audit Legacy Storage

OpenRecall includes a read-only audit command to inspect your database and screenshot storage before making any changes.

To run the audit:
```bash
openrecall --audit-legacy-storage
```

If your storage folder is in a custom location, specify the path using `--storage-path`:
```bash
# Linux / macOS:
openrecall --audit-legacy-storage --storage-path /path/to/storage

# Windows:
openrecall --audit-legacy-storage --storage-path C:\OpenRecall\storage
```

> **Note**: The `--storage-path` option accepts either a storage directory (containing `recall.db` and `screenshots/`) or a path to the database file itself (such as `/path/to/recall.db`).

### What the Audit Tells You
The audit checks your files and displays a summary report in your terminal:
- **Database Status**: Confirms your database file exists, measures its size, and checks its format version.
- **Backup Status**: Checks if an automatic backup (`recall-legacy.db`) already exists and verifies whether it matches the current database.
- **Screenshot Storage Status**: Scans your screenshot folder, counts physical images, checks how many database records match image files, and counts any extra or missing screenshots.
- **Migration Readiness**: Displays an overall readiness message (such as `100% Ready for zero-mutation migration`).

The audit command is completely read-only and will never modify or delete any files.

---

## Step 2 — Review the Audit Results

Look for the following key indicators in your audit report:
- **Database State**: Should report `legacy` (indicating an older database format).
- **Migration Readiness**: Should confirm that your storage is ready for migration.

If the audit reports an `Unsupported or unknown database schema`, stop and check that you specified the correct storage directory.

---

## Step 3 — Run the Migration

To update your database, run:
```bash
openrecall --migrate
```

Or with a custom storage path:
```bash
# Linux / macOS:
openrecall --migrate --storage-path /path/to/storage

# Windows:
openrecall --migrate --storage-path C:\OpenRecall\storage
```

### What Happens During Migration
When you run `--migrate`, OpenRecall performs the following steps automatically:
1. **Verifies Database Version**: Confirms that your database is an older supported version.
2. **Creates a Verified Backup Copy**: Calculates a SHA-256 digital fingerprint (checksum) of your database file and creates a backup named `recall-legacy.db` in the same directory. OpenRecall verifies that the digital fingerprint of the backup matches the original before proceeding.
3. **Scans Existing Screenshots**: Checks your screenshot folder to link existing image files with your recorded history, without changing any image files.
4. **Updates Database Format**: Safely updates your database in a single step, adding new fields for multi-platform support, linking screenshot filenames, and rebuilding the full-text search index.
5. **Verifies Data Integrity**: Checks that all your historical records match the backup copy and tests that search functionality works correctly.

When migration finishes, OpenRecall displays:
`Successfully migrated legacy database at ... to the current format.`

---

## What the Migration Changes

- **In-Place Database Update**: OpenRecall creates a SHA-256-verified backup copy of your pre-migration database as `recall-legacy.db`. The original `recall.db` file is then updated in place to the current format.
- **All Historical Data Preserved**: Your past capture records remain intact. Application names, window titles, extracted OCR text, timestamps, and feature data are all preserved.
- **Search Rebuilt**: The full-text search index is completely rebuilt so you can search your past desktop history using the current search interface.

---

## What Happens to Screenshots?

- **Zero Image Changes**: Your existing screenshot image files are **not** moved, renamed, copied, or deleted.
- **Matching Images to Records**: OpenRecall connects existing screenshot files in your `screenshots/` folder to your database history using capture timestamps:
  - OpenRecall first checks for a screenshot named `{timestamp}_0.webp`, and falls back to `{timestamp}.webp`.
  - If both files exist for a single timestamp, `{timestamp}_0.webp` is selected while both physical files remain untouched on disk.
- **Missing Screenshots**: If a screenshot file is missing from disk, your record is still safely migrated; no image is linked to that record. Missing screenshots do not cause migration to fail.
- **Unmatched Screenshots**: Extra screenshot files in your folder that do not match a database record are left completely untouched.
- **Missing Screenshots Folder**: If your `screenshots/` folder is empty or missing, your database will still migrate successfully.

---

## Automatic Backup Copy (`recall-legacy.db`)

Before making any changes to your database, OpenRecall creates an automatic backup copy named `recall-legacy.db` in your storage directory.

- **Digital Fingerprint Verification**: OpenRecall uses a SHA-256 checksum — a digital fingerprint — to verify that `recall-legacy.db` is an exact duplicate of your database before any changes are made. If the fingerprint check fails, migration stops immediately and your original database is left untouched.
- **Existing Backup Copies**:
  - If `recall-legacy.db` already exists and matches your database fingerprint, OpenRecall reuses the existing backup copy.
  - If `recall-legacy.db` exists but has different contents, OpenRecall stops migration to prevent accidentally overwriting an earlier backup.
- **Keeping Backups**: Keep both your manual backup and `recall-legacy.db` until you have tested OpenRecall and confirmed that your timeline and search work properly. You decide when backups are no longer needed.

---

## After Migration

### Verification Checklist
After migration completes successfully:

1. **Launch OpenRecall**:
   ```bash
   openrecall
   ```
2. **Open the Web UI**: Visit [http://localhost:8082](http://localhost:8082) in your web browser.
3. **Check the Timeline**: Drag the timeline slider back to view past activity timestamps.
4. **Check Gallery Mode**: Visit `http://localhost:8082/?mode=gallery` to view your screenshot cards.
5. **Test Search**: Search for words or phrases from your past screen history to test the search index.
6. **Inspect Screenshots**: Open a few past captures to confirm images display correctly.

---

## Re-running Migration

Running `openrecall --migrate` on a database that has already been updated is completely safe.

OpenRecall will recognize that your database is already up to date, display:
`Database at ... is already current (v3); no migration required. No migration was performed.`
and exit cleanly without modifying any files.

---

## Troubleshooting and Common Questions

| Problem / Situation | What Happens & What to Do |
| :--- | :--- |
| **OpenRecall stops on startup with a legacy database error** | This is a built-in safety feature to protect your data. Run `openrecall --migrate` to update your database. |
| **Audit command reports an error** | The audit command is strictly read-only and does not change your files. Check that your storage path is correct and that you have permission to read the files. |
| **Backup copy creation fails** | Migration stops **before** making any changes to `recall.db`. Check that your hard drive has enough free space and write permissions in the storage folder. |
| **Backup copy mismatch error** | OpenRecall detected an existing `recall-legacy.db` file that does not match `recall.db`. Move or rename the existing `recall-legacy.db` file after checking your manual backups. |
| **Database update fails** | If an error occurs while updating the database, OpenRecall cancels the update, leaving `recall.db` in its original state. Your `recall-legacy.db` backup remains safe on disk. |
| **Unsupported or unknown database error** | OpenRecall rejected a file that is not a valid OpenRecall database. Verify that you specified the correct storage path. |
| **Migration finishes but verification fails** | If post-migration verification fails, your database file may already be updated to the new format. Your pre-migration backup (`recall-legacy.db`) remains safe on disk. Keep your backups intact and seek assistance or report the issue. |

---

## Important Safety Reminders

- **Do not interrupt migration**: Do not close the console window, turn off your computer, or stop the process while `openrecall --migrate` is running.
- **Keep your backups**: Do not delete your manual backup or `recall-legacy.db` until you have checked your history in OpenRecall and verified that everything works as expected.
- **Preserve files if errors occur**: If an error occurs, keep your original database files and manual backups intact before attempting any further steps.
