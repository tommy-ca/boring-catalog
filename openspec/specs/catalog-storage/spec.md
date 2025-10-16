# Catalog Storage

## Requirements

### Requirement: JSON-backed catalog storage
The system SHALL persist catalog namespaces and tables to a single JSON document when `storage_backend` is `json` (default).

#### Scenario: Initialize JSON backend
- **GIVEN** the user runs `ice init` without overriding `--catalog-backend`
- **WHEN** the command finishes successfully
- **THEN** `.ice/index` SHALL record `storage_backend: json`
- **AND** the catalog JSON SHALL exist at the configured `catalog_uri`
- **AND** namespaces/tables SHALL be read/write through the JSON backend.

#### Scenario: Conditional writes enforce concurrency
- **GIVEN** a JSON-backed catalog
- **WHEN** a table commit occurs
- **THEN** the catalog SHALL use ETag-based conditional writes to prevent overwriting concurrent updates
- **AND** the operation SHALL raise `ConcurrentModificationError` if the stored metadata has changed.

### Requirement: SlateDB-backed catalog storage
The system SHALL support SlateDB as an optional storage backend that stores namespace and table entries as key/value pairs.

#### Scenario: Initialize SlateDB backend
- **GIVEN** the user runs `ice init --catalog-backend slatedb --slatedb-path <path>`
- **WHEN** initialization succeeds
- **THEN** `.ice/index` SHALL include `storage_backend: slatedb` and persisted SlateDB configuration keys
- **AND** a SlateDB database SHALL be created at `<path>` containing catalog configuration.

#### Scenario: Persist table metadata pointers
- **GIVEN** a SlateDB-backed catalog with an existing namespace
- **WHEN** a table is created or committed
- **THEN** the SlateDB store SHALL persist the namespace/table combination under a `table:` key with the latest `metadata_location`
- **AND** it SHALL reject updates when the stored `metadata_location` does not match the expected location.

#### Scenario: Optional dependency handling
- **GIVEN** SlateDB Python bindings are not installed
- **WHEN** the user selects `--catalog-backend slatedb`
- **THEN** the CLI SHALL surface an actionable error instructing the user to install the SlateDB package.

### Requirement: CLI catalog selection
The CLI SHALL route commands through the storage backend recorded in `.ice/index`.

#### Scenario: CLI loads catalog backend
- **GIVEN** `.ice/index` defines `storage_backend`
- **WHEN** the user runs any `ice` sub-command
- **THEN** the CLI SHALL construct `BoringCatalog` with the recorded backend and its configuration keys.

#### Scenario: DuckDB catalog inspection
- **GIVEN** the user runs `ice duck`
- **WHEN** the backing store is not JSON
- **THEN** the CLI SHALL stage a temporary JSON export of the current catalog and clean it up after DuckDB exits.
