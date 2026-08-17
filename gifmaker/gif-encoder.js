/*
 * gif-encoder.js -- a dependency-free GIF89a encoder.
 *
 * Everything the app needs to turn a list of RGBA frames into an animated GIF:
 * median-cut colour quantisation (256 colours per frame), optional
 * Floyd-Steinberg dithering, and the LZW compressor the format requires.
 *
 * It is vendored rather than pulled from a CDN so the app keeps working
 * offline, and it is written to load three ways:
 *
 *   browser  <script src="gif-encoder.js">   -> window.GifEncoder
 *   worker   importScripts('gif-encoder.js') -> self.GifEncoder
 *   node     require('./gif-encoder.js')     -> module.exports
 *
 * The node path exists so test_gif_encoder.py can encode fixtures and decode
 * them again in Python, which is how this file is checked for correctness.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.GifEncoder = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ---------------------------------------------------------------- bytes */

  /** A growable byte buffer. GIF is little-endian for its 16-bit fields. */
  function ByteStream(capacity) {
    this.buf = new Uint8Array(capacity || 1 << 16);
    this.len = 0;
  }
  ByteStream.prototype._room = function (n) {
    if (this.len + n <= this.buf.length) return;
    var cap = this.buf.length;
    while (cap < this.len + n) cap *= 2;
    var next = new Uint8Array(cap);
    next.set(this.buf.subarray(0, this.len));
    this.buf = next;
  };
  ByteStream.prototype.byte = function (v) {
    this._room(1);
    this.buf[this.len++] = v & 0xff;
  };
  ByteStream.prototype.bytes = function (arr) {
    this._room(arr.length);
    this.buf.set(arr, this.len);
    this.len += arr.length;
  };
  ByteStream.prototype.short = function (v) {
    this.byte(v & 0xff);
    this.byte((v >> 8) & 0xff);
  };
  ByteStream.prototype.ascii = function (s) {
    for (var i = 0; i < s.length; i++) this.byte(s.charCodeAt(i));
  };
  ByteStream.prototype.take = function () {
    return this.buf.slice(0, this.len);
  };

  /**
   * GIF payloads are carried in "sub-blocks": a length byte (1-255) followed
   * by that many data bytes, terminated by a zero-length block.
   */
  function SubBlocks(out) {
    this.out = out;
    this.buf = new Uint8Array(255);
    this.n = 0;
  }
  SubBlocks.prototype.push = function (b) {
    this.buf[this.n++] = b;
    if (this.n === 255) this.flush();
  };
  SubBlocks.prototype.flush = function () {
    if (this.n === 0) return;
    this.out.byte(this.n);
    this.out.bytes(this.buf.subarray(0, this.n));
    this.n = 0;
  };
  SubBlocks.prototype.end = function () {
    this.flush();
    this.out.byte(0);
  };

  /* ------------------------------------------------------------------ LZW */

  /**
   * Variable-width LZW as GIF specifies it: codes are packed least
   * significant bit first, the code width grows as the dictionary fills, and
   * the dictionary is reset with a clear code once it reaches 4096 entries.
   *
   * The one thing that is easy to get wrong: a decoder is always one entry
   * behind the encoder, because it cannot add a dictionary entry until it has
   * read the *following* code. So the width has to grow based on the entry
   * count as it stood before this iteration's insert -- widening a code early
   * desynchronises the two and the image decodes to noise. Emitting and only
   * then inserting keeps this encoder in step, the same order the original
   * compress(1)-derived GIF encoders used.
   */
  function lzwEncode(out, minCodeSize, indices) {
    var blocks = new SubBlocks(out);
    var clearCode = 1 << minCodeSize;
    var eoiCode = clearCode + 1;
    var codeSize = minCodeSize + 1;
    var next = eoiCode + 1;
    var dict = new Map();

    var acc = 0;
    var accBits = 0;
    function emit(code) {
      acc |= code << accBits;
      accBits += codeSize;
      while (accBits >= 8) {
        blocks.push(acc & 0xff);
        acc >>= 8;
        accBits -= 8;
      }
      // Widen only once the table has outgrown the current width.
      if (next >= 1 << codeSize && codeSize < 12) codeSize++;
    }

    emit(clearCode);

    if (indices.length > 0) {
      var prefix = indices[0];
      for (var i = 1; i < indices.length; i++) {
        var k = indices[i];
        var key = (prefix << 8) | k;
        var found = dict.get(key);
        if (found !== undefined) {
          prefix = found;
          continue;
        }
        emit(prefix);
        if (next < 4096) {
          dict.set(key, next++);
        } else {
          // Table full: tell the decoder to start over.
          emit(clearCode);
          dict = new Map();
          next = eoiCode + 1;
          codeSize = minCodeSize + 1;
        }
        prefix = k;
      }
      emit(prefix);
    }

    emit(eoiCode);
    if (accBits > 0) blocks.push(acc & 0xff);
    blocks.end();
  }

  /* --------------------------------------------------------- quantisation */

  // Luma weights. Splitting and matching in a perceptually weighted space
  // keeps skin tones and skies from collapsing before greens do.
  var WR = 0.299, WG = 0.587, WB = 0.114;

  /**
   * Median cut: put every sampled pixel in one box, then repeatedly split the
   * box with the widest colour spread at its median until we have `maxColors`
   * boxes. The palette is each box's average colour.
   */
  function medianCut(pts, total, maxColors) {
    var idx = new Uint32Array(total);
    for (var i = 0; i < total; i++) idx[i] = i;

    function makeBox(lo, hi) {
      var rmin = 255, rmax = 0, gmin = 255, gmax = 0, bmin = 255, bmax = 0;
      for (var i = lo; i < hi; i++) {
        var p = idx[i] * 3;
        var r = pts[p], g = pts[p + 1], b = pts[p + 2];
        if (r < rmin) rmin = r;
        if (r > rmax) rmax = r;
        if (g < gmin) gmin = g;
        if (g > gmax) gmax = g;
        if (b < bmin) bmin = b;
        if (b > bmax) bmax = b;
      }
      var dr = (rmax - rmin) * WR, dg = (gmax - gmin) * WG, db = (bmax - bmin) * WB;
      var axis = 0, span = dr;
      if (dg > span) { axis = 1; span = dg; }
      if (db > span) { axis = 2; span = db; }
      return { lo: lo, hi: hi, axis: axis, span: span, count: hi - lo };
    }

    var boxes = [makeBox(0, total)];
    while (boxes.length < maxColors) {
      var best = -1, bestSpan = 0;
      for (var b = 0; b < boxes.length; b++) {
        var box = boxes[b];
        if (box.count < 2 || box.span <= 0) continue;
        if (best < 0 || box.span > bestSpan) { best = b; bestSpan = box.span; }
      }
      if (best < 0) break; // every box is a single colour already

      var target = boxes[best];
      var axis = target.axis;
      // Sort just this box's slice along its widest axis, then cut in half.
      var slice = Array.prototype.slice.call(idx.subarray(target.lo, target.hi));
      slice.sort(function (x, y) { return pts[x * 3 + axis] - pts[y * 3 + axis]; });
      idx.set(slice, target.lo);

      var mid = target.lo + (target.count >> 1);
      boxes[best] = makeBox(target.lo, mid);
      boxes.push(makeBox(mid, target.hi));
    }

    var palette = new Uint8Array(boxes.length * 3);
    for (var n = 0; n < boxes.length; n++) {
      var box2 = boxes[n];
      var sr = 0, sg = 0, sb = 0;
      for (var j = box2.lo; j < box2.hi; j++) {
        var q = idx[j] * 3;
        sr += pts[q];
        sg += pts[q + 1];
        sb += pts[q + 2];
      }
      var c = box2.count || 1;
      palette[n * 3] = Math.round(sr / c);
      palette[n * 3 + 1] = Math.round(sg / c);
      palette[n * 3 + 2] = Math.round(sb / c);
    }
    return palette;
  }

  /** Sample at most ~40k pixels; median cut does not need more than that. */
  function samplePixels(rgba) {
    var n = rgba.length >> 2;
    var stride = Math.max(1, Math.ceil(n / 40000));
    var count = 0;
    for (var i = 0; i < n; i += stride) count++;
    var pts = new Uint8Array(count * 3);
    var m = 0;
    for (var j = 0; j < n; j += stride) {
      var p = j << 2;
      pts[m++] = rgba[p];
      pts[m++] = rgba[p + 1];
      pts[m++] = rgba[p + 2];
    }
    return { pts: pts, total: m / 3 };
  }

  /**
   * Nearest-palette-entry lookup, memoised on the top 5 bits of each channel.
   * A full scan of <=256 entries per distinct colour would dominate encode
   * time on a phone; the 32k-entry cache turns it into one scan per bucket.
   */
  function makeMatcher(palette) {
    var cache = new Int16Array(32768).fill(-1);
    var n = palette.length / 3;
    return function (r, g, b) {
      var key = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
      var hit = cache[key];
      if (hit >= 0) return hit;
      var best = 0, bestDist = Infinity;
      for (var i = 0; i < n; i++) {
        var p = i * 3;
        var dr = r - palette[p], dg = g - palette[p + 1], db = b - palette[p + 2];
        var d = dr * dr * WR + dg * dg * WG + db * db * WB;
        if (d < bestDist) {
          bestDist = d;
          best = i;
          if (d === 0) break;
        }
      }
      cache[key] = best;
      return best;
    };
  }

  function clamp255(v) {
    return v < 0 ? 0 : v > 255 ? 255 : v;
  }

  /** Straight nearest-colour mapping, no error diffusion. */
  function mapDirect(rgba, palette, out) {
    var match = makeMatcher(palette);
    for (var i = 0, p = 0; i < out.length; i++, p += 4) {
      out[i] = match(rgba[p], rgba[p + 1], rgba[p + 2]);
    }
  }

  /**
   * Floyd-Steinberg error diffusion, walking alternate rows in reverse
   * (serpentine) so the error does not streak in one direction.
   */
  function mapDithered(rgba, width, height, palette, out) {
    var match = makeMatcher(palette);
    var buf = new Float32Array(width * height * 3);
    for (var i = 0, p = 0, q = 0; i < width * height; i++, p += 4, q += 3) {
      buf[q] = rgba[p];
      buf[q + 1] = rgba[p + 1];
      buf[q + 2] = rgba[p + 2];
    }

    for (var y = 0; y < height; y++) {
      var leftToRight = (y & 1) === 0;
      var xStart = leftToRight ? 0 : width - 1;
      var xEnd = leftToRight ? width : -1;
      var step = leftToRight ? 1 : -1;

      for (var x = xStart; x !== xEnd; x += step) {
        var o = (y * width + x) * 3;
        var r = clamp255(buf[o]), g = clamp255(buf[o + 1]), b = clamp255(buf[o + 2]);
        var idx = match(r | 0, g | 0, b | 0);
        out[y * width + x] = idx;

        var pe = idx * 3;
        var er = r - palette[pe], eg = g - palette[pe + 1], eb = b - palette[pe + 2];

        // 7/16 ahead, then 3/16 - 5/16 - 1/16 across the row below.
        spread(buf, width, height, x + step, y, er, eg, eb, 7 / 16);
        spread(buf, width, height, x - step, y + 1, er, eg, eb, 3 / 16);
        spread(buf, width, height, x, y + 1, er, eg, eb, 5 / 16);
        spread(buf, width, height, x + step, y + 1, er, eg, eb, 1 / 16);
      }
    }
  }

  function spread(buf, width, height, x, y, er, eg, eb, f) {
    if (x < 0 || x >= width || y < 0 || y >= height) return;
    var o = (y * width + x) * 3;
    buf[o] += er * f;
    buf[o + 1] += eg * f;
    buf[o + 2] += eb * f;
  }

  /**
   * Quantise one RGBA frame.
   * Returns { palette, indices } where palette is packed RGB triples.
   */
  function quantizeFrame(rgba, width, height, options) {
    var opts = options || {};
    var maxColors = Math.max(2, Math.min(256, opts.maxColors || 256));
    var sample = samplePixels(rgba);
    var palette = medianCut(sample.pts, sample.total, maxColors);
    var indices = new Uint8Array(width * height);
    if (opts.dither) mapDithered(rgba, width, height, palette, indices);
    else mapDirect(rgba, palette, indices);
    return { palette: palette, indices: indices };
  }

  /* -------------------------------------------------------------- writing */

  /** Colour tables must be a power-of-two length; pad the tail with black. */
  function padPalette(palette) {
    var used = palette.length / 3;
    var bits = 1;
    while (1 << bits < used) bits++;
    var size = 1 << bits;
    var padded = new Uint8Array(size * 3);
    padded.set(palette);
    return { table: padded, bits: bits, size: size };
  }

  function writeHeader(out, width, height, globalTable) {
    out.ascii('GIF89a');
    out.short(width);
    out.short(height);
    // Packed: global colour table flag, colour resolution 8bpp, table size.
    out.byte(0x80 | 0x70 | (globalTable.bits - 1));
    out.byte(0); // background colour index
    out.byte(0); // pixel aspect ratio (unspecified)
    out.bytes(globalTable.table);
  }

  /** The de facto looping extension every browser honours. */
  function writeLoop(out, loopCount) {
    out.byte(0x21);
    out.byte(0xff);
    out.byte(11);
    out.ascii('NETSCAPE2.0');
    out.byte(3);
    out.byte(1);
    out.short(loopCount); // 0 == forever
    out.byte(0);
  }

  function writeFrame(out, frame, width, height, useLocalTable) {
    var padded = padPalette(frame.palette);

    // Graphic control extension: disposal "do not dispose" (frames are
    // full-size and opaque, so each one simply replaces the last), no
    // transparency, delay in hundredths of a second.
    var delay = Math.max(2, Math.round((frame.delayMs || 100) / 10));
    out.byte(0x21);
    out.byte(0xf9);
    out.byte(4);
    out.byte(1 << 2);
    out.short(delay);
    out.byte(0); // transparent colour index (unused)
    out.byte(0);

    // Image descriptor.
    out.byte(0x2c);
    out.short(0);
    out.short(0);
    out.short(width);
    out.short(height);
    out.byte(useLocalTable ? 0x80 | (padded.bits - 1) : 0x00);
    if (useLocalTable) out.bytes(padded.table);

    var minCodeSize = Math.max(2, padded.bits);
    out.byte(minCodeSize);
    lzwEncode(out, minCodeSize, frame.indices);
  }

  /**
   * Assemble quantised frames into a GIF.
   *
   * frames: [{ palette, indices, delayMs }] -- all width*height sized.
   * Returns a Uint8Array.
   */
  function writeGif(frames, width, height, options) {
    var opts = options || {};
    if (!frames.length) throw new Error('a GIF needs at least one frame');

    var out = new ByteStream(width * height + 4096);
    // The first frame's palette doubles as the global table so that decoders
    // which assume one exists are happy; every frame still carries its own.
    writeHeader(out, width, height, padPalette(frames[0].palette));
    writeLoop(out, opts.loop === false ? 1 : opts.loopCount || 0);
    for (var i = 0; i < frames.length; i++) {
      writeFrame(out, frames[i], width, height, true);
    }
    out.byte(0x3b); // trailer
    return out.take();
  }

  /**
   * One-shot helper: quantise and write in a single call.
   * rgbaFrames: [{ data: Uint8ClampedArray, delayMs }]
   */
  function encode(rgbaFrames, width, height, options) {
    var opts = options || {};
    var quantised = rgbaFrames.map(function (f) {
      var q = quantizeFrame(f.data, width, height, opts);
      q.delayMs = f.delayMs;
      return q;
    });
    return writeGif(quantised, width, height, opts);
  }

  return {
    encode: encode,
    writeGif: writeGif,
    quantizeFrame: quantizeFrame,
    // exported for tests
    _lzwEncode: lzwEncode,
    _ByteStream: ByteStream,
  };
});
