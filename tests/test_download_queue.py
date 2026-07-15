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

    with app_module.app.app_context():
        assert app_module.ensure_download_job_schema() == 0

    monkeypatch.setattr(
        app_module,
        'determine_series_target_path',
        lambda url, series_id=None, library_id=None: (None, None),
    )

    first_started = threading.Event()
    release_first = threading.Event()
    calls = []
    received_selections = []

    def fake_download(url, series_path=None, selection=None, progress_cb=None):
        calls.append(url)
        received_selections.append(selection)
        app_module.scraper.download_status.start_download()
        try:
            app_module.scraper.download_status.update(
                current_season=1,
                current_episode=2,
                completed_episodes=1,
                total_episodes=3,
                episode_progress=50.0,
            )
            if progress_cb:
                progress_cb(50.0, None, None, 'Halb fertig')
            if url.endswith('/first'):
                first_started.set()
                deadline = time.monotonic() + 2.0
                while not release_first.wait(timeout=0.02):
                    assert time.monotonic() < deadline
                    assert app_module.scraper.download_status.wait_if_paused()
            if url.endswith('/fails'):
                raise RuntimeError('simulierter Fehler')
            return True
        finally:
            app_module.scraper.download_status.finish_download()

    monkeypatch.setattr(app_module.scraper, 'start_download', fake_download)
    client = app_module.app.test_client()

    catalog_data = {
        'series_name': 'Testserie',
        'url': 'https://example.test/first',
        'seasons': [{
            'number': 1,
            'url': 'https://example.test/first/season-1',
            'episodes': [{'number': 1, 'title': 'Pilot', 'url': 'https://example.test/e1'}],
        }],
    }
    monkeypatch.setattr(app_module.scraper, 'get_series_catalog', lambda url: catalog_data)
    catalog = client.post('/api/download/catalog', json={'url': 'https://example.test/first'})
    assert catalog.status_code == 200
    assert catalog.get_json()['data'] == catalog_data

    first_selection = {'1': [1, 2, 3]}
    first = client.post('/download', json={
        'url': 'https://example.test/first',
        'series_name': 'Erster Auftrag',
        'selection': first_selection,
    })
    assert first.status_code == 202
    assert first.get_json()['job']['selection'] == first_selection
    assert first.get_json()['job']['selected_episode_count'] == 3
    assert first_started.wait(timeout=2.0)

    active = _wait_for(
        lambda: client.get('/api/downloads/status').get_json()['data']['active']
    )
    assert active['progress'] == 50.0
    assert active['episode_progress'] == 50.0
    assert active['current_season'] == 1
    assert active['current_episode'] == 2
    assert active['completed_episodes'] == 1
    assert active['total_episodes'] == 3
    assert active['message'] == 'Halb fertig'

    pause = client.post(f"/api/downloads/{first.get_json()['job']['id']}/pause")
    assert pause.status_code == 200
    assert pause.get_json()['job']['status'] == 'paused'
    paused = client.get('/api/downloads/status').get_json()['data']
    assert paused['is_paused'] is True
    assert paused['active']['status'] == 'paused'

    resume = client.post(f"/api/downloads/{first.get_json()['job']['id']}/resume")
    assert resume.status_code == 202
    assert resume.get_json()['job']['status'] == 'downloading'

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
    assert received_selections[0] == first_selection

    monkeypatch.setattr(
        app_module.scraper,
        'start_download',
        lambda url, series_path=None, selection=None, progress_cb=None: True,
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
        paused_job = app_module.DownloadJob(
            url='https://example.test/paused',
            status='paused',
            progress=41.0,
            message='Bewusst pausiert',
        )
        app_module.db.session.add(paused_job)
        app_module.db.session.commit()
        assert app_module.recover_interrupted_download_jobs() == 1
        assert interrupted.status == 'pending'
        assert interrupted.progress == 0.0
        assert interrupted.started_at is None
        assert paused_job.status == 'paused'
        assert paused_job.progress == 41.0

        app_module._queue_wakeup.set()
        time.sleep(0.05)
        app_module.db.session.expire_all()
        assert app_module.db.session.get(app_module.DownloadJob, interrupted.id).status == 'pending'

        cancel_pending = client.post(f'/api/downloads/{interrupted.id}/cancel')
        assert cancel_pending.status_code == 200
        assert cancel_pending.get_json()['job']['status'] == 'cancelled'

        cancel_paused = client.post(f'/api/downloads/{paused_job.id}/cancel')
        assert cancel_paused.status_code == 200
        assert cancel_paused.get_json()['job']['status'] == 'cancelled'
