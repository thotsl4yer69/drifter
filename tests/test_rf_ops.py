import rf_ops


def test_au_band_context_is_region_aware_without_overclaiming():
    assert 'UHF-CB' in rf_ops.band_context(476.525, 'AU')
    assert 'airband' in rf_ops.band_context(121.5, 'AU')
    assert 'cellular-band energy' == rf_ops.band_context(935.0, 'AU')
    assert 'IMSI' not in rf_ops.band_context(935.0, 'AU')


def test_summary_candidates_rank_delta_and_dedupe_neighbours():
    summary = {'bins': [
        {'freq_hz': 100e6, 'level_db_max': -80},
        {'freq_hz': 107e6, 'level_db_max': -79},
        {'freq_hz': 430e6, 'level_db_max': -42},
        {'freq_hz': 434e6, 'level_db_max': -40},
        {'freq_hz': 915e6, 'level_db_max': -51},
        {'freq_hz': 1200e6, 'level_db_max': -82},
        {'freq_hz': 1500e6, 'level_db_max': -81},
    ]}
    out = rf_ops.summary_candidates(summary, margin_db=10, max_count=5)
    assert out
    assert out[0]['delta_db'] > 20
    assert not (any(x['freq_mhz'] == 430 for x in out) and any(x['freq_mhz'] == 434 for x in out))


def test_analyse_bins_returns_local_noise_delta_and_bandwidth():
    bins = []
    for i in range(41):
        db = -80.0
        if 18 <= i <= 22:
            db = -42.0 - abs(i - 20) * 1.5
        bins.append({'freq_hz': 433_800_000 + i * 10_000, 'db': db})
    out = rf_ops.analyse_bins(bins, seed_mhz=434.0)
    assert out is not None
    assert 433.9 <= out['freq_mhz'] <= 434.1
    assert out['delta_db'] >= 30
    assert out['bandwidth_khz'] >= 20
    assert '433' in out['context']
