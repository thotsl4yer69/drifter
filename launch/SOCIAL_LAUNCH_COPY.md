# DRIFTER VIM — Launch Copy Bank

## Hero post

THE FAULT VANISHED.  
THE EVIDENCE DIDN'T.

DRIFTER VIM is a dedicated local vehicle computer built around Raspberry Pi.

It watches OBD-II telemetry, logs drives and preserves a bounded evidence window around incidents: 90 seconds before, the event, and 45 seconds after.

The point isn't to pretend software magically knows what broke.

The point is to keep the evidence you normally lose.

Hardware-integrated prototype. Founding beta. Primary validation vehicle: 2004 Jaguar X-Type.

MAZLABZ / DRIFTER VIM

## Short reel caption

Your car can stop misbehaving before you get a scanner on it.

DRIFTER VIM keeps the window around the event.

90 seconds before. Fault capture. 45 seconds after.

Founding beta.

## Technical community post

I'm building DRIFTER VIM as a persistent Raspberry Pi vehicle node rather than a phone-connected scan session.

The current stack separates adapter state, ECU state and live PID flow; records drive telemetry; and keeps a bounded incident buffer so an intermittent event can preserve what changed before and after it.

The primary validation vehicle is a 2004 Jaguar X-Type using the ELM327/K-line path. The project is still in hardware acceptance, so I'm explicitly not claiming universal compatibility yet.

I'm looking for technically capable OBD-II testers once the primary field gate is green.

## Profile bio

Vehicle intelligence module by MAZLABZ.  
Local OBD-II telemetry · diagnostics · incident evidence.  
Hardware-integrated prototype / founding beta.
