import pytest

from publisher.playlist import load_speed_overrides


def test_loads_device_to_speed_mapping(tmp_path):
    path = tmp_path / "playlist.csv"
    path.write_text("device,speed\nOrg/PlantA/Line1/Press1,2.5\nOrg/PlantB/Boiler1,0.5\n")

    overrides = load_speed_overrides(str(path))

    assert overrides == {"Org/PlantA/Line1/Press1": 2.5, "Org/PlantB/Boiler1": 0.5}


def test_blank_device_rows_are_skipped(tmp_path):
    path = tmp_path / "playlist.csv"
    path.write_text("device,speed\n,1.0\nOrg/Device,3.0\n")

    overrides = load_speed_overrides(str(path))

    assert overrides == {"Org/Device": 3.0}


def test_missing_required_columns_raises(tmp_path):
    path = tmp_path / "playlist.csv"
    path.write_text("name,rate\nOrg/Device,1.0\n")

    with pytest.raises(ValueError):
        load_speed_overrides(str(path))
