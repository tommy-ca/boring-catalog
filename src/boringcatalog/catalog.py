from typing import Dict, List, Optional, Set, Tuple, Union, Any
import json
import os
import logging
from pyiceberg.io import load_file_io
from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.serializers import FromInputFile
from pyiceberg.table import CommitTableResponse, Table
from pyiceberg.table.locations import load_location_provider
from pyiceberg.table.metadata import new_table_metadata
from pyiceberg.table.sorting import UNSORTED_SORT_ORDER, SortOrder
from pyiceberg.table.update import TableRequirement, TableUpdate
from pyiceberg.typedef import EMPTY_DICT, Identifier, Properties
from pyiceberg.catalog import (
    Catalog,
    MetastoreCatalog,
    METADATA_LOCATION,
    PREVIOUS_METADATA_LOCATION,
    TABLE_TYPE,
    ICEBERG,
    PropertiesUpdateSummary,
)
from pyiceberg.exceptions import (
    NamespaceAlreadyExistsError,
    NamespaceNotEmptyError,
    NoSuchNamespaceError,
    NoSuchTableError,
    TableAlreadyExistsError,
    NoSuchPropertyException,
    NoSuchIcebergTableError,
    CommitFailedException,
)

from .storage import (
    CatalogStorage,
    JsonCatalogStorage,
    NamespaceRecord,
    TableRecord,
    ConcurrentUpdateError,
)

try:  # SlateDB backend is optional
    from .storage import SlateDbCatalogStorage
except ImportError:  # pragma: no cover - optional dependency
    SlateDbCatalogStorage = None


# Set up logging
logger = logging.getLogger(__name__)

DEFAULT_INIT_CATALOG_TABLES = "true"
DEFAULT_CATALOG_NAME = "boring"
class ConcurrentModificationError(CommitFailedException):
    """Raised when a concurrent modification is detected."""
    pass

class BoringCatalog(MetastoreCatalog):
    """A simple file-based Iceberg catalog implementation."""
    
    def __init__(self, name: str = None, **properties: str):
        index_path = os.path.join(os.getcwd(), ".ice/index")
        index: Optional[Dict[str, Any]] = None
        if (name is None or not properties) and os.path.exists(index_path):
            with open(index_path, "r") as f:
                index = json.load(f)
            if name is None:
                name = index.get("catalog_name", DEFAULT_CATALOG_NAME)
            if not properties:
                properties = index.get("properties", {}).copy()

        if name is None:
            name = DEFAULT_CATALOG_NAME

        properties = properties.copy()
        if index is not None:
            if index.get("catalog_uri") and "uri" not in properties:
                properties["uri"] = index["catalog_uri"]
            if "storage_backend" in index and "storage_backend" not in properties:
                properties["storage_backend"] = index["storage_backend"]
            for key, value in (index.get("storage_config") or {}).items():
                properties.setdefault(key, value)

        storage_backend = properties.pop("storage_backend", "json").lower()
        storage_config = {
            key: properties.pop(key)
            for key in list(properties.keys())
            if key.startswith("slatedb_")
        }

        file_io_properties = {
            key: value
            for key, value in properties.items()
            if key not in {"storage_backend"}
        }

        super().__init__(name, **file_io_properties)
        self.properties.update(properties)
        self.properties.update(storage_config)
        self.properties.setdefault("storage_backend", storage_backend)
        self._storage_backend = storage_backend
        self._storage_config = storage_config
        self._file_io_properties = dict(file_io_properties)

        self.uri = self._determine_catalog_identity(name, index)

        if (
            self._storage_backend == "json"
            and self.uri
            and not self.properties.get("warehouse")
        ):
            warehouse_path = os.path.dirname(self.uri)
            self.properties["warehouse"] = warehouse_path
            logging.info(
                "No --warehouse specified for the catalog. Using catalog folder to store iceberg data: %s",
                warehouse_path,
            )

        self._storage = self._build_storage(name)

    @property
    def storage_backend(self) -> str:
        return self._storage_backend

    def close(self) -> None:
        if hasattr(self, "_storage"):
            self._storage.close()

    def _determine_catalog_identity(
        self, catalog_name: str, index: Optional[Dict[str, Any]]
    ) -> str:
        if self._storage_backend == "json":
            if index and index.get("catalog_uri"):
                return index["catalog_uri"]
            if self.properties.get("uri"):
                return self.properties["uri"]
            warehouse = self.properties.get("warehouse")
            if warehouse:
                return os.path.join(
                    os.path.join(warehouse, "catalog"),
                    f"catalog_{catalog_name}.json",
                )
            raise ValueError(
                "Either provide 'catalog' or 'warehouse' property to initialize BoringCatalog"
            )

        if self._storage_backend == "slatedb":
            identity = (
                self._storage_config.get("slatedb_url")
                or self._storage_config.get("slatedb_path")
                or self.properties.get("uri")
            )
            if identity:
                return identity
            raise ValueError(
                "SlateDB backend requires 'slatedb_path' or 'slatedb_url' to initialize BoringCatalog"
            )

        raise ValueError(f"Unsupported storage backend: {self._storage_backend}")

    def _build_storage(self, catalog_name: str) -> CatalogStorage:
        if self._storage_backend == "json":

            def io_factory(*, location: str):
                return load_file_io(
                    properties=self._file_io_properties,
                    location=location,
                )

            return JsonCatalogStorage(
                uri=self.uri,
                catalog_name=catalog_name,
                io_factory=io_factory,
            )

        if self._storage_backend == "slatedb":
            if SlateDbCatalogStorage is None:
                raise ImportError(
                    "SlateDB backend requested but 'slatedb' package is not installed"
                )
            return SlateDbCatalogStorage(
                catalog_name=catalog_name,
                path=self._storage_config.get("slatedb_path"),
                url=self._storage_config.get("slatedb_url"),
                env_file=self._storage_config.get("slatedb_env_file"),
            )

        raise ValueError(f"Unsupported storage backend: {self._storage_backend}")

    @property
    def catalog(self):
        return self._storage.dump_catalog()

    def _table_key(self, namespace: str, table_name: str) -> str:
        return f"{namespace}.{table_name}"

    def _file_io(self, location: str):
        return load_file_io(properties=self._file_io_properties, location=location)
    
    def create_table(
        self,
        identifier: Union[str, Identifier],
        schema: Union[Schema, "pa.Schema"],
        location: Optional[str] = None,
        partition_spec: PartitionSpec = UNPARTITIONED_PARTITION_SPEC,
        sort_order: SortOrder = UNSORTED_SORT_ORDER,
        properties: Properties = EMPTY_DICT,
    ) -> Table:
        """Create an Iceberg table."""
        schema = self._convert_schema_if_needed(schema)  # type: ignore
        namespace_tuple = Catalog.namespace_from(identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(identifier)

        if not self._namespace_exists(namespace):
            raise NoSuchNamespaceError(f"Namespace does not exist: {namespace}")

        location = self._resolve_table_location(location, namespace, table_name)
        location_provider = load_location_provider(
            table_location=location, table_properties=properties
        )
        metadata_location = location_provider.new_table_metadata_file_location()

        metadata = new_table_metadata(
            location=location,
            schema=schema,
            partition_spec=partition_spec,
            sort_order=sort_order,
            properties=properties,
        )
        io = self._file_io(metadata_location)
        self._write_metadata(metadata, io, metadata_location)

        record = TableRecord(
            namespace=namespace,
            name=table_name,
            metadata_location=metadata_location,
            previous_metadata_location=None,
        )
        self._storage.put_table(record, expect_metadata_location=None)

        return self.load_table(identifier)

    def load_table(self, identifier: Union[str, Identifier], catalog_name: str = None) -> Table:
        """Load the table's metadata and return the table instance."""
        namespace_tuple = Catalog.namespace_from(identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(identifier)
        table_key = self._table_key(namespace, table_name)
        record = self._storage.load_table(table_key)
        if not record:
            raise NoSuchTableError(f"Table does not exist: {namespace}.{table_name}")
        metadata_location = record.metadata_location
        io = self._file_io(metadata_location)
        file = io.new_input(metadata_location)
        metadata = FromInputFile.table_metadata(file)
        return Table(
            identifier=Catalog.identifier_to_tuple(namespace) + (table_name,),
            metadata=metadata,
            metadata_location=metadata_location,
            io=self._load_file_io(metadata.properties, metadata_location),
            catalog=self
        )

    def drop_table(self, identifier: Union[str, Identifier]) -> None:
        """Drop a table."""
        namespace_tuple = Catalog.namespace_from(identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(identifier)
        table_key = self._table_key(namespace, table_name)
        self._storage.delete_table(table_key)

    def rename_table(self, from_identifier: Union[str, Identifier], to_identifier: Union[str, Identifier]) -> Table:
        """Rename a table."""
        from_namespace_tuple = Catalog.namespace_from(from_identifier)
        from_namespace = Catalog.namespace_to_string(from_namespace_tuple)
        from_table_name = Catalog.table_name_from(from_identifier)
        from_table_key = self._table_key(from_namespace, from_table_name)

        to_namespace_tuple = Catalog.namespace_from(to_identifier)
        to_namespace = Catalog.namespace_to_string(to_namespace_tuple)
        to_table_name = Catalog.table_name_from(to_identifier)

        if not self._namespace_exists(to_namespace):
            raise NoSuchNamespaceError(f"Namespace does not exist: {to_namespace}")

        existing = self._storage.load_table(from_table_key)
        if existing is None:
            raise NoSuchTableError(f"Table does not exist: {from_namespace}.{from_table_name}")

        destination = TableRecord(
            namespace=to_namespace,
            name=to_table_name,
            metadata_location=existing.metadata_location,
            previous_metadata_location=existing.previous_metadata_location,
        )
        try:
            self._storage.rename_table(
                from_table_key,
                destination,
                expect_metadata_location=existing.metadata_location,
            )
        except ConcurrentUpdateError as exc:  # pragma: no cover - depends on race conditions
            raise ConcurrentModificationError(str(exc)) from exc

        return self.load_table(to_identifier)

    def create_namespace(self, namespace: Union[str, Identifier], properties: Properties = EMPTY_DICT) -> None:
        """Create a namespace."""
        namespace_str = Catalog.namespace_to_string(namespace)
        record = NamespaceRecord(
            name=namespace_str,
            properties=dict(properties) if properties else {"exists": "true"},
        )
        self._storage.put_namespace(record)

    def drop_namespace(self, namespace: Union[str, Identifier]) -> None:
        """Drop a namespace."""
        namespace_str = Catalog.namespace_to_string(namespace)
        self._storage.delete_namespace(namespace_str)

    def list_tables(self, namespace: Union[str, Identifier]) -> List[Identifier]:
        """List tables under the given namespace."""
        namespace_str = Catalog.namespace_to_string(namespace)
        if namespace_str and not self._namespace_exists(namespace_str):
            raise NoSuchNamespaceError(f"Namespace does not exist: {namespace_str}")
        result: List[Identifier] = []
        for record in self._storage.iter_tables():
            if namespace_str and record.namespace != namespace_str:
                continue
            result.append(
                Catalog.identifier_to_tuple(record.namespace) + (record.name,)
            )
        return result

    def list_namespaces(self, namespace: Union[str, Identifier] = ()) -> List[Identifier]:
        """List namespaces."""
        all_namespaces = [record.name for record in self._storage.iter_namespaces()]
        if not namespace:
            return [Catalog.identifier_to_tuple(ns) for ns in all_namespaces]
        ns_tuple = Catalog.identifier_to_tuple(namespace)
        result: List[Identifier] = []
        for ns in all_namespaces:
            ns_parts = Catalog.identifier_to_tuple(ns)
            if ns_parts[: len(ns_tuple)] == ns_tuple and len(ns_parts) == len(ns_tuple) + 1:
                result.append(ns_parts)
        return result

    def load_namespace_properties(self, namespace: Union[str, Identifier]) -> Properties:
        """Get properties for a namespace."""
        namespace_str = Catalog.namespace_to_string(namespace)
        record = self._storage.load_namespace(namespace_str)
        if record is None:
            raise NoSuchNamespaceError(f"Namespace {namespace_str} does not exist")
        return dict(record.properties)

    def _namespace_exists(self, namespace: Union[str, Identifier]) -> bool:
        namespace_str = Catalog.namespace_to_string(namespace)
        return self._storage.load_namespace(namespace_str) is not None

    def _table_exists(self, identifier: Union[str, Identifier]) -> bool:
        namespace_tuple = Catalog.namespace_from(identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(identifier)
        table_key = self._table_key(namespace, table_name)
        return self._storage.load_table(table_key) is not None

    def list_views(self, namespace: Union[str, Identifier]) -> List[Identifier]:
        return []

    def drop_view(self, identifier: Union[str, Identifier]) -> None:
        raise NotImplementedError("Views are not supported")

    def view_exists(self, identifier: Union[str, Identifier]) -> bool:
        return False

    def commit_table(
        self, table: Table, requirements: Tuple[TableRequirement, ...], updates: Tuple[TableUpdate, ...]
    ) -> CommitTableResponse:
        """Commit updates to a table."""
        table_identifier = table.name()
        namespace_tuple = Catalog.namespace_from(table_identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(table_identifier)

        try:
            current_table = self.load_table(table_identifier)
        except NoSuchTableError:
            current_table = None

        updated_staged_table = self._update_and_stage_table(
            current_table, table.name(), requirements, updates
        )
        if current_table and updated_staged_table.metadata == current_table.metadata:
            return CommitTableResponse(
                metadata=current_table.metadata,
                metadata_location=current_table.metadata_location,
            )

        self._write_metadata(
            metadata=updated_staged_table.metadata,
            io=updated_staged_table.io,
            metadata_path=updated_staged_table.metadata_location,
        )

        record = TableRecord(
            namespace=namespace,
            name=table_name,
            metadata_location=updated_staged_table.metadata_location,
            previous_metadata_location=current_table.metadata_location if current_table else None,
        )
        expected_metadata = (
            current_table.metadata_location if current_table else None
        )

        try:
            self._storage.put_table(
                record, expect_metadata_location=expected_metadata
            )
        except TableAlreadyExistsError:
            # Re-raise for clarity when attempting to commit without existing table
            raise
        except ConcurrentUpdateError as exc:  # pragma: no cover - depends on race conditions
            try:
                updated_staged_table.io.delete(
                    updated_staged_table.metadata_location
                )
            except Exception:  # pragma: no cover - best effort cleanup
                pass
            raise ConcurrentModificationError(str(exc)) from exc
        except Exception as exc:
            try:
                updated_staged_table.io.delete(
                    updated_staged_table.metadata_location
                )
            except Exception:  # pragma: no cover
                pass
            raise exc

        return CommitTableResponse(
            metadata=updated_staged_table.metadata,
            metadata_location=updated_staged_table.metadata_location,
        )

    def register_table(self, identifier: Union[str, Identifier], metadata_location: str) -> Table:
        """Register a new table using existing metadata."""
        namespace_tuple = Catalog.namespace_from(identifier)
        namespace = Catalog.namespace_to_string(namespace_tuple)
        table_name = Catalog.table_name_from(identifier)

        if not self._namespace_exists(namespace):
            raise NoSuchNamespaceError(f"Namespace does not exist: {namespace}")

        record = TableRecord(
            namespace=namespace,
            name=table_name,
            metadata_location=metadata_location,
            previous_metadata_location=None,
        )
        self._storage.put_table(record, expect_metadata_location=None)

        return self.load_table(identifier)

    def update_namespace_properties(
        self, namespace: Union[str, Identifier], removals: Optional[Set[str]] = None, updates: Properties = EMPTY_DICT
    ) -> PropertiesUpdateSummary:
        """Remove provided property keys and update properties for a namespace."""
        namespace_str = Catalog.namespace_to_string(namespace)
        updates_dict = dict(updates) if updates else {}
        updated_properties = self._storage.update_namespace_properties(
            namespace_str,
            removals=removals,
            updates=updates_dict,
        )
        # Placeholder summary until richer reporting is needed.
        return PropertiesUpdateSummary()
