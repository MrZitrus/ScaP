import threading
import time

from scraper import DownloadStatus, StreamScraper


def test_download_status_pause_resume_and_cancel() -> None:
    status = DownloadStatus()
    status.start_download()
    assert status.request_pause() is True
    assert status.get_status()['is_paused'] is True

    released = []
    waiter = threading.Thread(target=lambda: released.append(status.wait_if_paused()))
    waiter.start()
    time.sleep(0.03)
    assert waiter.is_alive()

    assert status.resume() is True
    waiter.join(timeout=1.0)
    assert released == [True]

    assert status.request_pause() is True
    cancelled = threading.Thread(target=lambda: released.append(status.wait_if_paused()))
    cancelled.start()
    time.sleep(0.03)
    assert status.request_cancel() is True
    cancelled.join(timeout=1.0)
    assert released[-1] is False


def test_process_series_applies_episode_selection(monkeypatch, tmp_path) -> None:
    scraper = StreamScraper(download_dir=str(tmp_path))
    scraper.download_status.start_download()
    catalog = {
        'series_name': 'Auswahltest',
        'url': 'https://example.test/show',
        'seasons': [
            {
                'number': 1,
                'url': 'https://example.test/show/season-1',
                'episodes': [
                    {'number': 1, 'title': 'Eins', 'url': 'https://example.test/e1'},
                    {'number': 2, 'title': 'Zwei', 'url': 'https://example.test/e2'},
                ],
            },
            {
                'number': 2,
                'url': 'https://example.test/show/season-2',
                'episodes': [
                    {'number': 1, 'title': 'Drei', 'url': 'https://example.test/e3'},
                ],
            },
        ],
    }
    monkeypatch.setattr(scraper, 'get_series_catalog', lambda url, respect_controls=False: catalog)

    processed = []

    def fake_process(url, series_name, season_num, series_path, episodes=None, progress_state=None):
        processed.append((season_num, [episode['number'] for episode in episodes]))
        progress_state['completed'] += len(episodes)
        return True

    monkeypatch.setattr(scraper, '_process_season', fake_process)
    assert scraper.process_series('https://example.test/show', selection={'1': [2], '2': [1]}) is True
    assert processed == [(1, [2]), (2, [1])]
    assert scraper.download_status.get_status()['total_episodes'] == 2


def test_process_series_reports_episode_failure(monkeypatch, tmp_path) -> None:
    scraper = StreamScraper(download_dir=str(tmp_path))
    scraper.download_status.start_download()
    catalog = {
        'series_name': 'Fehlertest',
        'url': 'https://example.test/show',
        'seasons': [{
            'number': 1,
            'url': 'https://example.test/show/season-1',
            'episodes': [{'number': 1, 'title': 'Eins', 'url': 'https://example.test/e1'}],
        }],
    }
    monkeypatch.setattr(scraper, 'get_series_catalog', lambda url, respect_controls=False: catalog)
    monkeypatch.setattr(
        scraper,
        '_process_season',
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('Episode fehlgeschlagen')),
    )

    assert scraper.process_series('https://example.test/show') is False
