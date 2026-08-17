/*
 * search.js -- image search for the GIF maker.
 *
 * Two providers work with no sign-up at all (Wikimedia Commons and Openverse),
 * which is the point: the app should be useful the moment you open it. Pexels
 * is there for people who want stock photography and don't mind pasting a free
 * API key.
 *
 * Everything is fetched as a Blob rather than pointed at with <img src>. That
 * keeps the canvas untainted (so it can still be exported), and it means a
 * picture can be stored in IndexedDB and survive a reload.
 */
(function (global) {
  'use strict';

  var KEY_PREFIX = 'gifmaker.apikey.';

  // Set by the app when gifmaker/serve.py is the thing serving this page.
  // Without it the browser talks to the APIs directly, which works as long as
  // they send permissive CORS headers.
  var proxyBase = null;

  function setProxyBase(base) {
    proxyBase = base || null;
  }

  function hasProxy() {
    return !!proxyBase;
  }

  function getKey(provider) {
    try {
      return localStorage.getItem(KEY_PREFIX + provider) || '';
    } catch (err) {
      return '';
    }
  }

  function setKey(provider, value) {
    try {
      if (value) localStorage.setItem(KEY_PREFIX + provider, value);
      else localStorage.removeItem(KEY_PREFIX + provider);
    } catch (err) {
      /* private browsing; keys just won't persist */
    }
  }

  function asJson(response) {
    if (!response.ok) {
      throw new Error('search failed (' + response.status + ')');
    }
    return response.json();
  }

  /* ------------------------------------------------------- Wikimedia Commons */

  // Commons thumbnails are named ".../<width>px-<file>", so a bigger version
  // can be asked for by rewriting the width in place.
  function commonsAtWidth(url, width) {
    if (!url) return url;
    return url.replace(/\/(\d+)px-([^/]*)$/, '/' + width + 'px-$2');
  }

  function searchCommons(query, signal) {
    var params = new URLSearchParams({
      action: 'query',
      format: 'json',
      origin: '*', // anonymous CORS
      generator: 'search',
      gsrsearch: 'filetype:bitmap ' + query,
      gsrnamespace: '6', // File:
      gsrlimit: '40',
      prop: 'imageinfo',
      iiprop: 'url|mime|extmetadata',
      iiurlwidth: '320',
    });
    var url = 'https://commons.wikimedia.org/w/api.php?' + params.toString();

    return fetch(url, { signal: signal }).then(asJson).then(function (data) {
      var pages = (data.query && data.query.pages) || {};
      var out = [];
      Object.keys(pages).forEach(function (id) {
        var page = pages[id];
        var info = page.imageinfo && page.imageinfo[0];
        if (!info || !info.thumburl) return;
        if (!/^image\/(jpeg|png|webp|gif)$/.test(info.mime || '')) return;

        var meta = info.extmetadata || {};
        var artist = meta.Artist && stripTags(meta.Artist.value);
        var licence = meta.LicenseShortName && meta.LicenseShortName.value;

        out.push({
          id: 'commons-' + id,
          title: (page.title || '').replace(/^File:/, ''),
          thumb: info.thumburl,
          full: commonsAtWidth(info.thumburl, 1024),
          credit: [artist, licence].filter(Boolean).join(' · '),
          link: info.descriptionurl || '',
        });
      });
      return out;
    });
  }

  function stripTags(html) {
    var div = document.createElement('div');
    div.innerHTML = html;
    return (div.textContent || '').trim().slice(0, 80);
  }

  /* --------------------------------------------------------------- Openverse */

  function searchOpenverse(query, signal) {
    var params = new URLSearchParams({
      q: query,
      page_size: '40',
      mature: 'false',
    });
    var url = 'https://api.openverse.org/v1/images/?' + params.toString();

    return fetch(url, { signal: signal }).then(asJson).then(function (data) {
      return (data.results || []).map(function (item) {
        // The thumbnail is served through Openverse's own proxy, which is both
        // CORS-friendly and already a sensible size for a GIF frame.
        var thumb = item.thumbnail || item.url;
        return {
          id: 'openverse-' + item.id,
          title: item.title || 'Untitled',
          thumb: thumb,
          full: thumb,
          credit: [item.creator, item.license && item.license.toUpperCase()]
            .filter(Boolean).join(' · '),
          link: item.foreign_landing_url || '',
        };
      });
    });
  }

  /* ------------------------------------------------------------------ Pexels */

  function searchPexels(query, signal) {
    var key = getKey('pexels');
    if (!key) {
      return Promise.reject(new Error(
        'Pexels needs a free API key. Add one under Settings.'
      ));
    }
    var url = 'https://api.pexels.com/v1/search?per_page=40&query=' +
      encodeURIComponent(query);

    return fetch(url, { headers: { Authorization: key }, signal: signal })
      .then(asJson)
      .then(function (data) {
        return (data.photos || []).map(function (photo) {
          return {
            id: 'pexels-' + photo.id,
            title: photo.alt || 'Photo',
            thumb: photo.src.tiny,
            full: photo.src.large,
            credit: photo.photographer ? photo.photographer + ' · Pexels' : 'Pexels',
            link: photo.url || '',
          };
        });
      });
  }

  /* ---------------------------------------------------------------- dispatch */

  var PROVIDERS = [
    {
      id: 'commons',
      label: 'Wikimedia',
      note: 'Free, no key needed',
      needsKey: false,
      run: searchCommons,
    },
    {
      id: 'openverse',
      label: 'Openverse',
      note: 'Openly licensed, no key needed',
      needsKey: false,
      run: searchOpenverse,
    },
    {
      id: 'pexels',
      label: 'Pexels',
      note: 'Stock photos, needs a free API key',
      needsKey: true,
      run: searchPexels,
    },
  ];

  function providerById(id) {
    for (var i = 0; i < PROVIDERS.length; i++) {
      if (PROVIDERS[i].id === id) return PROVIDERS[i];
    }
    return PROVIDERS[0];
  }

  /**
   * Run a search, falling back to the local helper server when the browser
   * refuses the direct call (usually a CORS policy, occasionally an ad
   * blocker). The proxy only exists when the page came from serve.py.
   */
  function search(providerId, query, signal) {
    var provider = providerById(providerId);
    var trimmed = (query || '').trim();
    if (!trimmed) return Promise.resolve([]);

    return provider.run(trimmed, signal).catch(function (err) {
      if (err && err.name === 'AbortError') throw err;
      if (!proxyBase || provider.needsKey) throw err;

      var url = proxyBase + 'api/search?provider=' + encodeURIComponent(provider.id) +
        '&q=' + encodeURIComponent(trimmed);
      return fetch(url, { signal: signal }).then(asJson).then(function (data) {
        return data.results || [];
      });
    });
  }

  /**
   * Download a picture as a Blob. Direct first, proxy second -- a remote image
   * loaded straight into a canvas would otherwise taint it and block export.
   */
  function loadBlob(url) {
    return fetch(url, { mode: 'cors' })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.blob();
      })
      .catch(function (err) {
        if (!proxyBase) {
          throw new Error(
            "Couldn't download that image (the site blocked it). " +
            'Try another result, or run the app from serve.py.'
          );
        }
        return fetch(proxyBase + 'api/fetch?url=' + encodeURIComponent(url))
          .then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.blob();
          });
      });
  }

  global.ImageSearch = {
    PROVIDERS: PROVIDERS,
    search: search,
    loadBlob: loadBlob,
    getKey: getKey,
    setKey: setKey,
    setProxyBase: setProxyBase,
    hasProxy: hasProxy,
  };
})(window);
