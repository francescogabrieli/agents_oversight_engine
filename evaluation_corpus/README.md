# Controlled evaluation corpus

This tracked corpus contains fictional, anonymized, versioned material used by
the read-only `controlled_document.read` and `controlled_dataset.read`
capabilities. It is neither a production data source nor a substitute for public
evidence. Every usable resource is explicitly registered in
`configs/layer2_controlled_corpus.json`.

The documents support obligation and communication tasks; the CSV snapshots
support reporting, triage, and reconciliation tasks. Do not place personal,
confidential, or live operational data here.

`scripts/build_evaluation_database.py` derives the dedicated SQLite reporting
fixture from `datasets/evaluation_incidents_v1.csv`. The database itself lives in
ignored runtime artifacts and is exposed only through the read-only
`evaluation_reporting_read_only` profile.
