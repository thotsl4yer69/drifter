import sdr_arbiter


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(sdr_arbiter, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(sdr_arbiter, 'LOCK_PATH', tmp_path / '.rtl_sdr.lock')
    monkeypatch.setattr(sdr_arbiter, 'OWNER_PATH', tmp_path / 'rtl_sdr_owner.json')


def test_lease_records_owner_and_releases(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    lease = sdr_arbiter.SDRLease('survey', detail='test', timeout=0)
    assert lease.acquire() is True
    owner = sdr_arbiter.read_owner()
    assert owner['owner'] == 'survey'
    assert owner['detail'] == 'test'
    lease.release()
    assert sdr_arbiter.read_owner()['owner'] == 'idle'


def test_second_lease_fails_while_first_is_held(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    first = sdr_arbiter.SDRLease('hunt', timeout=0)
    second = sdr_arbiter.SDRLease('audio', timeout=0.05)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
        assert sdr_arbiter.read_owner()['owner'] == 'hunt'
    finally:
        first.release()
