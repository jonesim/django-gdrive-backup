import datetime


class PruneBackups:

    def __init__(self, backup_dict):
        self.keep = []
        self.backup_dict = backup_dict

    def backups_to_remove(self, recipe):
        for r in recipe:
            if 'months' in r:
                self.keep_monthly_backups(r['months'], r['number'])
            elif 'days' in r:
                self.keep_backups(datetime.timedelta(days=r['days']), r['number'])
            elif 'hours' in r:
                self.keep_backups(datetime.timedelta(hours=r['hours']), r['number'])
        return {k: v for k, v in self.backup_dict.items() if k not in self.keep}

    def keep_backups(self, period, number):
        period_dict = {}
        start_time = datetime.datetime(2000, 1, 1) + int((datetime.datetime.today() -
                                                          datetime.datetime(2000, 1, 1)) / period) * period + period
        for b in self.backup_dict:
            period_dict.setdefault(int((start_time - b) / period), []).append(b)
        for k in sorted(period_dict.keys()):
            self.keep.append(max(period_dict[k]))
            number -= 1
            if number < 1:
                break

    def keep_monthly_backups(self, period, number):
        period_dict = {}
        for b in self.backup_dict:
            period_dict.setdefault(int(int(b.strftime('%Y%m')) / period), []).append(b)
        for k in sorted(period_dict.keys(), reverse=True):
            self.keep.append(max(period_dict[k]))
            number -= 1
            if number < 1:
                break
