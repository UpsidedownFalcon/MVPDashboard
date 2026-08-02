# MVP Dashboard — Injury-Risk Prediction

A trainer-facing web dashboard that predicts when trainees are approaching injury.
1–5 wearable devices stream raw IMU data over UDP to an ingest service; a Python
biomechanical pipeline converts it into constant 60Hz metric streams (5 primitives +
1 composite risk index per device); the dashboard shows live charts, historical
rolling windows, regression-based forecasts of the composite, and rules-based
insights. Built in three stages: local real-time biomech, public VPS deployment with
intelligence, then the designed product frontend with login.

**Start here: read [docs/PLAN.md](docs/PLAN.md) first.** It anchors the full doc
suite (TRD, backend schema, app flow, implementation plan, and per-stage task lists).

## Quickstart

_Placeholder — filled in by S1-T13 (compose up → simulator → `/debug` viewer,
including the Windows firewall note for LAN wearables)._

## Configuration

Everything is wired from a single root `.env` (copy [.env.example](.env.example),
which documents every key). No other config location exists on purpose.
