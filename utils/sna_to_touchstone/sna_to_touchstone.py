#!/usr/bin/env python3
"""Pull a Touchstone .sNp file from a Siglent SNA5000A over SCPI.

Has the instrument save its own Touchstone file for the active channel
(:MMEM:STOR:SNP), transfers it back over the raw socket (:MMEM:TRANsfer?),
and deletes the temporary file from the instrument. Using the instrument's
own SNP writer means the file carries exactly what the instrument would
export from the front panel: the real frequency axis (including segment
sweeps), current correction state, and all requested ports.

Usage:
    sna_to_touchstone.py --host 192.168.1.100                    # ports 1,2 -> .s2p
    sna_to_touchstone.py --host sna --ports 1,2,3 -o dut.s3p
    sna_to_touchstone.py --host sna --channel 2 --format MA
"""
import argparse
import re
import socket
import sys
from pathlib import Path

SNP_TYPE_CMD = {1: 'S1P', 2: 'S2P', 3: 'S3P', 4: 'S4P'}


class Scpi:
    def __init__(self, host, port, timeout, dry_run=False, verbose=False):
        self.dry_run, self.verbose = dry_run, verbose
        if not dry_run:
            self.sock = socket.create_connection((host, port), timeout=timeout)
            self.buf = b''

    def send(self, cmd):
        if self.verbose or self.dry_run:
            print(f'  > {cmd}')
        if not self.dry_run:
            self.sock.sendall(cmd.encode() + b'\n')

    def _recv_more(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError('connection closed by instrument')
        self.buf += chunk

    def query(self, cmd, default=''):
        self.send(cmd)
        if self.dry_run:
            return default
        while b'\n' not in self.buf:
            self._recv_more()
        line, self.buf = self.buf.split(b'\n', 1)
        reply = line.decode().strip()
        if self.verbose:
            print(f'  < {reply}')
        return reply

    def query_block(self, cmd):
        """Query returning an IEEE 488.2 definite-length block (#<n><len><bytes>)."""
        self.send(cmd)
        if self.dry_run:
            return b''
        while len(self.buf) < 2:
            self._recv_more()
        if not self.buf.startswith(b'#'):
            # plain ASCII fallback: one line
            while b'\n' not in self.buf:
                self._recv_more()
            line, self.buf = self.buf.split(b'\n', 1)
            return line
        ndigits = int(self.buf[1:2])
        while len(self.buf) < 2 + ndigits:
            self._recv_more()
        length = int(self.buf[2:2 + ndigits])
        header = 2 + ndigits
        while len(self.buf) < header + length:
            self._recv_more()
        payload = self.buf[header:header + length]
        self.buf = self.buf[header + length:].lstrip(b'\n')
        if self.verbose:
            print(f'  < <{length} byte block>')
        return payload

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


def main():
    ap = argparse.ArgumentParser(
        description='Pull a Touchstone .sNp file from a Siglent SNA5000A over SCPI.')
    ap.add_argument('--host', help='instrument hostname or IP')
    ap.add_argument('--port', type=int, default=5025, help='SCPI raw socket port (default 5025)')
    ap.add_argument('--channel', type=int, default=1,
                    help='channel to export (activated first; default 1)')
    ap.add_argument('--ports', default='1,2',
                    help='comma-separated instrument ports to include (default 1,2)')
    ap.add_argument('--format', default='RI', choices=['RI', 'MA', 'DB'],
                    help='touchstone number format (default RI, lossless)')
    ap.add_argument('-o', '--output', type=Path, default=None,
                    help='output file (default sna_ch<N>.s<K>p)')
    ap.add_argument('--keep-remote', action='store_true',
                    help="don't delete the temporary file from the instrument")
    ap.add_argument('--timeout', type=float, default=30.0, help='socket timeout seconds')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the SCPI commands without connecting')
    ap.add_argument('-v', '--verbose', action='store_true', help='show all SCPI traffic')
    args = ap.parse_args()

    if not args.dry_run and not args.host:
        sys.exit('--host is required (or use --dry-run)')

    ports = [int(p) for p in args.ports.split(',')]
    if not 1 <= len(ports) <= 4 or len(set(ports)) != len(ports) \
            or not all(1 <= p <= 4 for p in ports):
        sys.exit(f'--ports must be 1-4 distinct ports in 1..4, got {args.ports}')
    nports = len(ports)
    out = args.output or Path(f'sna_ch{args.channel}.s{nports}p')
    remote = f'local/siglent_utils_pull.s{nports}p'

    scpi = Scpi(args.host, args.port, args.timeout, args.dry_run, args.verbose)
    idn = scpi.query('*IDN?', default='(dry run)')
    print(f'instrument: {idn}')

    scpi.send(f':DISPlay:CHANnel{args.channel}:ACTivate')
    scpi.send(f':MMEMory:STORe:SNP:FORMat {args.format}')
    scpi.send(f':MMEMory:STORe:SNP:TYPE:{SNP_TYPE_CMD[nports]} '
              + ','.join(str(p) for p in ports))
    scpi.send(f':MMEMory:STORe:SNP "{remote}"')
    scpi.query('*OPC?', default='1')

    data = scpi.query_block(f':MMEMory:TRANsfer? "{remote}"')
    if not args.keep_remote:
        scpi.send(f':MMEMory:DELete "{remote}"')

    errors = scpi.drain_errors()
    if errors:
        print('instrument reported errors:', file=sys.stderr)
        for e in errors:
            print(f'  {e}', file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f'(dry run) would write {out}')
        return
    if not data.strip():
        sys.exit('received an empty file - is the channel sweeping S-parameters?')
    out.write_bytes(data)

    # summarize what was pulled
    text = data.decode(errors='replace')
    points = sum(1 for ln in text.splitlines()
                 if ln.strip() and not ln.lstrip().startswith(('!', '#')))
    if nports >= 3:  # 3+ port touchstone rows span multiple lines
        points //= nports
    m = re.search(r'^\s*#.*$', text, re.M)
    print(f'{out}: {len(data)} bytes, {points} points, '
          f'option line: {m.group(0).strip() if m else "none found"}')


if __name__ == '__main__':
    main()
