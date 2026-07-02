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
- Per-vehicle profiles (`vehicles/<VIN>.yaml`) contain a real VIN and are
  operator PII. `.gitignore` prevents **new** VIN profiles from being committed
  (only `vehicles/default.yaml` is tracked). One real profile
  (`vehicles/SAJEA51D44XD39283.yaml`) was committed before this policy and is
  still tracked — before publishing, remove it with
  `git rm --cached vehicles/SAJEA51D44XD39283.yaml` (it stays on the Pi under
  `/opt/drifter/vehicles/`). See `RELEASE-CHECKLIST.md`.

If you are deploying from a fork or an older revision: **rotate any API keys
that were ever committed to git history** (earlier revisions hardcoded live
OpenWeatherMap / Google Maps keys — treat them as compromised).

## Network exposure

- The MQTT broker binds to loopback `127.0.0.1:1883` (both Mosquitto and the
  shipped `config/nanomq.conf`); phones/clients reach the node over HTTP/WS on
  the `10.42.0.0/24` hotspot subnet, never MQTT directly.
- The `/api` control surface is ACL-scoped to the hotspot subnet and localhost.
- Service-control and OPSEC actions are gated by narrowly-enumerated `sudoers`
  drop-ins (the arsenal set deliberately excludes drive-only units).

## Supported versions

DRIFTER is developed on the default branch; security fixes land there. There is
no long-term-support branch. Deploy from a recent commit.
