import asyncio

from publisher.subscription_hub import SubscriptionHub


class FakeWebSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_json(self, data):
        if self.fail:
            raise RuntimeError("connection gone")
        self.sent.append(data)


def test_publish_only_reaches_subscribers_of_that_device():
    hub = SubscriptionHub()
    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()
    hub.register(ws_a)
    hub.register(ws_b)
    hub.set_subscriptions(ws_a, {"Org/Press1"})
    hub.set_subscriptions(ws_b, {"Org/Boiler1"})

    event = {"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100}
    asyncio.run(hub.publish(event))

    assert ws_a.sent == [event]
    assert ws_b.sent == []


def test_publish_updates_latest_for_the_device():
    hub = SubscriptionHub()
    assert hub.latest("Org/Press1") is None

    event = {"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100}
    asyncio.run(hub.publish(event))

    assert hub.latest("Org/Press1") == event


def test_publish_drops_subscribers_whose_send_fails():
    hub = SubscriptionHub()
    broken = FakeWebSocket(fail=True)
    hub.register(broken)
    hub.set_subscriptions(broken, {"Org/Press1"})

    asyncio.run(hub.publish({"device": "Org/Press1", "metrics": {}, "timestamp": 100}))

    assert broken not in hub._subscribers


def test_unregister_stops_further_delivery():
    hub = SubscriptionHub()
    ws = FakeWebSocket()
    hub.register(ws)
    hub.set_subscriptions(ws, {"Org/Press1"})
    hub.unregister(ws)

    asyncio.run(hub.publish({"device": "Org/Press1", "metrics": {}, "timestamp": 100}))

    assert ws.sent == []
