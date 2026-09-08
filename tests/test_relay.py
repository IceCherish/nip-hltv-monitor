import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from nip_monitor.notifiers import RelayNotifier
from nip_monitor.relay import RelayHandler


class RelayTests(unittest.TestCase):
    def test_signed_relay_forwards_to_onebot(self):
        old_secret = os.environ.get("RELAY_SECRET")
        old_group = os.environ.get("QQ_GROUP_ID")
        os.environ["RELAY_SECRET"] = "test-secret-with-at-least-20-characters"
        os.environ["QQ_GROUP_ID"] = "123456789"
        server = ThreadingHTTPServer(("127.0.0.1", 0), RelayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("nip_monitor.relay.OneBotNotifier.send") as send:
                RelayNotifier(
                    url=f"http://127.0.0.1:{server.server_port}/notify",
                    secret=os.environ["RELAY_SECRET"],
                ).send("test title", "test message")
                send.assert_called_once_with("test title", "test message")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            if old_secret is None:
                os.environ.pop("RELAY_SECRET", None)
            else:
                os.environ["RELAY_SECRET"] = old_secret
            if old_group is None:
                os.environ.pop("QQ_GROUP_ID", None)
            else:
                os.environ["QQ_GROUP_ID"] = old_group
