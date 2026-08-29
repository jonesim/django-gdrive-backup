"""Lifecycle-managed tiers for database dumps (s3-compatible destinations only).

Instead of deleting old backups from the application (prune_backups.py), every dump is
written to a unique, date-partitioned key under an 'hourly' prefix and the bucket's own
lifecycle rules do all the deleting. A scheduled job promotes one dump per day into
'daily' and one per month into 'monthly' with server-side copies, so each tier can carry
a different lifecycle rule:

    hourly/2026/07/30/db_2026_07_30_14_05_37.dump    expire after ~15 days
    daily/db_2026-07-30.dump                         expire after ~91 days
    monthly/db_2026-07.dump                          no rule - kept forever

The application never deletes anything, so the backup credential needs no delete
permission and retention survives a compromised app server. This module owns the layout
so the writer (backup_db), the promoter and the reader cannot drift apart - it must not
import backup_db, which imports this.
"""
import calendar
import datetime
import re

DUMP_EXTENSION = 'dump'
DB_FILE_EXTENSIONS = ('.' + DUMP_EXTENSION, '.bz2', '.gz', '.sql')

HOURLY = 'hourly'
DAILY = 'daily'
MONTHLY = 'monthly'
TIER_DIRS = (HOURLY, DAILY, MONTHLY)

# How long each tier is meant to be kept, in days - what the destination's lifecycle rules
# should say. None means kept indefinitely, which is the default for the monthly archive.
# The application never applies these: they are what the setup page generates rules from
# and checks the real rules against. Override per config with
# db_tiers = {'expire_days': {'monthly': 2557}}  (7 years)
DEFAULT_EXPIRE_DAYS = {HOURLY: 15, DAILY: 91, MONTHLY: None}

# How long a hidden (previous) version survives before the bucket purges it - B2's
# daysFromHidingToDeleting, S3's noncurrent-version expiry. Expiry only hides a dump, so
# this is also the window in which anything hidden or overwritten by an attacker can still
# be recovered: the same knob is the janitor and the undo window. Override per config with
# db_tiers = {'purge_days': 7}
DEFAULT_PURGE_DAYS = 1

# Who does the deleting. DELETE_LIFECYCLE (the default) leaves it to the destination's
# own rules, so the application needs no delete permission at all. DELETE_APP has the
# application delete aged-out dumps itself - visible and logged, at the price of a
# credential that can delete, which is why it pairs with lock_days on the tier that
# matters: an object-lock retention cannot be shortened by anything holding that key.
DELETE_LIFECYCLE = 'lifecycle'
DELETE_APP = 'app'
DELETE_MODES = (DELETE_LIFECYCLE, DELETE_APP)
LOCK_MODES = ('COMPLIANCE', 'GOVERNANCE')

# seconds, unlike the flat layout's minute precision: with lifecycle-only retention and
# no versioning, two dumps in the same minute overwriting each other is real data loss.
# Fixed-width and zero-padded, so lexical order of the names is chronological order.
TIERED_STAMP = '%Y_%m_%d_%H_%M_%S'
LEGACY_STAMP = '%Y_%m_%d_%H_%M'

# one less than the shortest sensible hourly lifecycle rule - looking further back only
# chases objects the rule has already deleted
DEFAULT_CATCHUP_DAYS = 14

# the base is greedy so a schema called '2026' still parses as schema_2026, not as a
# stray timestamp fragment
DUMP_RE = re.compile(r'^(?P<base>.+)_(?P<stamp>\d{4}_\d{2}_\d{2}_\d{2}_\d{2}(?:_\d{2})?)\.(?P<ext>[A-Za-z0-9.]+)$')
DAILY_RE = re.compile(r'^(?P<base>.+)_(?P<day>\d{4}-\d{2}-\d{2})\.(?P<ext>[A-Za-z0-9.]+)$')


def hourly_dir(when):
    return f'{HOURLY}/{when:%Y/%m/%d}'


def hourly_name(base, when, extension=DUMP_EXTENSION):
    return f'{base}_{when:{TIERED_STAMP}}.{extension}'


def daily_name(base, day, extension=DUMP_EXTENSION):
    return f'{base}_{day:%Y-%m-%d}.{extension}'


def monthly_name(base, month, extension=DUMP_EXTENSION):
    return f'{base}_{month:%Y-%m}.{extension}'


def parse_dump(name):
    """('db'|'schema_x'|'table_y', datetime, extension) for an hourly/flat dump name,
    or None when the name is not one of ours."""
    match = DUMP_RE.match(name)
    if not match:
        return None
    stamp = match.group('stamp')
    stamp_format = TIERED_STAMP if stamp.count('_') == TIERED_STAMP.count('_') else LEGACY_STAMP
    try:
        when = datetime.datetime.strptime(stamp, stamp_format)
    except ValueError:
        return None
    return match.group('base'), when, match.group('ext')


def parse_daily(name):
    """('db', date, extension) for a daily dump name, or None."""
    match = DAILY_RE.match(name)
    if not match:
        return None
    try:
        day = datetime.datetime.strptime(match.group('day'), '%Y-%m-%d').date()
    except ValueError:
        return None
    return match.group('base'), day, match.group('ext')


def backup_time(name):
    """The time the dump was taken, read from an hourly, daily or monthly name.
    Daily and monthly names only carry a date, so they come back at midnight - the
    exact time is kept in the backup_time metadata written at promotion."""
    parsed = parse_dump(name)
    if parsed:
        return parsed[1]
    parsed = parse_daily(name)
    if parsed:
        return datetime.datetime.combine(parsed[1], datetime.time())
    match = re.match(r'^.+_(?P<month>\d{4}-\d{2})\.[A-Za-z0-9.]+$', name)
    if match:
        return datetime.datetime.strptime(match.group('month'), '%Y-%m')


def tier_prefixes(base_prefix):
    """{tier: key prefix} for the tiers under a db folder - what a lifecycle rule has to
    match. Kept here rather than on BackupDb so the setup page can work it out from
    settings alone, with no database and no storage connection."""
    return {tier: f'{base_prefix}/{tier}/' for tier in TIER_DIRS}


def tier_of(file_id, base_prefix):
    """Which tier a stored file is in, from its key - derived rather than trusted from
    metadata so pre-existing and hand-copied objects are labelled correctly."""
    for tier in TIER_DIRS:
        if file_id.startswith(f'{base_prefix}/{tier}/'):
            return tier


def month_start(day):
    return day.replace(day=1)


class DbTierPromoter:
    """Promote database dumps between the lifecycle-managed tiers of one db folder with
    server-side copies. Needs no database connection and never deletes anything.

    Re-running is a no-op: a tier object that already exists is never re-copied, so the
    job can safely run nightly and self-heals after missed runs."""

    def __init__(self, storage, folder, logger, lock_days=None, lock_mode=None):
        """:param lock_days: {tier: object-lock days} stamped on the copy - normally only
        the monthly archive, so that the tier nothing else protects cannot be deleted by
        anything holding the backup credential"""
        self.storage = storage
        self.folder = folder
        self.logger = logger
        self.lock_days = lock_days or {}
        self.lock_mode = lock_mode
        self.daily_folder = storage.ensure_folder(DAILY, parent=folder)
        self.monthly_folder = storage.ensure_folder(MONTHLY, parent=folder)
        self.stats = {'daily': 0, 'monthly': 0, 'skipped': 0, 'deleted': 0, 'empty_days': [], 'errors': []}

    def promote(self, as_of=None, days=DEFAULT_CATCHUP_DAYS, resume=False, warn_empty=True):
        """
        :param days: how many days back to look for dumps that were never promoted
        :param resume: only look at days after the newest one already promoted, so a run
                       that happens after every backup costs one listing instead of `days`
                       of them. A full scan still happens when nothing has been promoted
                       yet, and after an outage it catches up by itself
        :param warn_empty: log days that had no dumps - what a stopped backup looks like
                           from here, but noise when promotion runs with every backup
        """
        as_of = as_of or datetime.date.today()
        daily_files = {f['name']: f for f in self.storage.list_files(self.daily_folder)}
        monthly_names = {f['name'] for f in self.storage.list_files(self.monthly_folder)}
        self.promote_daily(as_of, days, daily_files, resume=resume, warn_empty=warn_empty)
        # always after the daily step: on the first of the month the daily object the
        # monthly copy comes from may have been created seconds ago by that step
        self.promote_monthly(as_of, daily_files, monthly_names)
        return self.stats

    def promote_daily(self, as_of, days, daily_files, resume=False, warn_empty=True):
        promoted = [parse_daily(name)[1] for name in daily_files if parse_daily(name)]
        after = max(promoted) if resume and promoted else None
        # oldest first so a backfill after an outage completes in chronological order
        for offset in range(days, 0, -1):
            day = as_of - datetime.timedelta(days=offset)
            if after and day <= after:
                continue
            day_folder = self.storage.ensure_folder(hourly_dir(day), parent=self.folder)
            dumps = {}
            for f in self.storage.list_files(day_folder):
                parsed = parse_dump(f['name'])
                if parsed:
                    dumps.setdefault((parsed[0], parsed[2]), []).append((f['name'], parsed[1], f))
            if not dumps:
                self.stats['empty_days'].append(day.isoformat())
                if warn_empty:
                    self.logger.warning(f'No database dumps to promote for {day} in {self.folder["id"]}')
                continue
            for (base, extension), group in sorted(dumps.items()):
                name = daily_name(base, day, extension)
                if name in daily_files:
                    self.stats['skipped'] += 1
                    continue
                # names are fixed-width timestamps, so lexically newest is the latest dump
                source_name, taken, source = max(group)
                copied = self.copy(source, self.daily_folder, name, DAILY, taken)
                if copied:
                    daily_files[name] = copied
                    self.stats['daily'] += 1

    def promote_monthly(self, as_of, daily_files, monthly_names):
        months = {}
        for name, f in daily_files.items():
            parsed = parse_daily(name)
            if parsed:
                base, day, extension = parsed
                months.setdefault((base, extension, month_start(day)), []).append((day, f))
        current_month = month_start(as_of)
        for (base, extension, month), group in sorted(months.items(), key=lambda i: i[0][2]):
            if month >= current_month:
                continue  # still being written to
            name = monthly_name(base, month, extension)
            if name in monthly_names:
                self.stats['skipped'] += 1
                continue
            last_day = month.replace(day=calendar.monthrange(month.year, month.month)[1])
            source_day, source = max(group)
            if source_day != last_day:
                self.logger.warning(f'No dump for {last_day} - promoting {source["id"]} to {name}')
            copied = self.copy(source, self.monthly_folder, name, MONTHLY)
            if copied:
                monthly_names.add(name)
                self.stats['monthly'] += 1

    def prune(self, as_of=None, expire_days=None):
        """Delete dumps the tier policy says are past their age, when the application
        rather than the destination's rules is doing the deleting. A tier with no
        expire_days is never touched, and a locked object refuses the delete - both are
        the point rather than a failure, so a delete that is refused is a warning."""
        as_of = as_of or datetime.date.today()
        expire_days = expire_days or {}
        deleted = 0
        for tier in TIER_DIRS:
            days = expire_days.get(tier)
            if not days:
                continue
            oldest = as_of - datetime.timedelta(days=days)
            for stored, taken in self.tier_files(tier):
                if taken and taken.date() < oldest:
                    try:
                        self.storage.delete(stored['id'])
                    except Exception as e:  # noqa: BLE001 - object lock, or a key that
                        # cannot delete: the backup itself has already succeeded
                        self.stats['errors'].append(f"{stored['id']}: {e}")
                        self.logger.warning(f'Could not delete {stored["id"]}: {e}')
                        continue
                    deleted += 1
                    self.logger.info(f'Deleted {stored["id"]} - older than {days} days')
        self.stats['deleted'] += deleted
        return deleted

    def tier_files(self, tier):
        """(file, time it was taken) for everything in one tier. The hourly tier is date
        partitioned, so its listing is a walk while the others are one listing each."""
        folder = self.storage.ensure_folder(tier, parent=self.folder)
        if tier == HOURLY:
            for _path, stored in self.storage.walk(folder):
                parsed = parse_dump(stored['name'])
                yield stored, parsed[1] if parsed else None
            return
        for stored in self.storage.list_files(folder):
            yield stored, backup_time(stored['name'])

    def copy(self, source, folder, name, tier, taken=None):
        target = f"{folder['id']}/{name}"
        if not source['size']:
            self.logger.warning(f'Not promoting {source["id"]} to {target} - the stored file is empty')
            return
        metadata = {'tier': tier, 'promoted_from': source['id']}
        if taken is not None:
            # daily and monthly names only carry a date - keep the time the dump was
            # actually taken so the UI and get_latest_db_backup do not read the copy
            # time. Monthly copies inherit it from the daily object's metadata.
            metadata['backup_time'] = taken.isoformat()
        try:
            copied = self.storage.copy_stored_file(source, folder, name, extra_metadata=metadata,
                                                   lock_days=self.lock_days.get(tier),
                                                   lock_mode=self.lock_mode)
        except Exception as e:
            # one unpromotable dump must not stop the rest, as with prune/extend_retention
            self.stats['errors'].append(f'{target}: {e}')
            self.logger.warning(f'Could not promote {source["id"]} to {target}: {e}')
            return
        self.logger.info(f'Promoted {source["id"]} to {target}')
        return copied
