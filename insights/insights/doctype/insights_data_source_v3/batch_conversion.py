"""Preserve MariaDB TIME values when converting pandas import batches to Arrow."""

from datetime import time, timedelta

import ibis
import pandas as pd
import pyarrow as pa


def time_nanoseconds(value, column):
    if pd.isna(value):
        return None
    if isinstance(value, time):
        return (
            (value.hour * 3600 + value.minute * 60 + value.second) * 1_000_000_000
            + value.microsecond * 1000
        )
    if isinstance(value, (timedelta, pd.Timedelta)):
        nanoseconds = pd.Timedelta(value).value
        if 0 <= nanoseconds < 86_400_000_000_000:
            return nanoseconds
        raise ValueError(
            f"Column {column}: MariaDB TIME duration is outside the warehouse time-of-day "
            "range [00:00:00, 24:00:00). It cannot be imported without changing its meaning."
        )
    raise TypeError(f"Column {column}: expected a time-of-day or duration, got {type(value).__name__}.")


def batch_memtable(data, schema):
    if not isinstance(data, pd.DataFrame):
        return ibis.memtable(data)
    if not any(dtype.is_time() for dtype in schema.types):
        return ibis.memtable(data, schema=schema)
    arrow_schema = schema.to_pyarrow()
    arrays = []
    for field in arrow_schema:
        column = data[field.name]
        if schema[field.name].is_time():
            values = [time_nanoseconds(value, field.name) for value in column]
            array = pa.array(values, type=pa.int64()).cast(pa.time64("ns"))
            arrays.append(array.cast(field.type, safe=True))
        else:
            arrays.append(pa.array(column, type=field.type, from_pandas=True, safe=True))
    return ibis.memtable(pa.Table.from_arrays(arrays, schema=arrow_schema))
