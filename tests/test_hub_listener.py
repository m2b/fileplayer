import asyncio

from publisher.hub_listener import HubListener
from publisher.subscription_hub import SubscriptionHub


def test_publish_forwards_the_event_to_the_hub_in_process():
    hub = SubscriptionHub()
    listener = HubListener(hub)

    event = {"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100}
    asyncio.run(listener.publish(event))

    assert hub.latest("Org/Press1") == event
