from publisher.asset_browser import AssetBrowser


def _make_hierarchy(tmp_path):
    # Full hierarchy: Org/Division/Plant/Line/device.csv
    full_line = tmp_path / "AcmeCorp" / "West" / "PlantA" / "Line1"
    full_line.mkdir(parents=True)
    (full_line / "Press1.csv").write_text("timestamp,A\n100,1\n")
    (full_line / "Press2.csv").write_text("timestamp,A\n100,1\n")

    # Collapsed hierarchy: devices sit directly under the plant, no line dir.
    collapsed_plant = tmp_path / "AcmeCorp" / "West" / "PlantB"
    collapsed_plant.mkdir(parents=True)
    (collapsed_plant / "Boiler1.csv").write_text("timestamp,A\n100,1\n")
    return tmp_path


def test_get_children_lists_directories_until_csv_files_are_found(tmp_path):
    root = _make_hierarchy(tmp_path)
    browser = AssetBrowser(str(root))

    top = browser.get_children()
    assert [n.name for n in top] == ["AcmeCorp"]
    assert all(not n.is_device for n in top)

    plants = browser.get_children("AcmeCorp/West")
    assert sorted(n.name for n in plants) == ["PlantA", "PlantB"]
    assert all(not n.is_device for n in plants)


def test_collapsed_directory_exposes_csv_files_as_devices(tmp_path):
    root = _make_hierarchy(tmp_path)
    browser = AssetBrowser(str(root))

    devices = browser.get_children("AcmeCorp/West/PlantB")
    assert len(devices) == 1
    node = devices[0]
    assert node.is_device
    assert node.name == "Boiler1"
    assert node.path == "AcmeCorp/West/PlantB/Boiler1"
    assert node.file_path.endswith("Boiler1.csv")


def test_walk_devices_finds_every_device_regardless_of_depth(tmp_path):
    root = _make_hierarchy(tmp_path)
    browser = AssetBrowser(str(root))

    devices = sorted(n.path for n in browser.walk_devices())
    assert devices == [
        "AcmeCorp/West/PlantA/Line1/Press1",
        "AcmeCorp/West/PlantA/Line1/Press2",
        "AcmeCorp/West/PlantB/Boiler1",
    ]


def test_get_children_on_unknown_path_returns_empty(tmp_path):
    root = _make_hierarchy(tmp_path)
    browser = AssetBrowser(str(root))
    assert browser.get_children("DoesNotExist") == []
