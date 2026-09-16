import time

import pytest

from publisher.timeseries_file import TimeSeriesFile


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


def test_loads_header_and_rows_sorted_by_timestamp(tmp_path):
    path = _write(
        tmp_path,
        "device.csv",
        "timestamp,Temp,Running,Mode\n"
        "200,25.5,true,run\n"
        "100,20.1,false,idle\n",
    )
    ts_file = TimeSeriesFile(path)
    assert ts_file.columns == ["Temp", "Running", "Mode"]
    assert ts_file.timestamps == [100, 200]
    assert len(ts_file) == 2


def test_infers_primitive_types_per_cell(tmp_path):
    path = _write(
        tmp_path,
        "device.csv",
        "timestamp,Count,Ratio,Active,Name\n"
        "100,7,3.14,true,idle\n",
    )
    ts_file = TimeSeriesFile(path)
    metrics = ts_file.get_metrics(0)
    assert metrics == {"Count": 7, "Ratio": 3.14, "Active": True, "Name": "idle"}
    assert isinstance(metrics["Count"], int)
    assert isinstance(metrics["Ratio"], float)
    assert isinstance(metrics["Active"], bool)


def test_sparse_cells_are_omitted_from_metrics(tmp_path):
    path = _write(
        tmp_path,
        "device.csv",
        "timestamp,A,B\n"
        "100,1,\n"
        "200,,2\n",
    )
    ts_file = TimeSeriesFile(path)
    assert ts_file.get_metrics(0) == {"A": 1}
    assert ts_file.get_metrics(1) == {"B": 2}


def test_find_index_for_ts_picks_last_row_at_or_before_target(tmp_path):
    path = _write(
        tmp_path,
        "device.csv",
        "timestamp,A\n100,1\n200,2\n500,3\n",
    )
    ts_file = TimeSeriesFile(path)
    assert ts_file.find_index_for_ts(50) == 0  # before first row clamps to 0
    assert ts_file.find_index_for_ts(100) == 0
    assert ts_file.find_index_for_ts(199) == 0
    assert ts_file.find_index_for_ts(200) == 1
    assert ts_file.find_index_for_ts(999) == 2  # past last row clamps to last


def test_find_index_nearest_to_now_matches_time_of_day(tmp_path):
    # Build a file spanning a full day in 1-hour steps, dated arbitrarily in the past.
    day_start = 1704067200  # 2024-01-01T00:00:00Z, in seconds
    lines = ["timestamp,Value"]
    for hour in range(24):
        ts_ms = (day_start + hour * 3600) * 1000
        lines.append(f"{ts_ms},{hour}")
    path = _write(tmp_path, "device.csv", "\n".join(lines) + "\n")
    ts_file = TimeSeriesFile(path)

    # "now" is today at 14:30 local time - nearest row should be the 14:00 (or 15:00) row.
    now_struct = time.localtime()
    now = time.mktime((now_struct.tm_year, now_struct.tm_mon, now_struct.tm_mday, 14, 30, 0, 0, 0, -1))
    idx = ts_file.find_index_nearest_to_now(now)
    picked_hour = time.localtime(ts_file.timestamps[idx] / 1000.0).tm_hour
    assert picked_hour in (14, 15)


def test_missing_header_raises(tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp\n100\n")
    with pytest.raises(ValueError):
        TimeSeriesFile(path)


def test_no_data_rows_raises(tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp,A\n")
    with pytest.raises(ValueError):
        TimeSeriesFile(path)
