#!/bin/bash
# Terminal launcher multiplexer for the DRIFTER desktop icons. Runs the
# requested operator command in a terminal window and (for one-shot
# commands) pauses so the output stays readable. Follow commands
# (logs/mqtt) run until Ctrl-C.
mode="${1:-status}"
hr() { printf '\n\033[0;33m── %s ──\033[0m\n' "$1"; }

case "$mode" in
  diagnose) hr "drifter diagnose"; drifter diagnose; pause=1 ;;
  status)   hr "drifter status";   drifter status;   pause=1 ;;
  health)   hr "drifter healthz";  drifter healthz;  pause=1 ;;
  logs)     hr "drifter-dashboard logs — Ctrl-C to exit"
            exec journalctl -u drifter-dashboard -f ;;
  mqtt)     hr "MQTT firehose (drifter/#) — Ctrl-C to exit"
            exec mosquitto_sub -h localhost -t 'drifter/#' -v ;;
  restart)  hr "restart ALL drifter services"
            read -rp "Confirm restart of ALL drifter services? [y/N] " a
            if [[ "$a" =~ ^[Yy] ]]; then sudo drifter restart all; else echo "aborted."; fi
            pause=1 ;;
  *)        echo "unknown mode: $mode"; pause=1 ;;
esac

if [ "${pause:-0}" = 1 ]; then
  echo; read -rsn1 -p "── press any key to close ──"; echo
fi
