"""
Tests for the locking application
"""
import uuid

from datetime import datetime, timedelta
from django.conf import settings

from freezegun import freeze_time

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from locking.exceptions import AlreadyLocked, RenewalError, NonexistentLock, NotLocked, Expired
from locking.models import NonBlockingLock, _get_lock_name
from locking.tasks import clean_expired_locks


class NonBlockingLockTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='NonBlockingLock test')

    def test_acquire_and_release(self):
        """Tests an acquire/release cycle"""
        lock_1 = NonBlockingLock.objects.acquire_lock(self.user)
        self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        lock_1.release()
        self.assertTrue(not NonBlockingLock.objects.is_locked(self.user))
        self.assertRaises(NotLocked, lock_1.release, silent=False)
        lock_2 = NonBlockingLock.objects.acquire_lock(self.user)
        self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        NonBlockingLock.objects.release_lock(lock_2.pk)
        self.assertTrue(not NonBlockingLock.objects.is_locked(self.user))

    @override_settings(USE_TZ=True)
    def test_saved_as_timezone_aware(self):
        """Timezone-aware timestamps should be written to database if USE_TZ is on."""
        lock = NonBlockingLock.objects.acquire_lock(lock_name='foo')
        assert lock.expires_on.tzinfo is not None
        assert lock.created_on.tzinfo is not None
        assert lock.renewed_on.tzinfo is not None

    def test_renew_integrity_error(self):
        lock_1 = NonBlockingLock.objects.acquire_lock(self.user)
        self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        NonBlockingLock.objects.release_lock(lock_1.pk)
        self.assertFalse(NonBlockingLock.objects.is_locked(self.user))
        NonBlockingLock.objects.acquire_lock(self.user)
        self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        self.assertRaises(RenewalError, lock_1.renew)

    def test_obj_with_expired_lock_is_not_locked(self):
        """Tests that manager.is_locked returns False if locks are expired"""
        with freeze_time("2015-01-01 10:00"):
            NonBlockingLock.objects.acquire_lock(self.user, max_age=1)
        with freeze_time("2015-01-01 11:00"):
            self.assertFalse(NonBlockingLock.objects.is_locked(self.user))

    def test_acquire_and_renew(self):
        """Tests an aquire/renew cycle"""
        with freeze_time("2015-01-01 10:00"):
            lock_1 = NonBlockingLock.objects.acquire_lock(self.user)
            expires = lock_1.expires_on
        with freeze_time("2015-01-01 11:00"):
            lock_1.renew()
            self.assertLess(expires, lock_1.expires_on)

            lock_2 = NonBlockingLock.objects.renew_lock(lock_1.pk)
            self.assertEqual(lock_1.pk, lock_2.pk)
            self.assertEqual(lock_1.expires_on, lock_2.expires_on)

    def test_renew_expired(self):
        """Tests renew an expired lock"""
        with freeze_time("2015-01-01 10:00"):
            lock_1 = NonBlockingLock.objects.acquire_lock(self.user, 1)
        self.assertRaises(Expired, lock_1.renew)

    def test_renew_nonexistinglock(self):
        """Tests renewing a non existent lock"""
        self.assertRaises(NonexistentLock, NonBlockingLock.objects.renew_lock, uuid.uuid4())

    def test_release_nonexistinglock(self):
        """Tests release a non existent lock"""
        self.assertRaises(NotLocked, NonBlockingLock.objects.release_lock, uuid.uuid4())

    def test_lock_twice(self):
        """Tests a double locking (lock and try to lock again)"""
        lock = NonBlockingLock.objects.acquire_lock(self.user)
        self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        self.assertRaises(AlreadyLocked, NonBlockingLock.objects.acquire_lock, self.user)
        self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        lock.release()

    def test_unlock_twice(self):
        """Tests a double unlocking (unlock and try to unlock again)"""
        lock = NonBlockingLock.objects.acquire_lock(self.user)
        self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        lock.release()
        self.assertTrue(not NonBlockingLock.objects.is_locked(self.user))
        lock.release()

    def test_model(self):
        """Test the model"""
        lock = NonBlockingLock.objects.acquire_lock(self.user, max_age=10)
        self.assertTrue(lock.locked_object.endswith('%d' % self.user.id))
        self.assertEquals(lock.locked_object, _get_lock_name(self.user))
        self.assertTrue(lock.created_on)
        self.assertEquals(lock.max_age, 10)
        lock.release()
        lock = NonBlockingLock.objects.acquire_lock(lock_name='test_lock', max_age=10)
        self.assertEquals(lock.locked_object, 'test_lock')
        self.assertIsInstance(lock.id, uuid.UUID)

    def test_relock(self):
        """Test to allow lock if lock is expired"""
        with freeze_time("2015-01-01 10:00"):
            lock_1 = NonBlockingLock.objects.acquire_lock(self.user, max_age=10)
        with freeze_time("2015-01-01 11:00"):
            self.assertTrue(lock_1.is_expired)
            # try to lock again
            lock_2 = NonBlockingLock.objects.acquire_lock(self.user, max_age=1)
            self.assertNotEquals(lock_1.created_on, lock_2.created_on)

    def test_expired(self):
        """Test the expired locks"""
        with freeze_time("2015-01-01 10:00"):
            lock_1 = NonBlockingLock.objects.acquire_lock(self.user, max_age=0)
            lock_2 = NonBlockingLock.objects.acquire_lock(lock_1, max_age=1)
        with freeze_time("2015-01-01 11:00"):
            self.assertTrue(not lock_1.is_expired)
            self.assertTrue(lock_2.is_expired)
            expired_locks = NonBlockingLock.objects.get_expired_locks()
            self.assertEquals(len(expired_locks), 1)

    def test_context_manager(self):
        """A lock can be used as a context manager"""
        with NonBlockingLock.objects.acquire_lock(self.user):
            self.assertTrue(NonBlockingLock.objects.is_locked(self.user))
        self.assertFalse(NonBlockingLock.objects.is_locked(self.user))


class CleanExpiredLocksTest(TestCase):
    """Tests correct functioning of the task that cleans expired locks."""
    def setUp(self):
        self.user = User.objects.create(username='hellofoo')

    def test_clean(self):
        """Make expired lock, ensure the management command cleans it up."""
        with freeze_time("2015-01-01 10:00"):
            lock_to_be_released = NonBlockingLock.objects.acquire_lock(self.user, max_age=0)
            NonBlockingLock.objects.acquire_lock(lock_to_be_released, max_age=1)
        with freeze_time("2015-01-01 11:00"):
            # Only the non-expired lock should remain
            clean_expired_locks()
            self.assertEqual(NonBlockingLock.objects.get(), lock_to_be_released)

    def test_implicit_cleaning_disabled(self):
        """If no max_age parameter is given and locks aren't configured to autoexpire, don't clean them up."""
        assert hasattr(settings, 'LOCK_MAX_AGE') is False
        initial_timestamp = datetime(2017, 1, 1)

        with freeze_time(initial_timestamp):
            lock_to_remain = NonBlockingLock.objects.acquire_lock(self.user)
        with freeze_time(initial_timestamp + timedelta(days=9600)):
            clean_expired_locks()
            assert NonBlockingLock.objects.get() == lock_to_remain

    @override_settings(LOCK_MAX_AGE=0)
    def test_implicit_cleaning_set_to_zero(self):
        """If the LOCK_MAX_AGE setting is set to 0, no locks should autoexpire."""
        initial_timestamp = datetime(2017, 1, 1)

        with freeze_time(initial_timestamp):
            lock_to_remain = NonBlockingLock.objects.acquire_lock(self.user)
        with freeze_time(initial_timestamp + timedelta(days=9600)):
            clean_expired_locks()
            assert NonBlockingLock.objects.get() == lock_to_remain

    @override_settings(LOCK_MAX_AGE=1)
    def test_implicit_cleaning_set_to_nonzero(self):
        """If the LOCK_MAX_AGE setting is bigger than 0, locks should autoexpire."""
        initial_timestamp = datetime(2017, 1, 1)

        with freeze_time(initial_timestamp):
            NonBlockingLock.objects.acquire_lock(self.user)
        with freeze_time(initial_timestamp + timedelta(seconds=1)):
            clean_expired_locks()
            assert NonBlockingLock.objects.count() == 0
