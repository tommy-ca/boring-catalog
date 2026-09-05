from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Protocol

try:  # pragma: no cover - optional dependency
    from slatedb import SlateDB
except ImportError:  # pragma: no cover
    SlateDB = None

from pyiceberg.exceptions import (
    NamespaceAlreadyExistsError,
    NamespaceNotEmptyError,
    NoSuchNamespaceError,
    NoSuchTableError,
    TableAlreadyExistsError,
)


class ConcurrentUpdateError(RuntimeError):
    """Raised when optimistic concurrency checks fail."""


@dataclass
class NamespaceRecord:
    name: str
    properties: Dict[str, str]


@dataclass
class TableRecord:
    namespace: str
    name: str
    metadata_location: str
    previous_metadata_location: Optional[str] = None


class CatalogStorage(Protocol):
    """Storage interface for catalog metadata."""

    def close(self) -> None:
        ...

    def dump_catalog(self) -> Dict[str, object]:
        ...

    def load_namespace(self, name: str) -> Optional[NamespaceRecord]:
        ...

    def put_namespace(self, record: NamespaceRecord) -> None:
        ...

    def delete_namespace(self, name: str) -> None:
        ...

    def iter_namespaces(self) -> Iterable[NamespaceRecord]:
        ...

    def load_table(self, identifier: str) -> Optional[TableRecord]:
        ...

    def put_table(
        self,
        record: TableRecord,
        *,
        expect_metadata_location: Optional[str],
    ) -> None:
        ...

    def delete_table(self, identifier: str) -> None:
        ...

    def iter_tables(self) -> Iterable[TableRecord]:
        ...

    def rename_table(
        self,
        source_identifier: str,
        destination: TableRecord,
        *,
        expect_metadata_location: Optional[str],
    ) -> None:
        ...

    def update_namespace_properties(
        self,
        name: str,
        *,
        removals: Optional[Iterable[str]],
        updates: Dict[str, str],
    ) -> Dict[str, str]:
        ...


class JsonCatalogStorage:
    """Catalog storage backed by a single JSON document."""

    def __init__(self, *, uri: str, catalog_name: str, io_factory):
        self._uri = uri
        self._catalog_name = catalog_name
        self._io_factory = io_factory
        self._ensure_initialized()

    # ------------------------------------------------------------------
    # Helpers
    def _ensure_initialized(self) -> None:
        io = self._io_factory(location=self._uri)
        input_file = io.new_input(self._uri)
        if input_file.exists():
            return

        initial_catalog = {
            "catalog_name": self._catalog_name,
            "namespaces": {},
            "tables": {},
        }
        with io.new_output(self._uri).create(overwrite=True) as f:
            f.write(json.dumps(initial_catalog, indent=2).encode("utf-8"))

    def _read(self) -> tuple[Dict[str, object], Optional[str]]:
        io = self._io_factory(location=self._uri)
        input_file = io.new_input(self._uri)
        if not input_file.exists():
            return {
                "catalog_name": self._catalog_name,
                "namespaces": {},
                "tables": {},
            }, None

        with input_file.open() as f:
            data = json.loads(f.read().decode("utf-8"))

        metadata = input_file.metadata() if hasattr(input_file, "metadata") else {}
        return data, metadata.get("ETag")

    def _write(self, data: Dict[str, object], etag: Optional[str]) -> None:
        io = self._io_factory(location=self._uri)
        output = io.new_output(self._uri)
        if etag is not None and hasattr(output, "set_metadata"):
            output.set_metadata({"if_match": etag})
        with output.create(overwrite=True) as f:
            f.write(json.dumps(data, indent=2).encode("utf-8"))

    # ------------------------------------------------------------------
    # Public API
    def close(self) -> None:  # pragma: no cover - nothing to close for JSON
        return

    def dump_catalog(self) -> Dict[str, object]:
        data, _ = self._read()
        return data

    def load_namespace(self, name: str) -> Optional[NamespaceRecord]:
        data, _ = self._read()
        ns = data.get("namespaces", {}).get(name)
        if ns is None:
            return None
        return NamespaceRecord(name=name, properties=dict(ns.get("properties", {})))

    def put_namespace(self, record: NamespaceRecord) -> None:
        data, etag = self._read()
        namespaces = data.setdefault("namespaces", {})
        if record.name in namespaces:
            raise NamespaceAlreadyExistsError(f"Namespace already exists: {record.name}")
        namespaces[record.name] = {"properties": dict(record.properties)}
        self._write(data, etag)

    def delete_namespace(self, name: str) -> None:
        data, etag = self._read()
        namespaces = data.setdefault("namespaces", {})
        if name not in namespaces:
            raise NoSuchNamespaceError(f"Namespace does not exist: {name}")
        tables = data.get("tables", {})
        if any(tbl.get("namespace") == name for tbl in tables.values()):
            raise NamespaceNotEmptyError(f"Namespace {name} is not empty.")
        del namespaces[name]
        self._write(data, etag)

    def iter_namespaces(self) -> Iterable[NamespaceRecord]:
        data, _ = self._read()
        for name, value in data.get("namespaces", {}).items():
            yield NamespaceRecord(name=name, properties=dict(value.get("properties", {})))

    def update_namespace_properties(
        self,
        name: str,
        *,
        removals: Optional[Iterable[str]],
        updates: Dict[str, str],
    ) -> Dict[str, str]:
        data, etag = self._read()
        namespaces = data.setdefault("namespaces", {})
        if name not in namespaces:
            raise NoSuchNamespaceError(f"Namespace does not exist: {name}")
        current = namespaces[name].setdefault("properties", {})
        if removals:
            for key in removals:
                current.pop(key, None)
        if updates:
            current.update(updates)
        namespaces[name]["properties"] = current
        self._write(data, etag)
        return dict(current)

    def load_table(self, identifier: str) -> Optional[TableRecord]:
        data, _ = self._read()
        entry = data.get("tables", {}).get(identifier)
        if entry is None:
            return None
        return TableRecord(
            namespace=entry["namespace"],
            name=entry["name"],
            metadata_location=entry["metadata_location"],
            previous_metadata_location=entry.get("previous_metadata_location"),
        )

    def put_table(
        self,
        record: TableRecord,
        *,
        expect_metadata_location: Optional[str],
    ) -> None:
        data, etag = self._read()
        tables = data.setdefault("tables", {})
        identifier = f"{record.namespace}.{record.name}"
        current = tables.get(identifier)

        if current is not None and expect_metadata_location is None:
            raise TableAlreadyExistsError(
                f"Table {record.namespace}.{record.name} already exists"
            )

        if expect_metadata_location is not None:
            if current is None or current.get("metadata_location") != expect_metadata_location:
                raise ConcurrentUpdateError(
                    f"Table {record.namespace}.{record.name} metadata mismatch"
                )

        tables[identifier] = {
            "namespace": record.namespace,
            "name": record.name,
            "metadata_location": record.metadata_location,
            "previous_metadata_location": record.previous_metadata_location,
        }
        self._write(data, etag)

    def delete_table(self, identifier: str) -> None:
        data, etag = self._read()
        tables = data.setdefault("tables", {})
        if identifier not in tables:
            raise NoSuchTableError(f"Table does not exist: {identifier}")
        del tables[identifier]
        self._write(data, etag)

    def iter_tables(self) -> Iterable[TableRecord]:
        data, _ = self._read()
        for entry in data.get("tables", {}).values():
            yield TableRecord(
                namespace=entry["namespace"],
                name=entry["name"],
                metadata_location=entry["metadata_location"],
                previous_metadata_location=entry.get("previous_metadata_location"),
            )

    def rename_table(
        self,
        source_identifier: str,
        destination: TableRecord,
        *,
        expect_metadata_location: Optional[str],
    ) -> None:
        data, etag = self._read()
        tables = data.setdefault("tables", {})
        current = tables.get(source_identifier)
        if current is None:
            raise NoSuchTableError(f"Table does not exist: {source_identifier}")
        if expect_metadata_location is not None and current.get("metadata_location") != expect_metadata_location:
            raise ConcurrentUpdateError(
                f"Table {source_identifier} metadata mismatch"
            )

        new_identifier = f"{destination.namespace}.{destination.name}"
        if new_identifier in tables and new_identifier != source_identifier:
            raise TableAlreadyExistsError(
                f"Table {destination.namespace}.{destination.name} already exists"
            )

        tables[new_identifier] = {
            "namespace": destination.namespace,
            "name": destination.name,
            "metadata_location": destination.metadata_location,
            "previous_metadata_location": destination.previous_metadata_location,
        }

        if new_identifier != source_identifier:
            del tables[source_identifier]

        self._write(data, etag)


class SlateDbCatalogStorage:
    """Catalog storage backed by SlateDB."""

    _CONFIG_KEY = b"config:catalog"
    _NAMESPACE_PREFIX = b"namespace:"
    _TABLE_PREFIX = b"table:"
    _SCAN_TERMINATOR = b"\xff"

    def __init__(
        self,
        *,
        catalog_name: str,
        path: Optional[str],
        url: Optional[str],
        env_file: Optional[str],
    ) -> None:
        if SlateDB is None:  # pragma: no cover - optional dependency
            raise ImportError("SlateDB Python bindings are not installed")
        if not path:
            raise ValueError("SlateDB backend requires 'slatedb_path' to be set")

        kwargs = {}
        if url:
            kwargs["url"] = url
        if env_file:
            kwargs["env_file"] = env_file

        self._db = SlateDB(path, **kwargs)
        self._catalog_name = catalog_name
        self._ensure_config()

    # ------------------------------------------------------------------
    # Helpers
    def _ensure_config(self) -> None:
        if self._db.get(self._CONFIG_KEY) is None:
            payload = json.dumps({"catalog_name": self._catalog_name}).encode("utf-8")
            self._db.put(self._CONFIG_KEY, payload)

    @staticmethod
    def _namespace_key(name: str) -> bytes:
        return f"namespace:{name}".encode("utf-8")

    @staticmethod
    def _table_key(identifier: str) -> bytes:
        return f"table:{identifier}".encode("utf-8")

    def _iter_prefix(self, prefix: bytes):
        end = prefix + self._SCAN_TERMINATOR
        iterator = self._db.scan_iter(prefix, end)
        for key, value in iterator:
            yield bytes(key), bytes(value)

    def _iter_namespace_records(self) -> Iterable[NamespaceRecord]:
        for key, value in self._iter_prefix(self._NAMESPACE_PREFIX):
            name = key.decode("utf-8").split(":", 1)[1]
            payload = json.loads(value.decode("utf-8"))
            yield NamespaceRecord(name=name, properties=payload.get("properties", {}))

    def _iter_table_records(self) -> Iterable[TableRecord]:
        for key, value in self._iter_prefix(self._TABLE_PREFIX):
            identifier = key.decode("utf-8").split(":", 1)[1]
            namespace, table_name = identifier.rsplit(".", 1)
            payload = json.loads(value.decode("utf-8"))
            yield TableRecord(
                namespace=namespace,
                name=table_name,
                metadata_location=payload["metadata_location"],
                previous_metadata_location=payload.get("previous_metadata_location"),
            )

    def _load_table(self, identifier: str) -> Optional[Dict[str, str]]:
        raw = self._db.get(self._table_key(identifier))
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))

    # ------------------------------------------------------------------
    # Public API
    def close(self) -> None:
        self._db.close()

    def dump_catalog(self) -> Dict[str, object]:
        namespaces = {
            record.name: {"properties": dict(record.properties)}
            for record in self._iter_namespace_records()
        }
        tables = {}
        for record in self._iter_table_records():
            identifier = f"{record.namespace}.{record.name}"
            tables[identifier] = {
                "namespace": record.namespace,
                "name": record.name,
                "metadata_location": record.metadata_location,
                "previous_metadata_location": record.previous_metadata_location,
            }
        return {
            "catalog_name": self._catalog_name,
            "namespaces": namespaces,
            "tables": tables,
        }

    def load_namespace(self, name: str) -> Optional[NamespaceRecord]:
        raw = self._db.get(self._namespace_key(name))
        if raw is None:
            return None
        payload = json.loads(raw.decode("utf-8"))
        return NamespaceRecord(name=name, properties=payload.get("properties", {}))

    def put_namespace(self, record: NamespaceRecord) -> None:
        key = self._namespace_key(record.name)
        if self._db.get(key) is not None:
            raise NamespaceAlreadyExistsError(
                f"Namespace already exists: {record.name}"
            )
        payload = json.dumps({"properties": dict(record.properties)}).encode("utf-8")
        self._db.put(key, payload)

    def delete_namespace(self, name: str) -> None:
        key = self._namespace_key(name)
        if self._db.get(key) is None:
            raise NoSuchNamespaceError(f"Namespace does not exist: {name}")
        if any(rec.namespace == name for rec in self._iter_table_records()):
            raise NamespaceNotEmptyError(f"Namespace {name} is not empty.")
        self._db.delete(key)

    def iter_namespaces(self) -> Iterable[NamespaceRecord]:
        return self._iter_namespace_records()

    def update_namespace_properties(
        self,
        name: str,
        *,
        removals: Optional[Iterable[str]],
        updates: Dict[str, str],
    ) -> Dict[str, str]:
        key = self._namespace_key(name)
        raw = self._db.get(key)
        if raw is None:
            raise NoSuchNamespaceError(f"Namespace does not exist: {name}")
        payload = json.loads(raw.decode("utf-8"))
        props = payload.setdefault("properties", {})
        if removals:
            for item in removals:
                props.pop(item, None)
        if updates:
            props.update(updates)
        payload["properties"] = props
        self._db.put(key, json.dumps(payload).encode("utf-8"))
        return dict(props)

    def load_table(self, identifier: str) -> Optional[TableRecord]:
        payload = self._load_table(identifier)
        if payload is None:
            return None
        namespace, table_name = identifier.rsplit(".", 1)
        return TableRecord(
            namespace=namespace,
            name=table_name,
            metadata_location=payload["metadata_location"],
            previous_metadata_location=payload.get("previous_metadata_location"),
        )

    def put_table(
        self,
        record: TableRecord,
        *,
        expect_metadata_location: Optional[str],
    ) -> None:
        identifier = f"{record.namespace}.{record.name}"
        key = self._table_key(identifier)
        current = self._load_table(identifier)

        if current is not None and expect_metadata_location is None:
            raise TableAlreadyExistsError(
                f"Table {record.namespace}.{record.name} already exists"
            )

        if expect_metadata_location is not None:
            if current is None or current.get("metadata_location") != expect_metadata_location:
                raise ConcurrentUpdateError(
                    f"Table {record.namespace}.{record.name} metadata mismatch"
                )

        payload = {
            "metadata_location": record.metadata_location,
            "previous_metadata_location": record.previous_metadata_location,
        }
        self._db.put(key, json.dumps(payload).encode("utf-8"))

    def delete_table(self, identifier: str) -> None:
        key = self._table_key(identifier)
        if self._db.get(key) is None:
            raise NoSuchTableError(f"Table does not exist: {identifier}")
        self._db.delete(key)

    def iter_tables(self) -> Iterable[TableRecord]:
        return self._iter_table_records()

    def rename_table(
        self,
        source_identifier: str,
        destination: TableRecord,
        *,
        expect_metadata_location: Optional[str],
    ) -> None:
        source_payload = self._load_table(source_identifier)
        if source_payload is None:
            raise NoSuchTableError(f"Table does not exist: {source_identifier}")

        if expect_metadata_location is not None and source_payload.get("metadata_location") != expect_metadata_location:
            raise ConcurrentUpdateError(
                f"Table {source_identifier} metadata mismatch"
            )

        destination_identifier = f"{destination.namespace}.{destination.name}"
        dest_payload = self._load_table(destination_identifier)
        if (
            dest_payload is not None
            and destination_identifier != source_identifier
        ):
            raise TableAlreadyExistsError(
                f"Table {destination.namespace}.{destination.name} already exists"
            )

        payload = {
            "metadata_location": destination.metadata_location,
            "previous_metadata_location": destination.previous_metadata_location,
        }
        self._db.put(
            self._table_key(destination_identifier),
            json.dumps(payload).encode("utf-8"),
        )

        if destination_identifier != source_identifier:
            self._db.delete(self._table_key(source_identifier))
