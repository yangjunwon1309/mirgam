# Mirgam

Mirgam is a small farmer CRM with customer management, per-customer order entry,
product management, and Excel-compatible CSV export.

## Current architecture

The local browser UI and central data server run as separate processes.
The client calls the authenticated Flask API; only the API opens SQLite.
Customer and order data are isolated by farm account. Products remain shared.

```text
Browser → local Flask UI :5000 → central Flask API :5100 → SQLite
```

- Customer search, registration, editing, and individual soft deletion.
- Stable customer IDs distinguish matching names with different addresses.
- Individual order values and an optional apply-to-all panel.
- Calendar/name filters, server-side pagination, and filtered CSV downloads.
- Local Save As dialog, atomic order batches, and idempotent retries.
- XLSX/CSV customer upload with sheet/header selection, column mapping, preview,
  validation, duplicate skipping, add-only confirmation, and concurrency checks.
- SQLAlchemy/Alembic schema versioning, WAL transactions, and online backups.

Existing green/beige templates are preserved in `apps/templates`.
Original CSV files in `apps/static` are migration inputs only, not the live database.
The old `dist/Mirgam` EXE has not been replaced with this SQLite version.

## Run locally

Python 3.10 or newer, from the project root:

```powershell
python -m pip install -r requirements-server.txt -r requirements-client.txt

# Inspect first; --apply performs a repeat-safe, one-time migration to a new DB.
python scripts/migrate_csv_to_sqlite.py --source apps/static --data-dir var/server
python scripts/migrate_csv_to_sqlite.py --source apps/static --data-dir var/server --apply

# Starts two independent processes and opens the browser. Ctrl+C stops both.
python scripts/run_local.py
```

The development database has already been initialized on this laptop.
Alternatively double-click `Start-Mirgam-Local.cmd`.
Open <http://127.0.0.1:5000/> and use the existing farm login.

To start the programs separately:

```powershell
python scripts/run_api_server.py --data-dir var/server --port 5100
python scripts/run_client.py --api-url http://127.0.0.1:5100 --port 5000
```

For a different server use `--api-url`, `MIRGAM_API_BASE_URL`, or a
`client.json` file based on `client.example.json`.
Use HTTPS and access controls before hosting outside the laptop.

### Use SQLite Cloud from the API server

The browser client continues to call the Flask API (for example, `http://127.0.0.1:5100`).
Only the API server connects to SQLite Cloud; never put the database connection string or API key in `client.json`, the browser, or `MIRGAM_API_BASE_URL`.
Install the server requirements, then set `MIRGAM_DATABASE_URL` in the API server's environment to the native SQLite Cloud connection string for the existing database, such as `sqlitecloud://<node-host>:8860/mirgam.test?apikey=<API_KEY>`. Use the **native connection string/hostname from the dashboard**, not the Weblite HTTPS REST URL. The cloud database must already contain the current schema (`alembic_version` `0001`); this app checks the schema but does not migrate a cloud database at startup.

For a local test, enter the native node hostname and API key without putting the key in shell history, then start the normal local UI/API pair:

```powershell
$nodeHost = Read-Host 'SQLite Cloud native node hostname (not the Weblite URL)'
$keySecure = Read-Host 'SQLite Cloud API key' -AsSecureString
$apiKey = [System.Net.NetworkCredential]::new('', $keySecure).Password
$encodedKey = [uri]::EscapeDataString($apiKey)
$env:MIRGAM_DATABASE_URL = "sqlitecloud://$($nodeHost):8860/mirgam.test?apikey=$encodedKey"
Remove-Variable apiKey, encodedKey, keySecure
python scripts/run_local.py
# After Ctrl+C, clear the connection setting:
Remove-Item Env:MIRGAM_DATABASE_URL
```

The local `var/server/mirgam.sqlite3` is not read in cloud mode. Remove the environment variable to return to local SQLite. Cloud mode skips local file backups; arrange and test backups in SQLite Cloud separately. The client remains unchanged and still uses its normal API URL.

## Verify and back up

```powershell
python -m unittest tests.test_central_api -v
node scripts/verify_order_bulk.cjs

# Actual running API/client; reads migrated records without changing them.
python scripts/verify_live_sqlite.py

# Online SQLite backup; safe while the server is running.
python scripts/backup_server.py --data-dir var/server
```

`scripts/run_mirgam.py --verify-only` now runs isolated SQLite tests.
The old CSV-backend verification scripts are retained for the legacy version.

## Structure

```text
apps/app.py                 Local UI routes and gateway
apps/api_client.py          HTTP API adapter
apps/templates/             Existing UI and customer import preview
server/app.py               Authenticated central API
server/models.py            SQLite relational schema
server/migrations/          Alembic schema migrations
server/migrate.py           Explicit CSV initial migration
server/db.py                Transactions and online backup
server/maintenance.py       Daily backup and expired-upload cleanup
scripts/                    Run, migrate, verify, backup and staged-build CLIs
var/server/                 Private writable development database (gitignored)
```

## Documentation and desktop packaging

See [the implementation/run guide](docs/CENTRAL_SQLITE_IMPLEMENTATION.md)
for migration findings, import behavior, failure recovery, data locations,
security boundaries, validation limits and remaining deployment work.
The design baseline is [CENTRAL_SQLITE_WORK_SPEC.md](docs/CENTRAL_SQLITE_WORK_SPEC.md).

`scripts/build_desktop.ps1` prepares separate client/server packages under
`build/sqlite-release` without including real CSVs, login credentials or the DB.
This turn does not build or install those EXEs.
The existing `Update-Mirgam.cmd` targets the legacy update package, not the
new SQLite client. Do not use it to install this version.
