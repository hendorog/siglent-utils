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
import cmath
import io
import math
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


def write_csv(path, freqs, sections, ports):
    """Write freq + re/im/dB/deg columns for the square S-matrix over `ports`."""
    params = [f'S{r}{c}' for r in ports for c in ports]
    with open(path, 'w', newline='\n') as f:
        f.write('freq_hz,' + ','.join(
            f'{p}_re,{p}_im,{p}_db,{p}_deg' for p in params) + '\n')
        for idx, freq in enumerate(freqs):
            cols = ['%.9e' % freq]
            for p in params:
                z = sections[p][idx]
                mag = abs(z)
                db = 20 * math.log10(mag) if mag > 0 else -999.0
                cols += ['%.9e' % z.real, '%.9e' % z.imag,
                         '%.4f' % db, '%.4f' % math.degrees(cmath.phase(z))]
            f.write(','.join(cols) + '\n')


# Categorical series colors by receiver port (validated palette, fixed order)
PORT_COLORS = {1: '#2a78d6', 2: '#eb6834', 3: '#1baf7a', 4: '#eda100'}
INK, INK_MUTED, GRID, SURFACE = '#0b0b0b', '#898781', '#e1e0d9', '#fcfcfb'


def plot_channel(plt, freqs, sections, ports, title):
    """One figure per channel: a panel per source port, |Sxy| in dB, colored by receiver."""
    n = len(ports)
    fig, axes = plt.subplots(1, n, figsize=(4.4 * n, 3.9), sharey=True, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    ghz = [f / 1e9 for f in freqs]
    for ax, src in zip(axes[0], ports):
        ax.set_facecolor(SURFACE)
        for rcv in ports:
            name = f'S{rcv}{src}'
            db = [20 * math.log10(max(abs(z), 1e-12)) for z in sections[name]]
            ax.plot(ghz, db, color=PORT_COLORS[rcv], linewidth=1.8, label=name)
            ax.annotate(name, (ghz[-1], db[-1]), xytext=(4, 0),
                        textcoords='offset points', va='center', fontsize=8,
                        color=INK, clip_on=False)
        ax.set_title(f'source port {src}', fontsize=10, color=INK)
        ax.set_xlabel('frequency (GHz)', fontsize=9, color=INK_MUTED)
        ax.grid(True, color=GRID, linewidth=0.6)
        ax.tick_params(labelsize=8, colors=INK_MUTED)
        ax.margins(x=0)
        for spine in ax.spines.values():
            spine.set_color(GRID)
    axes[0][0].set_ylabel('magnitude (dB)', fontsize=9, color=INK_MUTED)
    fig.legend(handles=[plt.Line2D([], [], color=PORT_COLORS[p], linewidth=1.8,
                                   label=f'receiver port {p}') for p in ports],
               loc='upper right', fontsize=8, frameon=False, ncol=n)
    fig.suptitle(title, fontsize=11, color=INK, x=0.01, ha='left')
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return fig


def main():
    ap = argparse.ArgumentParser(
        description='Extract S-parameters from a Siglent SNA5000A .csa/.sta state file '
                    'into Touchstone .sNp files (one per channel), optionally with '
                    'CSV export and plots.')
    ap.add_argument('state_file', type=Path, help='.csa or .sta file saved by the instrument')
    ap.add_argument('-o', '--outdir', type=Path, default=None,
                    help='output directory (default: alongside the input file)')
    ap.add_argument('--info', action='store_true',
                    help='only list channels/sections, write nothing')
    ap.add_argument('--csv', action='store_true',
                    help='also write a CSV per channel (freq + re/im/dB/deg per S-param)')
    ap.add_argument('--plot', action='store_true',
                    help='also write a PNG plot per channel (requires matplotlib)')
    ap.add_argument('--show', action='store_true',
                    help='display the plots in interactive windows (implies --plot layout)')
    args = ap.parse_args()

    plt = None
    if args.plot or args.show:
        try:
            import matplotlib
            if not args.show:
                matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError:
            sys.exit('plotting requires matplotlib: pip install matplotlib')

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
        stem = f'{args.state_file.stem}_ch{ch}'
        out = outdir / f'{stem}.s{len(ports)}p'
        write_touchstone(out, freqs, sections, ports,
                         comment=f'extracted from {args.state_file.name} channel {ch} ({ident})')
        written = [out]
        if args.csv:
            csv_out = outdir / f'{stem}.csv'
            write_csv(csv_out, freqs, sections, ports)
            written.append(csv_out)
        if plt:
            fig = plot_channel(plt, freqs, sections, ports,
                               f'{args.state_file.name} ch{ch} — {span}, {len(freqs)} pts')
            if args.plot:
                png_out = outdir / f'{stem}.png'
                fig.savefig(png_out, dpi=130, facecolor=fig.get_facecolor())
                written.append(png_out)
        print(f'ch{ch}: {len(freqs)} pts {span}, driven ports {ports} -> '
              + ', '.join(p.name for p in written))

    if not found:
        sys.exit(f'no raw_data_chN.bin found in {args.state_file} - '
                 'was it saved as "State only"? (needs State+Data)')
    if plt and args.show:
        plt.show()


if __name__ == '__main__':
    main()
