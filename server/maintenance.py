"""Daily online backups and bounded cleanup of expired upload files."""
import logging
from pathlib import Path
from sqlalchemy import select
from .models import AuthSession, CustomerImport, now


def cleanup(database):
    uploads = database.directory / 'uploads'
    with database.session(write=True) as session:
        for login in session.scalars(select(AuthSession).where(AuthSession.expires_at <= now())):
            session.delete(login)
        jobs = list(session.scalars(select(CustomerImport).where(CustomerImport.expires_at <= now())))
        for job in jobs:
            path = Path(job.source_path).resolve()
            if path.parent == uploads.resolve():
                path.unlink(missing_ok=True)
            session.delete(job)


def maintenance(database, stop):
    while not stop.is_set():
        try:
            database.backup('daily')
            cleanup(database)
        except Exception as error:
            logging.error('Server maintenance failed: %s', type(error).__name__)
        if stop.wait(24 * 60 * 60):
            break
