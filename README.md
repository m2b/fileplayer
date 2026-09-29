# fileplayer

[![Tests](https://github.com/m2b/fileplayer/actions/workflows/test.yml/badge.svg)](https://github.com/m2b/fileplayer/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A general-purpose timeseries CSV file player. It plays back one or many CSV
files concurrently - each at its own speed - and publishes each row as a
JSON event to a pluggable listener. The importable package is named
`publisher` (see [Quick start](#quick-start) below); core playback has no
dependencies beyond the Python standard library, so it stays easy to
vendor or trim down if you only need part of it.

**Contents:** [Quick start](#quick-start) · [Data model](#data-model) ·
[Components](#components) · [Playback model](#playback-model) ·
[Published event format](#published-event-format) · [CLI](#cli) ·
[Demo frontend](#demo-frontend) · [Docker deployment](#docker-deployment) ·
[Tests](#tests) · [License](#license)

## Quick start

```
git clone https://github.com/m2b/fileplayer.git
cd fileplayer
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
python -m pytest tests                                # 43 tests, should all pass
python -m publisher.player_cli --root sample_data --websocket-url ws://localhost:1880/ingest --default-speed 20000 --tick 0.1 --log-level DEBUG
```
That last command plays the bundled demo asset hierarchy (`sample_data/`)
fast enough to see rows advance within a few seconds, publishing each
event to the given websocket url and (with `--log-level DEBUG`) logging it
as JSON. `--websocket-url` is required; if nothing is listening there the
player logs connection warnings and keeps retrying. Ctrl+C to stop.

## Data model

- Each CSV file has a header. The first column is an integer timestamp
  (epoch ms, or any arbitrary increasing integer - the player doesn't care
  which). The remaining columns are metrics.
- Metric values are inferred per cell as `bool`, `int`, `float`, or `str`.
- Data may be sparse: an empty cell means "no value at this timestamp" and
  is left out of the published event entirely, and rows don't need to be
  evenly spaced in time.
- Files live in a directory tree that models an asset hierarchy:
  `organization/division/plant/line/device.csv`. The hierarchy can be
  collapsed at any level - as soon as a directory holds `.csv` files
  directly, those are devices and browsing stops descending. See
  `sample_data/` for an example of both a full and a collapsed hierarchy.

## Components

- `TimeSeriesFile` - loads one CSV into memory, infers types, and answers
  "which row is active at timestamp T" (`find_index_for_ts`, bisect-based)
  and "which row is closest to right now, ignoring the date"
  (`find_index_nearest_to_now`) - used to make a fresh start pick up a
  sensible point in a daily cycle (e.g. solar data lines up with the
  current time of day regardless of what day the file was captured).
- `AssetBrowser` - lazily lists the hierarchy directory-by-directory
  (`get_children`) and can walk every device under a path
  (`walk_devices`), without scanning the whole tree up front.
- `StateStore` - durable JSON-backed store of the last row played per
  file, written atomically and debounced. This is what lets a restarted
  player resume mid-file instead of always restarting from the
  nearest-to-now row.
- `EventListener` / `StdoutListener` - abstract publish sink and a stdout
  implementation used for testing. Future listeners (OPC UA, MQTT, MQTT
  Sparkplug B, ...) subclass `EventListener`.
- `WebSocketListener` (`websocket_listener.py`) - publishes to a *remote*
  websocket server. Takes a single root url; each event's device path is
  appended to it to get that device's own endpoint (e.g. root
  `ws://host:1880/ingest` + device `Plant/Line1/Press1` ->
  `ws://host:1880/ingest/Plant/Line1/Press1`). One connection is opened
  lazily per device and reused. On a failed connect or a connection
  dropping mid-send, the connection is discarded and the call waits a
  configurable retry delay before returning - the actual retry happens the
  next time that device has a new value to publish, so a down/unreachable
  device never blocks any other device from publishing. Needs the
  `websockets` package. This is the kind of listener a future OPC UA or
  MQTT publisher would follow the same shape as: one `EventListener`
  subclass per protocol, wired in by `player_cli.py` behind a flag.
- `HubListener` / `SubscriptionHub` / `api_server.py` - the *in-process*
  alternative to `WebSocketListener`, used by `--serve-api` (see below):
  instead of publishing out over the network, events are handed straight
  to a `SubscriptionHub` living in the same process, which a small FastAPI
  app (`api_server.py`) reads from to serve live values to the `demo/`
  frontend. No network hop between "the data" and "the API serving it".
- `FilePlayer` - the main loop. On a fixed tick it works out, for every
  file independently, which row corresponds to "now" given that file's
  playback speed, and publishes it if it's different from the last row
  published for that file. A file loops back to its start once it reaches
  the end.

## Playback model

Each file gets an independent virtual clock: `speed` scales how fast the
file's own timestamp axis advances relative to the wall clock (not tied to
the tick interval), so irregularly-spaced rows still play back at the
right relative moments. On first-ever playback of a file (no durable
state), the virtual clock is anchored at the row nearest to the current
time of day. On resume, it's anchored at the last row that was durably
recorded, so playback picks up from there instead of re-syncing to "now".

## Published event format

```json
{"device": "AcmeCorp/West/PlantA/Line1/Press1", "metrics": {"Pressure": 138.9, "Temperature": 26.8, "Running": true}, "timestamp": 1730000000000}
```

`device` is the hierarchical asset path. `timestamp` is the wall-clock
epoch ms at which the event was published (not the row's own timestamp
column). Only JSON is supported today; BSON/protobuf are future work.

## CLI

```
python -m publisher.player_cli --root sample_data --websocket-url ws://host:1880/ingest --default-speed 1.0
```

This is one generic launcher for the player, not a per-protocol script:
which publishers run is picked by flags, each backed by an `EventListener`
subclass. Today that's a remote websocket (always on, `--websocket-url` is
required), plus stdout and/or the in-process API server; a future OPC UA or MQTT publisher is expected to plug in the
same way (a new flag, a new `EventListener` subclass, lazily imported so
its dependency is only needed when that flag is used) rather than getting
its own copy of this script.

Options:

- `--root` (required) - root of the asset hierarchy.
- `--default-speed` - speed applied to every discovered device (default `1.0`).
- `--playlist` - optional CSV (`device,speed` header) overriding the speed
  of specific devices, keyed by hierarchical device path.
- `--state-file` - path to the durable state JSON file (default
  `./fileplayer_state.json`).
- `--tick` - main loop interval in seconds (default `0.2`).
- `--log-level` - `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`.
- `--websocket-url` (required) - root websocket url to publish to via
  `WebSocketListener` (see above), e.g. `ws://host:1880/ingest`, for
  pushing to a remote/downstream system.
- `--no-stdout` - don't log events at DEBUG level.
- `--websocket-retry-delay` - seconds to wait after a failed websocket
  connect/send before it's retried (default `5.0`).
- `--serve-api` - also serve the browsing + live-subscribe API described
  below, for the `demo/` frontend.
- `--api-host` / `--api-port` - where `--serve-api` binds (default
  `0.0.0.0:8000`).

Try it against the bundled sample hierarchy at a speed fast enough to see
rows advance within a few seconds:

```
python -m publisher.player_cli --root sample_data --websocket-url ws://localhost:1880/ingest --default-speed 20000 --tick 0.1
```

## Demo frontend

`demo/index.html` is a standalone page - not served by the player - that
lets you browse a running player's asset hierarchy and watch selected
devices' values update live as ECharts scatter plots. It's for demoing the
data player, not a production UI: it takes any player's API URL as input,
so one copy of this page can point at whichever player instance you're
currently looking at.

Start the player with its API turned on:

```
python -m publisher.player_cli --root sample_data --websocket-url ws://localhost:1880/ingest --default-speed 20000 --serve-api --api-port 8000
```

Serve the demo page itself (it needs to be fetched over http(s), not
opened as a `file://` url, for its API calls to work reliably):

```
python -m http.server 8080 --directory demo
```

Open `http://localhost:8080/`, enter the player's API url (default
`http://localhost:8000`, remembered in the browser after that) and
connect. Expand the tree and check a device's checkbox to subscribe to it
- a card appears with one scatter chart per numeric metric (non-numeric
metrics show as badges) and updates as new values arrive.

How it fits together, all served by the player process itself when
`--serve-api` is set:

- `GET /api/tree?path=...` - `AssetBrowser.get_children()` as JSON, used by
  the frontend to lazily expand the tree.
- `GET /api/devices/{device path}/latest` - the last event seen for a
  device, if any.
- `WS /ws/subscribe` - the frontend's own channel. It sends
  `{"devices": [...]}` whenever the selection changes; the server replies
  with the events for just those devices (plus, on newly adding a device,
  whatever its cached latest value already is) as `HubListener` hands them
  to the `SubscriptionHub` in-process.

Needs `fastapi`, `uvicorn`, and `websockets` (see `requirements.txt`).

## Docker deployment

`Dockerfile` and `docker-compose.yml` deploy a specific configuration: a
player that always uses `--playlist` and `--serve-api` alongside the
required `--websocket-url` - continuously publishing the bundled sample hierarchy out over a
websocket, while also serving the `demo/` frontend's API for watching it
live. Of those, only `--websocket-url` is required by `player_cli.py` itself;
`--playlist` and `--serve-api` are a deployment choice, not a constraint
of the tool.

Build context is this directory, so the image only ever depends on what's
in this self-contained package:

```
cp .env.example .env    # then fill in WEBSOCKET_URL
docker compose up -d --build
```

What's baked in vs. configurable:

- `--root` and `--playlist` are host data, bind-mounted in (`ASSETS_DIR`,
  `PLAYLIST_FILE` - paths relative to `docker-compose.yml` if not
  absolute), never part of the image - change device CSVs or playlist rows
  and just restart the container, no rebuild. They default to the bundled
  `sample_data/`/`sample_playlist.csv` so `docker compose up` works with no
  setup; point them at your own asset hierarchy for real deployments.
- `--state-file` points at `/data/state/fileplayer_state.json`, backed by
  a named volume (`fileplayer-state`) - durable across container restarts
  and redeploys, which is the entire point of `StateStore`.
- `--serve-api` always binds `0.0.0.0:8000` inside the container; `API_PORT`
  (env var, default `8000`) controls what host port it's published on.
- `WEBSOCKET_URL` is required - compose refuses to start without it (see
  `.env.example`). `DEFAULT_SPEED`, `TICK`, `WEBSOCKET_RETRY_DELAY`, and
  `LOG_LEVEL` are all optional env var overrides with sane defaults.
- Published events are only logged (`StdoutListener`) at `--log-level DEBUG`
  - at the default `INFO` they produce no output, since there can be one
  per device per tick across many devices. `--log-dir /data/logs`, bind-
  mounted to `LOGS_DIR` on the host (default `./logs`), writes a daily-
  rotating log file kept for 3 days (`logging.handlers.TimedRotatingFileHandler`).
  Docker's own capture of stdout/stderr is separately capped (`max-size`/`max-file` in
  `docker-compose.yml`'s `logging:` block) as a complementary, size-based
  limit on the raw console stream.

`deploy.sh [user@host] [remote_dir]` (Git Bash/Linux/macOS) or
`deploy.ps1 [-RemoteTarget user@host] [-RemoteDir remote_dir]` (native
PowerShell, no Git Bash/WSL needed) copy this directory (including your
`.env`) to a remote host over ssh. Either prompts for whatever isn't
passed in, and both require `.env` to exist first.

Both deliberately only ever sync files - neither runs `docker` on the
remote host itself. Instead, they print the `docker compose up -d --build`
command at the end for you to run there yourself. The ssh user doing the
file sync may not be in that host's `docker` group (or have passwordless
sudo for it) even when it can write to the deploy directory fine, and a
non-interactive `ssh host "docker ..."` often can't see docker even when
an interactive login can (PATH/profile differences, or stale group
membership that only refreshes on a fresh login) - rather than chase every
way that can fail non-interactively, these scripts just don't try.

Auth tries key-based ssh first (fast, silent); if that's not set up, you
get a normal interactive password prompt instead, same as running `ssh`
directly - this never fails just because a key isn't configured.
`deploy.sh` multiplexes its connectivity check and the `rsync` transfer
over one shared connection (`ControlMaster`/`ControlPersist`), so you're
only asked for your password once. `deploy.ps1` does **not** - Windows'
OpenSSH port has never properly supported `ControlMaster`/`ControlPath`
(incomplete AF_UNIX socket support; fails with `getsockname failed: Not a
socket`), so on password auth you'll be prompted twice (once for the
check, once for the transfer).

`deploy.sh` needs `rsync`/`ssh` locally and transfers over `rsync`.
`deploy.ps1` needs `ssh`/`tar` locally (Windows' optional OpenSSH Client
feature, or Git for Windows'; `tar` has shipped in Windows itself since 10
1803) and transfers via `tar` piped over `ssh` - one connection for the
whole sync rather than one per file. Neither touches anything until you
actually run it against a real target.

## Systemd deployment (no docker)

`deploy_service.sh` installs the same configuration as `docker-compose.yml`
as a systemd service instead, running straight out of a copy of this repo
on a Linux host. Copy the files over (e.g. with `deploy.sh`, which leaves
the remote `.venv` alone), then on the host, from that directory:

```
bash deploy_service.sh ws://host:1880/ingest
```

It creates a venv in `./.venv` and installs `requirements.txt` into it
(or, with `USE_VENV=0`, uses `python3` - or `PYTHON` - as-is, which must
already have those packages), writes
`/etc/systemd/system/fileplayer.service` (running as the invoking user),
and enables and (re)starts it - asking for the websocket url if you don't
pass it. It uses `sudo` for the unit and `systemctl`. The same settings as
`.env.example` (`ASSETS_DIR`, `PLAYLIST_FILE`, `API_PORT`, `DEFAULT_SPEED`,
`TICK`, `WEBSOCKET_RETRY_DELAY`, `LOG_LEVEL`, `LOGS_DIR`), plus
`SERVICE_NAME`, `SERVICE_USER` and `STATE_FILE`, can be overridden as env
vars; see the top of the script. Re-run it after redeploying files to pick
them up. Logs go to the journal (`journalctl -u fileplayer -f`) and to
`./logs/fileplayer.log`.

## Tests

```
python -m pytest tests
```

## License

[MIT](LICENSE)
