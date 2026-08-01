#!/usr/bin/env python3
"""Extract S-parameter trace data from Siglent SNA5000A state files into Touchstone files.

Works with .csa (state + data) files saved by SNA5000-series vector network
analyzers. A .csa is a tar archive containing csa_tmp/state.sta (itself a tar)
which holds state.xml plus one raw_data_chN.bin per channel:

    !Siglent Technologies,<model>,<serial>,<firmware>
    !Date: <timestamp>
    !file type,bin
    !file version,2
    !Freq Axis: <start_hz>,<stop_hz>,<npoints>
    #S11,<npoints><npoints x complex float64 LE>
    #S12,<npoints>...
    ...

Sections present:
    #Sxy  - S-parameters, receiver port x, source port y
    #A..#D - test receiver raw waves per driven port (A=port1 .. D=port4)
    #Rxy  - reference receiver waves
Sections exist for every receiver against every *driven* source port, so a
channel that swept sources 1..K yields a complete KxK S-matrix -> .sKp.

The stored values are as-displayed at save time: whether they are
error-corrected depends on the channel's calibration state (CaliSwitch in
state.xml) when the file was saved.

Usage:
    csa_to_touchstone.py file.csa [-o OUTDIR] [--info]
"""
import argparse
import io
import re
import struct
import sys
import tarfile
from pathlib import Path

SECTION_RE = re.compile(rb'#(S\d\d|[ABCD]\d|R\d\d),(\d+)\n')
FREQ_RE = re.compile(rb'!Freq Axis: ([0-9.eE+-]+),([0-9.eE+-]+),(\d+)')
IDENT_RE = re.compile(rb'!(Siglent[^\n]*)\n')


def parse_bin(data):
    """Parse one raw_data_chN.bin -> (ident, freqs, {section: [complex]})."""
    fm = FREQ_RE.search(data)
    if not fm:
        raise ValueError('no "!Freq Axis:" header - not a SNA5000A raw data blob')
    start, stop, npts = float(fm.group(1)), float(fm.group(2)), int(fm.group(3))
    freqs = [start + (stop - start) * i / (npts - 1) for i in range(npts)]
    im = IDENT_RE.search(data)
    ident = im.group(1).decode(errors='replace') if im else 'unknown instrument'
    sections = {}
    for m in SECTION_RE.finditer(data):
        name, n = m.group(1).decode(), int(m.group(2))
        end = m.end() + 16 * n
        if end > len(data):
            continue  # marker bytes that happen to appear inside float data
        vals = struct.unpack_from(f'<{2 * n}d', data, m.end())
        sections[name] = [complex(vals[2 * i], vals[2 * i + 1]) for i in range(n)]
    return ident, freqs, sections


def driven_ports(sections):
    """Source ports actually swept = the source-index digits present in Sxy keys."""
    return sorted({int(k[2]) for k in sections if k.startswith('S')})


def write_touchstone(path, freqs, sections, ports, comment=''):
    """Write an .sNp for the square S-matrix over `ports` (Touchstone v1, RI, 50 ohm)."""
    n = len(ports)
    with open(path, 'w', newline='\n') as f:
        if comment:
            f.write(f'! {comment}\n')
        f.write(f'! ports: {", ".join(str(p) for p in ports)}\n')
        f.write('# Hz S RI R 50\n')
        for idx, freq in enumerate(freqs):
            if n == 1:
                z = sections[f'S{ports[0]}{ports[0]}'][idx]
                f.write('%.9e %.9e %.9e\n' % (freq, z.real, z.imag))
            elif n == 2:
                # Touchstone v1 2-port order is S11 S21 S12 S22
                order = [(ports[0], ports[0]), (ports[1], ports[0]),
                         (ports[0], ports[1]), (ports[1], ports[1])]
                v = [sections[f'S{r}{c}'][idx] for r, c in order]
                f.write('%.9e %s\n' % (freq, ' '.join(
                    '%.9e %.9e' % (z.real, z.imag) for z in v)))
            else:
                # 3+ ports: row-major, one matrix row per line
                f.write('%.9e' % freq)
                for r in ports:
                    v = [sections[f'S{r}{c}'][idx] for c in ports]
                    f.write(' ' + ' '.join('%.9e %.9e' % (z.real, z.imag) for z in v))
                    if r != ports[-1]:
                        f.write('\n         ')
                f.write('\n')


def iter_channel_blobs(path):
    """Yield (channel_number, bytes) for each raw_data_chN.bin inside a .csa or .sta."""
    def blobs_from_tar(tf):
        for m in tf.getmembers():
            mm = re.search(r'raw_data_ch(\d+)\.bin$', m.name)
            if mm:
                yield int(mm.group(1)), tf.extractfile(m).read()

    with tarfile.open(path) as outer:
        sta = [m for m in outer.getmembers() if m.name.endswith('.sta')]
        if sta:  # .csa: state.sta tar nested inside
            with tarfile.open(fileobj=io.BytesIO(outer.extractfile(sta[0]).read())) as inner:
                yield from sorted(blobs_from_tar(inner))
        else:  # bare .sta saved directly
            yield from sorted(blobs_from_tar(outer))


def main():
    ap = argparse.ArgumentParser(
        description='Extract S-parameters from a Siglent SNA5000A .csa/.sta state file '
                    'into Touchstone .sNp files (one per channel).')
    ap.add_argument('state_file', type=Path, help='.csa or .sta file saved by the instrument')
    ap.add_argument('-o', '--outdir', type=Path, default=None,
                    help='output directory (default: alongside the input file)')
    ap.add_argument('--info', action='store_true',
                    help='only list channels/sections, write nothing')
    args = ap.parse_args()

    outdir = args.outdir or args.state_file.parent
    outdir.mkdir(parents=True, exist_ok=True)

    found = False
    for ch, blob in iter_channel_blobs(args.state_file):
        found = True
        ident, freqs, sections = parse_bin(blob)
        ports = driven_ports(sections)
        span = f'{freqs[0] / 1e9:.6f}-{freqs[-1] / 1e9:.6f} GHz'
        if args.info:
            print(f'ch{ch}: {ident}')
            print(f'  {len(freqs)} pts {span}, driven ports {ports}')
            print(f'  sections: {", ".join(sections)}')
            continue
        out = outdir / f'{args.state_file.stem}_ch{ch}.s{len(ports)}p'
        write_touchstone(out, freqs, sections, ports,
                         comment=f'extracted from {args.state_file.name} channel {ch} ({ident})')
        print(f'ch{ch}: {len(freqs)} pts {span}, driven ports {ports} -> {out}')

    if not found:
        sys.exit(f'no raw_data_chN.bin found in {args.state_file} - '
                 'was it saved as "State only"? (needs State+Data)')


if __name__ == '__main__':
    main()
