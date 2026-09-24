import gzip
import json
from pathlib import Path
import subprocess

import pytest
from flask import Response

import app as app_module


def test_unchanged_query_poll_does_not_render_or_repeat_login_requests():
    source = (Path(__file__).resolve().parents[1] / 'templates/crm.html').read_text()
    function = source.split('        async function pollMultiBatchStatus', 1)[1].split(
        '        function startQuerySharedSync', 1
    )[0]
    program = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
let requests = [], renders = 0, statusChecks = 0;
let response = {success: true, job_id: 'job-1', revision: 'r1', running: false};
const context = {
    document: {hidden: false}, URLSearchParams,
    isMultiBatchPolling: false, currentBackgroundQueryJobId: 'job-1',
    backgroundQueryRevision: '', multiBatchPollTimer: null, multiBatchJobs: {items: []},
    sessionStorage: {setItem() {}},
    fetch: async url => { requests.push(url); return {json: async () => response}; },
    applyBackgroundQueryStatus() { renders++; }, setProgress() {},
    captureLastQuerySummary() {}, renderQuerySummary() {}, resetQueryButtons() {},
    checkStatus() { statusChecks++; }, setQueryButtonsRunning() {},
    setInterval() { return 1; }, clearInterval() {},
    rememberQueryNotice(error) { throw Error(error); },
};
vm.createContext(context);
vm.runInContext(FUNCTION, context);
(async () => {
    await context.pollMultiBatchStatus();
    response = {success: true, unchanged: true, revision: 'r1'};
    await context.pollMultiBatchStatus({latest: true});
    assert.equal(renders, 1);
    assert.equal(statusChecks, 1);
    assert.match(requests[1], /latest=1&revision=r1/);
    assert.equal(context.isMultiBatchPolling, false);
    response = {success: true, job_id: 'job-2', revision: 'r2', running: true};
    await context.pollMultiBatchStatus({latest: true});
    assert.equal(renders, 2);
    assert.equal(context.isMultiBatchRunning, true);
    assert.equal(context.backgroundQueryRevision, 'r2');
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace('FUNCTION', json.dumps('async function pollMultiBatchStatus' + function))
    result = subprocess.run(['node', '-e', program], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_html_and_static_text_are_compressed_without_changing_content():
    client = app_module.app.test_client()
    for path in ('/login', '/static/inventory.js?v=test', '/static/aurora.css?v=test'):
        plain = client.get(path)
        compressed = client.get(path, headers={'Accept-Encoding': 'gzip'})
        assert compressed.headers.get('Content-Encoding') == 'gzip', path
        assert gzip.decompress(compressed.data) == plain.data, path
        assert len(compressed.data) < len(plain.data) / 2, path
        assert 'Accept-Encoding' in compressed.vary
        assert compressed.content_length == len(compressed.data)


@pytest.mark.parametrize('encoding', ['', 'identity', 'gzip;q=0, identity', 'br'])
def test_compression_respects_client_encoding(encoding):
    response = app_module.app.test_client().get('/login', headers={'Accept-Encoding': encoding})
    assert 'Content-Encoding' not in response.headers


def test_compressed_static_keeps_cache_and_conditional_requests():
    client = app_module.app.test_client()
    response = client.get('/static/inventory.js?v=test', headers={'Accept-Encoding': 'gzip'})
    assert response.cache_control.max_age == 43200
    cached = client.get('/static/inventory.js?v=test', headers={
        'Accept-Encoding': 'gzip', 'If-None-Match': response.headers['ETag'],
    })
    assert cached.status_code == 304
    assert cached.data == b''
    partial = client.get('/static/inventory.js?v=test', headers={
        'Accept-Encoding': 'gzip', 'Range': 'bytes=0-31',
    })
    assert partial.status_code == 206
    assert len(partial.data) == 32
    assert 'Content-Encoding' not in partial.headers


def test_json_compression_skips_streams_binary_and_no_transform():
    with app_module.app.test_request_context(headers={'Accept-Encoding': 'gzip'}):
        body = b'{"data":"' + b'a' * 4096 + b'"}'
        result = app_module._compress_text_response(Response(body, mimetype='application/json'))
        assert gzip.decompress(result.data) == body
        for response in (
            Response(iter([body]), mimetype='text/event-stream'),
            Response(body, mimetype='application/octet-stream'),
            Response(body, mimetype='text/html', headers={'Cache-Control': 'no-transform'}),
        ):
            result = app_module._compress_text_response(response)
            assert 'Content-Encoding' not in result.headers
