# siglent-utils

Utilities for working with files produced by Siglent test & measurement
instruments.

## csa_to_touchstone.py

Extracts S-parameter trace data from a Siglent **SNA5000A-series VNA** state
file (`.csa` / `.sta`) into standard [Touchstone](https://ibis.org/touchstone_ver2.0/touchstone_ver2_0.pdf)
`.sNp` files — no instrument needed, pure Python 3 standard library.

```
usage: csa_to_touchstone.py [-h] [-o OUTDIR] [--info] state_file

positional arguments:
  state_file            .csa or .sta file saved by the instrument

options:
  -o, --outdir OUTDIR   output directory (default: alongside the input file)
  --info                only list channels/sections, write nothing
```

One Touchstone file is written per channel, named `<input>_chN.sKp`, where K
is the number of ports the channel actually drove — a channel sweeping
sources 1–3 yields a complete 3×3 S-matrix and therefore an `.s3p`.

```
$ python3 csa_to_touchstone.py mystate.csa
ch1: 201 pts 2.274500-2.325500 GHz, driven ports [1, 2] -> mystate_ch1.s2p
ch2: 201 pts 2.262235-2.413235 GHz, driven ports [1, 2, 3] -> mystate_ch2.s3p
```

Output is Touchstone v1: `# Hz S RI R 50`, real/imaginary pairs, standard
2-port ordering (S11 S21 S12 S22), row-per-line matrix layout for 3+ ports.
Files load directly into scikit-rf, ADS, AWR, QUCS, etc.

### The .csa file format

A `.csa` ("state and cal/data") file is a **tar archive** containing
`csa_tmp/state.sta`, which is itself a tar archive:

```
mystate.csa                    (tar)
└── csa_tmp/state.sta          (tar)
    └── sta_tmp/
        ├── state.xml          full instrument state (channels, traces, markers…)
        ├── raw_data_ch1.bin   per-channel measurement data
        ├── raw_data_ch1.bin.md5
        ├── raw_data_ch2.bin
        └── …
```

Each `raw_data_chN.bin` is self-describing — ASCII header lines starting with
`!`, then binary sections introduced by `#<name>,<count>\n`:

```
!Siglent Technologies,<model>,<serial>,<firmware>
!Date: 2026-02-11 15:18:24
!file type,bin
!file version,2
!Freq Axis: <start_hz>,<stop_hz>,<npoints>
#S11,201  <201 × little-endian complex float64 (re, im)>
#S12,201  …
#A1,201   …
#R11,201  …
```

Section families:

| Name    | Content                                                        |
|---------|----------------------------------------------------------------|
| `#Sxy`  | S-parameter: receiver port *x*, source port *y*                |
| `#A1`…`#D3` | test-receiver raw waves (A=port 1 … D=port 4) per driven port |
| `#Rxy`  | reference-receiver waves                                       |

Sections exist for **every receiver** against **every driven source port**,
so undriven ports appear only as extra receiver rows (leakage/noise floor)
and cannot form a complete larger matrix — the tool exports the square
submatrix of driven ports.

### Caveats

- The stored S-parameters are **as displayed at save time**. If the channel
  had no user calibration active (`CaliSwitch=0` in `state.xml`), the values
  are uncorrected raw ratios.
- The frequency axis is reconstructed as linear start→stop; segment or
  log-sweep channels are not handled.
- A `.csa` saved as *State only* contains no `raw_data_chN.bin` and nothing
  can be extracted; save as *State + Data* on the instrument.

Tested against files from an SNA5014A running firmware V1.0.0.2.15.

## License

MIT — see [LICENSE](LICENSE).
