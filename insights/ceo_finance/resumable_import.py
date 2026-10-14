"""Complete-history registration imports with durable, atomic batch checkpoints."""

import hashlib
from contextlib import contextmanager

import frappe

import insights
from insights.ceo_finance.api import TABLE_COLUMNS, database_identity, source_doc
from insights.insights.doctype.insights_data_source_v3.batch_conversion import batch_memtable
from insights.insights.doctype.insights_data_source_v3.insights_data_source_v3 import db_connections
from insights.insights.doctype.insights_table_v3.insights_table_v3 import get_table_name

BATCH_SIZE = 5000
CHECKPOINT_TABLE = "_checkpoint"


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def stage_schema(registration):
    return "ceo_registration_" + hashlib.sha256(registration.encode()).hexdigest()[:24]


@contextmanager
def transaction(db):
    db.raw_sql("BEGIN TRANSACTION")
    try:
        yield
        db.raw_sql("COMMIT")
    except BaseException:
        db.raw_sql("ROLLBACK")
        raise


def checkpoint(db, schema, table):
    row = db.con.execute(
        f"SELECT upper_key, last_key, rows_imported, complete, source_identity, signature, "
        "target_schema, staged_table, published "
        f"FROM {identifier(schema)}.{identifier(CHECKPOINT_TABLE)} WHERE table_name = ?",
        [table],
    ).fetchone()
    if row is None:
        return None
    return frappe._dict(
        zip(
            (
                "upper_key",
                "last_key",
                "rows_imported",
                "complete",
                "source_identity",
                "signature",
                "target_schema",
                "staged_table",
                "published",
            ),
            row,
            strict=True,
        )
    )


def ensure_stage(db, schema):
    db.raw_sql(f"CREATE SCHEMA IF NOT EXISTS {identifier(schema)}")
    db.raw_sql(
        f"CREATE TABLE IF NOT EXISTS {identifier(schema)}.{identifier(CHECKPOINT_TABLE)} ("
        "table_name VARCHAR PRIMARY KEY, upper_key VARCHAR, last_key VARCHAR, "
        "rows_imported BIGINT NOT NULL, complete BOOLEAN NOT NULL, "
        "source_identity VARCHAR NOT NULL, signature VARCHAR NOT NULL, "
        "target_schema VARCHAR NOT NULL, staged_table VARCHAR NOT NULL, published BOOLEAN NOT NULL)"
    )


def append_batch(
    db,
    schema,
    table,
    data,
    remote_schema,
    identity,
    upper_key,
    expected_last_key,
    target_schema=None,
):
    """The data and cursor commit together, so replay after SIGTERM cannot duplicate rows."""
    with transaction(db):
        state = checkpoint(db, schema, table)
        if state and state.last_key != expected_last_key:
            raise RuntimeError("Financial import checkpoint changed; another batch is running.")
        destination = target_schema or schema
        staged_table = f"_{schema}_{frappe.scrub(table)}" if target_schema else table
        db.raw_sql(f"CREATE SCHEMA IF NOT EXISTS {identifier(destination)}")
        if len(data):
            incoming = batch_memtable(data, remote_schema)
            if state:
                db.insert(staged_table, incoming, database=destination)
            else:
                db.create_table(staged_table, incoming, schema=remote_schema, database=destination)
            last_key = str(data["name"].iloc[-1])
        else:
            last_key = expected_last_key
            if not state:
                db.create_table(staged_table, schema=remote_schema, database=destination)
        count = (state.rows_imported if state else 0) + len(data)
        db.con.execute(
            f"INSERT OR REPLACE INTO {identifier(schema)}.{identifier(CHECKPOINT_TABLE)} "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                table,
                upper_key,
                last_key,
                count,
                len(data) < BATCH_SIZE,
                identity,
                str(remote_schema),
                destination,
                staged_table,
                False,
            ],
        )
    return count


def import_next_batch(record):
    """Import at most one batch per queue job; no row limit or reporting-date cutoff."""
    schema = stage_schema(record.name)
    from insights.insights.doctype.insights_data_source_v3.data_warehouse import get_warehouse_schema_name

    with insights.warehouse.get_write_connection(timeout=10) as db:
        ensure_stage(db, schema)
        pending = next(
            (table for table in TABLE_COLUMNS if not (checkpoint(db, schema, table) or {}).get("complete")),
            None,
        )
        state = checkpoint(db, schema, pending) if pending else None
    if pending is None:
        publish(record, schema)
        return True

    record.db_set(
        "message",
        f"Importing {pending}: {state.rows_imported if state else 0:,} rows saved. Retry resumes.",
        commit=True,
    )
    table_doc = frappe.get_doc("Insights Table v3", get_table_name(record.data_source, pending))
    if table_doc.row_limit or table_doc.before_import_script:
        frappe.throw(
            f"Remove row limits and import scripts from {pending} before registering financial data."
        )
    with db_connections():
        source = source_doc(record.data_source)
        identity = database_identity(source)
        if identity != record.database_identity:
            frappe.throw("The source database changed. Restore its original connection before resuming.")
        remote = source.get_ibis_table(pending)
        remote_schema = remote.schema()
        if "name" not in remote.columns:
            frappe.throw(f"{pending} requires the ERPNext name primary key for safe resumable imports.")
        if state and (state.source_identity != identity or state.signature != str(remote_schema)):
            frappe.throw(
                "The database or accounting schema changed during import. Restore it before resuming."
            )
        upper_key = state.upper_key if state else remote.name.max().execute()
        batch = remote
        if upper_key is not None:
            batch = batch.filter(remote.name <= upper_key)
        if state and state.last_key is not None:
            batch = batch.filter(remote.name > state.last_key)
        data = batch.order_by("name").limit(BATCH_SIZE).execute()
    if data["name"].isna().any() or data["name"].duplicated().any():
        frappe.throw(f"{pending} contains invalid or duplicate primary keys.")
    with insights.warehouse.get_write_connection(timeout=10) as db:
        count = append_batch(
            db,
            schema,
            pending,
            data,
            remote_schema,
            identity,
            upper_key,
            state.last_key if state else None,
            target_schema=get_warehouse_schema_name(record.data_source),
        )
    record.db_set("message", f"Importing {pending}: {count:,} rows saved. Retry resumes.", commit=True)
    return False


def publish(record, schema):
    """Expose all three complete tables together, never an interrupted staging dataset."""
    from insights.insights.doctype.insights_data_source_v3.data_warehouse import WarehouseTable

    with insights.warehouse.get_write_connection(timeout=10) as db:
        states = {table: checkpoint(db, schema, table) for table in TABLE_COLUMNS}
        if any(not state or not state.complete for state in states.values()):
            raise RuntimeError("Cannot publish an incomplete financial import.")
        with transaction(db):
            for table, state in states.items():
                if state.source_identity != record.database_identity:
                    raise RuntimeError("The source identity changed before financial import publication.")
                if state.published:
                    continue
                staged = f"{identifier(state.target_schema)}.{identifier(state.staged_table)}"
                rows, unique_rows = db.con.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT name) FROM {staged}"
                ).fetchone()
                if rows != state.rows_imported or rows != unique_rows:
                    raise RuntimeError(f"Financial import row verification failed for {table}.")
                target = WarehouseTable(record.data_source, table)
                if target.schema != state.target_schema:
                    raise RuntimeError("Financial import target schema changed.")
                db.raw_sql(
                    f"DROP TABLE IF EXISTS {identifier(target.schema)}."
                    f"{identifier(target.warehouse_table_name)}"
                )
                db.raw_sql(f"ALTER TABLE {staged} RENAME TO {identifier(target.warehouse_table_name)}")
                db.con.execute(
                    f"UPDATE {identifier(schema)}.{identifier(CHECKPOINT_TABLE)} "
                    "SET published = TRUE WHERE table_name = ?",
                    [table],
                )
    for table in TABLE_COLUMNS:
        doc = frappe.get_doc("Insights Table v3", get_table_name(record.data_source, table))
        doc.stored = 1
        doc.sync_mode = "Full"
        doc.last_synced_on = frappe.utils.now()
        doc.save(ignore_permissions=True)
    frappe.db.commit()


def cleanup_registration(registration):
    record = frappe.get_doc("CEO Company Source", registration)
    if record.status not in ("Registered", "Archived"):
        frappe.throw("Cannot remove checkpoints for an unfinished registration.")
    with insights.warehouse.get_write_connection(timeout=10) as db:
        db.raw_sql(f"DROP SCHEMA IF EXISTS {identifier(stage_schema(record.name))} CASCADE")
