from fastapi.testclient import TestClient

from publisher.api_server import create_app
from publisher.asset_browser import AssetBrowser
from publisher.subscription_hub import SubscriptionHub


def _write_hierarchy(tmp_path):
    plant = tmp_path / "Org" / "PlantA"
    plant.mkdir(parents=True)
    (plant / "Press1.csv").write_text("timestamp,A\n100,1\n")
    (plant / "Boiler1.csv").write_text("timestamp,A\n100,1\n")
    return tmp_path


def test_get_tree_walks_hierarchy_levels(tmp_path):
    root = _write_hierarchy(tmp_path)
    client = TestClient(create_app(AssetBrowser(str(root)), SubscriptionHub()))

    top = client.get("/api/tree").json()
    assert top == [{"name": "Org", "path": "Org", "is_device": False}]

    devices = client.get("/api/tree", params={"path": "Org/PlantA"}).json()
    assert sorted(d["name"] for d in devices) == ["Boiler1", "Press1"]
    assert all(d["is_device"] for d in devices)
    assert "file_path" not in devices[0]  # server-side filesystem paths are never exposed to the browser


def test_latest_defaults_to_empty_dict(tmp_path):
    root = _write_hierarchy(tmp_path)
    client = TestClient(create_app(AssetBrowser(str(root)), SubscriptionHub()))

    assert client.get("/api/devices/Org/PlantA/Press1/latest").json() == {}


def test_subscriber_receives_events_published_directly_to_the_hub(tmp_path):
    root = _write_hierarchy(tmp_path)
    hub = SubscriptionHub()
    client = TestClient(create_app(AssetBrowser(str(root)), hub))

    with client:
        with client.websocket_connect("/ws/subscribe") as sub_ws:
            sub_ws.send_json({"devices": ["Org/PlantA/Press1"]})

            event = {"device": "Org/PlantA/Press1", "metrics": {"A": 1}, "timestamp": 100}
            client.portal.call(hub.publish, event)

            assert sub_ws.receive_json() == event

    assert client.get("/api/devices/Org/PlantA/Press1/latest").json() == event


def test_subscriber_only_receives_events_for_devices_it_asked_for(tmp_path):
    root = _write_hierarchy(tmp_path)
    hub = SubscriptionHub()
    client = TestClient(create_app(AssetBrowser(str(root)), hub))

    with client:
        with client.websocket_connect("/ws/subscribe") as sub_ws:
            sub_ws.send_json({"devices": ["Org/PlantA/Press1"]})

            client.portal.call(hub.publish, {"device": "Org/PlantA/Boiler1", "metrics": {"A": 1}, "timestamp": 100})

            event = {"device": "Org/PlantA/Press1", "metrics": {"A": 2}, "timestamp": 200}
            client.portal.call(hub.publish, event)

            assert sub_ws.receive_json() == event


def test_new_subscription_immediately_gets_the_cached_latest_value(tmp_path):
    root = _write_hierarchy(tmp_path)
    hub = SubscriptionHub()
    client = TestClient(create_app(AssetBrowser(str(root)), hub))

    with client:
        event = {"device": "Org/PlantA/Press1", "metrics": {"A": 5}, "timestamp": 500}
        client.portal.call(hub.publish, event)

        with client.websocket_connect("/ws/subscribe") as sub_ws:
            sub_ws.send_json({"devices": ["Org/PlantA/Press1"]})
            assert sub_ws.receive_json() == event
