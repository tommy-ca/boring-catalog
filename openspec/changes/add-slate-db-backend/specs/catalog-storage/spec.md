## ADDED Requirements
### Requirement: SlateDB-backed catalog storage
The system SHALL support SlateDB as an optional storage backend that stores namespace and table entries as key/value pairs.

#### Scenario: Initialize SlateDB backend
- **WHEN** `ice init --catalog-backend slatedb --slatedb-path <path>` runs successfully
- **THEN** `.ice/index` SHALL record the backend and SlateDB configuration
- **AND** a SlateDB database SHALL contain the catalog configuration.

#### Scenario: Persist table metadata pointers
- **WHEN** a table is created or committed under a SlateDB-backed catalog
- **THEN** the catalog SHALL check the expected metadata location and refuse mismatched updates.

#### Scenario: Prevent namespace deletion while tables exist
- **WHEN** a namespace containing tables is dropped under a SlateDB-backed catalog
- **THEN** the operation SHALL raise `NamespaceNotEmptyError`
- **AND** the namespace SHALL remain until its tables are removed.

#### Scenario: Rename table across namespaces
- **WHEN** a table is renamed from one namespace/name to another within a SlateDB-backed catalog
- **THEN** the catalog SHALL create the destination entry and remove the original entry while preserving metadata.

### Requirement: CLI catalog selection
The CLI SHALL route commands through the storage backend recorded in `.ice/index`.

#### Scenario: DuckDB catalog inspection
- **WHEN** `ice duck` runs against a non-JSON backend
- **THEN** the CLI SHALL export a temporary JSON snapshot for DuckDB and delete it after exit.

#### Scenario: Persist SlateDB configuration in index
- **WHEN** the CLI initializes a SlateDB-backed catalog
- **THEN** `.ice/index` SHALL include `storage_backend: slatedb` and persist the SlateDB configuration values such as `slatedb_path`.
