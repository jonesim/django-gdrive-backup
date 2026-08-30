"""Read-only checks of each configured backup destination, and the Backblaze b2 CLI
commands that set one up.

This package never creates a bucket and never writes a lifecycle rule - the retention of
a tiered destination (db_tiers.py) is whatever rules the bucket itself carries, which is
the point: the backup credential needs no delete permission and no bucket-configuration
permission. That leaves a setup step to do by hand, so this module works out what is
missing and generates the commands to fix it, for the web page (enhanced_views
.StorageSetupBaseView) and the storage_setup management command.

Nothing here imports a cloud SDK: the module has to work when boto3 is missing, which is
one of the states it exists to report on.

A restore_only destination is reported on differently throughout: the bucket, its rules and
its tiers belong to the machine that backs up to it, so this side generates one read-only
key and says nothing about protection it neither applies nor can fix.

The expiry day counts below are seed values for text the user copies and can edit. They
are deliberately not settings: the application never applies them - a destination's real
rules are read back live - so a setting would be a second source of truth that could
disagree with the bucket while this page reported everything was fine.
"""
import datetime
import json
import re
import shlex
import sys

from django.conf import settings

from .config import get_config
from .db_tiers import (DAILY, DEFAULT_EXPIRE_DAYS, DEFAULT_PURGE_DAYS, DELETE_APP, DELETE_LIFECYCLE, MONTHLY,
                       TIER_DIRS, list_tier, parse_daily, tier_prefixes)
from .storages import get_storage

# a daily dump appears the day after the hourly ones it comes from, so one day behind is
# normal and two is not
STALE_PROMOTION_DAYS = 2

# What the application actually calls: list_objects_v2 (listFiles), head/get_object and
# download (readFiles), upload and server-side copy (writeFiles), head_bucket (listBuckets).
# The last three are read-only and only serve this page's checks - B2 gates each bucket
# read behind its own capability, and without them a protected bucket reads as unprotected
# because the query fails rather than answering "not configured":
#   readBuckets              -> Get Bucket Versioning
#   readBucketRetentions     -> Get Object Lock Configuration
#   readBucketLifecycleRules -> Get Lifecycle Configuration
# (S3-API capability names; they have no B2 native-API equivalent)
B2_KEY_CAPABILITIES = ('listBuckets', 'listFiles', 'readFiles', 'writeFiles',
                       'readBuckets', 'readBucketRetentions', 'readBucketLifecycleRules')
# only when retention is enforced by the application (BACKUP_DB_RETENTION / prune_backups)
# rather than by the bucket. With db_tiers the whole point is that the key cannot delete
B2_DELETE_CAPABILITY = 'deleteFiles'
# only when the 'lock' storage setting stamps Object Lock retention on uploads and the
# extend_retention task tops it up: put_object_retention and reading the retention back
B2_LOCK_CAPABILITIES = ('readFileRetentions', 'writeFileRetentions')
# a restore_only config reads another machine's backups and writes nothing, so the key it
# needs cannot upload, delete or change anything. The two readBucket* capabilities of the
# full set are dropped with it: they only feed the lifecycle, retention and tier rows,
# which belong to the config that owns the bucket and are not shown for this one
B2_READ_ONLY_CAPABILITIES = ('listBuckets', 'listFiles', 'readFiles', 'readBuckets')


def key_capabilities(check):
    if check.get('restore_only'):
        return list(B2_READ_ONLY_CAPABILITIES)
    capabilities = list(B2_KEY_CAPABILITIES)
    if check['prunes']:
        capabilities.append(B2_DELETE_CAPABILITY)
    if check['object_lock']:
        capabilities += B2_LOCK_CAPABILITIES
    return capabilities

# storage settings safe to display - everything else may be a credential
PUBLIC_STORAGE_KEYS = ('backend', 'bucket', 'container', 'root', 'region', 'endpoint_url', 'b2',
                       'shared_drive', 'account_url')
SECRET_STORAGE_KEYS = ('access_key_id', 'secret_key', 'credential', 'connection_string', 'credentials')

MAX_SCHEMA_RULES = 10

# A lifecycle rule is JSON, so every shell has to be persuaded to pass double quotes
# through to b2 unharmed - and they all do it differently. Getting this wrong is not
# subtle: b2 rejects the mangled argument.
BASH = 'bash'
POWERSHELL = 'powershell'
CMD = 'cmd'
SHELL_LABELS = {BASH: 'bash', POWERSHELL: 'PowerShell', CMD: 'cmd.exe'}


def default_shell():
    """What the machine running this code would use - right for the management command."""
    return POWERSHELL if sys.platform == 'win32' else BASH


def shell_for_request(request):
    """What the person reading the page would use. Deliberately the browser's platform
    and not the server's: the page is usually served from a Linux container to a Windows
    workstation, and it is the workstation that runs b2."""
    platform = (request.META.get('HTTP_SEC_CH_UA_PLATFORM') or '').strip('"')
    agent = request.META.get('HTTP_USER_AGENT') or ''
    if platform:
        return POWERSHELL if platform == 'Windows' else BASH
    if 'Windows' in agent:
        return POWERSHELL
    return BASH if agent else default_shell()


def is_backblaze(storage_settings):
    return bool(storage_settings.get('b2')) or 'backblazeb2.com' in (storage_settings.get('endpoint_url') or '')


def redact(text, storage_settings):
    """Credential values never belong in a rendered page or a log line, and they turn up
    in exception text - B2AuthError carries the raw authorize response, and an Azure
    connection string can appear in a ValueError."""
    text = str(text)
    for key in SECRET_STORAGE_KEYS:
        value = storage_settings.get(key)
        if isinstance(value, str) and len(value) >= 8:
            text = text.replace(value, '***')
    return text


def b2_lifecycle_rule(prefix, hide_days, delete_days=DEFAULT_PURGE_DAYS):
    """B2 expiry only hides the file; delete_days later the hidden version is purged -
    without that clause it would be kept and charged for."""
    return {'fileNamePrefix': prefix,
            'daysFromUploadingToHiding': hide_days,
            'daysFromHidingToDeleting': delete_days}


def b2_tier_rules(prefixes, expire_days=None, purge_days=DEFAULT_PURGE_DAYS):
    """The rules one db folder's tiers need. A tier whose expire_days is None gets no
    rule at all - by default that is the monthly archive, which is kept indefinitely."""
    expire_days = expire_days or DEFAULT_EXPIRE_DAYS
    return [b2_lifecycle_rule(prefixes[tier], expire_days[tier], purge_days)
            for tier in TIER_DIRS if expire_days.get(tier)]


def overlaps(prefix, other):
    """B2 rejects a rule set where one prefix contains another, so an existing rule that
    overlaps one of ours cannot be kept alongside it."""
    return prefix.startswith(other) or other.startswith(prefix)


def common_prefix(*paths):
    """The longest shared path-segment prefix, for scoping an application key."""
    split = [p.strip('/').split('/') for p in paths if p]
    if not split:
        return ''
    shared = []
    for segments in zip(*split):
        if len(set(segments)) > 1:
            break
        shared.append(segments[0])
    return '/'.join(shared)


def quote_rule(rule, shell=BASH):
    text = json.dumps(rule)
    if shell in (CMD, POWERSHELL):
        # the Windows C runtime unescapes \" inside a quoted argument. PowerShell cannot
        # be trusted to build that itself (5.1 and 7 disagree), so the command carries the
        # --% stop-parsing token and this is passed through verbatim
        return '"' + text.replace('"', '\\"') + '"'
    return shlex.quote(text)


def quote_arg(text, shell=BASH):
    if shell in (CMD, POWERSHELL):
        return f'"{text}"' if ' ' in text else text
    return shlex.quote(text)


def rule_options(rules, shell=BASH):
    return ' '.join(f'--lifecycle-rule {quote_rule(rule, shell)}' for rule in rules)


def key_name(config_name, bucket):
    """B2 key names allow letters, digits, hyphen and underscore only, up to 100 chars -
    a config name is free text, so anything else becomes a hyphen."""
    name = re.sub(r'[^a-z0-9]+', '-', f'django-cloud-backup-{config_name}-{bucket}'.lower())
    return name.strip('-')[:100]


def read_only_key_command(check, bucket, shell):
    """The key for a restore_only destination: it can find and download backups and
    nothing else."""
    prefix = common_prefix(check['root'], check['db_dir'])
    options = f'--bucket {quote_arg(bucket, shell)} '
    note = ('A read-only key for restoring backups made by another machine. It cannot upload, delete or '
            'change anything in this bucket, so the destination is no less safe for this installation '
            'holding it.')
    if prefix:
        options += f'--name-prefix {quote_arg(prefix + "/", shell)} '
        note += f' --name-prefix confines it to {prefix}/, which is where that machine backs up.'
    else:
        note += ' No --name-prefix: the backup root and the database folder have no common prefix.'
    return {'command': f'b2 key create {options}{key_name(check["name"], bucket)} '
                       f'{",".join(key_capabilities(check))}',
            'note': note}


def b2_setup_commands(check, shell=None):
    """The commands to run, in order, for a Backblaze destination. `check` is the dict
    from check_config(). Every command is text - nothing here is ever executed."""
    shell = shell if shell in SHELL_LABELS else default_shell()
    bucket = check['destination']
    commands = [{'command': 'b2 account authorize <accountKeyID> <applicationKey>',
                 'note': 'Use an account-level key with the writeBuckets, readBucketEncryption, '
                         'writeBucketEncryption and writeBucketRetentions capabilities. This is NOT the '
                         'key the application should use - that one is created below.'}]
    if check.get('restore_only'):
        # the bucket belongs to the machine that backs up to it: nothing here creates it,
        # and a lifecycle rule set from this side would replace the owner's
        commands.append(read_only_key_command(check, bucket, shell))
        commands.append({'command': 'b2 bucket list', 'note': 'Check the result.'})
        return commands
    rules = check['wanted_rules'] + check['keep_rules']
    options = (rule_options(rules, shell) + ' ') if rules else ''
    # without --% PowerShell rewrites the quoting of a JSON argument on its way to a
    # native command, and 5.1 and 7 do it differently
    stop = '--% ' if shell == POWERSHELL and rules else ''
    locked_tiers = [tier for tier, days in (check.get('lock_days') or {}).items() if days]
    lock_flag = '--file-lock-enabled ' if locked_tiers else ''
    if locked_tiers:
        lock_note = (f"--file-lock-enabled lets objects carry a retention date; only the "
                     f"{', '.join(locked_tiers)} tier gets one, so the rest stays deletable. Enabling it "
                     f"cannot be undone. On a bucket that already exists run "
                     f"'b2 bucket update --file-lock-enabled {bucket}' instead.")
    else:
        lock_note = ('Object Lock (--file-lock-enabled) is deliberately not set: a retention lock stops '
                     'lifecycle expiry, so the tiers would never be cleaned up.')
    if check['state'] in ('missing', 'denied', 'unknown'):
        commands.append({'command': f'b2 {stop}bucket create {lock_flag}{options}'
                                    f'{quote_arg(bucket, shell)} allPrivate',
                         'note': lock_note})
    elif locked_tiers:
        commands.append({'command': f'b2 bucket update --file-lock-enabled {quote_arg(bucket, shell)}',
                         'note': lock_note})
    if check['state'] != 'missing' and rules:
        note = ('b2 bucket update REPLACES the whole rule set - every rule the bucket should end up with '
                'has to be on this one command line.')
        if check.get('app_deletes'):
            note += (' The rule only expires hidden versions, one day after the application deletes a '
                     'dump - current dumps are never hidden or expired by the bucket.')
        if check['dropped_rules']:
            note += (' These existing rules are not included because they overlap the tier prefixes, which '
                     'B2 will not accept: ' + ', '.join(r['prefix'] or '(whole bucket)' for r in
                                                        check['dropped_rules']) + '.')
        if check['rules_unknown']:
            note += (' The rules already on the bucket could not be read, so this command may remove rules '
                     'that are not listed here - run b2 bucket list first.')
        commands.append({'command': f'b2 {stop}bucket update {options}{quote_arg(bucket, shell)}',
                         'note': note})
    prefix = common_prefix(check['root'], check['db_dir'])
    key_options = f'--bucket {quote_arg(bucket, shell)} '
    key_note = ('The key for BACKUP_STORAGE, restricted to this bucket. listFiles/readFiles/writeFiles '
                'list, restore and upload backups; listBuckets, readBuckets, readBucketRetentions and '
                'readBucketLifecycleRules are read-only and let this page report the versioning, '
                'object-lock and lifecycle configuration rather than showing Unknown. A key cannot be '
                'changed after it is created - to add capabilities, make a new one and update '
                'BACKUP_STORAGE.')
    if check.get('app_deletes'):
        deletable = ', '.join(tier for tier in TIER_DIRS if tier not in locked_tiers)
        key_note += f' {B2_DELETE_CAPABILITY} is included because this config deletes aged-out dumps itself.'
        if locked_tiers:
            key_note += (f" Anything holding this key can therefore delete the {deletable} tier(s) - the "
                         f"{', '.join(locked_tiers)} tier is what the object-lock retention protects.")
        else:
            key_note += (' Nothing then protects the backups from anything holding this key - consider '
                         'lock_days on the monthly tier.')
    elif check['prunes']:
        key_note += (f' {B2_DELETE_CAPABILITY} is included because this config prunes old backups itself '
                     f'(retention); with db_tiers the bucket does the deleting and the key does not need it.')
    else:
        key_note += (f' No {B2_DELETE_CAPABILITY}: nothing in this config deletes, which is what makes the '
                     f'backups safe from the application.')
    if check['object_lock']:
        key_note += (f' {", ".join(B2_LOCK_CAPABILITIES)} are needed to stamp and read the Object Lock '
                     f'retention.')
    if prefix:
        key_options += f'--name-prefix {quote_arg(prefix + "/", shell)} '
    else:
        key_note += ' No --name-prefix: the backup root and the database folder have no common prefix.'
    commands.append({'command': f'b2 key create {key_options}{key_name(check["name"], bucket)} '
                                f'{",".join(key_capabilities(check))}',
                     'note': key_note})
    commands.append({'command': 'b2 bucket list', 'note': 'Check the result.'})
    return commands


def guidance(check):
    """What to do by hand where there are no b2 commands to give."""
    backend = check['backend']
    if check.get('restore_only'):
        return [f"{check['destination']} holds backups made by another machine and is only read from here. "
                f'Give this installation credentials that can list and download, and nothing more.']
    if backend == 'gdrive':
        return [f"Create a folder named {check['root']} in Google Drive and share it with the backup "
                f'service account, or set BACKUP_TEAM_DRIVE to a shared drive it can already see.',
                'Google Drive has no lifecycle rules, so db_tiers is not available - retention there is '
                'BACKUP_DB_RETENTION, applied by the application.']
    if backend == 'azure':
        return [f"Create the container {check['destination']} and enable blob soft delete (or a container "
                f'immutability policy) in the portal.',
                'Azure has no lifecycle rules of the kind db_tiers needs, so retention there is '
                'BACKUP_DB_RETENTION, applied by the application.']
    lines = [f"{check['destination']} is not a Backblaze bucket, so no b2 commands are shown."]
    if check['wanted_rules']:
        lines.append('Create these expiry rules in your provider\'s console:')
        lines += [f"{rule['fileNamePrefix']} - expire after {rule['daysFromUploadingToHiding']} days, "
                  f"noncurrent versions {rule['daysFromHidingToDeleting']} day(s) after that"
                  if rule['daysFromUploadingToHiding'] else
                  f"{rule['fileNamePrefix']} - expire noncurrent versions "
                  f"{rule['daysFromHidingToDeleting']} day(s) after deletion; current objects untouched"
                  for rule in check['wanted_rules']]
        if check['app_deletes']:
            lines.append('The application deletes the aged-out dumps itself - on a versioned bucket that '
                         'only hides them, which is what this rule cleans up.')
        else:
            kept = [tier for tier, days in (check['expire_days'] or {}).items() if not days]
            if kept:
                lines.append(f"No rule for the {', '.join(kept)} tier(s) - kept indefinitely.")
            lines.append('On a versioned bucket also expire the noncurrent versions, or the expired objects '
                         'are kept and charged for.')
    return lines


def deletion_row(config, storage=None):
    """In app-delete mode the tiers have no lifecycle rules by design, so say what does
    the deleting instead of reporting every tier as unprotected. Given the storage to
    ask, also checks the versioned-bucket trap: a plain delete only hides the dump
    there, so without a hidden-version expiry rule the old versions are kept and
    charged for."""
    if not config.db_tiers or config.db_tier_delete != DELETE_APP:
        return None
    expires = [f'{tier} after {days} days' for tier, days in config.db_tier_expire_days.items() if days]
    kept = [tier for tier, days in config.db_tier_expire_days.items() if not days]
    detail = 'the application deletes ' + (', '.join(expires) or 'nothing')
    if kept:
        detail += f", and keeps {', '.join(kept)}"
    row = {'label': 'Deletion', 'status': 'Enabled', 'detail': detail}
    uncovered = unexpired_hidden_tiers(config, storage) if expires and storage is not None else []
    if uncovered:
        row.update(badge='warning', action='warn',
                   detail=detail + ' - but deletes on this versioned bucket only hide the dumps, and no '
                                   f"rule expires the hidden versions under {', '.join(uncovered)} - they "
                                   'are kept and charged for')
    return row


def unexpired_hidden_tiers(config, storage):
    """The app-deleted tiers whose hidden versions nothing expires, or [] - on a
    versioned bucket a plain delete only hides the object, so each deleted tier needs
    a rule with noncurrent days covering it. Never raises: where versioning or the
    rules cannot be read, there is nothing sure enough to warn about."""
    try:
        if not storage.versioned():
            return []
        rules = storage.lifecycle_rules() or []
    except Exception:  # noqa: BLE001 - a restricted key may not read the bucket configuration
        return []
    db_dir = config.db_dir.strip('/')
    return [tier for tier, days in config.db_tier_expire_days.items() if days
            and not any(rule['noncurrent_days'] and f'{db_dir}/{tier}/'.startswith(rule['prefix'])
                        for rule in rules)]


def lock_row(storage, config):
    """Object Lock is per object, so the only proof the archive is protected is a stored
    object carrying a retention date. Checks the newest monthly dump, which is one HEAD."""
    locked = [tier for tier, days in config.db_tier_lock_days.items() if days]
    if not locked:
        return None
    tier = locked[0]
    wanted = config.db_tier_lock_days[tier]
    label, folder = 'Archive lock', f"{config.db_dir.strip('/')}/{tier}/"
    detail = f'{tier} dumps locked for {wanted} days ({config.db_tier_lock_mode.lower()})'
    row = {'label': label, 'folder': folder}
    try:
        files = storage.list_files(storage.ensure_folder(f"{config.db_dir.strip('/')}/{tier}"))
        newest = max(files, key=lambda f: f['name']) if files else None
        retention = storage.object_retention(newest['id']) if newest else None
    except Exception as e:  # noqa: BLE001
        return dict(row, status='Unknown', action='warn',
                    detail=f'{detail} - could not check ({type(e).__name__})')
    if newest is None:
        return dict(row, status='Unknown', action='warn',
                    detail=f'{detail} - nothing in the {tier} tier to check yet')
    if not retention or not retention.get('retain_until'):
        return dict(row, status='Disabled', action='fix',
                    detail=f"{detail} - but {newest['name']} carries no retention: enable Object Lock on "
                           f'the bucket, then re-promote')
    days_left = (retention['retain_until'].date() - datetime.date.today()).days
    return dict(row, status='Enabled',
                detail=f"{detail} - {newest['name']} is locked for another {days_left} days "
                       f"({(retention.get('mode') or '').lower()})")


def promotion_row(storage, config):
    """Is anything actually reaching the longer-lived tiers? The hourly tier expires by
    itself, so promotion having quietly stopped is the one failure that loses every
    backup while everything else still looks healthy."""
    base = config.db_dir.strip('/')
    # the folder is a column of its own where these rows are shown, so it is not repeated
    # in the detail text
    row = {'label': 'Tier promotion', 'folder': f'{base}/{DAILY}/'}
    try:
        db_folder = storage.ensure_folder(base)
        daily = list(list_tier(storage, db_folder, DAILY))
        monthly = list(list_tier(storage, db_folder, MONTHLY))
    except Exception as e:  # noqa: BLE001
        return dict(row, status='Unknown', action='warn',
                    detail=f'could not list the daily tier ({type(e).__name__})')
    days = sorted(parsed[1] for parsed in map(parse_daily, [f['name'] for _server, f in daily]) if parsed)
    if not days:
        if not monthly:
            return dict(row, status='Unknown', action='warn',
                        detail='nothing has been promoted into the daily tier yet - expected within a '
                               'day of the first backup')
        return dict(row, status='Disabled', action='fix',
                    detail='no dumps in the daily tier - the hourly tier will expire with nothing '
                           'behind it')
    behind = (datetime.date.today() - days[-1]).days
    servers = {server for server, _f in daily if server}
    detail = f'newest daily dump {days[-1]}, {len(days)} in the daily tier' + (
        f' from {len(servers)} servers' if len(servers) > 1 else '')
    if behind > STALE_PROMOTION_DAYS:
        return dict(row, status='Disabled', action='fix',
                    detail=detail + f' - {behind} days behind, so nothing recent has been promoted')
    return dict(row, status='Enabled', detail=detail)


def storage_facts(config, storage_settings):
    """Displayable config values. Reads only from an allow-list of keys, so a credential
    cannot reach the page by being added to a storage dict later."""
    facts = [('Backend', storage_settings.get('backend', 'gdrive'))]
    for key in PUBLIC_STORAGE_KEYS:
        if key not in ('backend', 'root') and storage_settings.get(key) is not None:
            facts.append((key.replace('_', ' ').title(), str(storage_settings[key])))
    facts.append(('Backup root', config.root))
    if config.include_db:
        facts.append(('Database folder', config.db_dir))
    else:
        # a files-only destination: the database settings it inherited from the globals
        # are not in force here, so none of them is a fact about it
        facts.append(('Database', 'off - files only'))
    if config.restore_only:
        facts.append(('Restore only', 'yes - nothing is written to this destination'))
    if config.include_db:
        facts.append(('Lifecycle tiers', 'on' if config.db_tiers else 'off'))
        if config.retention:
            facts.append(('Retention', f'{len(config.retention)} rule(s) applied by the application'))
    facts.append(('Encryption', 'on' if config.encryption_key else 'off'))
    facts.append(('Changed files', config.changed_files))
    if storage_settings.get('lock'):
        facts.append(('Object lock', 'configured'))
    return facts


def check_config(name, shell=None):
    """Everything the setup page and management command show for one destination. Makes
    no changes, returns no credential values, and never raises: a broken config is
    exactly what the caller is trying to diagnose.
    :param shell: which shell the generated commands are quoted for"""
    check = {'name': name, 'error': None, 'storage_error': None, 'backend': None, 'destination': None,
             'root': None, 'db_dir': None, 'facts': [], 'rows': [], 'state': 'unknown',
             'wanted_rules': [], 'keep_rules': [], 'dropped_rules': [], 'rules_unknown': False,
             'commands': [], 'guidance': [], 'backblaze': False, 'prunes': False, 'object_lock': False,
             'expire_days': None, 'purge_days': DEFAULT_PURGE_DAYS, 'lock_days': {}, 'lock_mode': None,
             'app_deletes': False, 'restore_only': False,
             'shell': shell if shell in SHELL_LABELS else default_shell()}
    try:
        config = get_config(name)
    except Exception as e:  # noqa: BLE001 - render the message, never a 500
        check['error'] = redact(e, getattr(settings, 'BACKUP_STORAGE', None) or {})
        return check
    storage_settings = config.storage_settings
    # retention, tiers and the db folder are all about the dumps: a config with the
    # database off inherits those settings but never acts on them (backup.py runs the
    # prune and the promotion inside the db step), so the check ignores them too
    tiers = config.db_tiers and config.include_db
    prunes = bool(config.retention) and config.include_db
    check.update({'backend': storage_settings.get('backend', 'gdrive'),
                  'destination': storage_settings.get('bucket') or storage_settings.get('container') or config.root,
                  'root': config.root,
                  'db_dir': config.db_dir if config.include_db else None,
                  'backblaze': is_backblaze(storage_settings),
                  'restore_only': config.restore_only,
                  # what the application itself will do to the destination, which is what
                  # the key has to be allowed to do - a restore_only config does none of
                  # it, whatever its settings inherited
                  'prunes': not config.restore_only and (prunes or (tiers and config.db_tier_delete == DELETE_APP)),
                  'object_lock': not config.restore_only
                                 and (bool(storage_settings.get('lock'))
                                      or bool(tiers and any(config.db_tier_lock_days.values()))),
                  'lock_days': dict(config.db_tier_lock_days) if tiers and not config.restore_only else {},
                  'lock_mode': config.db_tier_lock_mode if tiers and not config.restore_only else None,
                  'app_deletes': bool(tiers and config.db_tier_delete == DELETE_APP and not config.restore_only),
                  'facts': storage_facts(config, storage_settings)})
    # the bucket, its lifecycle rules and its tiers belong to the config that writes them;
    # reporting them again here would double every warning on the machine that only reads
    if tiers and not config.restore_only:
        check['expire_days'] = config.db_tier_expire_days
        check['purge_days'] = config.db_tier_purge_days
        if config.db_tier_delete == DELETE_LIFECYCLE:
            check['wanted_rules'] = b2_tier_rules(tier_prefixes(config.db_dir.strip('/')),
                                                  check['expire_days'], check['purge_days'])
        elif any(check['expire_days'].values()):
            # the application does the expiry deletes, so no per-tier rules - but on a
            # versioned bucket those deletes only hide the dumps, so one noncurrent-only
            # rule over the whole db folder (schema sub-folders included) expires the
            # hidden versions without ever hiding a current dump. Dropped again below
            # when the bucket turns out not to be versioned
            check['wanted_rules'] = [b2_lifecycle_rule(f"{config.db_dir.strip('/')}/", None,
                                                       check['purge_days'])]
    try:
        storage = get_storage(storage_settings)
    except Exception as e:  # noqa: BLE001 - bad credentials, missing SDK, unknown backend
        # the commands come from settings alone, so they are still worth showing - this
        # is exactly the user who needs them
        check['storage_error'] = f'{type(e).__name__}: {redact(e, storage_settings)}'
        check['rules_unknown'] = True
        return finish_check(check)
    status = storage.destination_status(root=config.root)
    check['state'] = status['state']
    check['rows'] = [status]
    if config.restore_only:
        check['rows'].append({'label': 'Restore only', 'status': 'Enabled',
                              'detail': 'backups here are made elsewhere - this installation reads them and '
                                        'writes nothing. How they are protected is reported by the machine '
                                        'that backs up to this destination'})
        return finish_check(check)
    try:
        # no per-tier lifecycle rows when the application does the deleting: there are
        # meant to be no rules, so measuring the tiers against them says nothing
        check['rows'] += storage.protection_info(
            tier_prefixes=(tier_prefixes(config.db_dir.strip('/'))
                           if tiers and not check['app_deletes'] else None),
            expire_days=check['expire_days'], purge_days=check['purge_days'])
    except Exception as e:  # noqa: BLE001
        check['rows'].append({'label': 'Protection', 'status': 'Unknown',
                              'detail': redact(e, storage_settings)})
    if tiers:
        check['rows'] += [row for row in [deletion_row(config, storage)] if row]
    if tiers and status['state'] == 'ok':
        check['rows'].append(promotion_row(storage, config))
        check['rows'] += [row for row in [lock_row(storage, config)] if row]
    if check['wanted_rules'] and status['state'] == 'ok':
        if check['app_deletes']:
            # the one rule covers every prefix under the db folder, so no schema rules -
            # and an unversioned bucket needs no rule at all, its deletes really delete
            try:
                if not storage.versioned():
                    check['wanted_rules'] = []
            except Exception:  # noqa: BLE001 - unreadable: keep the rule, B2 is always versioned
                pass
        else:
            # the schema rules first: they add tier prefixes of their own, and an existing
            # rule that overlaps one of those has to be dropped rather than carried over
            add_schema_rules(check, storage, config)
        if check['wanted_rules']:
            add_existing_rules(check, storage)
    elif check['wanted_rules']:
        check['rules_unknown'] = True
    return finish_check(check)


def add_existing_rules(check, storage):
    """Split the rules already on the destination into the ones a `b2 bucket update` has
    to carry over and the ones it cannot keep."""
    try:
        existing = storage.lifecycle_rules()
    except Exception:  # noqa: BLE001 - not every service implements the call
        check['rules_unknown'] = True
        return
    if existing is None:
        return
    ours = [rule['fileNamePrefix'] for rule in check['wanted_rules']]
    for rule in existing:
        if any(overlaps(prefix, rule['prefix']) for prefix in ours):
            check['dropped_rules'].append(rule)
        # a rule that expires nothing still claims its prefix, and B2 will not take a
        # second rule on a prefix it already has
        elif rule['days'] or rule['noncurrent_days']:
            check['keep_rules'].append(b2_lifecycle_rule(rule['prefix'], rule['days'],
                                                         rule['noncurrent_days']))


def add_schema_rules(check, storage, config):
    """A rule on <db_dir>/hourly/ does not cover <db_dir>/<schema>/hourly/ - matching is
    by prefix - so a site backing schemas up separately needs a pair per schema folder."""
    try:
        folders = storage.list_folders(storage.ensure_folder(config.db_dir))
    except Exception:  # noqa: BLE001 - the rules for the db folder itself are still right
        return
    names = sorted(f['name'] for f in folders if f['name'] not in TIER_DIRS)
    for schema in names[:MAX_SCHEMA_RULES]:
        check['wanted_rules'] += b2_tier_rules(tier_prefixes(f"{config.db_dir.strip('/')}/{schema}"),
                                               config.db_tier_expire_days, config.db_tier_purge_days)
    if len(names) > MAX_SCHEMA_RULES:
        check['guidance'].append(f'{len(names) - MAX_SCHEMA_RULES} more schema folders need the same pair '
                                 f'of rules and are not included above.')


def finish_check(check):
    if check['backblaze'] and check['destination']:
        check['commands'] = b2_setup_commands(check, check['shell'])
    else:
        check['guidance'] = guidance(check) + check['guidance']
    return check


def command_text(check):
    return '\n'.join(command['command'] for command in check['commands'])
