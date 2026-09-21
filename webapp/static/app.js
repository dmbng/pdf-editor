/* PDF Editor web front end.
 *
 * All editing happens on the server; this file is the interface. Pages arrive as
 * PNG images and the word boxes of the page are drawn over them as transparent
 * elements, which is what makes selecting text with a mouse or a finger work.
 */

(function () {
  "use strict";

  var el = function (id) { return document.getElementById(id); };

  var state = {
    page: 0,
    pages: 0,
    zoom: 1.4,
    revision: 0,
    words: [],
    picked: [],
    found: [],
    images: [],
    selectedImage: null,
    pickedPages: [],
    config: {},
    pageSizes: []
  };

  var MIN_ZOOM = 0.35;
  var MAX_ZOOM = 3.0;

  /* ------------------------------------------------------------------ */
  /* Plumbing                                                            */
  /* ------------------------------------------------------------------ */

  function say(message, isError) {
    var bar = el("status");
    bar.textContent = message;
    bar.classList.toggle("is-error", !!isError);
  }

  function busy(on, label) {
    el("busy-text").textContent = label || "Working";
    el("busy").hidden = !on;
  }

  function request(url, options) {
    options = options || {};
    return fetch(url, options).then(function (response) {
      if (response.status === 401) {
        showGate("Your session ended. Sign in again.");
        throw new Error("signed out");
      }
      var isJson = (response.headers.get("content-type") || "").indexOf("json") >= 0;
      return (isJson ? response.json() : response.text()).then(function (body) {
        if (!response.ok) {
          throw new Error((body && body.detail) || "That did not work.");
        }
        return body;
      });
    });
  }

  function postJson(url, payload) {
    return request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
  }

  function postForm(url, form) {
    return request(url, { method: "POST", body: form });
  }

  function run(label, promise) {
    busy(true, label);
    return promise.then(function (value) {
      busy(false);
      return value;
    }).catch(function (error) {
      busy(false);
      if (error && error.message !== "signed out") {
        say(error.message, true);
      }
      throw error;
    });
  }

  /* ------------------------------------------------------------------ */
  /* Sign in                                                             */
  /* ------------------------------------------------------------------ */

  function showGate(message) {
    el("app").hidden = true;
    el("gate").hidden = false;
    el("gate-error").textContent = message || "";
  }

  function showApp() {
    el("gate").hidden = true;
    el("app").hidden = false;
  }

  function boot() {
    request("/api/config").then(function (config) {
      state.config = config;
      document.title = config.title;
      el("gate-title").textContent = config.title;
      el("empty-hint").textContent =
        "Up to " + config.maxUploadMb + " MB. Files are deleted after " +
        config.idleMinutes + " minutes of inactivity.";

      fillChoices(config);

      if (!config.configured) {
        showGate("This service has no password set yet. The operator must add the APP_PASSWORD secret.");
        el("gate-password").disabled = true;
        return;
      }
      if (config.authenticated) {
        showApp();
        refresh();
      } else {
        showGate("");
      }
    }).catch(function () {
      showGate("The server is not responding.");
    });
  }

  function fillChoices(config) {
    var levels = el("compress-level");
    levels.innerHTML = "";
    (config.compressionLevels || []).forEach(function (level) {
      var option = document.createElement("option");
      option.value = level.key;
      option.textContent = level.label;
      option.dataset.note = level.description;
      levels.appendChild(option);
    });
    levels.value = "medium";
    describeLevel();

    var methods = el("word-method");
    methods.innerHTML = "";
    var labels = { layout: "Keep layout (best)", text: "Text and images only" };
    (config.wordMethods || []).forEach(function (method) {
      var option = document.createElement("option");
      option.value = method;
      option.textContent = labels[method] || method;
      methods.appendChild(option);
    });
    if (!(config.wordMethods || []).length) {
      methods.innerHTML = "<option>Not available on this server</option>";
      el("btn-word").disabled = true;
    }
  }

  function describeLevel() {
    var choice = el("compress-level").selectedOptions[0];
    el("compress-note").textContent = choice ? choice.dataset.note || "" : "";
  }

  /* ------------------------------------------------------------------ */
  /* Document state                                                      */
  /* ------------------------------------------------------------------ */

  function refresh() {
    return request("/api/state").then(applyState);
  }

  function applyState(documentState) {
    state.revision += 1;
    var isOpen = !!documentState.open;

    el("doc-name").textContent = isOpen
      ? documentState.name + (documentState.dirty ? " *" : "")
      : "No document";
    el("btn-download").disabled = !isOpen;
    el("btn-undo").disabled = !isOpen || !documentState.canUndo;
    el("btn-redo").disabled = !isOpen || !documentState.canRedo;
    el("empty").hidden = isOpen;
    el("canvas-wrap").hidden = !isOpen;

    if (!isOpen) {
      state.pages = 0;
      el("thumbs").innerHTML = "";
      el("page-label").textContent = "0 / 0";
      return;
    }

    state.pages = documentState.pages;
    state.pageSizes = documentState.pageSizes || [];
    if (typeof documentState.focus === "number") {
      state.page = documentState.focus;
    }
    state.page = Math.max(0, Math.min(state.page, state.pages - 1));
    state.pickedPages = state.pickedPages.filter(function (index) {
      return index < state.pages;
    });

    if (documentState.result && documentState.result.summary) {
      say(documentState.result.summary);
      (documentState.result.warnings || []).forEach(function (warning) {
        say(warning, true);
      });
    }

    buildThumbnails();
    showPage(state.page);
  }

  function buildThumbnails() {
    var strip = el("thumbs");
    strip.innerHTML = "";
    for (var index = 0; index < state.pages; index += 1) {
      strip.appendChild(buildThumbnail(index));
    }
    markThumbnails();
  }

  function buildThumbnail(index) {
    var card = document.createElement("div");
    card.className = "thumb";
    card.dataset.index = String(index);

    var picture = document.createElement("img");
    picture.loading = "lazy";
    picture.alt = "Page " + (index + 1);
    picture.src = "/api/thumbnail/" + index + "?r=" + state.revision;

    var number = document.createElement("span");
    number.className = "num";
    number.textContent = String(index + 1);

    card.appendChild(picture);
    card.appendChild(number);

    var timer = null;
    card.addEventListener("pointerdown", function () {
      timer = setTimeout(function () {
        timer = null;
        togglePickedPage(index);
      }, 450);
    });
    var cancel = function () {
      if (timer) { clearTimeout(timer); timer = null; }
    };
    card.addEventListener("pointerup", cancel);
    card.addEventListener("pointerleave", cancel);

    card.addEventListener("click", function (event) {
      if (event.ctrlKey || event.metaKey) {
        togglePickedPage(index);
        return;
      }
      if (event.shiftKey && state.pickedPages.length) {
        var from = Math.min(state.pickedPages[0], index);
        var to = Math.max(state.pickedPages[0], index);
        state.pickedPages = [];
        for (var step = from; step <= to; step += 1) { state.pickedPages.push(step); }
        markThumbnails();
        return;
      }
      state.pickedPages = [];
      goToPage(index);
    });
    return card;
  }

  function togglePickedPage(index) {
    var at = state.pickedPages.indexOf(index);
    if (at >= 0) { state.pickedPages.splice(at, 1); } else { state.pickedPages.push(index); }
    markThumbnails();
  }

  function markThumbnails() {
    Array.prototype.forEach.call(el("thumbs").children, function (card) {
      var index = Number(card.dataset.index);
      card.classList.toggle("is-current", index === state.page);
      card.classList.toggle("is-picked", state.pickedPages.indexOf(index) >= 0);
    });
    var chosen = targetPages();
    el("pages-selection").textContent = state.pickedPages.length
      ? "Pages " + chosen.map(function (index) { return index + 1; }).join(", ")
      : "Current page only (page " + (state.page + 1) + ")";
  }

  function targetPages() {
    if (state.pickedPages.length) {
      return state.pickedPages.slice().sort(function (a, b) { return a - b; });
    }
    return [state.page];
  }

  /* ------------------------------------------------------------------ */
  /* The page view                                                       */
  /* ------------------------------------------------------------------ */

  function goToPage(index) {
    if (index < 0 || index >= state.pages) { return; }
    state.page = index;
    clearSelection();
    showPage(index);
  }

  function showPage(index) {
    var picture = el("page-image");
    picture.onload = function () {
      el("canvas").style.width = picture.width + "px";
      el("canvas").style.height = picture.height + "px";
      loadOverlay(index);
    };
    picture.src = "/api/page/" + index + "?zoom=" + state.zoom + "&r=" + state.revision;
    el("page-label").textContent = (index + 1) + " / " + state.pages;
    el("zoom-label").textContent = Math.round(state.zoom / 1.4 * 100) + "%";
    markThumbnails();
  }

  function loadOverlay(index) {
    Promise.all([
      request("/api/words/" + index),
      request("/api/images/" + index)
    ]).then(function (results) {
      if (index !== state.page) { return; }
      state.words = results[0].words || [];
      state.images = results[1].images || [];
      drawOverlay();
    }).catch(function () { /* already reported */ });
  }

  function drawOverlay() {
    var overlay = el("overlay");
    overlay.innerHTML = "";

    state.words.forEach(function (word) {
      var box = document.createElement("div");
      box.className = "word";
      box.style.left = (word.x0 * state.zoom) + "px";
      box.style.top = (word.y0 * state.zoom) + "px";
      box.style.width = ((word.x1 - word.x0) * state.zoom) + "px";
      box.style.height = ((word.y1 - word.y0) * state.zoom) + "px";
      if (state.picked.indexOf(word.i) >= 0) { box.classList.add("is-picked"); }
      if (state.found.indexOf(word.i) >= 0) { box.classList.add("is-found"); }
      overlay.appendChild(box);
    });

    state.images.forEach(function (picture) {
      if (state.selectedImage !== picture.xref) { return; }
      overlay.appendChild(buildImageBox(picture));
    });

    updateSelectionUi();
  }

  function buildImageBox(picture) {
    var box = document.createElement("div");
    box.className = "img-box";
    box.style.left = (picture.x0 * state.zoom) + "px";
    box.style.top = (picture.y0 * state.zoom) + "px";
    box.style.width = ((picture.x1 - picture.x0) * state.zoom) + "px";
    box.style.height = ((picture.y1 - picture.y0) * state.zoom) + "px";

    ["nw", "ne", "sw", "se"].forEach(function (corner) {
      var grip = document.createElement("div");
      grip.className = "grip " + corner;
      grip.dataset.corner = corner;
      box.appendChild(grip);
    });

    box.addEventListener("pointerdown", function (event) {
      event.stopPropagation();
      startImageDrag(event, box, picture, event.target.dataset.corner || null);
    });
    return box;
  }

  function startImageDrag(event, box, picture, corner) {
    event.preventDefault();
    var origin = { x: event.clientX, y: event.clientY };
    var start = {
      left: parseFloat(box.style.left),
      top: parseFloat(box.style.top),
      width: parseFloat(box.style.width),
      height: parseFloat(box.style.height)
    };

    function onMove(moveEvent) {
      var dx = moveEvent.clientX - origin.x;
      var dy = moveEvent.clientY - origin.y;
      if (!corner) {
        box.style.left = (start.left + dx) + "px";
        box.style.top = (start.top + dy) + "px";
        return;
      }
      var left = start.left, top = start.top, width = start.width, height = start.height;
      if (corner.indexOf("w") >= 0) { left = start.left + dx; width = start.width - dx; }
      if (corner.indexOf("e") >= 0) { width = start.width + dx; }
      if (corner.indexOf("n") >= 0) { top = start.top + dy; height = start.height - dy; }
      if (corner.indexOf("s") >= 0) { height = start.height + dy; }
      if (width < 16 || height < 16) { return; }
      box.style.left = left + "px";
      box.style.top = top + "px";
      box.style.width = width + "px";
      box.style.height = height + "px";
    }

    function onUp() {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      var rect = {
        x: parseFloat(box.style.left) / state.zoom,
        y: parseFloat(box.style.top) / state.zoom,
        width: parseFloat(box.style.width) / state.zoom,
        height: parseFloat(box.style.height) / state.zoom
      };
      if (Math.abs(rect.x - picture.x0) < 0.5 && Math.abs(rect.y - picture.y0) < 0.5 &&
          Math.abs(rect.width - (picture.x1 - picture.x0)) < 0.5) {
        return;
      }
      run("Moving the image", postJson("/api/image/move", {
        page: state.page, xref: picture.xref, rect: rect
      })).then(applyState);
    }

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  /* ------------------------------------------------------------------ */
  /* Selecting text                                                      */
  /* ------------------------------------------------------------------ */

  function setupSelection() {
    var overlay = el("overlay");
    var band = null;
    var origin = null;

    overlay.addEventListener("pointerdown", function (event) {
      if (event.target.closest && event.target.closest(".img-box")) { return; }

      var hit = imageUnder(event);
      if (hit) {
        state.selectedImage = hit.xref;
        el("image-actions").hidden = false;
        showPane("tools");
        drawOverlay();
        return;
      }

      state.selectedImage = null;
      el("image-actions").hidden = true;
      try { overlay.setPointerCapture(event.pointerId); } catch (ignored) { /* older browsers */ }
      origin = localPoint(event);
      state.picked = [];
      band = document.createElement("div");
      band.className = "band";
      overlay.appendChild(band);
    });

    overlay.addEventListener("pointermove", function (event) {
      if (!origin || !band) { return; }
      var point = localPoint(event);
      var left = Math.min(origin.x, point.x);
      var top = Math.min(origin.y, point.y);
      var width = Math.abs(point.x - origin.x);
      var height = Math.abs(point.y - origin.y);
      band.style.left = left + "px";
      band.style.top = top + "px";
      band.style.width = width + "px";
      band.style.height = height + "px";
      pickWordsIn(left / state.zoom, top / state.zoom,
                  (left + width) / state.zoom, (top + height) / state.zoom);
    });

    var finish = function (event) {
      if (!origin) { return; }
      var point = localPoint(event);
      var moved = Math.abs(point.x - origin.x) + Math.abs(point.y - origin.y);
      if (moved < 6) {
        var word = wordAt(point.x / state.zoom, point.y / state.zoom);
        state.picked = word ? [word.i] : [];
      }
      origin = null;
      if (band) { band.remove(); band = null; }
      state.found = [];
      drawOverlay();
    };
    overlay.addEventListener("pointerup", finish);
    overlay.addEventListener("pointercancel", finish);
  }

  function localPoint(event) {
    var box = el("overlay").getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }

  function imageUnder(event) {
    var point = localPoint(event);
    var x = point.x / state.zoom;
    var y = point.y / state.zoom;
    var found = null;
    state.images.forEach(function (picture) {
      if (x >= picture.x0 && x <= picture.x1 && y >= picture.y0 && y <= picture.y1) {
        found = picture;
      }
    });
    return found;
  }

  function wordAt(x, y) {
    for (var index = 0; index < state.words.length; index += 1) {
      var word = state.words[index];
      if (x >= word.x0 && x <= word.x1 && y >= word.y0 && y <= word.y1) { return word; }
    }
    return null;
  }

  function pickWordsIn(x0, y0, x1, y1) {
    state.picked = state.words.filter(function (word) {
      return word.x1 >= x0 && word.x0 <= x1 && word.y1 >= y0 && word.y0 <= y1;
    }).map(function (word) { return word.i; });

    Array.prototype.forEach.call(el("overlay").querySelectorAll(".word"), function (box, position) {
      var word = state.words[position];
      if (word) { box.classList.toggle("is-picked", state.picked.indexOf(word.i) >= 0); }
    });
    updateSelectionUi();
  }

  function selectedText() {
    var byIndex = {};
    state.words.forEach(function (word) { byIndex[word.i] = word.text; });
    return state.picked.map(function (index) { return byIndex[index]; }).join(" ");
  }

  function updateSelectionUi() {
    var has = state.picked.length > 0;
    el("selected-text").value = has ? selectedText() : "";
    ["btn-replace", "btn-delete-text", "btn-highlight", "btn-redact"].forEach(function (id) {
      el(id).disabled = !has;
    });
    el("selection-bar").hidden = !has;
  }

  function clearSelection() {
    state.picked = [];
    state.found = [];
    state.selectedImage = null;
    el("image-actions").hidden = true;
    updateSelectionUi();
  }

  /* ------------------------------------------------------------------ */
  /* Actions                                                             */
  /* ------------------------------------------------------------------ */

  function editText(endpoint, extra, label) {
    var payload = {
      page: state.page,
      words: state.picked,
      reflow: el("reflow").checked
    };
    Object.keys(extra || {}).forEach(function (key) { payload[key] = extra[key]; });
    return run(label, postJson(endpoint, payload)).then(function (documentState) {
      clearSelection();
      el("replacement").value = "";
      applyState(documentState);
    });
  }

  function pageAction(endpoint, extra, label) {
    var payload = { pages: targetPages() };
    Object.keys(extra || {}).forEach(function (key) { payload[key] = extra[key]; });
    return run(label, postJson(endpoint, payload)).then(function (documentState) {
      state.pickedPages = [];
      applyState(documentState);
    });
  }

  function addResult(title, body, token, filename) {
    var card = document.createElement("div");
    card.className = "result";
    var line = document.createElement("div");
    var strong = document.createElement("strong");
    strong.textContent = title;
    line.appendChild(strong);
    line.appendChild(document.createElement("br"));
    line.appendChild(document.createTextNode(body));
    card.appendChild(line);
    if (token) {
      var link = document.createElement("a");
      link.href = "/api/artifact/" + token;
      link.textContent = "Download " + filename;
      link.setAttribute("download", "");
      card.appendChild(link);
    }
    el("results").prepend(card);
  }

  function showPane(which) {
    ["text", "pages", "tools"].forEach(function (name) {
      el("pane-" + name).hidden = name !== which;
      el("tab-" + name).classList.toggle("is-active", name === which);
    });
  }

  function openFile(input) {
    input.value = "";
    input.click();
  }

  /* The change handlers are bound once, at start-up, so a file that arrives by
     any route (the picker, a drop, automation) is always picked up. */
  function bindFileInput(input, handler) {
    input.addEventListener("change", function () {
      if (input.files && input.files.length) { handler(input.files); }
    });
  }

  function uploadDocument(files) {
    var form = new FormData();
    form.append("file", files[0]);
    run("Opening " + files[0].name, postForm("/api/upload", form)).then(function (documentState) {
      state.page = 0;
      state.pickedPages = [];
      clearSelection();
      applyState(documentState);
      say("Opened " + documentState.name + " (" + documentState.pages + " pages)");
    });
  }

  function mergeDocuments(files) {
    var form = new FormData();
    Array.prototype.forEach.call(files, function (file) { form.append("files", file); });
    form.append("position", "-1");
    run("Merging", postForm("/api/pages/merge", form)).then(applyState);
  }

  function placeImage(files) {
    var form = new FormData();
    form.append("file", files[0]);
    form.append("page", String(state.page));
    form.append("x", "0");
    form.append("y", "0");
    form.append("width", "0");
    form.append("height", "0");
    run("Placing the image", postForm("/api/image/insert", form)).then(function (documentState) {
      applyState(documentState);
      say("Image placed. Tap it to move or resize it.");
    });
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */

  function wire() {
    el("gate-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var password = el("gate-password").value;
      run("Signing in", postJson("/api/login", { password: password })).then(function () {
        el("gate-password").value = "";
        showApp();
        say("Signed in.");
        refresh();
      }).catch(function (error) {
        el("gate-error").textContent = error.message;
      });
    });

    el("btn-signout").addEventListener("click", function () {
      postJson("/api/logout").then(function () {
        showGate("Signed out. Your files were deleted.");
      });
    });

    bindFileInput(el("file-pdf"), uploadDocument);
    bindFileInput(el("file-merge"), mergeDocuments);
    bindFileInput(el("file-image"), placeImage);

    el("btn-open").addEventListener("click", function () { openFile(el("file-pdf")); });
    el("empty-open").addEventListener("click", function () { openFile(el("file-pdf")); });

    el("btn-download").addEventListener("click", function () {
      window.location.href = "/api/download";
    });

    el("btn-undo").addEventListener("click", function () {
      run("Undoing", postJson("/api/undo")).then(function (documentState) {
        clearSelection();
        applyState(documentState);
      });
    });
    el("btn-redo").addEventListener("click", function () {
      run("Redoing", postJson("/api/redo")).then(function (documentState) {
        clearSelection();
        applyState(documentState);
      });
    });

    el("page-prev").addEventListener("click", function () { goToPage(state.page - 1); });
    el("page-next").addEventListener("click", function () { goToPage(state.page + 1); });

    el("zoom-in").addEventListener("click", function () {
      state.zoom = Math.min(MAX_ZOOM, state.zoom * 1.25);
      showPage(state.page);
    });
    el("zoom-out").addEventListener("click", function () {
      state.zoom = Math.max(MIN_ZOOM, state.zoom / 1.25);
      showPage(state.page);
    });

    el("menu-toggle").addEventListener("click", function () {
      el("sidebar").classList.toggle("is-open");
    });
    el("select-all").addEventListener("click", function () {
      state.pickedPages = [];
      for (var index = 0; index < state.pages; index += 1) { state.pickedPages.push(index); }
      markThumbnails();
    });

    ["text", "pages", "tools"].forEach(function (name) {
      el("tab-" + name).addEventListener("click", function () { showPane(name); });
    });

    /* Text */
    el("btn-replace").addEventListener("click", function () {
      editText("/api/text/replace", { text: el("replacement").value }, "Replacing text");
    });
    el("btn-delete-text").addEventListener("click", function () {
      editText("/api/text/delete", {}, "Deleting text");
    });
    el("btn-highlight").addEventListener("click", function () {
      editText("/api/text/highlight", {}, "Highlighting");
    });
    el("btn-redact").addEventListener("click", function () {
      if (!window.confirm("Redaction removes the text for good. Continue?")) { return; }
      editText("/api/text/redact", {}, "Redacting");
    });

    el("selection-bar").addEventListener("click", function (event) {
      var action = event.target.dataset.act;
      if (!action) { return; }
      if (action === "clear") { clearSelection(); drawOverlay(); return; }
      if (action === "replace") {
        showPane("text");
        el("replacement").focus();
        return;
      }
      if (action === "delete") { editText("/api/text/delete", {}, "Deleting text"); }
      if (action === "highlight") { editText("/api/text/highlight", {}, "Highlighting"); }
    });

    el("btn-find").addEventListener("click", findText);
    el("find-input").addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); findText(); }
    });

    /* Pages */
    el("btn-duplicate").addEventListener("click", function () {
      pageAction("/api/pages/duplicate", {}, "Duplicating pages");
    });
    el("btn-rotate-left").addEventListener("click", function () {
      pageAction("/api/pages/rotate", { angle: -90 }, "Rotating");
    });
    el("btn-rotate-right").addEventListener("click", function () {
      pageAction("/api/pages/rotate", { angle: 90 }, "Rotating");
    });
    el("btn-delete-pages").addEventListener("click", function () {
      var pages = targetPages();
      if (!window.confirm("Delete page(s) " + pages.map(function (p) { return p + 1; }).join(", ") + "?")) {
        return;
      }
      pageAction("/api/pages/delete", {}, "Deleting pages");
    });
    el("btn-move-up").addEventListener("click", function () { movePage(-1); });
    el("btn-move-down").addEventListener("click", function () { movePage(1); });

    el("btn-blank").addEventListener("click", function () {
      var where = el("blank-where").value;
      var size = el("blank-size").value;
      var position = where === "before" ? state.page : (where === "end" ? state.pages : state.page + 1);
      var payload = { position: position, count: 1 };
      if (size === "match") { payload.match = state.page; } else { payload.paper = size; }
      run("Adding a page", postJson("/api/pages/blank", payload)).then(function (documentState) {
        state.page = position;
        applyState(documentState);
      });
    });

    el("btn-merge").addEventListener("click", function () { openFile(el("file-merge")); });

    /* Tools */
    el("btn-image").addEventListener("click", function () { openFile(el("file-image")); });

    el("btn-image-delete").addEventListener("click", function () {
      if (!state.selectedImage) { return; }
      run("Deleting the image", postJson("/api/image/delete", {
        page: state.page, xref: state.selectedImage
      })).then(function (documentState) {
        clearSelection();
        applyState(documentState);
      });
    });

    el("compress-level").addEventListener("change", describeLevel);

    el("btn-compress").addEventListener("click", function () {
      run("Compressing", postJson("/api/compress", {
        level: el("compress-level").value,
        lossless: el("compress-lossless").checked
      })).then(function (report) {
        addResult("Compressed", report.before + " to " + report.after +
          " (" + report.ratio + "% smaller)", report.token, "PDF");
        say(report.summary);
      });
    });

    el("btn-word").addEventListener("click", function () {
      run("Exporting to Word", postJson("/api/export/word", {
        method: el("word-method").value
      })).then(function (report) {
        addResult("Word document", report.summary, report.token, ".docx");
        say(report.summary);
      });
    });

    document.addEventListener("keydown", function (event) {
      if (el("app").hidden) { return; }
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        el(event.shiftKey ? "btn-redo" : "btn-undo").click();
        return;
      }
      if (typing) { return; }
      if (event.key === "Backspace" || event.key === "Delete") {
        if (state.picked.length) {
          event.preventDefault();
          editText("/api/text/delete", {}, "Deleting text");
        }
        return;
      }
      if (event.key === "ArrowLeft") { goToPage(state.page - 1); }
      if (event.key === "ArrowRight") { goToPage(state.page + 1); }
      if (event.key === "Escape") { clearSelection(); drawOverlay(); }
    });
  }

  function movePage(delta) {
    var destination = state.page + delta;
    if (destination < 0 || destination >= state.pages) {
      say("The page is already at the end.");
      return;
    }
    run("Moving the page", postJson("/api/pages/move", {
      page: state.page, destination: destination
    })).then(function (documentState) {
      state.page = destination;
      applyState(documentState);
    });
  }

  function findText() {
    var query = el("find-input").value.trim();
    if (!query) { return; }
    run("Searching", postJson("/api/search", { query: query })).then(function (answer) {
      var matches = answer.matches || [];
      if (!matches.length) {
        el("find-result").textContent = "Not found.";
        return;
      }
      var total = matches.reduce(function (sum, match) { return sum + match.count; }, 0);
      el("find-result").textContent = total + " match(es) on " + matches.length + " page(s).";
      var first = matches.filter(function (match) { return match.page >= state.page; })[0] || matches[0];
      state.page = first.page;
      showPage(first.page);
      setTimeout(function () {
        state.found = first.words;
        state.picked = [];
        drawOverlay();
      }, 300);
    });
  }

  wire();
  setupSelection();
  boot();
}());
