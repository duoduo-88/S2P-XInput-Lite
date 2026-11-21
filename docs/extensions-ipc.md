# Extensions IPC protocol v1

[繁體中文](extensions-ipc_zh-TW.md)

S2P-XInput-Lite receives controller input state from independent extension
tools (S2P-Extensions, such as macro or multi-device input tools) over a local
Windows named pipe. It merges that state with the physical controller and
sends the result through S2P's one existing virtual Xbox controller. Tools do
not need to, and must not, talk to ViGEmBus directly.

```
Physical controller → S2P input processing (curves, gyro, mapping layers) → Extensions merge → ViGEmBus → XInput
                                                                              ↑
                                  Extension tools A, B… (up to 4) ── named pipe (protocol v1)
```

## Architecture and lifetime

| Component | Location | Role |
|---|---|---|
| `ExtensionManager` | `src/extension_bridge.py` | Created in the connector. Starts and stops the server from `config.ini`, identifies tools, resolves conflicts, reports status, and starts tools. |
| `ExtensionPipeServer` | same file | 4 instances of one pipe name (one per possible tool), each with overlapped I/O. |
| `ExtensionInputHub` | same file | The single object attached to `XInputController`; merges every connected tool in order. |
| `ExtensionInputMixer` | same file | One tool's input state; one lock per report. |
| Merge point | `XInputController.update()` | After the report is built and before `pad.update()`. When disabled, the cost is a single `is None` check. |
| Settings window | `src/gui_sections/extensions.py` | Tool list, permissions, conflict check. It only writes the global settings and sends commands. |

Threads: one daemon thread `S2P-Extensions-IPC` per pipe instance (4),
present only while a tool is enabled in the list. Each blocks in
`WaitForMultipleObjects` (pipe I/O, a shared stop event, and its own drop
event) and never polls. Disabling the last tool or stopping the connector sets
the stop event and joins the threads (measured under 0.5 s).

Connector commands (sent by the GUI through the `controller_commands` queue):

| Command | Action |
|---|---|
| `extensions_apply` | Re-read the settings. Enabling or disabling a tool never disconnects the others; only tools whose settings changed are dropped (state `waiting` / `settings_changed`; the tool reconnects by itself and gets its new permissions). |
| `extensions_connect[:<tool id>]` | Used by the **Open** button and the notification area menu: start the tool (a `.py` development tool gets a one-time token). |
| `extensions_disconnect[:<tool id>]` | Drop and pause that tool (or all tools) and clear its input at once. Not used by the UI; use **Enable/Disable** instead. |

Status is published every 0.5 s in the `extensions` field of
`controller_status.json`:

```json
{"state": "connected", "code": "", "enabled": true, "protocol": 1, "max_tools": 4,
 "tools": {"<id>": {"state": "connected", "code": "", "name": "S2P-Joystick-Macro",
                    "capabilities": ["input"], "sticks": ["left"]}}}
```

The connector window also prints one line whenever a tool connects,
disconnects, fails, times out, or waits to reconnect after a settings change.

Settings (`config.ini`, global, not part of profiles):

```ini
[extensions]
enabled = true

[extensions.tool.1a2b3c4d]
order = 0
path = C:\Tools\S2P-Joystick-Macro.exe
enabled = true
auto_launch = false
allow_input = true
allow_read = false
```

## Connection and security

- Pipe name: `\\.\pipe\S2P-XInput-Lite.Extensions`. A tool started by S2P gets
  the name in `S2P_EXTENSION_PIPE` and a one-time token in
  `S2P_EXTENSION_TOKEN`.
- Enabled tools are started by S2P when the connection starts (or when they are
  enabled) with `S2P_EXTENSION_BACKGROUND=1`: the tool should run in the
  background (for example in the notification area) without showing a window.
  A tool that is already running is not started again.
- **Open** and the notification area menu start the tool again (without the
  background flag). A tool should keep a single instance, and the second
  launch should tell the running copy to show its window. Windows lets only
  the foreground process, or one it just started, bring a window to the
  front: the second copy was started by S2P and may, the running copy may
  not. So the second copy calls `AllowSetForegroundWindow` with the running
  copy's process id (which the running copy publishes, for example in named
  shared memory) before signalling it, then exits. The running copy restores its
  window even when it is minimized or hidden in the notification area,
  brings it to the front and gives it focus. Example: S2P-Extensions
  `macro/s2p_macro/single_instance.py`.
- Tools should send `ping` regularly (the SDK does once a second) to notice
  when S2P ends the session and reconnect by themselves; this is how
  permission changes take effect.
- The built-in **Gamepad Test** does not use the pipe; the S2P GUI opens it
  directly.
- `FILE_FLAG_FIRST_PIPE_INSTANCE`: no other program can create the pipe name
  first; if the name is taken, the state is `pipe_in_use`.
- `PIPE_REJECT_REMOTE_CLIENTS` and the DACL
  `D:P(D;;GA;;;NU)(A;;GA;;;<current user SID>)`: only local connections from
  the same user are accepted.
- Identification: the client process's image path must equal the EXE of an
  enabled tool in the list, or the hello must carry the token S2P issued when
  it started that tool (the path used by `.py` development scripts). Any
  other program receives `unauthorized`.
- Permissions are granted separately: `input` (submit input) is allowed by
  default and `read` (read controller state) is off by default; the user
  turns them on per tool. A tool gets the intersection of what its hello
  declares and what the user allows.

## Conflict rules

| Situation | Handling |
|---|---|
| The same EXE is added twice | The settings window refuses it; when loading settings only the first entry is kept. |
| More than 4 tools are enabled | The settings window refuses to enable more; the 5th and later are marked **Not loaded**. |
| A tool has no permission | The settings window refuses it and the conflict check lists it. |
| Two tools claim the same stick | The tool that connects later receives `conflict`. The conflict check lists it until the other tool releases the stick, even if the refused tool reconnects without it. |
| The same tool connects twice | The second connection receives `already_connected` and does not take over the first; the conflict check lists it for 30 s after the last attempt. |
| A tool asks for a permission that is not allowed | It connects with the allowed permissions only (or receives `permission_denied` when none is left); the conflict check lists the missing permission. |
| A program that is not in the list connects | It receives `unauthorized`; the conflict check names the EXE for 30 s after its last attempt. |
| Another program holds the pipe name | No tool can connect (`pipe_in_use`); the conflict check says so. |
| Buttons and triggers | No conflict: buttons are ORed and triggers take the maximum. |

## Message format

The pipe uses message mode. Each message is one UTF-8 JSON object, ideally
ending with `\n`; one message may also carry several lines of JSON. A single
message is limited to 4096 bytes.

### 1. Handshake (must arrive within 3 s)

```json
{"type": "hello", "protocol": 1, "name": "S2P-Joystick-Macro", "version": "0.1.0",
 "capabilities": ["input"], "sticks": ["left"], "token": "<optional>"}
```

| Field | Meaning |
|---|---|
| `capabilities` | Requested permissions: `input`, `read`. Defaults to `["input"]` when omitted. |
| `sticks` | Sticks to control exclusively: `left`, `right`. Omit when the tool does not move sticks. |
| `token` | Token provided when S2P started the tool; not needed for an EXE started by the user. |

Success:

```json
{"type": "welcome", "protocol": 1, "server": "S2P-XInput-Lite", "server_version": "0.8.2",
 "capabilities": ["input"], "sticks": ["left"], "input_timeout_ms": 1000, "host_pid": 12345}
```

`host_pid` is the process ID of the S2P window. A tool that should close
together with S2P waits for that process to exit (for example with
`OpenProcess(SYNCHRONIZE)` and `WaitForSingleObject`). It stays the same while
only the S2P connection restarts. Older S2P versions omit it.

On failure S2P replies and then closes the connection (giving the tool up to
200 ms to read the reply):

```json
{"type": "error", "code": "conflict", "detail": "left stick is used by another tool"}
```

Error codes: `unauthorized`, `unsupported_protocol`, `handshake_timeout`,
`permission_denied`, `conflict`, `already_connected`, `user_disconnected`,
`bad_message`, `message_too_large`.

### 2. Submitting input (needs `input`; full state, not incremental)

```json
{"type": "state", "buttons": ["A", "LB"], "lt": 0, "rt": 255, "lx": 0, "ly": 32767}
```

| Field | Type | Range | When omitted |
|---|---|---|---|
| `buttons` | Array of names or an XUSB bit mask integer | `UP DOWN LEFT RIGHT START BACK L_STK R_STK LB RB GUIDE A B X Y` | No button pressed |
| `lt`, `rt` | Integer | 0–255 | That trigger is not driven |
| `lx`+`ly`, `rx`+`ry` | Integers (in pairs, and the stick must be claimed in hello) | -32768–32767 | That stick is not driven |

Each `state` replaces that tool's previous one. To release everything, send
`"buttons": []` or `release_all`.

**Heartbeat**: while a tool holds any input, it must send a message (its
current state again, or `ping`) at least every `input_timeout_ms` (1000 ms).
Otherwise S2P clears that tool's input but keeps the connection; the status
shows **input timed out**, and the next message recovers. A tool with no
input may stay silent.

### 3. Reading (needs `read`)

| Request | Reply |
|---|---|
| `{"type": "get_controller"}` | `{"type": "controller", "connected": true, "mode": "bluetooth", "battery_percent": 80}` |
| `{"type": "get_input"}` | `{"type": "input", "frame": 1234, "age_ms": 1.8, "buttons": ["A"], "lt": 0, "rt": 0, "lx": 0, "ly": 0, "rx": 0, "ry": 0}` |

`get_input` returns S2P's output from *before* any extension tool is merged
(with curves, gyro and mapping applied), so a tool never reads back its own
input or another tool's. `frame` increases by 1 with each physical report.
The snapshot is captured only while a connected tool holds `read`.

### 4. Other messages

| Message | Action |
|---|---|
| `{"type": "release_all"}` | Clear all of this tool's input immediately. |
| `{"type": "ping", "seq": 1}` | Replies `{"type": "pong", "seq": 1}`; the tool must read the reply. |
| `{"type": "bye"}` | Normal end; S2P clears this tool's input and waits for a new connection. |

An unknown `type`, an invalid value, an unclaimed stick, or a request beyond
the granted permissions gets an `error` and a disconnect, and the tool's
input is cleared.

## Merge rules

- **Buttons**: OR. A button is output while the physical controller or any
  tool holds it, and released only when all release. The physical report is
  rebuilt every frame before the merge, so a tool releasing or disconnecting
  never releases a button still held on the controller. A tool that presses
  and releases between two physical reports still produces one frame of the
  press, so short taps are not lost.
- **Triggers**: the maximum of the physical controller and all tools; changes
  are continuous, without jumps.
- **Sticks**: at most one tool per stick (checked on connect), merged with
  physical priority and hysteresis. The tool's value is used only while the
  physical stick (after curves and gyro output) is at rest; the player takes
  over as soon as the physical magnitude reaches 12%, and control returns to
  the tool after it stays below 6% for 0.15 s. A tool sending a neutral
  value (0, 0) never overrides a stick the player is moving.
- **Disconnect, timeout, crash**: the tool's state is cleared at once and the
  next physical report is back to normal; other tools are unaffected.

Merging runs at the physical controller's report rate; a state sent by a tool
takes effect with the next physical report (usually within 4–8 ms). Tools
never add a second output, an extra `pad.update()`, or any waiting for a
tool's reply.

## Flood protection

Bursts of messages are handled immediately. If a tool keeps sending faster
than about 2000 messages/s (more than 100 per 50 ms), its thread pauses
reading for the rest of that window and releases the GIL, so a misbehaving
tool cannot delay the physical input thread. The pipe buffer is 4 KB, which
bounds the backlog; after the pause the queued messages are processed up to
the latest state.

## Tool manifest and updates

A tool must ship a manifest next to its EXE, named after the EXE:
`S2P-Joystick-Macro.exe` → `S2P-Joystick-Macro.s2p.json` (UTF-8 JSON, at most
64 KB). **Check for Updates** in the Extensions window reads it; a tool
without one is listed as "No update info".

```json
{"format": "s2p-extension", "name": "S2P-Joystick-Macro", "version": "0.1.0",
 "update_url": "https://github.com/duoduo-88/S2P-Extensions/releases/latest/download/s2p-extensions-updates.json",
 "homepage": "https://github.com/duoduo-88/S2P-Extensions"}
```

| Field | Meaning |
|---|---|
| `format` | Always `s2p-extension`. |
| `name` | The tool's name; the key in a multi-tool feed. |
| `version` | Installed version, `MAJOR.MINOR.PATCH`. Keep it equal to the version the tool reports in `hello`. |
| `update_url` | HTTPS address of the update feed. Optional; without it the tool is listed as "No update info". |
| `homepage` | Optional HTTPS page, offered when the feed gives no download page. |

The update feed is a JSON document (at most 256 KB) for one tool or for
several tools keyed by `name`:

```json
{"format": "s2p-extension-updates", "version": "0.1.1", "url": "https://example.com/releases/0.1.1"}
```

```json
{"format": "s2p-extension-updates",
 "tools": {"S2P-Joystick-Macro": {"version": "0.1.1",
                                  "url": "https://github.com/duoduo-88/S2P-Extensions/releases/tag/v0.1.1"}}}
```

- S2P reads the feed only when the user selects **Check for Updates**. Tools
  that share one `update_url` are answered from a single download.
- Only `https` addresses are used. S2P never downloads or runs a tool; it
  shows "Update available: vX.Y.Z" in the status column and offers to open
  `url` in the browser.
- A newer `version` than the manifest's means an update. An unreachable or
  invalid feed, or one that does not list the tool, is shown as "Update check
  failed".
- Hosting the feed as a GitHub release asset and pointing `update_url` at
  `/releases/latest/download/<feed>` keeps the feed in step with each release.

## Writing an independent tool (minimal example)

Only the standard library is needed. A complete reference implementation is
`tests/extension_ipc_test_client.py` (development only, not shipped). The
shared IPC client is `sdk/s2p_ext` in
[S2P-Extensions](https://github.com/duoduo-88/S2P-Extensions).

```python
import json, os, threading

pipe_name = os.environ.get("S2P_EXTENSION_PIPE", r"\\.\pipe\S2P-XInput-Lite.Extensions")
pipe = open(pipe_name, "r+b", buffering=0)          # each write() is one message
lock = threading.Lock()

def send(message):
    with lock:
        pipe.write((json.dumps(message) + "\n").encode("utf-8"))

hello = {"type": "hello", "protocol": 1, "name": "S2P-Joystick-Macro", "version": "0.1.0",
         "capabilities": ["input"]}
if os.environ.get("S2P_EXTENSION_TOKEN"):
    hello["token"] = os.environ["S2P_EXTENSION_TOKEN"]
send(hello)
assert json.loads(pipe.read(4096))["type"] == "welcome"

send({"type": "state", "buttons": ["A"]})            # hold A (resend every 250 ms while held)
send({"type": "state", "buttons": []})               # release
send({"type": "bye"})
pipe.close()
```

Recommendations:

- Hold each button state for at least 16 ms so a game reading at 60 FPS
  always sees it.
- While holding input, resend the current state every 250 ms as the
  heartbeat.
- When connecting fails (`ERROR_PIPE_BUSY`, or S2P is not running), retrying
  every 0.5–1 s is enough.
- Declare only the permissions and sticks you need, to avoid conflicts with
  other tools.
- Release the tool as a standalone EXE that the user adds to the Extensions
  list (that path is how the tool is identified).

## Tests

```powershell
& ".\runtime\python.exe" ".\tests\run_tests.py"   # includes test_extension_bridge.py
```

Manual check: add `tests\extension_ipc_test_client.py` to the Extensions list,
select it, and choose **Open**. S2P starts the client in development mode and
it runs an A, A+B, X, Y and left-stick demo; watch the merged XInput output
in the existing **Gamepad Test**.
