import rf_ops


def test_au_band_context_is_region_aware_without_overclaiming():
    assert 'UHF-CB' in rf_ops.band_context(476.525, 'AU')
    assert 'airband' in rf_ops.band_context(121.5, 'AU')
    assert rf_ops.band_context(935.0, 'AU') == 'cellular-band energy'
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


def test_summary_candidates_accept_mean_only_bins():
    summary = {'bins': [
        {'freq_hz': 100e6, 'level_db_mean': -81},
        {'freq_hz': 200e6, 'level_db_mean': -80},
        {'freq_hz': 433.92e6, 'level_db_mean': -42},
        {'freq_hz': 800e6, 'level_db_mean': -82},
        {'freq_hz': 1200e6, 'level_db_mean': -79},
    ]}
    out = rf_ops.summary_candidates(summary, margin_db=10, max_count=3)
    assert out
    assert abs(out[0]['freq_mhz'] - 433.92) < 0.01


def test_summary_candidates_center_and_cover_downsampled_group():
    summary = {
        'scan_range_mhz': '24-1766',
        'bin_count': 256,
        'bins': [
            {'freq_hz': 24e6, 'level_db_max': -82},
            {'freq_hz': 30.8e6, 'level_db_max': -81},
            {'freq_hz': 430.0e6, 'level_db_max': -40},
            {'freq_hz': 900.0e6, 'level_db_max': -80},
            {'freq_hz': 1500.0e6, 'level_db_max': -83},
        ],
    }
    out = rf_ops.summary_candidates(summary, margin_db=10, max_count=3)
    assert out
    candidate = out[0]
    expected_group_width = (1766 - 24) / 256
    assert candidate['freq_mhz'] > candidate['summary_start_mhz']
    assert candidate['target_span_mhz'] >= expected_group_width
    low = candidate['freq_mhz'] - candidate['target_span_mhz'] / 2
    high = candidate['freq_mhz'] + candidate['target_span_mhz'] / 2
    assert low <= candidate['summary_start_mhz']
    assert high >= candidate['summary_start_mhz'] + expected_group_width


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
