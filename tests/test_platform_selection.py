from __future__ import annotations

from account_identity import migrate_bound_platform, record_current_identity
from product_store import ProductStore


def test_platform_migration_preserves_uid_and_observation(tmp_path, monkeypatch):
    monkeypatch.setenv('XHS_ACCOUNTS_HOME', str(tmp_path/'accounts'))
    monkeypatch.setenv('XHS_PRODUCT_HOME', str(tmp_path/'product'))
    monkeypatch.setattr('account_identity._account_platform', lambda _: 'xiaohongshu')
    old = record_current_identity('alpha', {'logged_in':True, 'user_id':'uid1',
                                  'observed_at':'2026-09-15T00:00:00Z'}, source='test')
    store = ProductStore()
    store.set_setting('runtime_identity_checks', {
        'alpha':{'platform':'xiaohongshu','user_id':'uid1'},
        'other':{'platform':'xiaohongshu','user_id':'uid2'}})
    monkeypatch.setattr('account_identity._account_platform', lambda _: 'rednote')
    result = migrate_bound_platform('alpha','rednote')
    assert result['current']['user_id'] == 'uid1'
    assert result['current']['platform'] == 'rednote'
    assert result['current']['observed_at'] == old['current']['observed_at']
    assert 'alpha' not in store.get_setting('runtime_identity_checks')
    assert 'other' in store.get_setting('runtime_identity_checks')
    assert migrate_bound_platform('alpha','rednote') == result
