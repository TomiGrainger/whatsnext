"""Minimal QR code encoder — standard library only.

Enough of the spec to turn a short join URL into a scannable code:
byte mode, error-correction level L, versions 1-10, all eight masks scored by
the standard penalty rules. Returns a matrix of booleans; render_svg turns that
into an SVG string.

Not a general-purpose library — no kanji/numeric modes, no ECI, no structured
append. It exists so the projector can show a join code without pulling in a
dependency.
"""

# --- Galois field (GF(256)) tables for Reed-Solomon -------------------------

EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def _rs_generator(n):
    """Generator polynomial for n error-correction codewords."""
    poly = [1]
    for i in range(n):
        poly = _poly_mul(poly, [1, EXP[i]])
    return poly


def _poly_mul(a, b):
    out = [0] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        for j, bv in enumerate(b):
            out[i + j] ^= _mul(av, bv)
    return out


def _rs_encode(data, n):
    gen = _rs_generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j, g in enumerate(gen):
                rem[i + j] ^= _mul(g, coef)
    return rem[len(data):]


# --- Version tables (level L only) ------------------------------------------
# version -> (total codewords, ec codewords per block, [block sizes])
VERSIONS = {
    1: (26, 7, [19]),
    2: (44, 10, [34]),
    3: (70, 15, [55]),
    4: (100, 20, [80]),
    5: (134, 26, [108]),
    6: (172, 18, [68, 68]),
    7: (196, 20, [78, 78]),
    8: (242, 24, [97, 97]),
    9: (292, 30, [116, 116]),
    10: (346, 18, [68, 68, 69, 69]),
}

ALIGN_POS = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


def _capacity(version):
    """Data codewords available at level L."""
    return sum(VERSIONS[version][2])


def _pick_version(length):
    for v in range(1, 11):
        # 4 bits mode + 8/16 bits length + payload, in codewords
        count_bits = 8 if v < 10 else 16
        needed = (4 + count_bits + length * 8 + 7) // 8
        if needed <= _capacity(v):
            return v
    raise ValueError("data too long for this encoder (max ~270 bytes)")


def _bitstream(data, version):
    count_bits = 8 if version < 10 else 16
    bits = []

    def put(value, n):
        for i in range(n - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)               # byte mode
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)

    cap = _capacity(version) * 8
    put(0, min(4, cap - len(bits)))          # terminator
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) < _capacity(version):
        codewords.append(pad[i % 2])
        i += 1
    return codewords


def _interleave(codewords, version):
    _, ec_len, blocks = VERSIONS[version]
    data_blocks, ec_blocks, pos = [], [], 0
    for size in blocks:
        chunk = codewords[pos:pos + size]
        pos += size
        data_blocks.append(chunk)
        ec_blocks.append(_rs_encode(chunk, ec_len))

    out = []
    for i in range(max(len(b) for b in data_blocks)):
        for b in data_blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_len):
        for b in ec_blocks:
            out.append(b[i])
    return out


# --- Matrix construction ----------------------------------------------------

def _new_matrix(size):
    return [[None] * size for _ in range(size)]


def _place_finder(m, r, c):
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = r + dr, c + dc
            if not (0 <= rr < len(m) and 0 <= cc < len(m)):
                continue
            inside = 0 <= dr <= 6 and 0 <= dc <= 6
            ring = inside and (dr in (0, 6) or dc in (0, 6))
            core = 2 <= dr <= 4 and 2 <= dc <= 4
            m[rr][cc] = ring or core


def _place_function_patterns(m, version):
    size = len(m)
    _place_finder(m, 0, 0)
    _place_finder(m, 0, size - 7)
    _place_finder(m, size - 7, 0)

    for i in range(8, size - 8):            # timing
        bit = i % 2 == 0
        m[6][i] = bit
        m[i][6] = bit

    for r in ALIGN_POS[version]:            # alignment
        for c in ALIGN_POS[version]:
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1

    m[size - 8][8] = True                   # dark module

    if version >= 7:                        # version info areas reserved
        for i in range(18):
            m[i // 3][size - 11 + i % 3] = False
            m[size - 11 + i % 3][i // 3] = False

    for i in range(9):                      # format info areas reserved
        if m[8][i] is None:
            m[8][i] = False
        if m[i][8] is None:
            m[i][8] = False
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = False
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = False


def _reserved(version, size):
    """Mask of cells taken by function patterns, so data skips them."""
    m = _new_matrix(size)
    _place_function_patterns(m, version)
    return [[cell is not None for cell in row] for row in m]


def _place_data(m, reserved, bits):
    size = len(m)
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:                        # skip the vertical timing column
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if reserved[row][c]:
                    continue
                bit = bits[idx] if idx < len(bits) else 0
                idx += 1
                m[row][c] = bool(bit)
        upward = not upward
        col -= 2


MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _apply_mask(m, reserved, mask):
    out = [row[:] for row in m]
    for r in range(len(m)):
        for c in range(len(m)):
            if not reserved[r][c] and MASKS[mask](r, c):
                out[r][c] = not out[r][c]
    return out


def _penalty(m):
    size = len(m)
    score = 0

    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 1, line[0]
        for cell in line[1:]:
            if cell == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, cell
        if run >= 5:
            score += 3 + (run - 5)

    for r in range(size - 1):               # 2x2 blocks
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3

    pattern = [True, False, True, True, True, False, True]
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 6):
            if line[i:i + 7] == pattern:
                before = line[max(0, i - 4):i]
                after = line[i + 7:i + 11]
                if len(before) == 4 and not any(before):
                    score += 40
                if len(after) == 4 and not any(after):
                    score += 40

    dark = sum(1 for row in m for cell in row if cell)
    pct = dark * 100 // (size * size)
    score += 10 * min(abs(pct - 50) // 5, 10)
    return score


FORMAT_GEN = 0b10100110111
FORMAT_XOR = 0b101010000010010


def _format_bits(mask):
    """Level L is 0b01 in the format field."""
    data = (0b01 << 3) | mask
    rem = data << 10
    for i in range(4, -1, -1):
        if rem & (1 << (i + 10)):
            rem ^= FORMAT_GEN << i
    return ((data << 10) | rem) ^ FORMAT_XOR


def _place_format(m, mask):
    """Both copies of the 15-bit format field, bit 0 first.

    The two copies run in opposite directions: one down column 8 and along row
    8 near the top-left finder, the other along row 8 on the right and up
    column 8 at the bottom. The dark module is restored last because the
    bottom copy runs through where it sits.
    """
    size = len(m)
    bits = _format_bits(mask)
    for i in range(15):
        bit = bool((bits >> i) & 1)

        # copy 1: down column 8, then up from the bottom of column 8
        if i < 6:
            m[i][8] = bit
        elif i < 8:
            m[i + 1][8] = bit
        else:
            m[size - 15 + i][8] = bit

        # copy 2: right-hand end of row 8, then back along row 8
        if i < 8:
            m[8][size - 1 - i] = bit
        elif i == 8:
            m[8][7] = bit
        else:
            m[8][14 - i] = bit

    m[size - 8][8] = True


VERSION_GEN = 0b1111100100101


def _version_bits(version):
    rem = version << 12
    for i in range(5, -1, -1):
        if rem & (1 << (i + 12)):
            rem ^= VERSION_GEN << i
    return (version << 12) | rem


def _place_version(m, version):
    """Versions 7 and up carry an 18-bit version block twice: above the
    bottom-left finder and left of the top-right one."""
    if version < 7:
        return
    size = len(m)
    bits = _version_bits(version)
    for i in range(18):
        bit = bool((bits >> i) & 1)
        m[i // 3][size - 11 + i % 3] = bit
        m[size - 11 + i % 3][i // 3] = bit


def encode(text):
    """text -> matrix of booleans (True = dark module)."""
    data = text.encode("utf-8")
    version = _pick_version(len(data))
    size = version * 4 + 17

    codewords = _interleave(_bitstream(data, version), version)
    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)

    reserved = _reserved(version, size)
    base = _new_matrix(size)
    _place_function_patterns(base, version)
    _place_data(base, reserved, bits)

    best, best_score = None, None
    for mask in range(8):
        candidate = _apply_mask(base, reserved, mask)
        _place_format(candidate, mask)
        _place_version(candidate, version)
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best


def render_svg(text, size_px=300, quiet=4):
    """A crisp, scalable QR as a standalone SVG document."""
    m = encode(text)
    n = len(m)
    total = n + quiet * 2
    scale = size_px / total
    rects = []
    for r, row in enumerate(m):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                rects.append('<rect x="%g" y="%g" width="%g" height="%g"/>'
                             % ((start + quiet) * scale, (r + quiet) * scale,
                                (c - start) * scale, scale))
            else:
                c += 1
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
        'shape-rendering="crispEdges" role="img" aria-label="Join QR code">'
        '<rect width="%d" height="%d" fill="#f5f3ef"/><g fill="#0a0a0b">%s</g></svg>'
        % (size_px, size_px, size_px, size_px, size_px, size_px, "".join(rects))
    )
