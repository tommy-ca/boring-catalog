## Why
Developers need a scalable catalog state store so that Boring Catalog is not limited to a single JSON file. Supporting SlateDB unlocks object-store-backed metadata persistence while maintaining the existing lightweight experience.

## What Changes
- Introduce a `CatalogStorage` abstraction with JSON and SlateDB implementations
- Extend the CLI to configure and persist backend-specific settings in `.ice/index`
- Export DuckDB-compatible catalog snapshots when a non-JSON backend is in use
- Document the new backend and declare the optional `slatedb` dependency

## Impact
- Affected specs: `catalog-storage`
- Affected code: `src/boringcatalog/catalog.py`, `storage.py`, `cli.py`, README, tests, project dependencies
