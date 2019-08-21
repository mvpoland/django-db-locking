from celery import shared_task

from locking.models import NonBlockingLock


@shared_task
def clean_expired_locks():
    """
    Delete all expired locks.
    """
    NonBlockingLock.objects.get_expired_locks().delete()
