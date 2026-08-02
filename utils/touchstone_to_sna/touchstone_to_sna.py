#!/usr/bin/env python3
"""Upload Touchstone S-parameter traces to a Siglent SNA5000A as memory traces.

Reads a Touchstone .sNp file (e.g. one extracted by csa_to_touchstone) and
pushes each S-parameter into the instrument's memory trace layer over SCPI
(raw socket, port 5025). For every uploaded parameter the tool:

  1. finds a trace on the target channel already measuring that parameter,
     or creates one (:CALC{ch}:PAR{tr}:DEF)
  2. allocates its memory trace (:CALC{ch}:TRAC{tr}:MATH:MEMorize)
  3. overwrites the memory with the file data (:CALC{ch}:TRAC{tr}:DATA:SMEM)
  4. turns the memory trace display on (:DISP:WIND{ch}:TRAC{tr}:MEM ON)

The uploaded data appears as the static memory ("user") trace behind the live
sweep. By default the channel sweep is re-pointed to match the file's
frequency axis first — :DATA:SMEM requires the point count to match, and the
x-axis must match for the overlay to mean anything.

Usage:
    touchstone_to_sna.py traces.s2p --host 192.168.1.100
    touchstone_to_sna.py traces.s3p --host sna --params S11,S21 --channel 2
    touchstone_to_sna.py traces.s2p --dry-run
"""
import argparse
import cmath
import math
import re
import socket
import sys
from pathlib import Path

# ---------------------------------------------------------------- touchstone

FREQ_MULT = {'HZ': 1.0, 'KHZ': 1e3, 'MHZ': 1e6, 'GHZ': 1e9}


def read_touchstone(path):
    """Parse a Touchstone v1 .sNp -> (freqs_hz, {'S11': [complex], ...})."""
    m = re.search(r'\.s(\d+)p$', path.name, re.I)
    if not m:
        sys.exit(f'{path}: expected a .sNp extension')
    nports = int(m.group(1))
    unit_mult, fmt = 1e9, 'MA'  # touchstone defaults
    tokens = []
    for line in open(path):
        line = line.split('!', 1)[0].strip()
        if not line:
            continue
        if line.startswith('#'):
            parts = line[1:].upper().split()
            for i, p in enumerate(parts):
                if p in FREQ_MULT:
                    unit_mult = FREQ_MULT[p]
                elif p in ('RI', 'MA', 'DB'):
                    fmt = p
            continue
        tokens += [float(t) for t in line.split()]

    per_point = 1 + 2 * nports * nports
    if len(tokens) % per_point:
        sys.exit(f'{path}: token count {len(tokens)} is not a multiple of '
                 f'{per_point} (1 freq + {nports}x{nports} complex values)')

    def to_complex(a, b):
        if fmt == 'RI':
            return complex(a, b)
        mag = 10 ** (a / 20) if fmt == 'DB' else a
        return mag * cmath.exp(1j * math.radians(b))

    freqs, sparams = [], {}
    for off in range(0, len(tokens), per_point):
        freqs.append(tokens[off] * unit_mult)
        vals = tokens[off + 1:off + per_point]
        if nports == 2:  # touchstone v1 quirk: 2-port order is S11 S21 S12 S22
            order = [(1, 1), (2, 1), (1, 2), (2, 2)]
        else:
            order = [(r, c) for r in range(1, nports + 1) for c in range(1, nports + 1)]
        for k, (r, c) in enumerate(order):
            sparams.setdefault(f'S{r}{c}', []).append(
                to_complex(vals[2 * k], vals[2 * k + 1]))
    return freqs, sparams


# ---------------------------------------------------------------------- scpi

class Scpi:
    def __init__(self, host, port, timeout, dry_run=False, verbose=False):
        self.dry_run, self.verbose = dry_run, verbose
        if not dry_run:
            self.sock = socket.create_connection((host, port), timeout=timeout)
            self.buf = b''

    def send(self, cmd):
        if self.verbose or self.dry_run:
            shown = cmd if len(cmd) <= 120 else cmd[:117] + '...'
            print(f'  > {shown}')
        if not self.dry_run:
            self.sock.sendall(cmd.encode() + b'\n')

    def query(self, cmd, default=''):
        self.send(cmd)
        if self.dry_run:
            return default
        while b'\n' not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('connection closed by instrument')
            self.buf += chunk
        line, self.buf = self.buf.split(b'\n', 1)
        reply = line.decode().strip()
        if self.verbose:
            print(f'  < {reply}')
        return reply

    def drain_errors(self):
        errors = []
        if self.dry_run:
            return errors
        for _ in range(20):
            err = self.query(':SYSTem:ERRor?')
            if err.split(',')[0].lstrip('+-') == '0':
                break
            errors.append(err)
        return errors


# -------------------------------------------------------------------- upload

def read_trace_map(scpi, ch):
    """Query the channel's traces once -> ({param: first trace index}, count)."""
    count = int(scpi.query(f':CALCulate{ch}:PARameter:COUNt?', default='1') or 1)
    trace_map = {}
    for tr in range(1, count + 1):
        existing = scpi.query(f':CALCulate{ch}:PARameter{tr}:DEFine?',
                              default='S11' if tr == 1 else '').upper().strip()
        trace_map.setdefault(existing, tr)
    return trace_map, count


def main():
    ap = argparse.ArgumentParser(
        description='Upload Touchstone traces to a Siglent SNA5000A as memory traces over SCPI.')
    ap.add_argument('touchstone', type=Path, help='.sNp file to upload')
    ap.add_argument('--host', help='instrument hostname or IP')
    ap.add_argument('--port', type=int, default=5025, help='SCPI raw socket port (default 5025)')
    ap.add_argument('--channel', type=int, default=1, help='target channel (default 1)')
    ap.add_argument('--params', help='comma-separated subset to upload, e.g. S11,S21 '
                                     '(default: all in the file)')
    ap.add_argument('--port-map', help='comma-separated instrument ports for file ports 1..N, '
                                       'e.g. "1,3" maps file S21 to instrument S31')
    ap.add_argument('--keep-sweep', action='store_true',
                    help="don't re-point the channel sweep to the file's frequency axis "
                         '(sweep points must already match)')
    ap.add_argument('--no-display', action='store_true',
                    help="upload memory only, don't turn the memory trace display on")
    ap.add_argument('--timeout', type=float, default=10.0, help='socket timeout seconds')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the SCPI commands without connecting')
    ap.add_argument('-v', '--verbose', action='store_true', help='show all SCPI traffic')
    args = ap.parse_args()

    if not args.dry_run and not args.host:
        sys.exit('--host is required (or use --dry-run)')

    freqs, sparams = read_touchstone(args.touchstone)
    npts = len(freqs)

    if args.port_map:
        pmap = {i + 1: int(p) for i, p in enumerate(args.port_map.split(','))}
        sparams = {f'S{pmap[int(k[1])]}{pmap[int(k[2])]}': v for k, v in sparams.items()}

    params = sorted(sparams)
    if args.params:
        want = [p.strip().upper() for p in args.params.split(',')]
        missing = [p for p in want if p not in sparams]
        if missing:
            sys.exit(f'not in file: {", ".join(missing)} (file has {", ".join(params)})')
        params = want

    print(f'{args.touchstone.name}: {npts} pts '
          f'{freqs[0] / 1e9:.6f}-{freqs[-1] / 1e9:.6f} GHz, uploading {", ".join(params)} '
          f'to channel {args.channel}')

    scpi = Scpi(args.host, args.port, args.timeout, args.dry_run, args.verbose)
    idn = scpi.query('*IDN?', default='(dry run)')
    print(f'instrument: {idn}')

    ch = args.channel
    scpi.send(':FORMat:DATA ASCii')
    if not args.keep_sweep:
        scpi.send(f':SENSe{ch}:SWEep:TYPE LINear')
        scpi.send(f':SENSe{ch}:SWEep:POINts {npts}')
        scpi.send(f':SENSe{ch}:FREQuency:STARt {freqs[0]:.6f}')
        scpi.send(f':SENSe{ch}:FREQuency:STOP {freqs[-1]:.6f}')
    elif not args.dry_run:
        actual = int(scpi.query(f':SENSe{ch}:SWEep:POINts?') or 0)
        if actual != npts:
            sys.exit(f'channel {ch} has {actual} sweep points but the file has {npts}; '
                     'drop --keep-sweep or fix the instrument setup')

    trace_map, count = read_trace_map(scpi, ch)
    for param in params:
        tr, created = trace_map.get(param), False
        if tr is None:
            count += 1
            tr, created = count, True
            scpi.send(f':CALCulate{ch}:PARameter:COUNt {count}')
            scpi.send(f':CALCulate{ch}:PARameter{tr}:DEFine {param}')
            trace_map[param] = tr
        values = sparams[param]
        data = ','.join(f'{z.real:.9e},{z.imag:.9e}' for z in values)
        scpi.send(f':CALCulate{ch}:TRACe{tr}:MATH:MEMorize')
        scpi.send(f':CALCulate{ch}:TRACe{tr}:DATA:SMEMory {data}')
        if not args.no_display:
            scpi.send(f':DISPlay:WINDow{ch}:TRACe{tr}:MEMory ON')
        verified = ''
        if not args.dry_run:
            # read back: MEMorize above copied live data into memory, so a
            # failed SMEMory write would otherwise pass unnoticed
            try:
                readback = [float(v) for v in
                            scpi.query(f':CALCulate{ch}:TRACe{tr}:DATA:SMEMory?').split(',')]
                sent = [x for z in values for x in (z.real, z.imag)]
                ok = (len(readback) == len(sent)
                      and max(abs(a - b) for a, b in zip(readback, sent)) < 1e-6)
                verified = ', verified' if ok else ', READBACK MISMATCH'
            except (socket.timeout, ValueError):
                verified = ', readback failed'
        print(f'  {param} -> channel {ch} trace {tr} memory'
              + (' (new trace)' if created else '') + verified)

    scpi.query('*OPC?', default='1')
    errors = scpi.drain_errors()
    if errors:
        print('instrument reported errors:', file=sys.stderr)
        for e in errors:
            print(f'  {e}', file=sys.stderr)
        sys.exit(1)
    print('done, no instrument errors')


if __name__ == '__main__':
    main()
