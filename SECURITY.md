# Security Policy

## Reporting a vulnerability

If you find a security issue in DRIFTER, please report it privately rather than
opening a public issue:

- Use GitHub's **[Report a vulnerability](../../security/advisories/new)**
  (Security → Advisories) on this repository, **or**
- email the maintainer at the address on the repository owner's profile.

Please include what you found, how to reproduce it, and the impact. We aim to
acknowledge reports within a few days.

## Scope and intended use

DRIFTER is a **defensive, situational-awareness and diagnostics** platform for a
vehicle you own. It also ships network- and RF-testing tooling intended **only
for authorized testing of your own equipment**, gated behind the non-default
`foot` persona (see [CAPABILITIES.md](CAPABILITIES.md)). Using those tools
against systems, networks, vehicles, or radio services you do not own or are not
explicitly authorized to test may be illegal. You are responsible for operating
within the law in your jurisdiction.

## Handling of secrets

- **No secrets are committed to this repository.** The Wi-Fi hotspot PSK is
  generated per node at install (or supplied via `$DRIFTER_HOTSPOT_PSK`) and
  is recoverable only on the device (`nmcli --show-secrets`).
- API keys live only in `/opt/drifter/.env` (git-ignored), seeded from
  `config/.env.example`.
- Per-vehicle profiles (`vehicles/<VIN>.yaml`) can contain a real VIN and are
  operator data. The public tree tracks only the generic default and sanitised
  examples. Real vehicle profiles belong under `/opt/drifter/vehicles/` on the
  node and are ignored by git.

If you are deploying from a fork or an older revision: **rotate any API keys
that were ever committed to git history** (earlier revisions hardcoded live
OpenWeatherMap / Google Maps keys — treat them as compromised).

## Network exposure

- The MQTT broker binds to loopback `127.0.0.1:1883` (both Mosquitto and the
  shipped `config/nanomq.conf`); phones/clients reach the node over HTTP/WS on
  the `10.42.0.0/24` hotspot subnet, never MQTT directly.
- The `/api` control surface is ACL-scoped to the hotspot subnet and localhost.
- That ACL is one shared check, `config.is_local_peer`, used by the dashboard
  `/api`, the OPSEC dashboard, the telemetry/audio WebSockets (ports 8081/8082)
  and the RealDash TCP bridge (35000). Trusted prefixes default to
  `127.0.0.1,10.42.0.` and can be changed with `DRIFTER_TRUSTED_PREFIXES`.
- Listeners bind `0.0.0.0` by default so the hotspot keeps working. Set
  `DRIFTER_BIND_HOST=10.42.0.1` to listen on the hotspot only.
- Optional host firewall: `sudo ./scripts/install-firewall.sh` drops the node's
  service ports (8080/8443/8081/8082/8090/35000/1883) on every interface except
  loopback and the `wlan0` hotspot. SSH and other ports are untouched.
  `--dry-run` checks the ruleset; `--remove` undoes it.
- The fleet API (8420) requires a real login. Create users on the server with
  `python3 /opt/drifter/fleet_server.py set-password <user> [admin|viewer]`;
  with no users configured nobody can log in. `/ws/fleet` needs a token too
  (`?token=` or `Authorization: Bearer`).
- Service-control and OPSEC actions are gated by narrowly-enumerated `sudoers`
  drop-ins (the arsenal set deliberately excludes drive-only units).

## Supported versions

DRIFTER is developed on the default branch; security fixes land there. There is
no long-term-support branch. Deploy from a recent commit.
