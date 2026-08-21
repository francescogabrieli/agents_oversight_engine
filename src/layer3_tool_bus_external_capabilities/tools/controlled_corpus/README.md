# Controlled corpus capability

This package owns the read-only, local-runtime capability used by the evaluation
corpus. It exposes only registry-defined documents and small CSV snapshots: the
actor supplies a stable `document_id` or `dataset_id`, never a filesystem path,
SQL statement, or query language.

The canonical registry is `configs/layer2_controlled_corpus.json`; its resources
must stay beneath `evaluation_corpus/`. Add a resource by committing the versioned
artifact, registering its ID and metadata, then adding capability and runtime tests.
The adapter returns the raw bounded content as a `ToolObservation` with a
`controlled-document://` or `controlled-dataset://` locator. It does not score,
rank, or semantically assess evidence.
