from datetime import time, timedelta
from unittest import TestCase

import ibis
import pandas as pd
import pyarrow as pa

from insights.insights.doctype.insights_data_source_v3.batch_conversion import batch_memtable
from insights.insights.doctype.insights_data_source_v3.data_warehouse import WarehouseTableWriter


class TestBatchConversion(TestCase):
    def test_duration_time_and_null_values_preserve_arrow_schema(self):
        schema = ibis.schema({"name": "string", "start_time": "time", "duration": "interval('ns')"})
        data = pd.DataFrame(
            {
                "name": ["a", "b", "c", "d"],
                "start_time": [time(0), timedelta(hours=9, microseconds=123456), pd.NaT, None],
                "duration": pd.to_timedelta(["25:00:00", "-01:00:00", None, "00:00:01"]),
            }
        )
        batch = batch_memtable(data, schema)
        actual = batch.op().data.to_pyarrow(batch.schema())
        self.assertEqual(actual.schema, schema.to_pyarrow())
        self.assertEqual(
            actual["start_time"].to_pylist(),
            [
                time(0),
                time(9, 0, 0, 123456),
                None,
                None,
            ],
        )
        self.assertEqual(
            actual["duration"].cast(pa.int64()).to_pylist(),
            [
                90_000_000_000_000,
                -3_600_000_000_000,
                None,
                1_000_000_000,
            ],
        )
        self.assertIsInstance(data["start_time"].iloc[1], timedelta)

    def test_submicrosecond_time_is_preserved_in_arrow(self):
        schema = ibis.schema({"start_time": "time"})
        data = pd.DataFrame({"start_time": pd.to_timedelta(["00:00:00.000000123", None])})
        batch = batch_memtable(data, schema)
        actual = batch.op().data.to_pyarrow(batch.schema())
        self.assertEqual(actual["start_time"].cast(pa.int64()).to_pylist(), [123, None])

    def test_all_null_time_column(self):
        data = pd.DataFrame({"start_time": pd.to_timedelta([None, None])})
        actual = batch_memtable(data, ibis.schema({"start_time": "time"})).to_pyarrow()
        self.assertEqual(actual["start_time"].null_count, 2)

    def test_normal_refresh_writer_uses_same_conversion(self):
        schema = ibis.schema({"name": "string", "start_time": "time"})
        data = pd.DataFrame(
            {
                "name": ["a", "b"],
                "start_time": pd.to_timedelta(["12:30:00.123456", None]),
            }
        )
        writer = WarehouseTableWriter("test", schema)
        try:
            writer.__enter__()
            batch = writer.insert(data)
            self.assertEqual(batch.execute()["start_time"].tolist(), [time(12, 30, 0, 123456), None])
        finally:
            writer.rollback()

    def test_non_time_columns_keep_original_types(self):
        schema = ibis.schema({"name": "string", "amount": "decimal(12, 2)", "posted": "date"})
        from datetime import date
        from decimal import Decimal

        data = pd.DataFrame({"name": ["a"], "amount": [Decimal("123.45")], "posted": [date(2026, 10, 14)]})
        actual = batch_memtable(data, schema).to_pyarrow()
        self.assertEqual(actual.schema, schema.to_pyarrow())
        self.assertEqual(actual.to_pylist()[0], data.iloc[0].to_dict())
