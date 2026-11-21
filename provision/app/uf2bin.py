#!/usr/bin/env python3
"""UF2 -> raw binary + load address, for flashing the T114 over SWD (flash.sh swd).

  uf2bin.py <in.uf2> <out.bin>     prints the hex load address (e.g. 0x26000)

Only nRF52840 blocks (family 0xADA52840) are taken; gaps between blocks are 0xFF.
"""
import struct
import sys

UF2_MAGIC = (0x0A324655, 0x9E5D5157, 0x0AB16F30)
FAMILY_NRF52840 = 0xADA52840
FLAG_FAMILY = 0x2000

blocks = {}
data = open(sys.argv[1], "rb").read()
for i in range(0, len(data) - 511, 512):
    m0, m1, flags, addr, size, _n, _t, family = struct.unpack("<8I", data[i:i + 32])
    if (m0, m1) != UF2_MAGIC[:2] or struct.unpack("<I", data[i + 508:i + 512])[0] != UF2_MAGIC[2]:
        sys.exit(f"bad UF2 block at {i}")
    if flags & FLAG_FAMILY and family != FAMILY_NRF52840:
        continue
    blocks[addr] = data[i + 32:i + 32 + size]
if not blocks:
    sys.exit("no nRF52840 blocks in this UF2")
base = min(blocks)
end = max(a + len(b) for a, b in blocks.items())
out = bytearray(b"\xff" * (end - base))
for a, b in blocks.items():
    out[a - base:a - base + len(b)] = b
open(sys.argv[2], "wb").write(out)
print(hex(base))
