## Dependencies
1. Oracle database (COLIN data load)
2. Postgres database (NameX/Entities data load)
3. Solr connection (data import)

## Setup
Clone the repo and submit a PR from a new branch.

### Install the dependencies
```bash
poetry install
```

### Configure the .env
(see .env.sample)

Database connections use Cloud SQL Connector when `DATABASE_CONNECTION_NAME`
(or `LEAR_DATABASE_CONNECTION_NAME`) is set. Otherwise, host connections use
the configured database password as before. To use IAM authentication over a
TCP tunnel instead, set `DATABASE_TCP_IAM_AUTH=True` (or
`LEAR_DATABASE_TCP_IAM_AUTH=True`), set the corresponding host and port,
and leave the corresponding connection name unset. The corresponding
`GOOGLE_APPLICATION_CREDENTIALS_NAMEX` or `GOOGLE_APPLICATION_CREDENTIALS_LEAR`
must identify the IAM database user.

```bash
eval $(poetry env activate)
```

### Run the importer
```bash
python import_data.py
```

### Run Linting
```bash
ruff check --fix
```

### Run unit tests
```bash
pytest
```
