"""Read the migrated ``pitwall`` schema back from Postgres' own catalogs.

Migration tests assert what the database *is* after the full ledger has been applied
(``information_schema``, ``pg_constraint``, ``pg_index``, ``pg_trigger``), never a substring of
the SQL file that produced it, so a reformatted or reordered migration cannot fail them and a
migration that did not do what its text says cannot pass them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import asyncpg

from pitwall.migrations import MigrationRecord, discover_migrations
from tests._migration_ledger import MIGRATION_DIR

SCHEMA = "pitwall"


def squash(definition: str | None) -> str:
    """Collapse Postgres' normalized expression text to compare it without formatting.

    Whitespace, parentheses and ``::type`` casts are dropped and the result lower-cased, so
    ``CHECK (((a IS NULL) OR (a >= 5)))`` and ``a is null or a>=5`` squash alike.
    """
    text = re.sub(r"::[a-zA-Z_]+(\[\])?", "", definition or "")
    return re.sub(r"[\s()]", "", text).lower()


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    udt_name: str
    nullable: bool
    default: str | None
    precision: int | None
    scale: int | None

    @property
    def array_of(self) -> str | None:
        """Element type of an array column (``int4`` for ``INTEGER[]``), else ``None``."""
        return self.udt_name.removeprefix("_") if self.data_type == "ARRAY" else None


@dataclass(frozen=True)
class Constraint:
    name: str
    kind: str  # "check", "foreign key", "primary key", "unique", or "exclusion"
    definition: str
    columns: tuple[str, ...]
    on_delete: str | None = None

    @property
    def squashed(self) -> str:
        return squash(self.definition)

    def contains(self, fragment: str) -> bool:
        """Whether the definition holds *fragment*, ignoring spacing, parentheses, and casts."""
        return squash(fragment) in self.squashed

    @property
    def literals(self) -> list[str]:
        """The quoted text literals of a check (``IN``/``= ANY (ARRAY[...])``), in order."""
        return re.findall(r"'((?:[^']|'')*)'::text", self.definition)


@dataclass(frozen=True)
class Index:
    name: str
    table: str
    unique: bool
    columns: tuple[str, ...]
    predicate: str | None
    access_method: str
    descending: tuple[bool, ...] = ()


_KIND = {"c": "check", "f": "foreign key", "p": "primary key", "u": "unique", "x": "exclusion"}
_ON_DELETE = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


class Catalog:
    """Catalog queries over one open connection to the fully migrated test database."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    async def tables(self) -> set[str]:
        rows = await self.connection.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_type = 'BASE TABLE'",
            SCHEMA,
        )
        return {row["table_name"] for row in rows}

    async def columns(self, table: str) -> dict[str, Column]:
        rows = await self.connection.fetch(
            "SELECT column_name, data_type, udt_name, is_nullable, column_default, "
            "numeric_precision, numeric_scale FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position",
            SCHEMA,
            table,
        )
        return {
            row["column_name"]: Column(
                name=row["column_name"],
                data_type=row["data_type"],
                udt_name=row["udt_name"],
                nullable=row["is_nullable"] == "YES",
                default=row["column_default"],
                precision=row["numeric_precision"],
                scale=row["numeric_scale"],
            )
            for row in rows
        }

    async def column(self, table: str, name: str) -> Column:
        columns = await self.columns(table)
        assert name in columns, f"{SCHEMA}.{table} has no column {name!r}: {sorted(columns)}"
        return columns[name]

    async def constraints(self, table: str) -> dict[str, Constraint]:
        rows = await self.connection.fetch(
            "SELECT c.conname, c.contype::text AS contype, pg_get_constraintdef(c.oid) AS definition, "
            "c.confdeltype::text AS confdeltype, "
            "ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
            "      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
            "      ORDER BY k.ord) AS columns "
            "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = $1 AND t.relname = $2",
            SCHEMA,
            table,
        )
        return {
            row["conname"]: Constraint(
                name=row["conname"],
                kind=_KIND.get(row["contype"], row["contype"]),
                definition=row["definition"],
                columns=tuple(row["columns"]),
                on_delete=_ON_DELETE.get(row["confdeltype"]) if row["contype"] == "f" else None,
            )
            for row in rows
        }

    async def constraint(self, table: str, name: str) -> Constraint:
        constraints = await self.constraints(table)
        assert name in constraints, (
            f"{SCHEMA}.{table} has no constraint {name!r}: {sorted(constraints)}"
        )
        return constraints[name]

    async def indexes(self, table: str) -> dict[str, Index]:
        rows = await self.connection.fetch(
            "SELECT i.relname AS name, t.relname AS table_name, x.indisunique AS is_unique, "
            "am.amname AS method, pg_get_expr(x.indpred, x.indrelid) AS predicate, "
            "ARRAY(SELECT pg_get_indexdef(x.indexrelid, k.n, true) "
            "      FROM generate_series(1, x.indnkeyatts) AS k(n)) AS columns, "
            "ARRAY(SELECT (x.indoption[k.n - 1] & 1) = 1 "
            "      FROM generate_series(1, x.indnkeyatts) AS k(n)) AS descending "
            "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_class t ON t.oid = x.indrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "JOIN pg_am am ON am.oid = i.relam "
            "WHERE n.nspname = $1 AND t.relname = $2",
            SCHEMA,
            table,
        )
        return {
            row["name"]: Index(
                name=row["name"],
                table=row["table_name"],
                unique=row["is_unique"],
                columns=tuple(row["columns"]),
                predicate=row["predicate"],
                access_method=row["method"],
                descending=tuple(row["descending"]),
            )
            for row in rows
        }

    async def index(self, table: str, name: str) -> Index:
        indexes = await self.indexes(table)
        assert name in indexes, f"{SCHEMA}.{table} has no index {name!r}: {sorted(indexes)}"
        return indexes[name]

    async def triggers(self, table: str) -> dict[str, str]:
        """Non-internal trigger name -> its normalized ``CREATE TRIGGER`` definition."""
        rows = await self.connection.fetch(
            "SELECT tg.tgname, pg_get_triggerdef(tg.oid) AS definition FROM pg_trigger tg "
            "JOIN pg_class t ON t.oid = tg.tgrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = $1 AND t.relname = $2 AND NOT tg.tgisinternal",
            SCHEMA,
            table,
        )
        return {row["tgname"]: row["definition"] for row in rows}

    async def function_source(self, name: str) -> str:
        value: Any = await self.connection.fetchval(
            "SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = $1 AND p.proname = $2",
            SCHEMA,
            name,
        )
        assert value is not None, f"no function {SCHEMA}.{name}"
        return str(value)

    async def rejects(self, statement: str, *args: object) -> type[BaseException] | None:
        """Run *statement* in a rolled-back savepoint; return the integrity error it raised."""
        try:
            async with self.connection.transaction():
                await self.connection.execute(statement, *args)
                raise _Rollback
        except _Rollback:
            return None
        except asyncpg.IntegrityConstraintViolationError as error:
            return type(error)

    async def expect_columns(self, table: str, **spec: str) -> None:
        """Assert each named column has the declared type, nullability, and default.

        The declaration reads like the DDL it checks: ``"numeric(12,6) not null"``,
        ``"timestamptz default now()"``, ``"int4[] not null default ARRAY[]::integer[]"``. The type
        is Postgres' ``udt_name`` spelling (``text``, ``int4``, ``timestamptz``, ``jsonb``). A
        declaration without ``not null`` requires the column to be nullable; a default is compared
        after :func:`squash`. Extra columns on the table are not an error.
        """
        actual = await self.columns(table)
        problems: list[str] = []
        for name, declaration in spec.items():
            if name not in actual:
                problems.append(f"{name}: missing")
                continue
            problems.extend(_column_problems(actual[name], declaration))
        assert not problems, f"{SCHEMA}.{table}: " + "; ".join(problems)

    async def primary_key(self, table: str) -> tuple[str, ...]:
        keys = [c for c in (await self.constraints(table)).values() if c.kind == "primary key"]
        assert len(keys) == 1, f"{SCHEMA}.{table} should have exactly one primary key: {keys}"
        return keys[0].columns


_DECLARATION = re.compile(
    r"^(?P<type>\w+)(?:\((?P<precision>\d+),(?P<scale>\d+)\))?(?P<array>\[\])?"
    r"(?P<not_null> not null)?(?: default (?P<default>.+))?$",
    re.IGNORECASE,
)


def _column_problems(column: Column, declaration: str) -> list[str]:
    match = _DECLARATION.match(declaration.strip())
    assert match, f"unreadable column declaration {declaration!r}"
    problems: list[str] = []
    wanted_type = match["type"].lower()
    actual_type = column.array_of if match["array"] else column.udt_name
    if (column.data_type == "ARRAY") != bool(match["array"]) or actual_type != wanted_type:
        problems.append(f"{column.name}: type {column.udt_name} is not {declaration}")
    if match["precision"] and (column.precision, column.scale) != (
        int(match["precision"]),
        int(match["scale"]),
    ):
        problems.append(
            f"{column.name}: numeric({column.precision},{column.scale}) is not {declaration}"
        )
    if column.nullable == bool(match["not_null"]):
        problems.append(f"{column.name}: nullable={column.nullable} but declared {declaration!r}")
    if match["default"] and squash(column.default) != squash(match["default"]):
        problems.append(f"{column.name}: default {column.default!r} is not {match['default']!r}")
    return problems


class _Rollback(Exception):
    """Raised inside a savepoint so a statement that succeeded leaves no row behind."""


class MigrationSandbox:
    """Apply the migration ledger one record at a time inside a transaction the test rolls back.

    For migrations that change *data* (a backfill, a rewrite) the schema alone cannot show what
    they did: stop before the migration, insert rows, apply it, read the rows back.
    """

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection
        self._records: list[MigrationRecord] = discover_migrations(MIGRATION_DIR)
        self._applied = 0

    async def apply_through(self, version: str) -> None:
        """Apply every not-yet-applied record up to and including *version* (a ``0023_...`` stem)."""
        versions = [record.version for record in self._records]
        assert version in versions, f"no migration {version!r}"
        if self._applied == 0:
            await self.connection.execute("DROP SCHEMA IF EXISTS pitwall CASCADE")
        stop = versions.index(version) + 1
        for record in self._records[self._applied : stop]:
            await self.connection.execute((MIGRATION_DIR / record.filename).read_text())
        self._applied = max(self._applied, stop)

    async def apply_all(self) -> None:
        await self.apply_through(self._records[-1].version)

    async def reapply(self, version: str) -> None:
        """Run an already-applied record's SQL a second time."""
        record = next(r for r in self._records if r.version == version)
        await self.connection.execute((MIGRATION_DIR / record.filename).read_text())
