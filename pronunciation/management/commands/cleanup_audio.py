import logging
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Delete generated audio task data older than CLEANUP_DAYS.

    Targets:
      - <MEDIA_ROOT>/audio_tasks/<task_id>/
      - <MEDIA_ROOT>/audio_tmp/<tmp_id>/

    Uses directory mtime as the age signal.
    """

    help = 'Delete audio task files older than CLEANUP_DAYS'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=int(getattr(settings, 'CLEANUP_DAYS', 7) or 7),
            help='Delete items older than this number of days (default: settings.CLEANUP_DAYS or 7)',
        )

    def handle(self, *args, **options):
        days = int(options.get('days') or 7)
        cutoff = timezone.now() - timedelta(days=days)

        media_root = Path(getattr(settings, 'MEDIA_ROOT', 'media'))
        tasks_root = media_root / 'audio_tasks'
        tmp_root = media_root / 'audio_tmp'

        deleted = 0

        for root in (tasks_root, tmp_root):
            if not root.exists() or not root.is_dir():
                continue
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                try:
                    mtime = timezone.datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.get_current_timezone())
                    if mtime < cutoff:
                        shutil.rmtree(child, ignore_errors=False)
                        deleted += 1
                except Exception:
                    logger.exception('Failed to delete %s', child)

        self.stdout.write(self.style.SUCCESS(f"Cleanup completed. Deleted {deleted} directories."))
