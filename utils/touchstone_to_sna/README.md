# touchstone_to_sna

Uploads S-parameter traces from a Touchstone `.sNp` file to a Siglent
**SNA5000A-series VNA** as **memory traces**, over SCPI (raw socket, port
5025). Pure Python 3 standard library.

The natural companion to [csa_to_touchstone](../csa_to_touchstone/): extract
traces from a saved state file, then push them back onto any SNA5000A as
static reference ("user") traces behind the live sweep — compare a golden
unit against the DUT on the bench, restore a reference measurement without
the original state file, or display simulated data next to reality.

```
usage: touchstone_to_sna.py [-h] [--host HOST] [--port PORT] [--channel CHANNEL]
                            [--params PARAMS] [--port-map PORT_MAP] [--keep-sweep]
                            [--no-display] [--timeout TIMEOUT] [--dry-run] [-v]
                            touchstone

positional arguments:
  touchstone           .sNp file to upload

options:
  --host HOST          instrument hostname or IP
  --port PORT          SCPI raw socket port (default 5025)
  --channel CHANNEL    target channel (default 1)
  --params PARAMS      comma-separated subset to upload, e.g. S11,S21
                       (default: all in the file)
  --port-map PORT_MAP  comma-separated instrument ports for file ports 1..N,
                       e.g. "1,3" maps file S21 to instrument S31
  --keep-sweep         don't re-point the channel sweep to the file's
                       frequency axis (sweep points must already match)
  --no-display         upload memory only, don't turn the memory display on
  --timeout TIMEOUT    socket timeout seconds (default 10)
  --dry-run            print the SCPI commands without connecting
  -v, --verbose        show all SCPI traffic
```

Example — upload every parameter of a 2-port file:

```
$ python3 touchstone_to_sna.py mystate_ch1.s2p --host 192.168.1.100
mystate_ch1.s2p: 201 pts 2.274500-2.325500 GHz, uploading S11, S12, S21, S22 to channel 1
instrument: Siglent Technologies,SNA5014A,...
  S11 -> channel 1 trace 1 memory, verified
  S12 -> channel 1 trace 2 memory (new trace), verified
  S21 -> channel 1 trace 3 memory (new trace), verified
  S22 -> channel 1 trace 4 memory (new trace), verified
done, no instrument errors
```

## How it works

For each uploaded parameter the tool:

1. re-points the channel sweep to the file's frequency axis
   (`:SENS{ch}:SWE:POIN`, `:FREQ:STAR/STOP`) — skipped with `--keep-sweep` —
   because the memory write requires a matching point count and the overlay
   is only meaningful on a matching x-axis;
2. reuses the first trace on the channel already measuring that parameter,
   or defines a new one (`:CALC{ch}:PAR{tr}:DEF`);
3. allocates the trace's memory (`:CALC{ch}:TRAC{tr}:MATH:MEMorize`), then
   overwrites it with the file data (`:CALC{ch}:TRAC{tr}:DATA:SMEMory`);
4. turns on the memory-trace display (`:DISP:WIND{ch}:TRAC{tr}:MEM ON`);
5. reads the memory back (`:DATA:SMEMory?`) and compares against what was
   sent, so a silently ignored write cannot pass as success.

Any queued instrument errors (`:SYST:ERR?`) are drained and reported at the
end; a non-empty queue exits non-zero.

## Notes

- `:CALC:TRAC:DATA:SMEMory` is implemented by firmware V1.0.0.2.15 but
  absent from older programming guides — semantics match the Keysight ENA
  equivalent (write corrected complex data into the memory trace).
- The Touchstone parser accepts v1 files in RI, MA, or DB format with any
  frequency unit, including multi-line matrix rows for 3+ ports.
- Memory traces live on top of a measured trace, so uploading S-parameters
  the instrument can't measure (e.g. port 4 on a 2-port unit) will fail.
- The uploaded memory trace is held by the instrument until overwritten —
  the live sweep keeps updating the data trace independently.
