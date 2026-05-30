# tests/test_trip_computer.py
"""Smoke tests for trip_computer: distance and fuel accumulation."""
import sys
import time
sys.path.insert(0, 'src')

import pytest
from trip_computer import TripState


FUEL_PRICE = 1.55
TANK_L = 60.0
AVG_L = 12.0


def make_state():
    return TripState(fuel_price=FUEL_PRICE, tank_l=TANK_L, avg_l_per_100=AVG_L)


def test_distance_accumulates():
    state = make_state()
    now = time.time()
    # First tick sets start_ts with dt=0, so 9 of 10 ticks accumulate distance.
    # 9s * (72/3600) km/s = 0.18 km
    for i in range(10):
        state.tick(now + i, speed_kph=72.0, maf_gps=None)
    assert state.distance_km == pytest.approx(0.18, abs=0.005)


def test_fuel_accumulates_from_maf():
    state = make_state()
    now = time.time()
    # 10 ticks at 1s intervals with MAF = 3.8 g/s (typical idle)
    for i in range(10):
        state.tick(now + i, speed_kph=0.0, maf_gps=3.8)
    # fuel = 3.8 / 14.7 * 10s / 1000 / 0.745 ≈ 0.00348 L
    assert state.fuel_l > 0
    assert state.fuel_l < 0.1


def test_to_dict_keys():
    state = make_state()
    d = state.to_dict()
    for key in ('duration_s', 'distance_km', 'fuel_l', 'avg_l_per_100km', 'cost_gbp',
                'fuel_price_per_l', 'speed_kph', 'ts'):
        assert key in d, f"Missing key: {key}"


def test_reset_clears_state():
    state = make_state()
    now = time.time()
    state.tick(now, speed_kph=60.0, maf_gps=5.0)
    state.tick(now + 1, speed_kph=60.0, maf_gps=5.0)
    assert state.distance_km > 0
    state.reset()
    assert state.distance_km == 0.0
    assert state.fuel_l == 0.0
    assert state.fuel_price == FUEL_PRICE  # config preserved


def test_cost_calculation():
    state = make_state()
    now = time.time()
    # Drive 1 hour at 100 km/h with MAF = 10 g/s
    for i in range(3600):
        state.tick(now + i, speed_kph=100.0, maf_gps=10.0)
    d = state.to_dict()
    assert d['cost_gbp'] == pytest.approx(state.fuel_l * FUEL_PRICE, abs=0.01)


def test_instantaneous_consumption():
    state = make_state()
    now = time.time()
    # Give it a speed and MAF so cur_l_per_100km is computed
    state.tick(now, speed_kph=100.0, maf_gps=10.0)
    state.tick(now + 1, speed_kph=100.0, maf_gps=10.0)
    d = state.to_dict()
    assert d['cur_l_per_100km'] is not None
    assert d['cur_l_per_100km'] > 0
