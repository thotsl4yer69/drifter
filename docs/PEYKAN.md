# Peykan integration — DRIFTER VIM

Peykan 0.2.1 (MIT): https://github.com/farzadnadiri/Peykan

DRIFTER can now run Peykan's actual MCP server alongside its existing OBD
and CAN bridges. It does **not** change which transport feeds the safety engine,
and the Jaguar's ELM327/K-line path remains the primary validated target.

## Install on the Pi (opt-in)

First deploy DRIFTER main as normal; then from the DRIFTER checkout:

    sudo bash scripts/install-peykan.sh --demo

This starts Peykan's virtual ECU simulator, the MCP server bound to loopback
127.0.0.1:6278/mcp, and the MQTT evidence bridge. Vivi never treats simulated
values as actual vehicle readings.

For a vehicle with a genuine SocketCAN interface and a **matching real DBC**:

    sudo bash scripts/install-peykan.sh --socketcan can0 --dbc /absolute/vehicle.dbc

The script requires an existing CAN interface and a readable DBC. The bundled
Peykan sample DBC **must not** be treated as a mapping of the Jaguar or any
other real car. A 2004 X-Type's OBD connector may use K-line, which Peykan's
SocketCAN backend cannot read. Do not plug into a vehicle expecting this
integration to replace DRIFTER's ELM327 diagnostics.

The Peykan deployment is separately installed in
/opt/drifter/peykan-venv (pinned at 0.2.1). No Peykan dependency changes
the primary DRIFTER venv. Both optional services run as the 'drifter' user,
are memory-capped, and stay outside config.SERVICES and DRIFTER mode/health
requirements, so first boot and diag mode are unchanged.

## MQTT and Vivi

- drifter/peykan/status — retained status (live/simulated/no-data/unavailable/offline)
- drifter/peykan/snapshot — decoded *fresh* signals (max observed age 5s)
- drifter/peykan/analysis/request — JSON {"id":"my-check","path":"sample"}
- drifter/peykan/analysis/result — JSON report or an explicit failure

The request accepts "sample" or a basename with an .asc, .blf, .trc, .log
or .csv extension in /opt/drifter/peykan-logs. No arbitrary file paths.
Only the snapshot and log-analysis MCP tools are callable by the bridge;
no OBD request, UDS write, fault injection, or bus replay tools are exposed
through DRIFTER. Peykan transmit and write services are explicitly disabled
in the dedicated env file, including in demo mode.

Vivi appends only fresh socketcan evidence to its prompt, tagged as
independent DBC-decoded CAN observations. Simulated or stale values can
never be reported as live readings.

## Verify

    sudo systemctl status drifter-peykan-mcp drifter-peykan-bridge
    curl -fsS http://127.0.0.1:6278/healthz
    mosquitto_sub -C 1 -t drifter/peykan/status
    mosquitto_pub -t drifter/peykan/analysis/request -m '{"id":"bench","path":"sample"}'
    mosquitto_sub -C 1 -t drifter/peykan/analysis/result

Logs: journalctl -u drifter-peykan-mcp -u drifter-peykan-bridge -n 80

Disable without affecting DRIFTER's main vehicle services:

    sudo systemctl disable --now drifter-peykan-bridge drifter-peykan-mcp

The integration requires Pi deployment and real vehicle field acceptance
before live CAN functionality can be claimed as tested.
