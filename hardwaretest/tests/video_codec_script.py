"""Isolated, bounded FFmpeg NVENC/NVDEC checks; standard library only."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

STOP = False


class Cancelled(Exception):
    pass


class CommandError(Exception):
    pass


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def stop_handler(*_):
    global STOP
    STOP = True


def child_setup():
    # Also kill FFmpeg if this worker is killed without a chance to clean up.
    parent = os.getppid()
    ctypes.CDLL(None).prctl(1, signal.SIGKILL)
    if os.getppid() != parent:
        os._exit(1)


def terminate(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def invoke(args, env, timeout=90):
    # Files avoid pipe deadlocks and unbounded Python output buffers.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=err, start_new_session=True, preexec_fn=child_setup)
        started = time.monotonic()
        try:
            while proc.poll() is None:
                if STOP:
                    raise Cancelled()
                if time.monotonic() - started > timeout:
                    raise CommandError('Zeitlimit bei FFmpeg überschritten')
                time.sleep(.05)
            out.seek(0); err.seek(0)
            stdout = out.read(2 * 1024 * 1024).decode(errors='replace')
            stderr = err.read(128 * 1024).decode(errors='replace')
            if proc.returncode:
                raise CommandError(stderr[-12000:] or f'Exit {proc.returncode}')
            return stdout, stderr
        finally:
            terminate(proc)


def classify_error(detail):
    text = detail.lower()
    if any(s in text for s in ('cannot load', 'cannot open shared object', 'unknown encoder',
                               'unknown decoder', 'minimum required nvidia driver',
                               'driver does not support', 'cuda_error_no_device',
                               'out of memory', 'too many concurrent sessions', 'no va display found',
                               'failed to initialise vaapi', 'failed to initialize vaapi',
                               'device creation failed', 'permission denied')):
        return 'unavailable'
    if any(s in text for s in ('unsupported device', 'codec not supported', 'no usable encoding profile',
                               'no usable decoding profile', 'no usable encoding entrypoint',
                               'no support for codec', 'hardware does not support',
                               'does not support the required nvenc features',
                               'no nvenc capable devices found', 'not supported on this gpu')):
        return 'unsupported'
    return 'error'


def frame_hashes(text):
    rows = [line.split(',') for line in text.splitlines() if line.strip() and not line.startswith('#')]
    if not rows or any(len(row) != 6 for row in rows):
        raise CommandError('Keine vollständigen Frame-Prüfsummen erhalten')
    return [(int(row[4]), row[5].strip()) for row in rows]


def quality_score(text):
    scores = re.findall(r'SSIM.*?All:([0-9.]+)', text)
    if not scores:
        raise CommandError('SSIM-Qualitätsmessung fehlt')
    return float(scores[-1])


def telemetry(uuid, env):
    if uuid.startswith('/dev/dri/'):
        return {'available': False}
    try:
        text, _ = invoke(['nvidia-smi', '-i', uuid, '-q', '-x'], env, timeout=5)
        gpu = ET.fromstring(text).find('gpu')
        if gpu is None:
            return {'available': False}
        fields = {'encoder': 'utilization/encoder_util', 'decoder': 'utilization/decoder_util',
                  'temperature': 'temperature/gpu_temp', 'pci': 'pci/pci_bus_id',
                  'power': 'gpu_power_readings/power_draw', 'clock': 'clocks/graphics_clock',
                  'width': 'pci/pci_gpu_link_info/link_widths/current_link_width',
                  'uncorrected_ecc': 'ecc_errors/volatile/uncorrected/total'}
        if gpu.find(fields["power"]) is None:
            fields["power"] = "power_readings/power_draw"
        return {key: gpu.findtext(path, default='nicht verfügbar') for key, path in fields.items()}
    except (OSError, CommandError, ET.ParseError):
        return {'available': False}


def stress(commands, seconds, streams, env, uuid):
    started = time.monotonic()
    round_index = 0
    next_sample = started
    while time.monotonic() - started < seconds:
        processes = []; files = []
        selected = [commands[(round_index * streams + i) % len(commands)] for i in range(streams)]
        batch_end = min(started + seconds, time.monotonic() + 10)
        try:
            for i in range(streams):
                out = tempfile.TemporaryFile(); err = tempfile.TemporaryFile()
                files.append((out, err))
                cmd = selected[i][2]
                cmd = cmd + ['-progress','pipe:1','-stats_period','1']
                processes.append(subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                    stdout=out, stderr=err, start_new_session=True, preexec_fn=child_setup))
            last_frames = [0] * streams
            last_change = [time.monotonic()] * streams
            while True:
                if STOP: raise Cancelled()
                for proc, (_, err) in zip(processes, files):
                    if proc.poll() is not None:
                        err.seek(0)
                        raise CommandError('Videostream unerwartet beendet: ' + err.read(12000).decode(errors='replace'))
                now = time.monotonic()
                for index, (out, _) in enumerate(files):
                    end = os.fstat(out.fileno()).st_size
                    tail = os.pread(out.fileno(), 4096, max(0, end - 4096))
                    frames = re.findall(rb"(?m)^frame= *([0-9]+)\r?$", tail)
                    frame = int(frames[-1]) if frames else 0
                    if frame > last_frames[index]:
                        last_frames[index] = frame
                        last_change[index] = now
                    elif now - last_change[index] >= 5 or (now >= batch_end and not frame):
                        codec, direction, _ = selected[index]
                        raise CommandError(f"{uuid} {codec} {direction} Stream {index+1}: kein Bildfortschritt (Frame {last_frames[index]})")
                if now >= batch_end:
                    break
                if now >= next_sample:
                    progress = []
                    for index, (out, _) in enumerate(files):
                        end = os.fstat(out.fileno()).st_size
                        tail = os.pread(out.fileno(),4096,max(0,end-4096))
                        pairs = [line.split('=',1) for line in tail.decode(errors='replace').splitlines() if '=' in line]
                        fields = dict(pairs)
                        progress.append(dict(fields, case='/'.join(selected[index][:2])))
                    emit(event='telemetry',gpu=uuid,elapsed=round(time.monotonic()-started,1),
                         values=telemetry(uuid,env),streams=progress)
                    next_sample = time.monotonic()+5
                time.sleep(.1)
        finally:
            for proc in processes: terminate(proc)
            for out,err in files: out.close(); err.close()
        round_index += 1
        if started + seconds - time.monotonic() < 1:
            break


def main(argv=None):
    global STOP
    STOP = False
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpus', required=True, help='Comma-separated NVIDIA UUIDs or VA-API render nodes')
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--codec', choices=['all','h264','hevc','av1'], default='all')
    parser.add_argument('--mode', choices=['both','encode','decode'], default='both')
    parser.add_argument('--seconds', type=int, default=0, help='Stress duration per GPU after validation')
    parser.add_argument('--streams', type=int, choices=range(1,5), default=2)
    args = parser.parse_args(argv)
    uuids = list(dict.fromkeys(args.gpus.split(',')))
    if not all(re.fullmatch(r'(?:GPU-[A-Za-z0-9-]+|/dev/dri/renderD[0-9]+)', uuid) for uuid in uuids) or not 0 <= args.seconds <= 86400:
        parser.error('Ungültige GPU-Kennung oder Dauer')
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    env = {k:v for k,v in os.environ.items() if k not in ('LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME')}
    env['LC_ALL'] = 'C'
    ffmpeg = shutil.which('ffmpeg', path='/usr/bin:/bin')
    if not ffmpeg:
        emit(event='result', status='unavailable', detail='FFmpeg fehlt'); return 2
    base = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-xerror', '-y']
    cases = 0; errors = 0; incomplete = 0
    try:
        manifest = json.loads((args.vectors/'manifest.json').read_text())
        codecs = list(manifest) if args.codec == 'all' else [args.codec]
        with tempfile.TemporaryDirectory(prefix='hardwaretest-video-') as temp:
            for uuid in uuids:
                vaapi = uuid.startswith('/dev/dri/')
                if vaapi:
                    env.pop('CUDA_VISIBLE_DEVICES', None)
                else:
                    env['CUDA_VISIBLE_DEVICES'] = uuid
                before = telemetry(uuid, env)
                emit(event='gpu', gpu=uuid, values=before)
                commands = []
                for codec in codecs:
                    info = manifest[codec]; ref = args.vectors/info['file']
                    if hashlib.sha256(ref.read_bytes()).hexdigest() != info['sha256']:
                        raise CommandError('Referenzvideo-Prüfsumme stimmt nicht')
                    cpu, _ = invoke(base + ['-hwaccel','none','-i',str(ref),'-an','-pix_fmt','yuv420p',
                                           '-vsync','0','-f','framemd5','-'], env)
                    expected = frame_hashes(cpu)
                    if len(expected) != info['frames']:
                        raise CommandError('Referenzvideo hat eine unerwartete Bildanzahl')
                    for direction in (['encode','decode'] if args.mode == 'both' else [args.mode]):
                        try:
                            if direction == 'decode':
                                inp = ['-c:v',codec+'_cuvid','-gpu','0','-i',str(ref)]
                                download = []
                                if vaapi:
                                    inp = ['-hwaccel', 'vaapi', '-hwaccel_device', uuid,
                                           '-hwaccel_output_format', 'vaapi', '-i', str(ref)]
                                    download = ['-vf', 'hwdownload,format=nv12,format=yuv420p']
                                raw, _ = invoke(base + inp + download +
                                                ['-an','-pix_fmt','yuv420p','-vsync','0','-f','framemd5','-'], env)
                                if frame_hashes(raw) != expected:
                                    raise CommandError('Decoder: Frame-Prüfsummen oder Bildanzahl weichen ab')
                                command = base + ['-stream_loop','-1'] + inp + download + ['-an','-f','null','-']
                                detail = f'{len(expected)} Bilder stimmen mit der Software-Referenz überein'
                            else:
                                encoded = Path(temp)/(codec+'.mkv')
                                enc = ['-c:v',codec+'_nvenc','-gpu','0','-preset','p4','-b:v','10M','-pix_fmt','yuv420p']
                                inp = ['-hwaccel','none','-i',str(ref),'-an']
                                encode_base = base
                                if vaapi:
                                    encode_base = base + ['-vaapi_device', uuid]
                                    enc = ['-vf', 'format=nv12,hwupload', '-c:v', codec+'_vaapi',
                                           '-b:v', '10M']
                                invoke(encode_base + inp + enc + ['-frames:v',str(info['frames']),str(encoded)],env)
                                raw, _ = invoke(base + ['-hwaccel','none','-i',str(encoded),'-an',
                                                        '-pix_fmt','yuv420p','-f','framemd5','-'],env)
                                if len(frame_hashes(raw)) != len(expected):
                                    raise CommandError('Encoder: Bildanzahl weicht ab')
                                _, log = invoke([ffmpeg,'-hide_banner','-nostdin','-loglevel','info','-xerror',
                                    '-hwaccel','none','-i',str(encoded),'-hwaccel','none','-i',str(ref),
                                    '-filter_complex','[0:v][1:v]ssim','-an','-f','null','-'],env)
                                score = quality_score(log)
                                if score < .95:
                                    raise CommandError(f'Encoder: SSIM {score:.6f} unter Testgrenze 0.95 (kein eindeutiger Hardwaredefekt)')
                                detail = f'{len(expected)} Bilder dekodierbar; SSIM={score:.6f} (Testgrenze 0.95)'
                                command = encode_base + ['-stream_loop','-1'] + inp + enc + ['-f','null','-']
                            cases += 1; commands.append((codec, direction, command))
                            emit(event='result', gpu=uuid, codec=codec, direction=direction, status='passed', detail=detail)
                        except CommandError as exc:
                            status = classify_error(str(exc))
                            errors += status == 'error'; incomplete += status != 'error'
                            emit(event='result', gpu=uuid, codec=codec, direction=direction,status=status,detail=str(exc))
                if args.seconds and commands:
                    try:
                        stress(commands,args.seconds,args.streams,env,uuid)
                        emit(event='result',gpu=uuid,direction='stress',status='passed',
                             detail=f'{args.seconds}s / {args.streams} Streams; Validierung erfolgte vor der Lastphase')
                    except CommandError as exc:
                        status = classify_error(str(exc))
                        errors += status == 'error'; incomplete += status != 'error'
                        emit(event='result',gpu=uuid,direction='stress',status=status,detail=str(exc))
                after = telemetry(uuid,env)
                emit(event='gpu_end',gpu=uuid,values=after)
                low,high = str(before.get('uncorrected_ecc','')),str(after.get('uncorrected_ecc',''))
                if low.isdigit() and high.isdigit() and int(high)>int(low):
                    errors += 1
                    emit(event='result',gpu=uuid,status='error',direction='ECC',detail=f'Neue unkorrigierbare ECC-Ereignisse: {int(high)-int(low)}')
        emit(event='summary', passed=cases, errors=errors, incomplete=incomplete,
             detail='Kein vollständiger Hardwaredefektnachweis; Telemetrie kann nicht verfügbar sein. Kein CPU-Fallback für GPU-Prüfungen.')
        return 1 if errors else (2 if incomplete or not cases else 0)
    except Cancelled:
        emit(event='cancelled', status='cancelled'); return 130
    except (OSError, ValueError, KeyError, CommandError) as exc:
        emit(event='result',status='error',detail=str(exc)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
