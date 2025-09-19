import threading
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func


class DownloadManager:
    """Coordinates download jobs, queue management, and status syncing."""

    def __init__(self, app, scraper, socketio, db, job_model, result_model, logger):
        self.app = app
        self.scraper = scraper
        self.socketio = socketio
        self.db = db
        self.Job = job_model
        self.Result = result_model
        self.logger = logger

        self.queue = deque()
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.queue_paused = False
        self.paused_jobs = set()
        self.current_job_id = None

        self.scraper.set_status_listener(self._handle_status_update)
        self.scraper.set_event_listener(self._handle_event)

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self._restore_pending_jobs()

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------
    def _restore_pending_jobs(self):
        with self.app.app_context():
            pending_statuses = ['queued', 'running', 'paused']
            jobs = (self.Job.query
                    .filter(self.Job.status.in_(pending_statuses))
                    .order_by(self.Job.queue_position.asc(), self.Job.created_at.asc())
                    .all())
            max_position = self._max_queue_position()
            for job in jobs:
                if job.status in ('running', 'paused'):
                    job.status = 'queued'
                    job.started_at = None
                    job.finished_at = None
                    job.progress = 0.0
                if job.queue_position is None or job.queue_position == 0:
                    max_position += 1
                    job.queue_position = max_position
                self.db.session.commit()
                self.queue.append(job.id)

    def _max_queue_position(self):
        with self.app.app_context():
            value = self.db.session.query(func.max(self.Job.queue_position)).scalar()
            return value or 0

    def _next_queue_position(self):
        return self._max_queue_position() + 1

    def enqueue_job(self, url: str, title: str, options: Optional[dict] = None, job_type: str = 'series'):
        options = options or {}
        with self.app.app_context():
            position = self._next_queue_position()
            job = self.Job(
                url=url,
                title=title,
                series_title=options.get('series_title') or title,
                status='queued',
                queue_position=position,
                options=options,
                job_type=job_type,
            )
            self.db.session.add(job)
            self.db.session.commit()
            job_id = job.id
        with self.condition:
            self.queue.append(job_id)
            self.condition.notify_all()
        self._emit_queue_snapshot()
        return job

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------
    def _worker_loop(self):
        while True:
            with self.condition:
                while (not self.queue or self.queue_paused):
                    self.condition.wait()
                job_id = self.queue.popleft()
                if job_id in self.paused_jobs:
                    self.queue.append(job_id)
                    self.condition.wait()
                    continue
            with self.app.app_context():
                job = self.Job.query.get(job_id)
                if not job:
                    continue
                if job.status == 'canceled':
                    continue
                job.status = 'running'
                job.started_at = datetime.utcnow()
                job.progress = 0.0
                job.error_message = None
                self.db.session.commit()
                options = (job.options or {}).copy()
                options['job_id'] = job.id
                options.setdefault('title', job.series_title or job.title or job.url)
            self.current_job_id = job_id
            results: List[Dict[str, Any]] = []
            state = 'failed'
            message = ''
            try:
                result = self.scraper.start_download(job.url, job_options=options)
                state = result.get('state', 'completed' if result.get('success') else 'failed')
                message = result.get('message', '')
                results = result.get('results', [])
            except Exception as exc:
                state = 'failed'
                message = str(exc)
                self.logger.exception(f"Download job {job_id} failed: {exc}")
            with self.app.app_context():
                job = self.Job.query.get(job_id)
                if job:
                    job.status = state
                    job.finished_at = datetime.utcnow()
                    if state == 'completed':
                        job.progress = 100.0
                        job.error_message = None
                    elif message:
                        job.error_message = message
                    self.db.session.commit()
                    self._store_results(job, results)
            self.current_job_id = None
            self._emit_queue_snapshot()

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------
    def _store_results(self, job, results: List[Dict[str, Any]]):
        with self.app.app_context():
            self.Result.query.filter_by(job_id=job.id).delete()
            for item in results:
                result = self.Result(
                    job_id=job.id,
                    season_num=item.get('season_num'),
                    episode_num=item.get('episode_num'),
                    title=item.get('title'),
                    file_path=item.get('output_path'),
                    file_size=item.get('file_size'),
                    language_tag=item.get('language_tag'),
                    success=item.get('success', True),
                    skipped=item.get('skipped', False),
                    error_message=item.get('error'),
                )
                self.db.session.add(result)
            self.db.session.commit()

    # ------------------------------------------------------------------
    # Status handling
    # ------------------------------------------------------------------
    def _handle_status_update(self, status: Dict[str, Any]):
        job_id = status.get('job_id') or self.current_job_id
        if not job_id:
            return
        with self.app.app_context():
            job = self.Job.query.get(job_id)
            if job:
                job.progress = status.get('progress', job.progress)
                job.bytes_downloaded = status.get('bytes_downloaded', job.bytes_downloaded)
                job.bytes_total = status.get('bytes_total', job.bytes_total)
                job.speed = status.get('speed', job.speed)
                job.eta = status.get('eta', job.eta)
                job.status = status.get('state', job.status)
                job.series_title = status.get('current_title') or job.series_title
                job.current_episode_title = status.get('current_episode_title') or job.current_episode_title
                job.language_tag = status.get('language_tag') or job.language_tag
                self.db.session.commit()
        payload = {
            'status': status,
            'queue': self.serialize_queue(),
            'history': self.serialize_history(limit=20),
            'active': self.serialize_active_job()
        }
        self.socketio.emit('status_update', payload)

    def _handle_event(self, event_type: str, data: Dict[str, Any]):
        self.socketio.emit('job_event', data)

    # ------------------------------------------------------------------
    # Public queue controls
    # ------------------------------------------------------------------
    def cancel_job(self, job_id: int):
        with self.app.app_context():
            job = self.Job.query.get(job_id)
            if not job:
                return False, 'Job nicht gefunden'
            if job.status in ('completed', 'failed', 'canceled'):
                return False, 'Job bereits abgeschlossen'
            if job_id == self.current_job_id:
                if self.scraper.download_status.request_cancel():
                    job.status = 'canceled'
                    job.finished_at = datetime.utcnow()
                    self.db.session.commit()
                    return True, 'Aktiver Download wird abgebrochen'
                return False, 'Download konnte nicht abgebrochen werden'
            with self.condition:
                try:
                    self.queue.remove(job_id)
                except ValueError:
                    pass
                self.paused_jobs.discard(job_id)
            job.status = 'canceled'
            job.finished_at = datetime.utcnow()
            job.progress = 0.0
            self.db.session.commit()
        self._emit_queue_snapshot()
        return True, 'Job aus Warteschlange entfernt'

    def pause_job(self, job_id: int):
        with self.app.app_context():
            job = self.Job.query.get(job_id)
            if not job:
                return False, 'Job nicht gefunden'
            if job.status in ('completed', 'failed', 'canceled'):
                return False, 'Job bereits abgeschlossen'
            if job_id == self.current_job_id:
                if self.scraper.download_status.request_pause():
                    job.status = 'paused'
                    self.db.session.commit()
                    self._emit_queue_snapshot()
                    return True, 'Aktiver Download angehalten'
                return False, 'Download konnte nicht pausiert werden'
            with self.condition:
                self.paused_jobs.add(job_id)
            job.status = 'paused'
            self.db.session.commit()
        self._emit_queue_snapshot()
        return True, 'Job pausiert'

    def resume_job(self, job_id: int):
        with self.app.app_context():
            job = self.Job.query.get(job_id)
            if not job:
                return False, 'Job nicht gefunden'
            if job_id == self.current_job_id:
                if self.scraper.download_status.resume():
                    job.status = 'running'
                    self.db.session.commit()
                    self._emit_queue_snapshot()
                    return True, 'Download wird fortgesetzt'
                return False, 'Download konnte nicht fortgesetzt werden'
            with self.condition:
                if job_id in self.paused_jobs:
                    self.paused_jobs.remove(job_id)
                    if job_id not in self.queue:
                        self.queue.appendleft(job_id)
                    self.condition.notify_all()
            job.status = 'queued'
            self.db.session.commit()
        self._emit_queue_snapshot()
        return True, 'Job wieder aufgenommen'

    def toggle_queue_pause(self, paused: bool):
        with self.condition:
            self.queue_paused = paused
            self.condition.notify_all()
        self._emit_queue_snapshot()
        return self.queue_paused

    def get_status_snapshot(self):
        status = self.scraper.download_status.get_status()
        status['job_id'] = self.current_job_id
        return {
            'status': status,
            'queue': self.serialize_queue(),
            'history': self.serialize_history(limit=20),
            'active': self.serialize_active_job(),
            'queue_paused': self.queue_paused
        }

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------
    def serialize_job(self, job, include_results: bool = False) -> Dict[str, Any]:
        data = {
            'id': job.id,
            'external_id': job.external_id,
            'url': job.url,
            'title': job.title,
            'series_title': job.series_title,
            'status': job.status,
            'job_type': job.job_type,
            'progress': job.progress,
            'bytes_downloaded': job.bytes_downloaded,
            'bytes_total': job.bytes_total,
            'speed': job.speed,
            'eta': job.eta,
            'current_episode_title': job.current_episode_title,
            'language_tag': job.language_tag,
            'queue_position': job.queue_position,
            'created_at': job.created_at.isoformat() if job.created_at else None,
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'error_message': job.error_message,
        }
        if include_results:
            data['results'] = [self.serialize_result(res) for res in job.results]
        return data

    def serialize_result(self, result) -> Dict[str, Any]:
        return {
            'id': result.id,
            'job_id': result.job_id,
            'season_num': result.season_num,
            'episode_num': result.episode_num,
            'title': result.title,
            'file_path': result.file_path,
            'file_size': result.file_size,
            'language_tag': result.language_tag,
            'success': result.success,
            'skipped': result.skipped,
            'error_message': result.error_message,
            'created_at': result.created_at.isoformat() if result.created_at else None,
        }

    def serialize_queue(self) -> List[Dict[str, Any]]:
        with self.app.app_context():
            jobs = (self.Job.query
                    .filter(self.Job.status.in_(['queued', 'paused']))
                    .order_by(self.Job.queue_position.asc(), self.Job.created_at.asc())
                    .all())
            return [self.serialize_job(job) for job in jobs]

    def serialize_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.app.app_context():
            jobs = (self.Job.query
                    .filter(self.Job.status.in_(['completed', 'failed', 'canceled']))
                    .order_by(self.Job.finished_at.desc(), self.Job.created_at.desc())
                    .limit(limit)
                    .all())
            return [self.serialize_job(job, include_results=True) for job in jobs]

    def serialize_active_job(self) -> Optional[Dict[str, Any]]:
        if not self.current_job_id:
            return None
        with self.app.app_context():
            job = self.Job.query.get(self.current_job_id)
            if not job:
                return None
            return self.serialize_job(job, include_results=True)

    def _emit_queue_snapshot(self):
        payload = {
            'queue': self.serialize_queue(),
            'history': self.serialize_history(limit=20),
            'active': self.serialize_active_job(),
            'queue_paused': self.queue_paused
        }
        self.socketio.emit('queue_update', payload)
