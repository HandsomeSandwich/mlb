/*
 * app.js -- the GIF maker's UI.
 *
 * Rough shape of the thing:
 *
 *   frames[]  pictures, in order, each with its own on-screen time
 *   layers[]  text overlays, each shown on every frame or just one
 *   render()  paints one frame (background -> picture -> text) onto <canvas>
 *
 * The same render path draws the on-screen preview and the frames handed to
 * the encoder, so what you see really is what gets saved. Work is kept in
 * IndexedDB, because on a phone the tab gets evicted the moment you go and
 * look at something else.
 */
(function () {
  'use strict';

  /* ============================================================ constants */

  var FONTS = {
    impact: 'Impact, Haettenschweiler, "Arial Narrow", "Arial Black", sans-serif',
    sans: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif',
    serif: 'Georgia, "Times New Roman", Times, serif',
    rounded: '"Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif',
    mono: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
    comic: '"Comic Sans MS", "Chalkboard SE", "Comic Neue", cursive',
  };

  var ASPECTS = { '1:1': 1, '4:5': 0.8, '9:16': 0.5625, '4:3': 4 / 3, '16:9': 16 / 9 };

  // Imported pictures are re-encoded down to this longest edge. Phone cameras
  // produce 12MP files; holding twenty of them would exhaust memory long
  // before it helped, and the GIF is at most 720px anyway.
  var IMPORT_MAX_EDGE = 1280;

  var MAX_FRAMES = 60;

  /* ================================================================ state */

  var state = {
    frames: [],       // { id, blob, url, img, delayMs }
    layers: [],       // text overlays
    frameIndex: 0,
    layerId: null,
    aspect: '1:1',
    size: 480,
    fit: 'cover',
    bg: '#000000',
    maxColors: 256,
    dither: true,
    loop: true,
    defaultDelay: 400,
    playing: false,
  };

  var nextId = 1;
  function makeId(prefix) {
    return prefix + '-' + (nextId++) + '-' + Math.random().toString(36).slice(2, 7);
  }

  /* ================================================================= dom */

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

  var stage = $('#stage');
  var ctx = stage.getContext('2d');

  /* =============================================================== toast */

  var toastTimer = null;
  function toast(message) {
    var el = $('#toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 2800);
  }

  /* ========================================================== persistence */

  var DB_NAME = 'gifmaker';
  var STORE = 'state';
  var RECORD = 'current';
  var dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve, reject) {
      var request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = function () {
        request.result.createObjectStore(STORE);
      };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error); };
    }).catch(function () { return null; });
    return dbPromise;
  }

  function idbPut(value) {
    return openDb().then(function (db) {
      if (!db) return;
      return new Promise(function (resolve) {
        var tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).put(value, RECORD);
        tx.oncomplete = resolve;
        tx.onerror = resolve; // a failed autosave must never break editing
        tx.onabort = resolve;
      });
    });
  }

  function idbGet() {
    return openDb().then(function (db) {
      if (!db) return null;
      return new Promise(function (resolve) {
        var tx = db.transaction(STORE, 'readonly');
        var req = tx.objectStore(STORE).get(RECORD);
        req.onsuccess = function () { resolve(req.result || null); };
        req.onerror = function () { resolve(null); };
      });
    });
  }

  var saveTimer = null;
  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      saveTimer = null;
      save();
    }, 500);
  }

  /**
   * Write straight away instead of waiting out the debounce. Phones freeze or
   * discard a backgrounded tab with no warning, so anything still pending when
   * the app goes out of view has to be written now or it is gone.
   */
  function flushSave() {
    if (saveTimer === null) return;
    clearTimeout(saveTimer);
    saveTimer = null;
    save();
  }

  function save() {
    idbPut({
      frames: state.frames.map(function (f) {
        return { id: f.id, blob: f.blob, delayMs: f.delayMs };
      }),
      layers: state.layers.map(stripRuntime),
      settings: {
        aspect: state.aspect, size: state.size, fit: state.fit, bg: state.bg,
        maxColors: state.maxColors, dither: state.dither, loop: state.loop,
        defaultDelay: state.defaultDelay,
      },
    });
  }

  /** Hit boxes are recomputed every render; they must not be persisted. */
  function stripRuntime(layer) {
    var copy = {};
    Object.keys(layer).forEach(function (key) {
      if (key.charAt(0) !== '_') copy[key] = layer[key];
    });
    return copy;
  }

  function restore() {
    return idbGet().then(function (record) {
      if (!record) return;
      if (record.settings) {
        Object.keys(record.settings).forEach(function (key) {
          if (record.settings[key] !== undefined) state[key] = record.settings[key];
        });
      }
      state.layers = (record.layers || []).map(function (layer) {
        return Object.assign(makeLayer(), layer);
      });
      if (state.layers.length) state.layerId = state.layers[0].id;

      var frames = record.frames || [];
      return Promise.all(frames.map(function (saved) {
        return blobToImage(saved.blob).then(function (loaded) {
          return {
            id: saved.id, blob: saved.blob, url: loaded.url, img: loaded.img,
            delayMs: saved.delayMs,
          };
        }).catch(function () { return null; });
      })).then(function (loaded) {
        state.frames = loaded.filter(Boolean);
      });
    }).catch(function () { /* start empty */ });
  }

  /* ========================================================= image import */

  function blobToImage(blob) {
    var url = URL.createObjectURL(blob);
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve({ img: img, url: url }); };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('that file is not an image the browser can open'));
      };
      img.src = url;
    });
  }

  /**
   * Re-encode an imported picture: shrink it, and drop it through a canvas so
   * EXIF rotation is baked in (otherwise photos taken sideways stay sideways).
   */
  function normalize(blob) {
    return blobToImage(blob).then(function (loaded) {
      var img = loaded.img;
      var scale = Math.min(1, IMPORT_MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
      var w = Math.max(1, Math.round(img.naturalWidth * scale));
      var h = Math.max(1, Math.round(img.naturalHeight * scale));

      var work = document.createElement('canvas');
      work.width = w;
      work.height = h;
      var wctx = work.getContext('2d');
      wctx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(loaded.url);

      return new Promise(function (resolve) {
        work.toBlob(function (out) { resolve(out || blob); }, 'image/jpeg', 0.9);
      });
    });
  }

  // normalize() gives back a Blob and blobToImage() an <img>; keep them
  // together so the frame can be saved without re-encoding.
  function addFrameFromBlob(blob) {
    return normalize(blob).then(function (small) {
      return blobToImage(small).then(function (loaded) {
        return {
          id: makeId('frame'),
          blob: small,
          url: loaded.url,
          img: loaded.img,
          delayMs: state.defaultDelay,
        };
      });
    });
  }

  function importBlobs(blobs) {
    var room = MAX_FRAMES - state.frames.length;
    if (room <= 0) {
      toast('That is the ' + MAX_FRAMES + '-frame limit.');
      return Promise.resolve();
    }
    var list = blobs.slice(0, room);
    var skipped = blobs.length - list.length;

    return list.reduce(function (chain, blob) {
      return chain
        .then(function () { return addFrameFromBlob(blob); })
        .then(function (frame) { state.frames.push(frame); })
        .catch(function (err) { toast(err.message || 'Could not add that picture.'); });
    }, Promise.resolve()).then(function () {
      if (skipped > 0) toast('Only had room for ' + list.length + ' more.');
      if (state.frames.length && state.frameIndex >= state.frames.length) {
        state.frameIndex = state.frames.length - 1;
      }
      refreshAll();
      scheduleSave();
    });
  }

  /* ============================================================ rendering */

  function outputSize() {
    var ratio = ASPECTS[state.aspect] || 1;
    var w, h;
    if (ratio >= 1) { w = state.size; h = Math.round(state.size / ratio); }
    else { h = state.size; w = Math.round(state.size * ratio); }
    return { w: Math.max(2, w), h: Math.max(2, h) };
  }

  function drawPicture(context, img, W, H) {
    var iw = img.naturalWidth || img.width;
    var ih = img.naturalHeight || img.height;
    if (!iw || !ih) return;
    var scale = state.fit === 'contain'
      ? Math.min(W / iw, H / ih)
      : Math.max(W / iw, H / ih);
    var dw = iw * scale;
    var dh = ih * scale;
    context.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
  }

  function layersForFrame(index) {
    var frame = state.frames[index];
    return state.layers.filter(function (layer) {
      return layer.scope === 'all' || (frame && layer.scope === frame.id);
    });
  }

  function fontFor(layer, H) {
    var px = Math.max(6, (layer.size / 100) * H);
    var css = (layer.italic ? 'italic ' : '') + (layer.bold ? '700 ' : '400 ') +
      px + 'px ' + (FONTS[layer.family] || FONTS.sans);
    return { px: px, css: css };
  }

  function wrapLines(context, text, maxWidth) {
    var out = [];
    text.split('\n').forEach(function (para) {
      var words = para.split(/\s+/).filter(Boolean);
      if (!words.length) { out.push(''); return; }
      var line = words[0];
      for (var i = 1; i < words.length; i++) {
        var test = line + ' ' + words[i];
        if (context.measureText(test).width <= maxWidth) line = test;
        else { out.push(line); line = words[i]; }
      }
      out.push(line);
    });
    return out;
  }

  /**
   * Draw one text overlay. When `track` is set (the on-screen preview, not the
   * export pass) it also records where the text landed, so a tap can find it.
   */
  function drawLayer(context, layer, W, H, track) {
    var text = layer.uppercase ? layer.text.toUpperCase() : layer.text;
    if (!text.trim()) return;

    var font = fontFor(layer, H);
    context.save();
    context.font = font.css;
    context.textBaseline = 'middle';

    var lines = wrapLines(context, text, W * 0.92);
    var lineH = font.px * (layer.lineHeight / 100);
    var blockH = lineH * lines.length;

    var widths = lines.map(function (line) { return context.measureText(line).width; });
    var widest = widths.reduce(function (a, b) { return Math.max(a, b); }, 0);

    // Anchor x within the block depends on the alignment the user picked.
    var anchorX = layer.align === 'left' ? -widest / 2
      : layer.align === 'right' ? widest / 2
      : 0;

    context.translate(layer.x * W, layer.y * H);
    if (layer.rotation) context.rotate(layer.rotation * Math.PI / 180);
    context.globalAlpha = layer.opacity / 100;
    context.textAlign = layer.align;

    var pad = font.px * 0.16;
    if (layer.highlightOn) {
      context.fillStyle = layer.highlight;
      lines.forEach(function (line, i) {
        if (!line) return;
        var lw = widths[i];
        var x0 = layer.align === 'left' ? anchorX
          : layer.align === 'right' ? anchorX - lw
          : -lw / 2;
        context.fillRect(x0 - pad, -blockH / 2 + lineH * i, lw + pad * 2, lineH);
      });
    }

    var strokeWidth = font.px * (layer.outlineWidth / 100);

    lines.forEach(function (line, i) {
      if (!line) return;
      var y = -blockH / 2 + lineH * (i + 0.5);

      if (strokeWidth > 0) {
        // Shadow rides on the outline pass so the fill doesn't darken twice.
        if (layer.shadow) {
          context.shadowColor = 'rgba(0,0,0,0.55)';
          context.shadowBlur = font.px * 0.18;
          context.shadowOffsetY = font.px * 0.06;
        }
        context.lineWidth = strokeWidth;
        context.lineJoin = 'round';
        context.miterLimit = 2;
        context.strokeStyle = layer.outline;
        context.strokeText(line, anchorX, y);
        context.shadowColor = 'transparent';
        context.shadowBlur = 0;
        context.shadowOffsetY = 0;
      } else if (layer.shadow) {
        context.shadowColor = 'rgba(0,0,0,0.55)';
        context.shadowBlur = font.px * 0.18;
        context.shadowOffsetY = font.px * 0.06;
      }

      context.fillStyle = layer.color;
      context.fillText(line, anchorX, y);
      context.shadowColor = 'transparent';
      context.shadowBlur = 0;
      context.shadowOffsetY = 0;

      if (layer.underline) {
        var lw = widths[i];
        var ux = layer.align === 'left' ? anchorX
          : layer.align === 'right' ? anchorX - lw
          : -lw / 2;
        context.fillRect(ux, y + font.px * 0.42, lw, Math.max(1, font.px * 0.055));
      }
    });

    context.restore();

    if (track) {
      layer._hit = {
        cx: layer.x * W,
        cy: layer.y * H,
        w: Math.max(widest + pad * 2, font.px),
        h: Math.max(blockH, font.px),
      };
    }
  }

  function renderInto(context, index, W, H, track) {
    context.save();
    context.fillStyle = state.bg;
    context.fillRect(0, 0, W, H);
    context.restore();

    var frame = state.frames[index];
    if (frame && frame.img) drawPicture(context, frame.img, W, H);

    // Anything not drawn this time round must not stay tappable -- a caption
    // pinned to another frame would otherwise keep its old hit box.
    if (track) {
      state.layers.forEach(function (layer) { layer._hit = null; });
    }

    layersForFrame(index).forEach(function (layer) {
      drawLayer(context, layer, W, H, track);
    });
  }

  function render() {
    var size = outputSize();
    if (stage.width !== size.w || stage.height !== size.h) {
      stage.width = size.w;
      stage.height = size.h;
    }
    renderInto(ctx, state.frameIndex, size.w, size.h, true);

    var empty = state.frames.length === 0;
    stage.classList.toggle('is-empty', empty);
    $('#stage-empty').hidden = !empty;

    // A selected layer gets a faint box so it is obvious what will move.
    var layer = currentLayer();
    if (layer && layer._hit && !state.playing) {
      ctx.save();
      ctx.strokeStyle = 'rgba(124,92,255,0.9)';
      ctx.setLineDash([6, 5]);
      ctx.lineWidth = Math.max(1, size.h / 320);
      ctx.strokeRect(
        layer._hit.cx - layer._hit.w / 2 - 4,
        layer._hit.cy - layer._hit.h / 2 - 4,
        layer._hit.w + 8,
        layer._hit.h + 8
      );
      ctx.restore();
    }
  }

  /* ============================================================= playback */

  var rafId = null;
  var accumulated = 0;
  var lastTick = 0;

  function stopPlaying() {
    state.playing = false;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    $('#btn-play').textContent = '▶';
    render();
  }

  function startPlaying() {
    if (state.frames.length < 2) {
      toast('Add at least two pictures to see it move.');
      return;
    }
    state.playing = true;
    accumulated = 0;
    lastTick = performance.now();
    $('#btn-play').textContent = '❚❚';

    function tick(now) {
      if (!state.playing) return;
      accumulated += now - lastTick;
      lastTick = now;
      var wait = state.frames[state.frameIndex].delayMs;
      if (accumulated >= wait) {
        accumulated -= wait;
        if (accumulated > 1000) accumulated = 0; // tab was backgrounded
        state.frameIndex = (state.frameIndex + 1) % state.frames.length;
        render();
        markStrip();
        updateFrameCount();
      }
      rafId = requestAnimationFrame(tick);
    }
    rafId = requestAnimationFrame(tick);
  }

  /* ============================================================ filmstrip */

  function buildStrip() {
    var strip = $('#strip');
    strip.textContent = '';
    state.frames.forEach(function (frame, index) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'thumb' + (index === state.frameIndex ? ' is-active' : '');
      button.setAttribute('aria-label', 'Frame ' + (index + 1));

      var img = document.createElement('img');
      img.src = frame.url;
      img.alt = '';
      button.appendChild(img);

      var num = document.createElement('span');
      num.className = 'num';
      num.textContent = String(index + 1);
      button.appendChild(num);

      button.addEventListener('click', function () {
        stopPlaying();
        state.frameIndex = index;
        refreshAll();
      });
      strip.appendChild(button);
    });
  }

  function markStrip() {
    $$('#strip .thumb').forEach(function (el, index) {
      el.classList.toggle('is-active', index === state.frameIndex);
    });
  }

  function updateFrameCount() {
    var total = state.frames.length;
    var label = total === 0 ? 'No frames'
      : 'Frame ' + (state.frameIndex + 1) + ' of ' + total + ' · ' + totalSeconds() + 's';
    $('#frame-count').textContent = label;
  }

  function totalSeconds() {
    var ms = state.frames.reduce(function (sum, f) { return sum + f.delayMs; }, 0);
    return (ms / 1000).toFixed(1);
  }

  /* ================================================================ text */

  function makeLayer() {
    return {
      id: makeId('text'),
      text: 'Your text',
      scope: 'all',
      x: 0.5,
      y: 0.84,
      size: 9,             // % of canvas height
      family: 'impact',
      bold: true,
      italic: false,
      underline: false,
      uppercase: true,
      color: '#ffffff',
      outline: '#000000',
      outlineWidth: 12,    // % of font size
      highlight: '#ffd400',
      highlightOn: false,
      align: 'center',
      lineHeight: 115,     // % of font size
      rotation: 0,
      opacity: 100,
      shadow: false,
    };
  }

  function currentLayer() {
    for (var i = 0; i < state.layers.length; i++) {
      if (state.layers[i].id === state.layerId) return state.layers[i];
    }
    return null;
  }

  function addLayer() {
    var layer = makeLayer();
    // Stack new captions so they don't land exactly on top of each other.
    if (state.layers.length) layer.y = state.layers.length % 2 === 1 ? 0.16 : 0.5;
    state.layers.push(layer);
    state.layerId = layer.id;
    refreshAll();
    scheduleSave();
    // Deliberately not focusing the textarea: on a phone that scrolls the
    // preview off the top of the screen just as you want to look at it.
  }

  function syncTextPanel() {
    var layer = currentLayer();
    $('#text-empty').hidden = !!layer;
    $('#text-editor').hidden = !layer;
    if (!layer) return;

    $('#t-text').value = layer.text;
    $('#t-family').value = layer.family;
    $('#t-size').value = layer.size;
    $('#t-size-out').textContent = layer.size + '%';
    $('#t-color').value = layer.color;
    $('#t-outline').value = layer.outline;
    $('#t-highlight').value = layer.highlight;
    $('#t-highlight-on').checked = layer.highlightOn;
    $('#t-outline-w').value = layer.outlineWidth;
    $('#t-outline-w-out').textContent = layer.outlineWidth + '%';
    $('#t-lh').value = layer.lineHeight;
    $('#t-lh-out').textContent = layer.lineHeight + '%';
    $('#t-rot').value = layer.rotation;
    $('#t-rot-out').textContent = layer.rotation + '°';
    $('#t-opacity').value = layer.opacity;
    $('#t-opacity-out').textContent = layer.opacity + '%';
    $('#t-shadow').checked = layer.shadow;

    $$('.toolbar .fmt[data-fmt]').forEach(function (button) {
      button.classList.toggle('is-on', !!layer[button.dataset.fmt]);
    });
    $$('.toolbar .fmt[data-align]').forEach(function (button) {
      button.classList.toggle('is-on', layer.align === button.dataset.align);
    });

    buildScopeSelect(layer);
    buildLayerList();
  }

  function buildScopeSelect(layer) {
    var select = $('#t-scope');
    select.textContent = '';

    var all = document.createElement('option');
    all.value = 'all';
    all.textContent = 'Every frame';
    select.appendChild(all);

    state.frames.forEach(function (frame, index) {
      var option = document.createElement('option');
      option.value = frame.id;
      option.textContent = 'Frame ' + (index + 1) + ' only';
      select.appendChild(option);
    });

    // A layer pinned to a deleted frame falls back to showing everywhere.
    var known = state.frames.some(function (f) { return f.id === layer.scope; });
    if (layer.scope !== 'all' && !known) layer.scope = 'all';
    select.value = layer.scope;
  }

  function buildLayerList() {
    var list = $('#layer-list');
    list.textContent = '';
    if (state.layers.length < 2) return;

    state.layers.forEach(function (layer) {
      var item = document.createElement('li');
      item.className = layer.id === state.layerId ? 'is-active' : '';

      var text = document.createElement('span');
      text.className = 'layer-text';
      text.textContent = (layer.text || '(empty)').replace(/\n/g, ' ');
      item.appendChild(text);

      var scope = document.createElement('span');
      scope.className = 'layer-scope';
      if (layer.scope === 'all') {
        scope.textContent = 'all';
      } else {
        var at = state.frames.findIndex(function (f) { return f.id === layer.scope; });
        scope.textContent = at >= 0 ? 'frame ' + (at + 1) : 'all';
      }
      item.appendChild(scope);

      item.addEventListener('click', function () {
        state.layerId = layer.id;
        syncTextPanel();
        render();
      });
      list.appendChild(item);
    });
  }

  function updateLayer(changes) {
    var layer = currentLayer();
    if (!layer) return;
    Object.assign(layer, changes);
    render();
    scheduleSave();
  }

  /* ====================================================== canvas dragging */

  var drag = null;

  function pointerToCanvas(event) {
    var rect = stage.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (stage.width / rect.width),
      y: (event.clientY - rect.top) * (stage.height / rect.height),
    };
  }

  function layerAt(point) {
    var visible = layersForFrame(state.frameIndex);
    for (var i = visible.length - 1; i >= 0; i--) {
      var hit = visible[i]._hit;
      if (!hit) continue;
      // A little slack makes small captions grabbable with a thumb.
      var padX = Math.max(hit.w * 0.1, 10);
      var padY = Math.max(hit.h * 0.25, 12);
      if (Math.abs(point.x - hit.cx) <= hit.w / 2 + padX &&
          Math.abs(point.y - hit.cy) <= hit.h / 2 + padY) {
        return visible[i];
      }
    }
    return null;
  }

  stage.addEventListener('pointerdown', function (event) {
    if (state.playing) return;
    var point = pointerToCanvas(event);
    var layer = layerAt(point);
    if (!layer) return;

    stage.setPointerCapture(event.pointerId);
    state.layerId = layer.id;
    drag = {
      id: layer.id,
      dx: layer.x * stage.width - point.x,
      dy: layer.y * stage.height - point.y,
    };
    syncTextPanel();
    render();
    event.preventDefault();
  });

  stage.addEventListener('pointermove', function (event) {
    if (!drag) return;
    var point = pointerToCanvas(event);
    var layer = currentLayer();
    if (!layer || layer.id !== drag.id) return;
    layer.x = Math.min(1, Math.max(0, (point.x + drag.dx) / stage.width));
    layer.y = Math.min(1, Math.max(0, (point.y + drag.dy) / stage.height));
    render();
    event.preventDefault();
  });

  function endDrag() {
    if (!drag) return;
    drag = null;
    scheduleSave();
  }
  stage.addEventListener('pointerup', endDrag);
  stage.addEventListener('pointercancel', endDrag);

  /* ========================================================= panel wiring */

  function refreshAll() {
    buildStrip();
    markStrip();
    updateFrameCount();
    syncTextPanel();
    syncFramesPanel();
    render();
  }

  function syncFramesPanel() {
    var frame = state.frames[state.frameIndex];
    $('#frames-empty').hidden = !!frame;
    $('#frames-editor').hidden = !frame;
    if (!frame) return;
    $('#f-index').textContent = (state.frameIndex + 1) + ' of ' + state.frames.length;
    $('#f-delay').value = frame.delayMs;
    $('#f-delay-out').textContent = frame.delayMs + 'ms';
  }

  function syncCanvasPanel() {
    $('#c-aspect').value = state.aspect;
    $('#c-size').value = state.size;
    $('#c-size-out').textContent = describeSize();
    $('#c-fit').value = state.fit;
    $('#c-bg').value = state.bg;
    $('#c-colors').value = state.maxColors;
    $('#c-colors-out').textContent = String(state.maxColors);
    $('#c-dither').checked = state.dither;
    $('#s-delay').value = state.defaultDelay;
    $('#s-delay-out').textContent = state.defaultDelay + 'ms';
    $('#s-loop').checked = state.loop;
  }

  function describeSize() {
    var size = outputSize();
    return size.w + '×' + size.h;
  }

  function bindTabs() {
    $$('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        $$('.tab').forEach(function (t) { t.classList.remove('is-active'); });
        tab.classList.add('is-active');
        $$('.panel').forEach(function (panel) {
          panel.classList.toggle('is-active', panel.dataset.panel === tab.dataset.tab);
        });
      });
    });
  }

  function bindStage() {
    $('#btn-play').addEventListener('click', function () {
      if (state.playing) stopPlaying();
      else startPlaying();
    });
    $('#btn-prev').addEventListener('click', function () {
      if (!state.frames.length) return;
      stopPlaying();
      state.frameIndex = (state.frameIndex - 1 + state.frames.length) % state.frames.length;
      refreshAll();
    });
    $('#btn-next').addEventListener('click', function () {
      if (!state.frames.length) return;
      stopPlaying();
      state.frameIndex = (state.frameIndex + 1) % state.frames.length;
      refreshAll();
    });
  }

  function bindImport() {
    var input = $('#file-input');
    $('#btn-add-photos').addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      var files = Array.prototype.slice.call(input.files || []);
      input.value = '';
      if (!files.length) return;
      stopPlaying();
      toast('Adding ' + files.length + ' picture' + (files.length > 1 ? 's' : '') + '…');
      importBlobs(files);
    });
  }

  function bindTextPanel() {
    $('#btn-add-text').addEventListener('click', addLayer);
    $('#btn-text-new').addEventListener('click', addLayer);

    $('#t-text').addEventListener('input', function () {
      updateLayer({ text: this.value });
      buildLayerList();
    });

    $('#t-family').addEventListener('change', function () {
      updateLayer({ family: this.value });
    });

    bindRange('#t-size', '#t-size-out', '%', function (value) {
      updateLayer({ size: value });
    });
    bindRange('#t-outline-w', '#t-outline-w-out', '%', function (value) {
      updateLayer({ outlineWidth: value });
    });
    bindRange('#t-lh', '#t-lh-out', '%', function (value) {
      updateLayer({ lineHeight: value });
    });
    bindRange('#t-rot', '#t-rot-out', '°', function (value) {
      updateLayer({ rotation: value });
    });
    bindRange('#t-opacity', '#t-opacity-out', '%', function (value) {
      updateLayer({ opacity: value });
    });

    $('#t-color').addEventListener('input', function () { updateLayer({ color: this.value }); });
    $('#t-outline').addEventListener('input', function () { updateLayer({ outline: this.value }); });
    $('#t-highlight').addEventListener('input', function () {
      updateLayer({ highlight: this.value, highlightOn: true });
      $('#t-highlight-on').checked = true;
    });
    $('#t-highlight-on').addEventListener('change', function () {
      updateLayer({ highlightOn: this.checked });
    });
    $('#t-shadow').addEventListener('change', function () {
      updateLayer({ shadow: this.checked });
    });
    $('#t-scope').addEventListener('change', function () {
      updateLayer({ scope: this.value });
      buildLayerList();
    });

    $$('.toolbar .fmt[data-fmt]').forEach(function (button) {
      button.addEventListener('click', function () {
        var layer = currentLayer();
        if (!layer) return;
        var key = button.dataset.fmt;
        var changes = {};
        changes[key] = !layer[key];
        updateLayer(changes);
        syncTextPanel();
      });
    });

    $$('.toolbar .fmt[data-align]').forEach(function (button) {
      button.addEventListener('click', function () {
        updateLayer({ align: button.dataset.align });
        syncTextPanel();
      });
    });

    $('#btn-text-del').addEventListener('click', function () {
      var layer = currentLayer();
      if (!layer) return;
      state.layers = state.layers.filter(function (l) { return l.id !== layer.id; });
      state.layerId = state.layers.length ? state.layers[state.layers.length - 1].id : null;
      refreshAll();
      scheduleSave();
    });

    $('#btn-text-dup').addEventListener('click', function () {
      var layer = currentLayer();
      if (!layer) return;
      var copy = Object.assign(stripRuntime(layer), { id: makeId('text') });
      copy.y = Math.min(0.95, copy.y + 0.08);
      state.layers.push(copy);
      state.layerId = copy.id;
      refreshAll();
      scheduleSave();
    });
  }

  function bindRange(selector, outSelector, suffix, onChange) {
    var input = $(selector);
    input.addEventListener('input', function () {
      var value = parseFloat(input.value);
      $(outSelector).textContent = value + suffix;
      onChange(value);
    });
  }

  function bindFramesPanel() {
    $('#f-delay').addEventListener('input', function () {
      var frame = state.frames[state.frameIndex];
      if (!frame) return;
      frame.delayMs = parseInt(this.value, 10);
      $('#f-delay-out').textContent = frame.delayMs + 'ms';
      updateFrameCount();
      scheduleSave();
    });

    $('#btn-frame-left').addEventListener('click', function () { moveFrame(-1); });
    $('#btn-frame-right').addEventListener('click', function () { moveFrame(1); });

    $('#btn-frame-dup').addEventListener('click', function () {
      var frame = state.frames[state.frameIndex];
      if (!frame) return;
      if (state.frames.length >= MAX_FRAMES) {
        toast('That is the ' + MAX_FRAMES + '-frame limit.');
        return;
      }
      // Share the decoded image; only the identity and timing are new.
      var copy = {
        id: makeId('frame'), blob: frame.blob, url: frame.url,
        img: frame.img, delayMs: frame.delayMs, sharesUrlWith: frame.id,
      };
      state.frames.splice(state.frameIndex + 1, 0, copy);
      state.frameIndex += 1;
      refreshAll();
      scheduleSave();
    });

    $('#btn-frame-del').addEventListener('click', function () {
      if (!state.frames.length) return;
      stopPlaying();
      state.frames.splice(state.frameIndex, 1);
      if (state.frameIndex >= state.frames.length) {
        state.frameIndex = Math.max(0, state.frames.length - 1);
      }
      refreshAll();
      scheduleSave();
    });

    $('#btn-frames-reverse').addEventListener('click', function () {
      state.frames.reverse();
      state.frameIndex = 0;
      refreshAll();
      scheduleSave();
    });

    $('#btn-frames-bounce').addEventListener('click', function () {
      if (state.frames.length < 2) { toast('Needs at least two frames.'); return; }
      var middle = state.frames.slice(1, -1).reverse();
      if (!middle.length) {
        toast('With two frames it already bounces.');
        return;
      }
      if (state.frames.length + middle.length > MAX_FRAMES) {
        toast('That would go past the ' + MAX_FRAMES + '-frame limit.');
        return;
      }
      middle.forEach(function (frame) {
        state.frames.push(Object.assign({}, frame, { id: makeId('frame') }));
      });
      refreshAll();
      scheduleSave();
    });

    $('#btn-frames-clear').addEventListener('click', function () {
      if (!state.frames.length) return;
      if (!confirm('Remove every frame? Your text stays.')) return;
      stopPlaying();
      state.frames = [];
      state.frameIndex = 0;
      refreshAll();
      scheduleSave();
    });
  }

  function moveFrame(step) {
    var from = state.frameIndex;
    var to = from + step;
    if (to < 0 || to >= state.frames.length) return;
    var moved = state.frames.splice(from, 1)[0];
    state.frames.splice(to, 0, moved);
    state.frameIndex = to;
    refreshAll();
    scheduleSave();
  }

  function bindCanvasPanel() {
    $('#c-aspect').addEventListener('change', function () {
      state.aspect = this.value;
      $('#c-size-out').textContent = describeSize();
      render();
      scheduleSave();
    });
    $('#c-size').addEventListener('input', function () {
      state.size = parseInt(this.value, 10);
      $('#c-size-out').textContent = describeSize();
      render();
      scheduleSave();
    });
    $('#c-fit').addEventListener('change', function () {
      state.fit = this.value;
      render();
      scheduleSave();
    });
    $('#c-bg').addEventListener('input', function () {
      state.bg = this.value;
      render();
      scheduleSave();
    });
    $('#c-colors').addEventListener('input', function () {
      state.maxColors = parseInt(this.value, 10);
      $('#c-colors-out').textContent = String(state.maxColors);
      scheduleSave();
    });
    $('#c-dither').addEventListener('change', function () {
      state.dither = this.checked;
      scheduleSave();
    });

    $('#s-delay').addEventListener('input', function () {
      state.defaultDelay = parseInt(this.value, 10);
      $('#s-delay-out').textContent = state.defaultDelay + 'ms';
      scheduleSave();
    });
    $('#btn-apply-delay').addEventListener('click', function () {
      state.frames.forEach(function (frame) { frame.delayMs = state.defaultDelay; });
      syncFramesPanel();
      updateFrameCount();
      scheduleSave();
      toast('Every frame set to ' + state.defaultDelay + 'ms.');
    });
    $('#s-loop').addEventListener('change', function () {
      state.loop = this.checked;
      scheduleSave();
    });
  }

  /* =============================================================== sheets */

  function openSheet(id) { $(id).hidden = false; }
  function closeSheet(id) { $(id).hidden = true; }

  function bindSheets() {
    $$('.sheet').forEach(function (sheet) {
      sheet.addEventListener('click', function (event) {
        if (event.target === sheet) sheet.hidden = true;
      });
      sheet.querySelectorAll('.sheet-close').forEach(function (button) {
        button.addEventListener('click', function () { sheet.hidden = true; });
      });
    });
  }

  /* =============================================================== search */

  var searchProvider = 'commons';
  var searchAbort = null;

  function buildProviderChips() {
    var row = $('#provider-row');
    row.textContent = '';
    ImageSearch.PROVIDERS.forEach(function (provider) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip' + (provider.id === searchProvider ? ' is-on' : '');
      chip.textContent = provider.label;
      chip.title = provider.note;
      chip.addEventListener('click', function () {
        searchProvider = provider.id;
        buildProviderChips();
        var query = $('#search-q').value.trim();
        if (query) runSearch(query);
      });
      row.appendChild(chip);
    });
  }

  function runSearch(query) {
    var status = $('#search-status');
    var results = $('#search-results');
    results.textContent = '';
    status.textContent = 'Searching…';

    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();

    ImageSearch.search(searchProvider, query, searchAbort.signal)
      .then(function (items) {
        if (!items.length) {
          status.textContent = 'Nothing found. Try different words.';
          return;
        }
        status.textContent = 'Tap a picture to add it as a frame.';
        items.forEach(function (item) { results.appendChild(resultTile(item)); });
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        status.textContent = err.message || 'Search failed.';
      });
  }

  function resultTile(item) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'result';

    var img = document.createElement('img');
    img.src = item.thumb;
    img.alt = item.title;
    img.loading = 'lazy';
    button.appendChild(img);

    if (item.credit) {
      var credit = document.createElement('span');
      credit.className = 'credit';
      credit.textContent = item.credit;
      button.appendChild(credit);
    }

    button.addEventListener('click', function () {
      if (button.classList.contains('is-busy')) return;
      button.classList.add('is-busy');
      ImageSearch.loadBlob(item.full)
        .then(function (blob) { return importBlobs([blob]); })
        .then(function () { toast('Added “' + item.title.slice(0, 30) + '”'); })
        .catch(function (err) { toast(err.message || 'Could not add that one.'); })
        .then(function () { button.classList.remove('is-busy'); });
    });

    return button;
  }

  function bindSearch() {
    $('#btn-add-search').addEventListener('click', function () {
      openSheet('#sheet-search');
      $('#search-q').focus();
    });
    $('#search-form').addEventListener('submit', function (event) {
      event.preventDefault();
      var query = $('#search-q').value.trim();
      if (query) runSearch(query);
      $('#search-q').blur();
    });
    buildProviderChips();
  }

  /* =============================================================== export */

  var worker = null;
  var lastGifUrl = null;

  function getWorker() {
    if (worker !== null) return worker;
    try {
      worker = new Worker('gif-worker.js');
    } catch (err) {
      worker = false; // file:// or a blocked worker; encode inline instead
    }
    return worker;
  }

  function collectFrames() {
    var size = outputSize();
    var work = document.createElement('canvas');
    work.width = size.w;
    work.height = size.h;
    var wctx = work.getContext('2d', { willReadFrequently: true });

    return state.frames.map(function (frame, index) {
      renderInto(wctx, index, size.w, size.h);
      var data = wctx.getImageData(0, 0, size.w, size.h).data;
      return { data: data, delayMs: frame.delayMs };
    });
  }

  function encodeInline(frames, size, options, onProgress) {
    var quantised = [];
    function step(i) {
      if (i >= frames.length) {
        return Promise.resolve(GifEncoder.writeGif(quantised, size.w, size.h, options));
      }
      return new Promise(function (resolve) { setTimeout(resolve, 0); }).then(function () {
        var frame = GifEncoder.quantizeFrame(frames[i].data, size.w, size.h, options);
        frame.delayMs = frames[i].delayMs;
        quantised.push(frame);
        onProgress(i + 1, frames.length);
        return step(i + 1);
      });
    }
    return step(0);
  }

  function makeGif() {
    if (!state.frames.length) {
      toast('Add at least one picture first.');
      return;
    }
    stopPlaying();

    openSheet('#sheet-export');
    $('#export-progress').hidden = false;
    $('#export-result').hidden = true;
    $('#export-error').hidden = true;
    setProgress(0, state.frames.length);

    var size = outputSize();
    var options = {
      maxColors: state.maxColors,
      dither: state.dither,
      loop: state.loop,
    };
    var frames = collectFrames();

    var handle = getWorker();
    if (handle) {
      handle.onmessage = function (event) {
        var msg = event.data;
        if (msg.type === 'progress') setProgress(msg.done, msg.total);
        else if (msg.type === 'done') showGif(new Uint8Array(msg.bytes), size);
        else if (msg.type === 'error') showError(msg.message);
      };
      handle.onerror = function () {
        showError('The encoder stopped unexpectedly.');
      };
      // Hand the pixel buffers over rather than copying them.
      var transfers = frames.map(function (f) { return f.data.buffer; });
      handle.postMessage({
        frames: frames, width: size.w, height: size.h, options: options,
      }, transfers);
    } else {
      encodeInline(frames, size, options, setProgress)
        .then(function (bytes) { showGif(bytes, size); })
        .catch(function (err) { showError(err.message || 'Encoding failed.'); });
    }
  }

  function setProgress(done, total) {
    var pct = total ? Math.round((done / total) * 100) : 0;
    $('#progress-fill').style.width = pct + '%';
    $('#progress-text').textContent = done >= total && total
      ? 'Putting it together…'
      : 'Encoding frame ' + done + ' of ' + total + '…';
  }

  function showError(message) {
    $('#export-progress').hidden = true;
    $('#export-result').hidden = true;
    var el = $('#export-error');
    el.hidden = false;
    el.textContent = message;
  }

  function showGif(bytes, size) {
    var blob = new Blob([bytes], { type: 'image/gif' });
    if (lastGifUrl) URL.revokeObjectURL(lastGifUrl);
    lastGifUrl = URL.createObjectURL(blob);

    $('#export-progress').hidden = true;
    $('#export-error').hidden = true;
    $('#export-result').hidden = false;
    $('#export-img').src = lastGifUrl;
    $('#export-meta').textContent =
      size.w + '×' + size.h + ' · ' + state.frames.length + ' frames · ' +
      formatBytes(blob.size);

    var link = $('#export-download');
    link.href = lastGifUrl;
    link.download = 'gif-' + Date.now() + '.gif';

    var file = null;
    try {
      file = new File([blob], link.download, { type: 'image/gif' });
    } catch (err) {
      file = null;
    }

    var shareButton = $('#export-share');
    var canShare = !!(file && navigator.canShare && navigator.canShare({ files: [file] }));
    shareButton.hidden = !canShare;
    if (canShare) {
      shareButton.onclick = function () {
        navigator.share({ files: [file] }).catch(function () { /* user cancelled */ });
      };
    }

    $('#export-hint').textContent = canShare
      ? 'Share sends it straight to Messages, Photos or anywhere else.'
      : 'Press and hold the picture above to save it to your camera roll.';
  }

  function formatBytes(size) {
    if (size < 1024) return size + ' B';
    if (size < 1024 * 1024) return (size / 1024).toFixed(0) + ' KB';
    return (size / (1024 * 1024)).toFixed(1) + ' MB';
  }

  /* ============================================================= settings */

  function bindSettings() {
    $('#btn-settings').addEventListener('click', function () {
      $('#key-pexels').value = ImageSearch.getKey('pexels');
      $('#storage-note').textContent = state.frames.length +
        ' frame(s) and ' + state.layers.length + ' text layer(s) saved on this device.';
      openSheet('#sheet-settings');
    });

    $('#btn-save-keys').addEventListener('click', function () {
      ImageSearch.setKey('pexels', $('#key-pexels').value.trim());
      toast('Saved.');
      closeSheet('#sheet-settings');
    });

    $('#btn-reset').addEventListener('click', function () {
      if (!confirm('Throw away this project and start fresh?')) return;
      stopPlaying();
      state.frames.forEach(function (frame) {
        if (!frame.sharesUrlWith) URL.revokeObjectURL(frame.url);
      });
      state.frames = [];
      state.layers = [];
      state.layerId = null;
      state.frameIndex = 0;
      save();
      refreshAll();
      closeSheet('#sheet-settings');
    });
  }

  /* ================================================================= init */

  function detectProxy() {
    var base = new URL('.', location.href).href;
    return fetch(base + 'api/health', { method: 'GET' })
      .then(function (response) {
        if (response.ok) ImageSearch.setProxyBase(base);
      })
      .catch(function () { /* plain static hosting; direct calls only */ });
  }

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    if (location.protocol === 'file:') return;
    navigator.serviceWorker.register('sw.js').catch(function () { /* fine */ });
  }

  function init() {
    bindTabs();
    bindStage();
    bindImport();
    bindTextPanel();
    bindFramesPanel();
    bindCanvasPanel();
    bindSheets();
    bindSearch();
    bindSettings();

    $('#btn-make').addEventListener('click', makeGif);

    // Going to the background: stop the preview (a runaway rAF loop is the
    // fastest way to flatten a phone battery) and commit anything unsaved.
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) return;
      if (state.playing) stopPlaying();
      flushSave();
    });
    window.addEventListener('pagehide', flushSave);

    restore().then(function () {
      syncCanvasPanel();
      refreshAll();
    });

    detectProxy();
    registerServiceWorker();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
