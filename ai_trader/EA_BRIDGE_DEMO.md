# EA receiver: communication test, not strategy research

This step stops adding research experiments and starts the MT5 component. It does
not claim that the current strategy is approved. Existing monitor, baseline,
filter, snapshots, .env and pilot databases are unchanged.

New pieces:
- `ea_bridge_demo.py`: Python-standard-library HTTP API, bound only to 127.0.0.1:8765.
- `mt5/NOG_SignalReceiver_DRYRUN.mq5`: demo-account-only receiver on an M5 chart.

These TWO new programs contain no order-sending path or execution switch. The
old EAs elsewhere in the repository are separate; do not attach them for this test.
No API key, OpenAI import/request, account upload or MT5 Python connection is used.
The Python program does not read or modify any existing data file.

## Run once, then test from MT5

Keep the old OpenAI monitor stopped to avoid its separate API usage. Use a demo
account in the MT5 terminal, with Algo Trading left off.

From `C:\NOG-EA31337-OpenAI\ai_trader`:

```powershell
git pull --ff-only origin openai-trader-v1
python ea_bridge_demo.py
```

Do not type another PowerShell command at `demo>`: that is now the test console.
It starts with NO_SIGNAL, not BUY. Keep this window open.

Copy `mt5\NOG_SignalReceiver_DRYRUN.mq5` into the active MT5 terminal's
`File > Open Data Folder > MQL5 > Experts` directory. Copy this file only.
Open MetaEditor (F4 in MT5), open that copied file and Compile (F7).
Do not proceed on a compilation error. This source has not been compiled in the
assistant's environment; actual MetaEditor compilation and attachment are required.

MT5 > Tools > Options > Expert Advisors: allow WebRequest for this URL only:

```text
http://127.0.0.1:8765
```

No DLL permissions or OpenAI URL/key are needed. WebRequest is synchronous; this
receiver uses a 1-second timeout and a 2-second OnTimer interval, not every tick.
Use a normal demo chart, NOT Strategy Tester: WebRequest is unavailable in Tester.

In Navigator > Expert Advisors, Refresh, then attach `NOG_SignalReceiver_DRYRUN`
to an XAUUSD M5 chart. Its initial status should include PARSER_SELF_TEST_PASS
in the Experts log and NO_SIGNAL once it connects to the empty API.
If the broker symbol differs, start Python with `--symbol XAUUSDm` (or the exact
ASCII Market Watch symbol) and attach the EA to that same symbol, M5.

Return to the Python `demo>` console and type, one at a time:

```text
BUY
EXPIRED
```

Observe each result in MT5 before typing the next command. Expected first-time
BUY event: `ACCEPTED_TEST BUY`. EXPIRED publishes a deliberately old TEST BUY;
expected result: `REJECT_EXPIRED`. Neither event is an order or risk approval.
The accepted status may quickly become DUPLICATE_IGNORED on later polls; the
first acceptance remains visible in the Experts log (Toolbox, Ctrl+T).

Other commands: SELL, WAIT, CLEAR, QUIT. Every BUY/SELL/WAIT command creates a new
random test ID, valid for 30 seconds. Polls do not renew the ID or expiry. CLEAR
removes the current message. QUIT or Ctrl+C stops this local API. A stopped API
should show API_UNAVAILABLE in MT5; it does not invoke a fallback strategy.

## Protocol and guards

ASCII single record, exactly 8 semicolon-separated fields, no trailing newline:

```text
NOG_DEMO_V1;TEST_ONLY;<32-lowercase-hex-id>;<symbol>;M5;<BUY|SELL|WAIT>;<issued-epoch>;<expires-epoch>
```

This is a deliberately separate DEMO transport format, not the OpenAI JSON format
and not a production execution contract. A later adapter must translate validated
research decisions; this demo server is NOT connected to the existing monitor.

EA checks demo account, M5/chart symbol, protocol, field count, ID, permitted
action, ten-digit epoch values, positive TTL no greater than 60 seconds,
future-clock skew no greater than 3 seconds, expiry and duplicate IDs. It rechecks
account/connection after the synchronous HTTP request. No account details are sent.
Malformed, absent, expired and disconnected states are displayed, never executed.

Duplicate memory is limited to 256 accepted IDs per EA attachment, then it rejects
new messages until reattached. It is NOT durable across terminal/EA restart, NOT
an exactly-once execution guarantee, and not shared between multiple chart copies.
Attach one receiver only. TimeGMT uses the local computer's clock/timezone setup,
not the guessed broker offset used in old research. Keep Windows clock/timezone
correct. This does not certify time synchronization or candle freshness.

The local endpoint is unauthenticated and has no production transport hardening.
Never expose it on a network or reuse this receiver as an execution gateway.
No SL/TP validation, lot sizing, 0.25% risk calculation, daily loss limit, persistent
order idempotency or broker fill handling is implemented yet. Those are separate
engineering work. There is intentionally no setting to enable orders here.

## Completion criteria (not profitability criteria)

The bridge step is complete when MetaEditor compiles the EA, MT5 accepts one new
TEST BUY, ignores repeated reads of that ID, rejects EXPIRED, and handles a stopped
API with no fallback. After that, preserve this as a communication test rather than
re-running historical AI experiments. Strategy approval and guarded demo execution
remain separate tasks.

## Test evidence

Ten focused Python tests passed in the assistant environment on 2026-09-16:
packet serialization, bounded TTL, deliberately expired data, bad inputs, polling
without renewal, clear, basic source checks, and loopback HTTP 204/200/404/501.
They used no external network, broker or OpenAI. They do NOT compile or execute
MQL5. The EA has parser checks that execute in OnInit when attached in MT5; their
runtime result is not known until you compile/attach it. No full old suite was run.

Developer test command (not a prerequisite to manually re-run every old study):

```powershell
python -m unittest discover -s tests -p "test_ea_bridge_demo.py" -v
```

Official platform references:
- https://www.mql5.com/en/docs/network/webrequest
- https://www.mql5.com/en/docs/eventfunctions/eventsettimer
- https://www.mql5.com/en/docs/dateandtime/timegmt
- https://www.mql5.com/en/docs/constants/environment_state/accountinformation
