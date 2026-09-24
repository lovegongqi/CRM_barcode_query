import app as app_module


def _account(username):
    return {
        'id': username,
        'username': username,
        'display_name': username,
        'password': f'{username}-password',
        'permissions': ['crm', 'results', 'transfer', 'inbound', 'inventory'],
        'updated_at': '',
    }


def _login(client, username):
    response = client.post('/api/app-auth/login', json={
        'username': username,
        'password': f'{username}-password',
    })
    assert response.get_json()['success']


def test_theme_choice_is_saved_per_account_and_applies_to_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ACCOUNTS_FILE', str(tmp_path / 'accounts.json'))
    app_module.save_accounts([_account('alice'), _account('bob')])
    alice = app_module.app.test_client()
    bob = app_module.app.test_client()
    _login(alice, 'alice')
    _login(bob, 'bob')

    assert alice.get('/api/app-auth/status').get_json()['account']['theme'] == 'light'
    assert b'/static/light_theme.css' in alice.get('/transfer').data
    assert b'id="themeSelect"' in alice.get('/accounts').data

    response = alice.post('/api/account/theme', json={'theme': 'dark'})
    assert response.get_json()['success']
    assert response.get_json()['account']['theme'] == 'dark'
    assert next(row for row in app_module.load_accounts() if row['username'] == 'alice')['theme'] == 'dark'
    for path in ('/crm', '/results', '/transfer', '/inbound', '/inventory',
                 '/service-close', '/product-library', '/accounts'):
        page = alice.get(path)
        assert page.status_code == 200, path
        assert b'/static/aurora.css' in page.data, path
        assert b'/static/light_theme.css' not in page.data, path
    assert b'/static/light_theme.css' in bob.get('/transfer').data

    fresh_session = app_module.app.test_client()
    _login(fresh_session, 'alice')
    assert fresh_session.get('/api/app-auth/status').get_json()['account']['theme'] == 'dark'
    assert fresh_session.post('/api/account/theme', json={'theme': 'light'}).get_json()['success']
    assert b'/static/light_theme.css' in fresh_session.get('/transfer').data


def test_theme_choice_rejects_invalid_values_and_requires_login(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ACCOUNTS_FILE', str(tmp_path / 'accounts.json'))
    app_module.save_accounts([_account('alice')])
    anonymous = app_module.app.test_client()
    response = anonymous.post('/api/account/theme', json={'theme': 'dark'})
    assert response.status_code == 401

    _login(anonymous, 'alice')
    response = anonymous.post('/api/account/theme', json={'theme': 'blue'})
    assert response.status_code == 400
    assert anonymous.get('/api/app-auth/status').get_json()['account']['theme'] == 'light'
