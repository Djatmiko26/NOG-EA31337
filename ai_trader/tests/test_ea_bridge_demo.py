"""Python/loopback tests only. These do not compile or execute the MQL5 receiver."""
import ast
import http.client
import threading
import unittest
from pathlib import Path

from ea_bridge_demo import DemoState, HOST, PROTOCOL, make_packet, handler_for, valid_symbol
from http.server import HTTPServer

ID = "0123456789abcdef0123456789abcdef"


class ProtocolTests(unittest.TestCase):
    def test_packet_matches_mql_selftest_vector(self):
        packet = make_packet("BUY", "XAUUSD", now=1700000000, signal_id=ID)
        self.assertEqual(packet.decode("ascii"),
                         f"NOG_DEMO_V1;TEST_ONLY;{ID};XAUUSD;M5;BUY;1700000000;1700000030")
        mql = (Path(__file__).resolve().parents[1] / "mt5" /
               "NOG_SignalReceiver_DRYRUN.mq5").read_text(encoding="utf-8")
        self.assertIn('BUY;1700000000;1700000030', mql)

    def test_actions_have_fixed_bounded_ttl(self):
        for action in ("BUY", "SELL", "WAIT"):
            packet = make_packet(action, "XAUUSDm", now=1700000000, signal_id=ID)
            fields = packet.decode("ascii").split(";")
            self.assertEqual(len(fields), 8)
            self.assertEqual(fields[5], action)
            self.assertEqual(int(fields[7]) - int(fields[6]), 30)
            self.assertLessEqual(len(packet), 512)

    def test_expired_packet_is_deliberately_old(self):
        fields = make_packet("BUY", "XAUUSD", now=1700000000, signal_id=ID,
                             expired=True).decode("ascii").split(";")
        self.assertEqual(int(fields[7]), 1699999910)
        self.assertEqual(int(fields[7])-int(fields[6]), 30)

    def test_invalid_values_rejected(self):
        for symbol in ("", "XAU;SELL", "XAU\nUSD", "XAU/USD", "X" * 65, "黄 金"):
            self.assertFalse(valid_symbol(symbol))
            with self.assertRaises(ValueError):
                make_packet("BUY", symbol)
        with self.assertRaises(ValueError):
            make_packet("EXECUTE", "XAUUSD")
        with self.assertRaises(ValueError):
            make_packet("BUY", "XAUUSD", signal_id="bad")
        with self.assertRaises(ValueError):
            make_packet("BUY", "XAUUSD", now=True)

    def test_polling_does_not_republish_or_renew(self):
        state = DemoState("XAUUSD")
        packet = state.publish("BUY")
        for _ in range(10):
            self.assertEqual(state.current(), packet)
        newer = state.publish("BUY")
        self.assertNotEqual(newer.split(b";")[2], packet.split(b";")[2])

    def test_clear_and_invalid_command_preserve_expected_state(self):
        state = DemoState("XAUUSD")
        self.assertIsNone(state.current())
        packet = state.publish("wait")
        with self.assertRaises(ValueError):
            state.publish("bad")
        self.assertEqual(state.current(), packet)
        state.publish("clear")
        self.assertIsNone(state.current())

    def test_new_code_has_no_trading_imports_or_calls(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ea_bridge_demo.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(x.name.split('.')[0] for x in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split('.')[0])
        self.assertFalse(imports & {"openai", "MetaTrader5", "dotenv", "requests"})
        mql = (root / "mt5" / "NOG_SignalReceiver_DRYRUN.mq5").read_text(encoding="utf-8")
        for forbidden in ("OrderSend(", "OrderSendAsync(", "CTrade", "#import", "#include"):
            self.assertNotIn(forbidden, mql)
        self.assertIn("ACCOUNT_TRADE_MODE_DEMO", mql)
        self.assertIn("REJECT_EXPIRED", mql)
        self.assertIn("WasSeen(signal.id)", mql)
        self.assertIn('http://127.0.0.1:8765/signal', mql)
        self.assertEqual(HOST, "127.0.0.1")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.state = DemoState("XAUUSD")
        self.server = HTTPServer((HOST, 0), handler_for(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method="GET", path="/signal"):
        conn = http.client.HTTPConnection(HOST, self.server.server_address[1], timeout=3)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_no_signal_is_204(self):
        status, headers, body = self.request()
        self.assertEqual((status, body), (204, b""))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_published_signal_is_stable(self):
        packet = self.state.publish("SELL")
        for _ in range(2):
            status, headers, body = self.request()
            self.assertEqual((status, body), (200, packet))
            self.assertEqual(int(headers["Content-Length"]), len(packet))

    def test_http_cannot_publish_and_other_paths_rejected(self):
        self.assertEqual(self.request(path="/signal?BUY")[0], 404)
        self.assertEqual(self.request(method="POST")[0], 501)
        self.assertIsNone(self.state.current())


if __name__ == "__main__":
    unittest.main()
