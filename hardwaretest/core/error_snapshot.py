"""Read cumulative EDAC, PCIe AER and NVIDIA counters around a test step."""
from pathlib import Path
import shutil
import subprocess


def snapshot():
    counters = {}
    for pattern in ('devices/system/edac/mc/mc*/[cu]e_count',
                    'bus/pci/devices/*/aer_dev_*'):
        for path in Path('/sys').glob(pattern):
            try:
                lines = path.read_text().strip().splitlines()
                if path.name.startswith('aer_'):
                    lines = [line.split()[-1] for line in lines if line.startswith('TOTAL_')]
                if len(lines) == 1:
                    counters[f'{path.parent.name}/{path.name}'] = int(lines[0])
            except (OSError, ValueError):
                continue
    if shutil.which('nvidia-smi'):
        try:
            output = subprocess.run(['nvidia-smi', '--query-gpu=pci.bus_id,ecc.errors.uncorrected.volatile.total',
                                     '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
            for row in output.stdout.splitlines():
                address, sep, count = row.partition(',')
                if sep and count.strip().isdigit():
                    counters[f'GPU {address}/ECC'] = int(count)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return counters


def differences(before, after):
    lines = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old is None or new is None or new < old:
            lines.append(f'{key}: Δ n/v ({old} → {new})')
        elif new or old:
            lines.append(f'{key}: {old} → {new} (+{new-old})')
    for name, token in [('RAM ECC', 'mc'), ('PCIe AER', '/aer_'), ('GPU ECC', 'GPU ')]:
        keys = [key for key in before.keys() & after.keys() if token in key]
        if not keys:
            lines.append(f'{name}: n/v')
        elif all(before[key] == after[key] == 0 for key in keys):
            lines.append(f'{name}: 0 (+0)')
    return lines
