"""Summarize comparable stream batches without retaining the entire sample log."""
from collections import defaultdict
from statistics import median
import math


def number(value):
    try:
        result = float(str(value).split()[0].removesuffix('x'))
        return result if math.isfinite(result) else None
    except (ValueError, IndexError):
        return None


class VideoComparison:
    def __init__(self):
        self.groups = {}

    def add(self, event):
        streams = event.get('streams', [])
        workload = ', '.join(str(stream.get('case', '?')) for stream in streams)
        if not workload:
            return
        gpu = event['gpu']
        values = event.get('values', {})
        row = self.groups.setdefault((gpu, workload), {'samples': 0, 'sum': 0, 'max': {}})
        row['pci'] = values.get('pci', '–')
        fps = [number(stream.get('fps')) for stream in streams]
        if all(value is not None and value > 0 for value in fps):
            row['samples'] += 1
            row['sum'] += sum(fps)
        for metric in ('temperature', 'power', 'clock', 'width'):
            value = number(values.get(metric))
            if value is not None:
                row['max'][metric] = max(row['max'].get(metric, value), value)

    def rows(self, models):
        peers = defaultdict(list)
        for (gpu, workload), row in self.groups.items():
            if row['samples'] and models.get(gpu):
                peers[models[gpu], workload].append(row['sum'] / row['samples'])
        for (gpu, workload), row in self.groups.items():
            fps = row['sum'] / row['samples'] if row['samples'] else None
            group = peers[models.get(gpu), workload]
            delta = '–'
            if fps and len(group) >= 2:
                percent = 100 * (fps / median(group) - 1)
                delta = f'{percent:+.1f} %' + (' · auffällig' if abs(percent) >= 20 else '')
            yield [gpu, row['pci'], workload, f'{fps:.1f}' if fps else '–',
                   *(str(row['max'].get(key, '–')) for key in ('temperature','power','clock','width')), delta]
