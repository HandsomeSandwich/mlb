"""Round-trip tests for gifmaker/gif-encoder.js.

The encoder is JavaScript, so the check is: encode fixture frames with node,
then decode the resulting bytes here with a from-scratch GIF89a parser (no
Pillow, nothing to install) and assert the structure and the pixels survived.

A hand-written decoder is the point -- it only agrees with the encoder if the
encoder really follows the spec, rather than both sharing a bug in a library.

    python test_gif_encoder.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENCODER = os.path.join(HERE, "gifmaker", "gif-encoder.js")


# --------------------------------------------------------------- decoding


class GifError(AssertionError):
    pass


def lzw_decode(min_code_size, data, expected_pixels):
    """Decode GIF's variable-width LZW stream into a list of palette indices."""
    clear_code = 1 << min_code_size
    eoi_code = clear_code + 1

    bitpos = 0
    total_bits = len(data) * 8

    def read_code(width):
        nonlocal bitpos
        if bitpos + width > total_bits:
            raise GifError("LZW stream ended mid-code")
        value = 0
        for i in range(width):
            byte = data[(bitpos + i) >> 3]
            bit = (byte >> ((bitpos + i) & 7)) & 1
            value |= bit << i
        bitpos += width
        return value

    def fresh_table():
        # +2 leaves room for the clear and end-of-information codes.
        return [[i] for i in range(clear_code)] + [[], []]

    table = fresh_table()
    code_size = min_code_size + 1
    out = []
    prev = None

    while True:
        code = read_code(code_size)
        if code == clear_code:
            table = fresh_table()
            code_size = min_code_size + 1
            prev = None
            continue
        if code == eoi_code:
            break

        if prev is None:
            if code >= len(table):
                raise GifError("first code after clear is out of range")
            entry = table[code]
        else:
            if code < len(table):
                entry = table[code]
            elif code == len(table):
                # The encoder may reference the entry it is about to add.
                entry = table[prev] + [table[prev][0]]
            else:
                raise GifError("code %d beyond table of %d" % (code, len(table)))
            if len(table) < 4096:
                table.append(table[prev] + [entry[0]])
                if len(table) == (1 << code_size) and code_size < 12:
                    code_size += 1

        out.extend(entry)
        prev = code

    if len(out) != expected_pixels:
        raise GifError("decoded %d pixels, expected %d" % (len(out), expected_pixels))
    return out


def read_sub_blocks(buf, pos):
    """Concatenate a run of length-prefixed sub-blocks; return (data, next)."""
    chunks = bytearray()
    while True:
        size = buf[pos]
        pos += 1
        if size == 0:
            return bytes(chunks), pos
        chunks += buf[pos:pos + size]
        pos += size


def decode_gif(buf):
    """Parse a GIF89a into {width, height, loop_count, frames:[...]}."""
    if buf[:6] != b"GIF89a":
        raise GifError("bad signature: %r" % buf[:6])

    width = buf[6] | (buf[7] << 8)
    height = buf[8] | (buf[9] << 8)
    packed = buf[10]
    pos = 13

    global_table = None
    if packed & 0x80:
        size = 2 << (packed & 0x07)
        global_table = buf[pos:pos + size * 3]
        pos += size * 3

    result = {
        "width": width,
        "height": height,
        "has_global_table": global_table is not None,
        "global_table": global_table,
        "loop_count": None,
        "frames": [],
    }

    pending_delay_ms = None
    pending_disposal = None

    while pos < len(buf):
        block = buf[pos]
        pos += 1

        if block == 0x3B:  # trailer
            if pos != len(buf):
                raise GifError("%d bytes trail the terminator" % (len(buf) - pos))
            return result

        if block == 0x21:  # extension
            label = buf[pos]
            pos += 1
            if label == 0xF9:  # graphic control
                size = buf[pos]
                if size != 4:
                    raise GifError("graphic control block is %d bytes" % size)
                fields = buf[pos + 1]
                pending_disposal = (fields >> 2) & 0x07
                pending_delay_ms = (buf[pos + 2] | (buf[pos + 3] << 8)) * 10
                pos += 1 + size
                if buf[pos] != 0:
                    raise GifError("graphic control block not terminated")
                pos += 1
            elif label == 0xFF:  # application
                size = buf[pos]
                name = bytes(buf[pos + 1:pos + 1 + size])
                pos += 1 + size
                data, pos = read_sub_blocks(buf, pos)
                if name == b"NETSCAPE2.0" and len(data) >= 3 and data[0] == 1:
                    result["loop_count"] = data[1] | (data[2] << 8)
            else:
                _, pos = read_sub_blocks(buf, pos)
            continue

        if block == 0x2C:  # image descriptor
            left = buf[pos] | (buf[pos + 1] << 8)
            top = buf[pos + 2] | (buf[pos + 3] << 8)
            fw = buf[pos + 4] | (buf[pos + 5] << 8)
            fh = buf[pos + 6] | (buf[pos + 7] << 8)
            fpacked = buf[pos + 8]
            pos += 9

            table = global_table
            local = bool(fpacked & 0x80)
            if local:
                size = 2 << (fpacked & 0x07)
                table = buf[pos:pos + size * 3]
                pos += size * 3
            if fpacked & 0x40:
                raise GifError("interlaced frames are not expected")

            min_code_size = buf[pos]
            pos += 1
            data, pos = read_sub_blocks(buf, pos)
            indices = lzw_decode(min_code_size, data, fw * fh)

            pixels = []
            for idx in indices:
                if (idx + 1) * 3 > len(table):
                    raise GifError("palette index %d outside colour table" % idx)
                pixels.append((table[idx * 3], table[idx * 3 + 1], table[idx * 3 + 2]))

            result["frames"].append({
                "left": left, "top": top, "width": fw, "height": fh,
                "has_local_table": local,
                "delay_ms": pending_delay_ms,
                "disposal": pending_disposal,
                "pixels": pixels,
            })
            pending_delay_ms = None
            pending_disposal = None
            continue

        raise GifError("unknown block 0x%02X at offset %d" % (block, pos - 1))

    raise GifError("file ended without a terminator")


# --------------------------------------------------------------- encoding


NODE = shutil.which("node") or shutil.which("nodejs")

HARNESS = r"""
const path = process.argv[2];
const spec = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
const GifEncoder = require(path);

// Frames are described compactly so the fixtures stay readable in Python:
//   solid    -> one colour everywhere
//   halves   -> left half colour a, right half colour b
//   gradient -> horizontal black-to-white ramp
function build(kind, w, h, colors) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let c;
      if (kind === 'solid') c = colors[0];
      else if (kind === 'halves') c = x < w / 2 ? colors[0] : colors[1];
      else { const v = Math.round((x / (w - 1)) * 255); c = [v, v, v]; }
      const p = (y * w + x) * 4;
      data[p] = c[0]; data[p + 1] = c[1]; data[p + 2] = c[2]; data[p + 3] = 255;
    }
  }
  return data;
}

const frames = spec.frames.map(f => ({
  data: build(f.kind, spec.width, spec.height, f.colors || []),
  delayMs: f.delayMs,
}));

const bytes = GifEncoder.encode(frames, spec.width, spec.height, spec.options || {});
process.stdout.write(Buffer.from(bytes));
"""


def encode_with_node(spec):
    if NODE is None:
        raise RuntimeError("node not found")
    with tempfile.TemporaryDirectory() as tmp:
        harness = os.path.join(tmp, "harness.js")
        specfile = os.path.join(tmp, "spec.json")
        with open(harness, "w") as fh:
            fh.write(HARNESS)
        with open(specfile, "w") as fh:
            json.dump(spec, fh)
        proc = subprocess.run(
            [NODE, harness, ENCODER, specfile],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        raise AssertionError("node encoder failed:\n" + proc.stderr.decode())
    return proc.stdout


# ------------------------------------------------------------------ tests


def close(a, b, tol=8):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_single_solid_frame():
    """The simplest possible GIF still has to parse and keep its colour."""
    gif = encode_with_node({
        "width": 8, "height": 8,
        "frames": [{"kind": "solid", "colors": [[220, 30, 60]], "delayMs": 100}],
    })
    out = decode_gif(gif)
    assert out["width"] == 8 and out["height"] == 8, out
    assert len(out["frames"]) == 1
    frame = out["frames"][0]
    assert frame["width"] == 8 and frame["height"] == 8
    assert len(frame["pixels"]) == 64
    for px in frame["pixels"]:
        assert close(px, (220, 30, 60)), px
    print("ok  single solid frame")


def test_multi_frame_delays_and_loop():
    """Per-frame delays, the loop extension and frame order must round-trip."""
    gif = encode_with_node({
        "width": 16, "height": 16,
        "frames": [
            {"kind": "solid", "colors": [[255, 0, 0]], "delayMs": 100},
            {"kind": "solid", "colors": [[0, 255, 0]], "delayMs": 250},
            {"kind": "solid", "colors": [[0, 0, 255]], "delayMs": 40},
        ],
    })
    out = decode_gif(gif)
    assert out["loop_count"] == 0, out["loop_count"]
    assert len(out["frames"]) == 3
    assert [f["delay_ms"] for f in out["frames"]] == [100, 250, 40]
    expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    for frame, want in zip(out["frames"], expected):
        assert close(frame["pixels"][0], want), (frame["pixels"][0], want)
    print("ok  multi-frame delays, order and looping")


def test_delay_floor():
    """Browsers clamp very short delays; the encoder writes a 2/100s floor."""
    gif = encode_with_node({
        "width": 4, "height": 4,
        "frames": [{"kind": "solid", "colors": [[10, 10, 10]], "delayMs": 5}],
    })
    out = decode_gif(gif)
    assert out["frames"][0]["delay_ms"] == 20, out["frames"][0]["delay_ms"]
    print("ok  sub-frame delays clamped to the 20ms floor")


def test_loop_once():
    """loop:false means play through a single time."""
    gif = encode_with_node({
        "width": 4, "height": 4,
        "frames": [{"kind": "solid", "colors": [[1, 2, 3]], "delayMs": 100}],
        "options": {"loop": False},
    })
    out = decode_gif(gif)
    assert out["loop_count"] == 1, out["loop_count"]
    print("ok  non-looping GIFs record a single play")


def test_two_tone_frame_keeps_edge():
    """A hard edge is where an off-by-one in the LZW stream would show up."""
    w, h = 32, 8
    gif = encode_with_node({
        "width": w, "height": h,
        "frames": [{
            "kind": "halves",
            "colors": [[0, 0, 0], [255, 255, 255]],
            "delayMs": 100,
        }],
    })
    out = decode_gif(gif)
    pixels = out["frames"][0]["pixels"]
    for y in range(h):
        for x in range(w):
            want = (0, 0, 0) if x < w // 2 else (255, 255, 255)
            got = pixels[y * w + x]
            assert close(got, want), "pixel (%d,%d) was %s, wanted %s" % (x, y, got, want)
    print("ok  hard edges survive quantisation and LZW")


def test_gradient_dithered_and_not():
    """Both mapping paths must produce a decodable, roughly correct image."""
    for dither in (False, True):
        gif = encode_with_node({
            "width": 64, "height": 16,
            "frames": [{"kind": "gradient", "delayMs": 100}],
            "options": {"dither": dither},
        })
        out = decode_gif(gif)
        pixels = out["frames"][0]["pixels"]
        assert len(pixels) == 64 * 16
        # Averaging a column cancels the dither noise, leaving the ramp.
        def column_mean(x):
            vals = [pixels[y * 64 + x][0] for y in range(16)]
            return sum(vals) / len(vals)
        assert column_mean(0) < 40, column_mean(0)
        assert column_mean(63) > 215, column_mean(63)
        assert column_mean(0) < column_mean(32) < column_mean(63)
        print("ok  gradient encodes cleanly (dither=%s)" % dither)


def test_large_frame_crosses_lzw_table_reset():
    """A big noisy-ish frame pushes LZW past 4096 codes and forces a reset."""
    gif = encode_with_node({
        "width": 256, "height": 256,
        "frames": [{"kind": "gradient", "delayMs": 100}],
        "options": {"dither": True},
    })
    out = decode_gif(gif)
    assert len(out["frames"][0]["pixels"]) == 256 * 256
    print("ok  large frames survive LZW dictionary resets")


def test_palette_cap_is_respected():
    """maxColors trims the colour table, which shrinks the file."""
    spec = {
        "width": 64, "height": 64,
        "frames": [{"kind": "gradient", "delayMs": 100}],
    }
    full = encode_with_node(dict(spec, options={"maxColors": 256}))
    small = encode_with_node(dict(spec, options={"maxColors": 8}))
    out = decode_gif(small)
    distinct = set(out["frames"][0]["pixels"])
    assert len(distinct) <= 8, len(distinct)
    assert len(small) < len(full), (len(small), len(full))
    print("ok  maxColors caps the palette and the file size")


def test_frames_carry_local_colour_tables():
    """Every frame gets its own palette so scene changes stay accurate."""
    gif = encode_with_node({
        "width": 16, "height": 16,
        "frames": [
            {"kind": "solid", "colors": [[200, 0, 0]], "delayMs": 100},
            {"kind": "solid", "colors": [[0, 0, 200]], "delayMs": 100},
        ],
    })
    out = decode_gif(gif)
    assert out["has_global_table"], "decoders expect a global table to exist"
    assert all(f["has_local_table"] for f in out["frames"])
    print("ok  frames carry their own colour tables")


TESTS = [
    test_single_solid_frame,
    test_multi_frame_delays_and_loop,
    test_delay_floor,
    test_loop_once,
    test_two_tone_frame_keeps_edge,
    test_gradient_dithered_and_not,
    test_large_frame_crosses_lzw_table_reset,
    test_palette_cap_is_respected,
    test_frames_carry_local_colour_tables,
]


def main():
    if NODE is None:
        print("SKIP: node is not installed, cannot exercise the JS encoder")
        return 0
    failures = 0
    for test in TESTS:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failures += 1
            print("FAIL %s: %s" % (test.__name__, exc))
    print()
    if failures:
        print("%d of %d tests failed" % (failures, len(TESTS)))
        return 1
    print("all %d GIF encoder tests passed" % len(TESTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
