# Project Context

## Purpose
Boring Catalog provides a lightweight Apache Iceberg catalog implementation that can be bootstrapped from the command line. It removes the need to operate an external catalog service by persisting namespaces and table metadata to storage providers such as the local filesystem or S3. The project now supports both the original single-file JSON catalog and a SlateDB-backed key/value catalog for better scalability.

## Tech Stack
- Python 3.10+
- PyIceberg for catalog/table abstractions
- Click for the `ice` CLI
- DuckDB integration for ad-hoc exploration
- SlateDB (optional) for catalog metadata storage
- PyArrow / Pandas for data handling in tests

## Project Conventions

### Code Style
- Follow Python standard library and PyIceberg naming conventions.
- PEP 8 formatting (4-space indents, snake_case functions, CapWords classes).
- Avoid adding inline comments unless they provide non-obvious context.
- Keep imports organized in logical groups (stdlib, third-party, local).

### Architecture Patterns
- `boringcatalog.catalog.BoringCatalog` subclasses `MetastoreCatalog` and delegates persistence to storage backends.
- Storage backends implement a `CatalogStorage` protocol (JSON file or SlateDB key/value store).
- CLI entry points live in `boringcatalog.cli` using Click commands; they construct `BoringCatalog` instances via `.ice/index` configuration.
- DuckDB scripts are templated SQL files that read catalog state.

### Testing Strategy
- Pytest is the primary test runner.
- CLI workflows are validated by invoking `python -m boringcatalog.cli` within temporary workspaces.
- Table operations are exercised through PyIceberg APIs; tests use temporary warehouses and in-memory data.
- Optional SlateDB tests are guarded with `pytest.importorskip("slatedb")` to avoid failures when the dependency is missing.

### Git Workflow
- Default branch is `main`.
- Changes are developed on feature branches and merged via pull requests (no enforced naming convention observed).
- Commits should be concise and focused; avoid committing generated artifacts.
- Always run the pytest suite before merging.

## Domain Context
- Apache Iceberg catalogs map namespace/table identifiers to metadata files stored in object storage.
- `BoringCatalog` must coordinate metadata writes with optimistic locking to avoid concurrent update issues.
- The CLI stores configuration pointers in `.ice/index` so that subsequent commands can reconstruct catalog instances.
- When using SlateDB, table metadata locations are still managed by PyIceberg; SlateDB only holds catalog state (namespaces/tables).

## Important Constraints
- The catalog should operate without a dedicated service—only filesystem or object storage access is assumed.
- JSON backend relies on ETag-based conditional writes; SlateDB backend enforces single-writer semantics.
- The CLI must remain backward compatible with existing `.ice/index` files.
- Tests should not require external infrastructure beyond optional SlateDB bindings.

## External Dependencies
- PyIceberg (catalog and table operations)
- DuckDB (analytics integration)
- FSSpec/S3FS for object storage IO
- SlateDB Python bindings (optional)
- PyArrow/Pandas for data ingestion in tests
