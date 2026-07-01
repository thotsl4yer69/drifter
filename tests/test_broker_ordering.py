"""Broker-ordering invariants.

DRIFTER runs either Mosquitto (default) or NanoMQ (--with-nanomq). To keep
service start-up ordering correct regardless of which broker is installed,
every MQTT consumer must order against the stable `drifter-broker.target`
anchor and NEVER against a concrete `nanomq.service` / `mosquitto.service`
(an `After=` on an uninstalled broker is silently a no-op, letting consumers
race the real broker). install.sh wires the target to the concrete broker via
a generated drop-in.

These tests guard that invariant so a future unit edit can't reintroduce a
raw broker dependency.
"""
import glob
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES_DIR = os.path.join(REPO, "services")
BROKER_TARGET = "drifter-broker.target"
CONCRETE_BROKERS = {"nanomq.service", "mosquitto.service"}


def _unit_files():
    return sorted(glob.glob(os.path.join(SERVICES_DIR, "*.service")))


def _ordering_tokens(text, key):
    """Return the union of tokens across all `key=` lines (After/Wants)."""
    toks = set()
    for line in text.splitlines():
        if line.startswith(key + "="):
            toks.update(line[len(key) + 1:].split())
    return toks


def test_broker_target_unit_exists():
    assert os.path.isfile(os.path.join(SERVICES_DIR, BROKER_TARGET)), (
        "services/drifter-broker.target is the anchor every MQTT consumer "
        "orders against — it must ship."
    )


def test_no_unit_references_a_concrete_broker():
    offenders = []
    for path in _unit_files():
        with open(path) as f:
            text = f.read()
        refs = (_ordering_tokens(text, "After") | _ordering_tokens(text, "Wants")) & CONCRETE_BROKERS
        if refs:
            offenders.append(f"{os.path.basename(path)}: {sorted(refs)}")
    assert not offenders, (
        "These units order against a concrete broker instead of "
        f"{BROKER_TARGET}:\n  " + "\n  ".join(offenders)
    )


def test_broker_target_ordering_is_symmetric():
    """A unit that Wants the broker target must also order After it (and vice
    versa) — otherwise it can start before the broker is actually up."""
    bad = []
    for path in _unit_files():
        with open(path) as f:
            text = f.read()
        after = BROKER_TARGET in _ordering_tokens(text, "After")
        wants = BROKER_TARGET in _ordering_tokens(text, "Wants")
        if after != wants:
            bad.append(
                f"{os.path.basename(path)}: After={after} Wants={wants}"
            )
    assert not bad, (
        "drifter-broker.target must appear in BOTH After= and Wants= (or "
        "neither):\n  " + "\n  ".join(bad)
    )
