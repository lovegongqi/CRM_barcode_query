import pathlib
import re

from app import app


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_versioned_static_assets_are_cached_without_caching_unversioned_files():
    client = app.test_client()
    versioned = client.get('/static/aurora.css?v=123')
    unversioned = client.get('/static/aurora.css')

    assert versioned.status_code == 200
    assert versioned.cache_control.max_age == 12 * 60 * 60
    assert unversioned.cache_control.max_age is None


def test_page_stylesheets_and_scripts_have_cache_busting_versions():
    for template in (ROOT / 'templates').glob('*.html'):
        html = template.read_text(encoding='utf-8')
        for url in re.findall(r'<(?:link|script|img)\b[^>]+(?:href|src)="(/static/[^\"]+)"', html):
            assert '?v=' in url or '{{' in url, (template.name, url)
