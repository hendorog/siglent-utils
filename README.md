# siglent-utils

Utilities for working with files produced by Siglent test & measurement
instruments. Each utility lives in its own directory under `utils/` with a
README explaining what it does and how to use it.

## Utilities

| Utility | Description |
|---|---|
| [csa_to_touchstone](utils/csa_to_touchstone/) | Extract S-parameter traces from SNA5000A-series VNA state files (`.csa`/`.sta`) into Touchstone `.sNp` files, CSV, or plots |
| [touchstone_to_sna](utils/touchstone_to_sna/) | Upload Touchstone `.sNp` traces to an SNA5000A as memory ("user") traces over SCPI |
| [sna_to_touchstone](utils/sna_to_touchstone/) | Pull Touchstone `.sNp` files from a live SNA5000A over SCPI |

## License

MIT — see [LICENSE](LICENSE).
