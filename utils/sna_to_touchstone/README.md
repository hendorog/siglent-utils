# sna_to_touchstone

Pulls a Touchstone `.sNp` file from a live Siglent **SNA5000A-series VNA**
over SCPI (raw socket, port 5025). Pure Python 3 standard library.

The download counterpart to [touchstone_to_sna](../touchstone_to_sna/):
rather than reconstructing S-parameters from trace-data queries, it has the
instrument save its own Touchstone file for the selected channel
(`:MMEM:STOR:SNP`), transfers the file back (`:MMEM:TRANsfer?`), and deletes
the temporary copy from the instrument. That way the file is byte-for-byte
what a front-panel *Save SnP* would produce: real frequency axis (segment
sweeps included), current correction state, instrument header comments.

```
usage: sna_to_touchstone.py [-h] [--host HOST] [--port PORT] [--channel CHANNEL]
                            [--ports PORTS] [--format {RI,MA,DB}] [-o OUTPUT]
                            [--keep-remote] [--timeout TIMEOUT] [--dry-run] [-v]

options:
  --host HOST          instrument hostname or IP
  --port PORT          SCPI raw socket port (default 5025)
  --channel CHANNEL    channel to export (activated first; default 1)
  --ports PORTS        comma-separated instrument ports to include (default 1,2)
  --format {RI,MA,DB}  touchstone number format (default RI, lossless)
  -o, --output OUTPUT  output file (default sna_ch<N>.s<K>p)
  --keep-remote        don't delete the temporary file from the instrument
  --timeout TIMEOUT    socket timeout seconds (default 30)
  --dry-run            print the SCPI commands without connecting
  -v, --verbose        show all SCPI traffic
```

Example — grab a 3-port measurement from channel 1:

```
$ python3 sna_to_touchstone.py --host 192.168.1.100 --ports 1,2,3 -o dut.s3p
instrument: Siglent Technologies,SNA5014A,...
dut.s3p: 67059 bytes, 201 points, option line: # HZ S RI R 50
```

## How it works

1. `:DISP:CHAN{ch}:ACT` — make the requested channel active (the SNP save
   always exports the active channel);
2. `:MMEM:STOR:SNP:FORMat RI` and `:MMEM:STOR:SNP:TYPE:S{K}P <ports>` —
   select number format and port set;
3. `:MMEM:STOR:SNP "local/…"` + `*OPC?` — save on the instrument;
4. `:MMEM:TRANsfer? "local/…"` — fetch the file as an IEEE 488.2
   definite-length block;
5. `:MMEM:DELete "local/…"` — clean up (skipped with `--keep-remote`);
6. drain `:SYST:ERR?` and exit non-zero if the instrument queued any errors.

## Notes

- **Not yet validated on hardware.** Built against the SCPI tree of firmware
  V1.0.0.2.15 and the SNA5000A programming guide; `:MMEM:TRANsfer?` is in
  the firmware but not the older guide, so its block framing follows the
  Keysight ENA convention. Use `--dry-run` to inspect what would be sent.
- The export reflects whatever the channel is currently measuring — for a
  complete K×K matrix the instrument must be sweeping all K ports (full
  N-port correction recommended). Ports not being driven will export as
  receiver leakage, exactly as on a front-panel save.
- For a consistent snapshot, put the instrument in hold or single-sweep
  first; the save is asynchronous with a running continuous sweep.
