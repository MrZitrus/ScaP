import importlib
import threading
import time


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError('Queue-Zustand wurde nicht rechtzeitig erreicht')


def test_persistent_queue_transitions(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('SCAP_STREAMS_DB_PATH', str(tmp_path / 'queue.db'))
    app_module = importlib.import_module('app')

    monkeypatch.setattr(
        app_module,
        'determine_series_target_path',
        lambda url, series_id=None, library_id=None: (None, None),
    )

    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def fake_download(url, series_path=None, progress_cb=None):
        calls.append(url)
        app_module.scraper.download_status.start_download()
        try:
            if progress_cb:
                progress_cb(50.0, None, None, 'Halb fertig')
            if url.endswith('/first'):
                first_started.set()
                assert release_first.wait(timeout=2.0)
            if url.endswith('/fails'):
                raise RuntimeError('simulierter Fehler')
            return True
        finally:
            app_module.scraper.download_status.finish_download()

    monkeypatch.setattr(app_module.scraper, 'start_download', fake_download)
    client = app_module.app.test_client()

    first = client.post('/download', json={
        'url': 'https://example.test/first',
        'series_name': 'Erster Auftrag',
    })
    assert first.status_code == 202
    assert first_started.wait(timeout=2.0)

    active = _wait_for(
        lambda: client.get('/api/downloads/status').get_json()['data']['active']
    )
    assert active['progress'] == 50.0
    assert active['message'] == 'Halb fertig'

    cancelled = client.post('/download', json={
        'url': 'https://example.test/cancelled',
        'series_name': 'Abgebrochener Auftrag',
    })
    failed = client.post('/download', json={
        'url': 'https://example.test/fails',
        'series_name': 'Fehlgeschlagener Auftrag',
    })
    assert cancelled.status_code == 202
    assert failed.status_code == 202

    cancelled_id = cancelled.get_json()['job']['id']
    failed_id = failed.get_json()['job']['id']
    cancel_response = client.post(f'/api/downloads/{cancelled_id}/cancel')
    assert cancel_response.status_code == 200
    assert cancel_response.get_json()['job']['status'] == 'cancelled'

    release_first.set()

    def terminal_states():
        payload = client.get('/api/downloads/status').get_json()['data']
        states = {job['id']: job['status'] for job in payload['history']}
        expected = {
            first.get_json()['job']['id']: 'completed',
            cancelled_id: 'cancelled',
            failed_id: 'failed',
        }
        return payload if all(states.get(job_id) == status for job_id, status in expected.items()) else None

    payload = _wait_for(terminal_states)
    failed_job = next(job for job in payload['history'] if job['id'] == failed_id)
    assert failed_job['error'] == 'simulierter Fehler'
    assert calls == ['https://example.test/first', 'https://example.test/fails']

    monkeypatch.setattr(
        app_module.scraper,
        'start_download',
        lambda url, series_path=None, progress_cb=None: True,
    )
    retry = client.post(f'/api/downloads/{failed_id}/retry')
    assert retry.status_code == 202

    def retry_completed():
        payload = client.get('/api/downloads/status').get_json()['data']
        retried = next((job for job in payload['history'] if job['id'] == failed_id), None)
        return retried if retried and retried['status'] == 'completed' else None

    retried = _wait_for(retry_completed)
    assert retried['progress'] == 100.0
    assert retried['error'] is None

    with app_module.app.app_context():
        interrupted = app_module.DownloadJob(
            url='https://example.test/interrupted',
            status='downloading',
            progress=73.0,
            message='Lief beim Neustart',
        )
        app_module.db.session.add(interrupted)
        app_module.db.session.commit()
        assert app_module.recover_interrupted_download_jobs() == 1
        assert interrupted.status == 'pending'
        assert interrupted.progress == 0.0
        assert interrupted.started_at is None
