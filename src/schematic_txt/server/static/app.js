// schematic.txt live editor

(function() {
  const codeInput = document.getElementById("code-input");
  const lineNumbers = document.getElementById("line-numbers");
  const svgViewport = document.getElementById("svg-viewport");
  const errorBox = document.getElementById("error-box");
  const errorMessage = document.getElementById("error-message");
  const errorLineInfo = document.getElementById("error-line-info");
  const statusIndicator = document.getElementById("status-indicator");
  const btnCompile = document.getElementById("btn-compile");
  const btnResetZoom = document.getElementById("btn-reset-zoom");
  const btnDownload = document.getElementById("btn-download");
  const btnDownloadPng = document.getElementById("btn-download-png");
  const btnTheme = document.getElementById("btn-theme");
  const renderProgressBar = document.getElementById("render-progress-bar");
  const docSelect = document.getElementById("doc-select");
  const effortSelect = document.getElementById("effort-select");
  const canvasWrapper = document.getElementById("canvas-wrapper");
  const dragDivider = document.getElementById("drag-divider");
  const editorPane = document.querySelector(".editor-pane");
  const previewPane = document.querySelector(".preview-pane");

  const SAMPLE = [
    "# Scratch mode: nothing is saved. Run `schematic serve FILE` to edit a file.",
    "MCU: ESP32",
    "  pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL)",
    "",
    "MCU.3V3 -> Sensor.VCC, Display.VCC : 3V3",
    "MCU.GND -> Sensor.GND, Display.GND : GND",
    "MCU.GPIO21 -> Sensor.SDA, Display.SDA : I2C_SDA",
    "MCU.GPIO22 -> Sensor.SCL, Display.SCL : I2C_SCL",
    "",
  ].join("\n");

  let darkTheme = true;
  let compileTimeout = null;
  let currentSvgRaw = "";
  let compileSeq = 0;

  // Document state: the file on disk (if any) and the document being edited
  let docs = [];
  let currentDocId = null;
  let fileVersion = 0;
  let saveTimeout = null;
  let saving = false;

  // Pan & Zoom state (Native SVG ViewBox)
  let baseVb = null;
  let currentVb = null;
  let isPanning = false;
  let startClientX = 0;
  let startClientY = 0;
  let startVbX = 0;
  let startVbY = 0;

  // 1. Line numbers synchronization
  function updateLineNumbers() {
    const lines = codeInput.value.split("\n");
    const count = Math.max(lines.length, 1);
    let nums = "";
    for (let i = 1; i <= count; i++) {
      nums += i + "\n";
    }
    lineNumbers.textContent = nums;
  }

  codeInput.addEventListener("scroll", () => {
    lineNumbers.scrollTop = codeInput.scrollTop;
  });

  // 2. Compilation
  async function compileSchematic() {
    const code = codeInput.value.trim();
    if (!code) {
      svgViewport.innerHTML = '<div class="empty-state">Enter schematic code to visualize</div>';
      errorBox.classList.add("hidden");
      setStatus("ok", "Ready");
      return;
    }

    setStatus("compiling", "Rendering...");
    if (renderProgressBar) renderProgressBar.classList.remove("hidden");
    const seq = ++compileSeq;

    try {
      const resp = await fetch("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: codeInput.value, theme: darkTheme ? "dark" : "light", effort: effortSelect.value })
      });

      const data = await resp.json();
      if (seq !== compileSeq) return; // a newer render is on its way

      if (data.svg) {
        currentSvgRaw = data.svg;
        svgViewport.innerHTML = data.svg;
        errorBox.classList.add("hidden");
        setStatus("ok", saving ? "Saving..." : "Rendered");

        const svg = svgViewport.querySelector("svg");
        if (svg) {
          const parsed = parseViewBox(svg);
          if (parsed) {
            const wasDefault = !currentVb || !baseVb || (
              Math.abs(currentVb.x - baseVb.x) < 0.5 &&
              Math.abs(currentVb.y - baseVb.y) < 0.5 &&
              Math.abs(currentVb.w - baseVb.w) < 0.5 &&
              Math.abs(currentVb.h - baseVb.h) < 0.5
            );
            baseVb = { ...parsed };
            if (wasDefault) {
              currentVb = { ...parsed };
            }
            applyViewBox();
          }
        }
      } else {
        const err = data.error || {};
        showError(err);
        setStatus("error", "Error in schematic");
      }
    } catch (err) {
      if (seq !== compileSeq) return;
      showError({ message: "Cannot reach the server: " + err.message });
      setStatus("error", "Server not reachable");
    } finally {
      if (seq === compileSeq && renderProgressBar) renderProgressBar.classList.add("hidden");
    }
  }

  function setStatus(type, text) {
    statusIndicator.className = "status " + type;
    statusIndicator.textContent = text;
  }

  function showError(err) {
    errorBox.classList.remove("hidden");
    errorMessage.textContent = err.message || "Unknown error";
    if (err.line) {
      errorLineInfo.textContent = `At line ${err.line}` + (err.line_content ? `: ${err.line_content.trim()}` : "");
    } else {
      errorLineInfo.textContent = "";
    }
  }

  // Jump to error line if error box is clicked
  errorBox.addEventListener("click", () => {
    const match = errorLineInfo.textContent.match(/line (\d+)/);
    if (match) {
      const lineNo = parseInt(match[1], 10);
      const lines = codeInput.value.split("\n");
      let pos = 0;
      for (let i = 0; i < lineNo - 1 && i < lines.length; i++) {
        pos += lines[i].length + 1;
      }
      codeInput.focus();
      codeInput.setSelectionRange(pos, pos + (lines[lineNo - 1] || "").length);
    }
  });

  // Debounced auto-compilation
  function scheduleCompile() {
    updateLineNumbers();
    clearTimeout(compileTimeout);
    compileTimeout = setTimeout(compileSchematic, 300);
  }

  codeInput.addEventListener("input", () => {
    scheduleCompile();
    scheduleSave();
  });

  // Tab key & shortcuts
  codeInput.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const start = codeInput.selectionStart;
      const end = codeInput.selectionEnd;
      codeInput.value = codeInput.value.substring(0, start) + "  " + codeInput.value.substring(end);
      codeInput.selectionStart = codeInput.selectionEnd = start + 2;
      scheduleCompile();
      scheduleSave();
    } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      clearTimeout(compileTimeout);
      compileSchematic();
    }
  });

  btnCompile.addEventListener("click", () => {
    clearTimeout(compileTimeout);
    compileSchematic();
  });

  // 3. Pan & Zoom Engine (Native SVG ViewBox)
  function parseViewBox(svg) {
    if (!svg) return null;
    const vbAttr = svg.getAttribute("viewBox");
    if (!vbAttr) return null;
    const parts = vbAttr.trim().split(/[\s,]+/).map(Number);
    if (parts.length === 4 && !parts.some(isNaN)) {
      return { x: parts[0], y: parts[1], w: parts[2], h: parts[3] };
    }
    return null;
  }

  function applyViewBox() {
    const svg = svgViewport.querySelector("svg");
    if (!svg || !currentVb) return;
    svg.setAttribute("viewBox", `${currentVb.x.toFixed(2)} ${currentVb.y.toFixed(2)} ${currentVb.w.toFixed(2)} ${currentVb.h.toFixed(2)}`);

    const canvasInfo = document.getElementById("canvas-info");
    if (canvasInfo && baseVb) {
      const zoomPct = Math.round((baseVb.w / currentVb.w) * 100);
      canvasInfo.textContent = `Scroll to zoom · Drag to pan · Zoom: ${zoomPct}%`;
    }
  }

  function resetZoom() {
    const svg = svgViewport.querySelector("svg");
    if (!svg) return;
    const vb = parseViewBox(svg);
    if (!vb) return;
    baseVb = { ...vb };
    currentVb = { ...vb };
    applyViewBox();
  }

  function getSvgPoint(e, svg) {
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    return pt.matrixTransform(ctm.inverse());
  }

  btnResetZoom.addEventListener("click", resetZoom);

  canvasWrapper.addEventListener("wheel", (e) => {
    e.preventDefault();
    const svg = svgViewport.querySelector("svg");
    if (!svg || !currentVb || !baseVb) return;

    const svgPt = getSvgPoint(e, svg);
    if (!svgPt) return;

    // Zoom factor: wheel up (< 0) -> zoom in; wheel down (> 0) -> zoom out
    const zoomFactor = e.deltaY < 0 ? 1.15 : (1 / 1.15);

    const currentZoom = baseVb.w / currentVb.w;
    const targetZoom = currentZoom * (e.deltaY < 0 ? 1.15 : 0.8695);
    if (targetZoom < 0.1 || targetZoom > 35.0) return;

    const newW = currentVb.w / zoomFactor;
    const newH = currentVb.h / zoomFactor;

    const fx = (svgPt.x - currentVb.x) / currentVb.w;
    const fy = (svgPt.y - currentVb.y) / currentVb.h;

    currentVb.x = svgPt.x - fx * newW;
    currentVb.y = svgPt.y - fy * newH;
    currentVb.w = newW;
    currentVb.h = newH;

    applyViewBox();
  }, { passive: false });

  canvasWrapper.addEventListener("mousedown", (e) => {
    if (e.target.closest(".error-box")) return;
    if (e.button !== 0 && e.button !== 1) return;
    const svg = svgViewport.querySelector("svg");
    if (!svg || !currentVb) return;

    isPanning = true;
    canvasWrapper.classList.add("is-panning");
    startClientX = e.clientX;
    startClientY = e.clientY;
    startVbX = currentVb.x;
    startVbY = currentVb.y;
  });

  window.addEventListener("mousemove", (e) => {
    if (!isPanning) return;
    const svg = svgViewport.querySelector("svg");
    if (!svg || !currentVb) return;

    const ctm = svg.getScreenCTM();
    if (!ctm) return;

    const dxScreen = e.clientX - startClientX;
    const dyScreen = e.clientY - startClientY;

    // Convert pixel delta to SVG units
    currentVb.x = startVbX - (dxScreen / ctm.a);
    currentVb.y = startVbY - (dyScreen / ctm.d);

    applyViewBox();
  });

  window.addEventListener("mouseup", () => {
    if (isPanning) {
      isPanning = false;
      canvasWrapper.classList.remove("is-panning");
    }
  });

  canvasWrapper.addEventListener("dblclick", (e) => {
    if (e.target.closest(".error-box")) return;
    resetZoom();
  });

  // 4. Download SVG
  btnDownload.addEventListener("click", () => {
    if (!currentSvgRaw) return;
    const blob = new Blob([currentSvgRaw], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "schematic.svg";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  // 4b. Download PNG with custom component images embedded
  async function exportPng() {
    if (!currentSvgRaw) return;
    setStatus("compiling", "Generating PNG...");
    if (renderProgressBar) renderProgressBar.classList.remove("hidden");

    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(currentSvgRaw, "image/svg+xml");
      const svgEl = doc.documentElement;

      // Extract original viewBox or dimensions
      let viewBox = svgEl.getAttribute("viewBox");
      let width = parseFloat(svgEl.getAttribute("width"));
      let height = parseFloat(svgEl.getAttribute("height"));

      if (viewBox) {
        const parts = viewBox.split(/\s+|,/).map(parseFloat);
        if (parts.length === 4) {
          if (!width) width = parts[2];
          if (!height) height = parts[3];
        }
      }
      if (!width || !height) {
        width = 1200;
        height = 800;
      }

      const svgBlob = new Blob([currentSvgRaw], { type: "image/svg+xml;charset=utf-8" });
      const svgUrl = URL.createObjectURL(svgBlob);

      const img = new Image();
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = reject;
        img.src = svgUrl;
      });

      // Render at 2x pixel ratio for crisp typography and lines
      const scale = 2;
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const ctx = canvas.getContext("2d");

      // Fill background matching current theme
      ctx.fillStyle = darkTheme ? "#0f172a" : "#f8fafc";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(svgUrl);

      canvas.toBlob((pngBlob) => {
        if (!pngBlob) return;
        const pngUrl = URL.createObjectURL(pngBlob);
        const a = document.createElement("a");
        a.href = pngUrl;
        a.download = "schematic.png";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(pngUrl);
        setStatus("ready", "PNG exported");
      }, "image/png");
    } catch (err) {
      console.error("PNG export error:", err);
      setStatus("error", "PNG export failed: " + err.message);
    } finally {
      if (renderProgressBar) renderProgressBar.classList.add("hidden");
    }
  }

  if (btnDownloadPng) {
    btnDownloadPng.addEventListener("click", exportPng);
  }

  // 5. Theme Toggle
  effortSelect.addEventListener("change", () => compileSchematic());

  btnTheme.addEventListener("click", () => {
    darkTheme = !darkTheme;
    document.body.classList.toggle("light-theme", !darkTheme);
    btnTheme.textContent = darkTheme ? "Theme" : "Dark";
    compileSchematic();
  });

  // 6. Split Pane Resizer
  let isResizing = false;
  dragDivider.addEventListener("mousedown", (e) => {
    isResizing = true;
    dragDivider.classList.add("dragging");
    document.body.style.cursor = "col-resize";
  });

  window.addEventListener("mousemove", (e) => {
    if (!isResizing) return;
    const totalW = window.innerWidth;
    const newLeftW = Math.max(250, Math.min(e.clientX, totalW - 250));
    const leftPct = (newLeftW / totalW) * 100;
    editorPane.style.flex = `0 0 ${leftPct}%`;
    previewPane.style.flex = `0 0 ${100 - leftPct}%`;
  });

  window.addEventListener("mouseup", () => {
    if (isResizing) {
      isResizing = false;
      dragDivider.classList.remove("dragging");
      document.body.style.cursor = "";
    }
  });

  // 7. Documents: the file given to `schematic serve` (or scratch mode)
  function loadDocument(id) {
    const doc = docs.find((d) => d.id === id);
    currentDocId = doc ? doc.id : null;
    codeInput.value = doc ? doc.code : SAMPLE;
    updateLineNumbers();
    baseVb = null;
    currentVb = null;
    compileSchematic();
  }

  function fillDocSelect(file) {
    docSelect.innerHTML = "";
    if (!file || !docs.length) {
      const opt = document.createElement("option");
      opt.textContent = file ? `${file}: no schematic blocks` : "Scratch (not saved)";
      docSelect.appendChild(opt);
      docSelect.disabled = true;
      return;
    }
    docs.forEach((doc) => {
      const opt = document.createElement("option");
      opt.value = String(doc.id);
      opt.textContent = doc.title === file ? file : `${file}: ${doc.title}`;
      docSelect.appendChild(opt);
    });
    docSelect.disabled = docs.length < 2;
    docSelect.value = String(currentDocId);
  }

  async function fetchDocuments() {
    const resp = await fetch("/api/documents");
    return resp.json();
  }

  async function initDocuments() {
    try {
      const data = await fetchDocuments();
      docs = data.documents;
      fileVersion = data.version;
      loadDocument(docs.length ? docs[0].id : null);
      fillDocSelect(data.file);
      if (data.file) {
        document.title = `${data.file} · schematic.txt`;
        setInterval(() => pollDocuments(data.file), 1500);
      }
    } catch (err) {
      loadDocument(null);
    }
  }

  // Changes made on disk (e.g. in a text editor) show up here.
  async function pollDocuments(file) {
    if (saving || saveTimeout) return;
    let data;
    try {
      data = await fetchDocuments();
    } catch (err) {
      return;
    }
    if (data.version === fileVersion || saving || saveTimeout) return;
    fileVersion = data.version;
    docs = data.documents;
    if (!docs.some((d) => d.id === currentDocId)) {
      currentDocId = docs.length ? docs[0].id : null;
    }
    fillDocSelect(file);
    const doc = docs.find((d) => d.id === currentDocId);
    if (doc && doc.code !== codeInput.value) {
      const { selectionStart, selectionEnd } = codeInput;
      codeInput.value = doc.code;
      codeInput.setSelectionRange(selectionStart, selectionEnd);
      updateLineNumbers();
      compileSchematic();
    }
  }

  function scheduleSave() {
    if (currentDocId === null) return;
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(saveDocument, 400);
  }

  async function saveDocument() {
    saveTimeout = null;
    saving = true;
    try {
      const resp = await fetch(`/api/documents/${currentDocId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: codeInput.value })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ? data.error.message : resp.statusText);
      fileVersion = data.version;
      const doc = docs.find((d) => d.id === currentDocId);
      if (doc) doc.code = codeInput.value;
    } catch (err) {
      setStatus("error", "Not saved: " + err.message);
    } finally {
      saving = false;
    }
  }

  docSelect.addEventListener("change", () => loadDocument(Number(docSelect.value)));

  // ================================================================= 8. Part Builder Modal
  const btnPartBuilder = document.getElementById("btn-part-builder");
  const partModal = document.getElementById("part-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const partNameInput = document.getElementById("part-name-input");
  const partFileInput = document.getElementById("part-file-input");
  const partWidthInput = document.getElementById("part-width-input");
  const partHeightInput = document.getElementById("part-height-input");
  const imgPosXInput = document.getElementById("img-pos-x");
  const imgPosYInput = document.getElementById("img-pos-y");
  const imgWidthInput = document.getElementById("img-width");
  const imgHeightInput = document.getElementById("img-height");
  const btnFitGrid = document.getElementById("btn-fit-grid");
  const btnCenterImage = document.getElementById("btn-center-image");
  const pinsList = document.getElementById("pins-list");
  const pinCount = document.getElementById("pin-count");
  const btnInsertPart = document.getElementById("btn-insert-part");
  const partGridSvg = document.getElementById("part-grid-svg");
  const canvasCursorPos = document.getElementById("canvas-cursor-pos");

  const partState = {
    name: "ESP32",
    width: 9,
    height: 18,
    imgX: 0,
    imgY: 0,
    imgW: 180,
    imgH: 360,
    imagePath: "",
    pins: []
  };

  const partCanvasWrapper = document.getElementById("part-canvas-wrapper");
  const btnPartZoomIn = document.getElementById("btn-part-zoom-in");
  const btnPartZoomOut = document.getElementById("btn-part-zoom-out");
  const btnPartZoomReset = document.getElementById("btn-part-zoom-reset");
  const partZoomVal = document.getElementById("part-zoom-val");

  let partCanvasZoom = 2.0; // 200% default scale for huge, clear working area

  function setPartZoom(val) {
    partCanvasZoom = Math.max(0.75, Math.min(4.0, val));
    if (partZoomVal) {
      partZoomVal.textContent = Math.round(partCanvasZoom * 100) + "%";
    }
    renderPartCanvas();
  }

  if (btnPartZoomIn) btnPartZoomIn.addEventListener("click", () => setPartZoom(partCanvasZoom + 0.25));
  if (btnPartZoomOut) btnPartZoomOut.addEventListener("click", () => setPartZoom(partCanvasZoom - 0.25));
  if (btnPartZoomReset) btnPartZoomReset.addEventListener("click", () => setPartZoom(2.0));

  if (partCanvasWrapper) {
    partCanvasWrapper.addEventListener("wheel", (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.2 : -0.2;
        setPartZoom(partCanvasZoom + delta);
      }
    }, { passive: false });
  }

  // Dragging & Scaling State for Image Transform Rectangle
  let isTransforming = false;
  let isPotentialMove = false;
  let transformMode = null; // 'move' or 'nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'
  let startPointerX = 0;
  let startPointerY = 0;
  let initialImgX = 0;
  let initialImgY = 0;
  let initialImgW = 0;
  let initialImgH = 0;

  function syncImageInputs() {
    if (imgPosXInput) imgPosXInput.value = Math.round(partState.imgX);
    if (imgPosYInput) imgPosYInput.value = Math.round(partState.imgY);
    if (imgWidthInput) imgWidthInput.value = Math.round(partState.imgW);
    if (imgHeightInput) imgHeightInput.value = Math.round(partState.imgH);
  }

  if (btnPartBuilder && partModal) {
    btnPartBuilder.addEventListener("click", () => {
      partModal.classList.remove("hidden");
      renderPartBuilder();
    });

    btnCloseModal.addEventListener("click", () => {
      partModal.classList.add("hidden");
    });

    partModal.addEventListener("click", (e) => {
      if (e.target === partModal) {
        partModal.classList.add("hidden");
      }
    });

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !partModal.classList.contains("hidden")) {
        partModal.classList.add("hidden");
      }
    });

    partNameInput.addEventListener("input", () => {
      partState.name = partNameInput.value.trim() || "Part";
    });

    partWidthInput.addEventListener("change", () => {
      const val = parseInt(partWidthInput.value, 10);
      if (val >= 2) {
        partState.width = val;
        renderPartCanvas();
      }
    });

    partHeightInput.addEventListener("change", () => {
      const val = parseInt(partHeightInput.value, 10);
      if (val >= 2) {
        partState.height = val;
        renderPartCanvas();
      }
    });

    // Image Bounds Direct Inputs
    if (imgPosXInput) {
      imgPosXInput.addEventListener("change", () => {
        partState.imgX = parseFloat(imgPosXInput.value) || 0;
        renderPartCanvas();
      });
    }
    if (imgPosYInput) {
      imgPosYInput.addEventListener("change", () => {
        partState.imgY = parseFloat(imgPosYInput.value) || 0;
        renderPartCanvas();
      });
    }
    if (imgWidthInput) {
      imgWidthInput.addEventListener("change", () => {
        partState.imgW = Math.max(10, parseFloat(imgWidthInput.value) || 20);
        renderPartCanvas();
      });
    }
    if (imgHeightInput) {
      imgHeightInput.addEventListener("change", () => {
        partState.imgH = Math.max(10, parseFloat(imgHeightInput.value) || 20);
        renderPartCanvas();
      });
    }

    if (btnFitGrid) {
      btnFitGrid.addEventListener("click", () => {
        partState.imgX = 0;
        partState.imgY = 0;
        partState.imgW = partState.width * 20;
        partState.imgH = partState.height * 20;
        syncImageInputs();
        renderPartCanvas();
      });
    }

    if (btnCenterImage) {
      btnCenterImage.addEventListener("click", () => {
        const gridW = partState.width * 20;
        const gridH = partState.height * 20;
        partState.imgX = Math.round((gridW - partState.imgW) / 2);
        partState.imgY = Math.round((gridH - partState.imgH) / 2);
        syncImageInputs();
        renderPartCanvas();
      });
    }

    // Global Mouse Handlers for Dragging and Scaling
    window.addEventListener("mousemove", (e) => {
      if (isPotentialMove && !isTransforming) {
        const moveDist = Math.hypot(e.clientX - startPointerX, e.clientY - startPointerY);
        if (moveDist > 4) {
          isTransforming = true;
          document.body.style.cursor = "move";
          renderPartCanvas();
        }
      }

      if (!isTransforming) return;

      const W = partState.width * 20;
      const H = partState.height * 20;
      const pad = 120;
      const totalW = W + pad * 2;
      const totalH = H + pad * 2;

      const svgRect = partGridSvg.getBoundingClientRect();
      if (!svgRect.width || !svgRect.height) return;

      const scaleX = totalW / svgRect.width;
      const scaleY = totalH / svgRect.height;
      const dx = (e.clientX - startPointerX) * scaleX;
      const dy = (e.clientY - startPointerY) * scaleY;

      if (transformMode === "move") {
        partState.imgX = Math.round(initialImgX + dx);
        partState.imgY = Math.round(initialImgY + dy);
      } else if (transformMode === "e") {
        // Independent X scale right
        partState.imgW = Math.max(20, Math.round(initialImgW + dx));
      } else if (transformMode === "w") {
        // Independent X scale left
        const newW = Math.max(20, Math.round(initialImgW - dx));
        partState.imgX = Math.round(initialImgX + (initialImgW - newW));
        partState.imgW = newW;
      } else if (transformMode === "s") {
        // Independent Y scale bottom
        partState.imgH = Math.max(20, Math.round(initialImgH + dy));
      } else if (transformMode === "n") {
        // Independent Y scale top
        const newH = Math.max(20, Math.round(initialImgH - dy));
        partState.imgY = Math.round(initialImgY + (initialImgH - newH));
        partState.imgH = newH;
      } else if (transformMode === "se") {
        partState.imgW = Math.max(20, Math.round(initialImgW + dx));
        partState.imgH = Math.max(20, Math.round(initialImgH + dy));
      } else if (transformMode === "sw") {
        const newW = Math.max(20, Math.round(initialImgW - dx));
        partState.imgX = Math.round(initialImgX + (initialImgW - newW));
        partState.imgW = newW;
        partState.imgH = Math.max(20, Math.round(initialImgH + dy));
      } else if (transformMode === "ne") {
        partState.imgW = Math.max(20, Math.round(initialImgW + dx));
        const newH = Math.max(20, Math.round(initialImgH - dy));
        partState.imgY = Math.round(initialImgY + (initialImgH - newH));
        partState.imgH = newH;
      } else if (transformMode === "nw") {
        const newW = Math.max(20, Math.round(initialImgW - dx));
        const newH = Math.max(20, Math.round(initialImgH - dy));
        partState.imgX = Math.round(initialImgX + (initialImgW - newW));
        partState.imgY = Math.round(initialImgY + (initialImgH - newH));
        partState.imgW = newW;
        partState.imgH = newH;
      }

      syncImageInputs();
      renderPartCanvas();
    });

    window.addEventListener("mouseup", (e) => {
      if (isPotentialMove && !isTransforming) {
        // Pure click on the image surface - treat as grid pin placement!
        isPotentialMove = false;
        transformMode = null;

        const pad = 120;
        const rect = partGridSvg.getBoundingClientRect();
        const ptX = (e.clientX - rect.left) / partCanvasZoom - pad;
        const ptY = (e.clientY - rect.top) / partCanvasZoom - pad;
        const gx = Math.round(ptX / 20);
        const gy = Math.round(ptY / 20);

        if (gx >= 0 && gx <= partState.width && gy >= 0 && gy <= partState.height) {
          const dist = Math.hypot(ptX - gx * 20, ptY - gy * 20);
          if (dist <= 15) {
            togglePin(gx, gy);
          }
        }
        return;
      }

      if (isTransforming) {
        isTransforming = false;
        isPotentialMove = false;
        transformMode = null;
        document.body.style.cursor = "";
        renderPartCanvas();
      }
    });

    // File Upload handling
    partFileInput.addEventListener("change", (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;

      // Auto-populate part name from filename
      const baseName = file.name.replace(/\.[^/.]+$/, "").replace(/[^A-Za-z0-9_]/g, "_");
      if (baseName) {
        partState.name = baseName;
        partNameInput.value = baseName;
      }

      // Save the image to parts/ next to the edited file, then fit it to the grid
      const reader = new FileReader();
      reader.onload = async (evt) => {
        try {
          const resp = await fetch("/api/parts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: file.name, data: evt.target.result })
          });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.error ? data.error.message : resp.statusText);
          partState.imagePath = data.path;
          partState.serverUrl = `/files/${data.path}?v=${Date.now()}`;

          const probe = new Image();
          probe.onload = () => {
            const gridW = partState.width * 20;
            const gridH = partState.height * 20;
            const aspect = probe.naturalWidth / probe.naturalHeight || 1;
            if (aspect > gridW / gridH) {
              partState.imgW = gridW;
              partState.imgH = Math.max(20, Math.round(gridW / aspect));
            } else {
              partState.imgH = gridH;
              partState.imgW = Math.max(20, Math.round(gridH * aspect));
            }
            partState.imgX = Math.round((gridW - partState.imgW) / 2);
            partState.imgY = Math.round((gridH - partState.imgH) / 2);
            syncImageInputs();
            renderPartCanvas();
          };
          probe.src = partState.serverUrl;
        } catch (err) {
          setStatus("error", "Image upload failed: " + err.message);
        }
      };
      reader.readAsDataURL(file);
    });

    // Insert into Schematic
    btnInsertPart.addEventListener("click", () => {
      if (!partState.imagePath) {
        setStatus("error", "Upload a part image first");
        return;
      }
      const partDsl = generatePartDsl();
      // Also instantiate the component so it appears immediately on the main canvas!
      const compInstance = `${partState.name}: ${partState.name}\n\n`;
      const currentCode = codeInput.value;
      codeInput.value = partDsl + "\n" + compInstance + currentCode;
      updateLineNumbers();
      partModal.classList.add("hidden");
      compileSchematic();
    });
  }

  function generatePartDsl() {
    let lines = [];
    lines.push(`part ${partState.name}:`);
    const cleanImgPath = (partState.imagePath || "").replace(/^\/+/, "");
    lines.push(`  image: ${cleanImgPath}`);
    lines.push(`  size: ${partState.width}x${partState.height}`);

    const defaultW = partState.width * 20;
    const defaultH = partState.height * 20;
    const rx = Math.round(partState.imgX);
    const ry = Math.round(partState.imgY);
    const rw = Math.round(partState.imgW);
    const rh = Math.round(partState.imgH);

    // If moved or resized away from default 0, 0, width*20, height*20:
    if (rx !== 0 || ry !== 0 || rw !== defaultW || rh !== defaultH) {
      lines.push(`  image_rect: ${rx}, ${ry}, ${rw}, ${rh}`);
    }

    lines.push(`  pins:`);
    partState.pins.forEach((p) => {
      lines.push(`    ${p.name}: ${p.x}, ${p.y}`);
    });
    return lines.join("\n") + "\n";
  }

  function renderPartBuilder() {
    partNameInput.value = partState.name;
    partWidthInput.value = partState.width;
    partHeightInput.value = partState.height;
    if (partState.imgW <= 0) partState.imgW = partState.width * 20;
    if (partState.imgH <= 0) partState.imgH = partState.height * 20;
    syncImageInputs();
    renderPinsList();
    renderPartCanvas();
  }

  function renderPinsList() {
    pinsList.innerHTML = "";
    pinCount.textContent = partState.pins.length;

    partState.pins.forEach((p, idx) => {
      const chip = document.createElement("div");
      chip.className = "pin-chip";
      chip.innerHTML = `
        <div>
          <span class="pin-chip-coord">(${p.x}, ${p.y})</span>
          <input type="text" value="${escapeHtml(p.name)}" data-idx="${idx}" />
        </div>
        <button class="pin-chip-del" data-idx="${idx}" title="Remove Pin">&times;</button>
      `;

      const input = chip.querySelector("input");
      input.addEventListener("input", (e) => {
        partState.pins[idx].name = e.target.value.trim() || `P${idx + 1}`;
        renderPartCanvas();
      });

      const delBtn = chip.querySelector(".pin-chip-del");
      delBtn.addEventListener("click", () => {
        partState.pins.splice(idx, 1);
        renderPinsList();
        renderPartCanvas();
      });

      pinsList.appendChild(chip);
    });
  }

  function renderPartCanvas() {
    const W = partState.width * 20;
    const H = partState.height * 20;
    const pad = 120; // Generous 120px workspace padding so nothing is cramped!
    const totalW = W + pad * 2;
    const totalH = H + pad * 2;

    const displayW = Math.round(totalW * partCanvasZoom);
    const displayH = Math.round(totalH * partCanvasZoom);

    partGridSvg.setAttribute("viewBox", `${-pad} ${-pad} ${totalW} ${totalH}`);
    partGridSvg.setAttribute("width", `${displayW}`);
    partGridSvg.setAttribute("height", `${displayH}`);

    // Resolve Image Source URL
    const imgSrc = partState.serverUrl || (partState.imagePath ? `/files/${partState.imagePath}` : "");

    // Sync HTML <img> tag (hardware-accelerated, transparent, zero-fail)
    const imgEl = document.getElementById("part-image-preview");
    if (imgEl) {
      if (imgSrc) {
        imgEl.src = imgSrc;
        imgEl.style.display = "block";
        imgEl.style.left = `${Math.round((pad + partState.imgX) * partCanvasZoom)}px`;
        imgEl.style.top = `${Math.round((pad + partState.imgY) * partCanvasZoom)}px`;
        imgEl.style.width = `${Math.round(partState.imgW * partCanvasZoom)}px`;
        imgEl.style.height = `${Math.round(partState.imgH * partCanvasZoom)}px`;
      } else {
        imgEl.style.display = "none";
      }
    }

    let svgHtml = [];

    // Transparent Background rect
    svgHtml.push(`<rect x="${-pad}" y="${-pad}" width="${totalW}" height="${totalH}" fill="transparent" />`);

    // Part image (behind the grid points)
    const ix = partState.imgX;
    const iy = partState.imgY;
    const iw = partState.imgW;
    const ih = partState.imgH;

    const activeOpacity = isTransforming ? "0.50" : "0.95";

    if (imgEl) {
      imgEl.style.opacity = activeOpacity;
    }

    if (imgSrc) {
      svgHtml.push(
        `<image href="${escapeHtml(imgSrc)}" xlink:href="${escapeHtml(imgSrc)}" x="${ix.toFixed(1)}" y="${iy.toFixed(1)}" ` +
        `width="${iw.toFixed(1)}" height="${ih.toFixed(1)}" preserveAspectRatio="none" opacity="${activeOpacity}" />`
      );
    }

    // Extended Grid Lines across the whole working canvas (subtle clean grid)
    for (let x = -pad; x <= W + pad; x += 20) {
      const isInside = x >= 0 && x <= W;
      svgHtml.push(`<line x1="${x}" y1="${-pad}" x2="${x}" y2="${H + pad}" stroke="rgba(255, 255, 255, ${isInside ? '0.20' : '0.07'})" stroke-width="1" pointer-events="none" />`);
    }
    for (let y = -pad; y <= H + pad; y += 20) {
      const isInside = y >= 0 && y <= H;
      svgHtml.push(`<line x1="${-pad}" y1="${y}" x2="${W + pad}" y2="${y}" stroke="rgba(255, 255, 255, ${isInside ? '0.20' : '0.07'})" stroke-width="1" pointer-events="none" />`);
    }

    // Component Bounding Box (cyan dashed frame)
    svgHtml.push(
      `<rect x="0" y="0" width="${W}" height="${H}" rx="4" ry="4" ` +
      `fill="none" stroke="#38bdf8" stroke-width="1.8" stroke-dasharray="6,4" pointer-events="none" />`
    );

    // Image Transform Rectangle: Drag Surface for Move (transparent)
    svgHtml.push(
      `<rect class="img-drag-surface" x="${ix.toFixed(1)}" y="${iy.toFixed(1)}" ` +
      `width="${iw.toFixed(1)}" height="${ih.toFixed(1)}" fill="transparent" pointer-events="all" />`
    );

    // Image Transform Rectangle: Dashed Selection Border
    svgHtml.push(
      `<rect class="img-selection-border" x="${ix.toFixed(1)}" y="${iy.toFixed(1)}" ` +
      `width="${iw.toFixed(1)}" height="${ih.toFixed(1)}" fill="none" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="5,3" pointer-events="none" />`
    );

    // 8 Resize Handles (Independent X and Y scaling)
    const hw = 10;
    const halfHw = hw / 2;
    const handles = [
      { id: "nw", x: ix, y: iy, cls: "handle-nw" },
      { id: "n",  x: ix + iw / 2, y: iy, cls: "handle-n" },
      { id: "ne", x: ix + iw, y: iy, cls: "handle-ne" },
      { id: "e",  x: ix + iw, y: iy + ih / 2, cls: "handle-e" },
      { id: "se", x: ix + iw, y: iy + ih, cls: "handle-se" },
      { id: "s",  x: ix + iw / 2, y: iy + ih, cls: "handle-s" },
      { id: "sw", x: ix, y: iy + ih, cls: "handle-sw" },
      { id: "w",  x: ix, y: iy + ih / 2, cls: "handle-w" }
    ];

    handles.forEach((h) => {
      svgHtml.push(
        `<rect class="transform-handle ${h.cls}" data-handle="${h.id}" ` +
        `x="${(h.x - halfHw).toFixed(1)}" y="${(h.y - halfHw).toFixed(1)}" ` +
        `width="${hw}" height="${hw}" rx="2" fill="#ffffff" stroke="#0284c7" stroke-width="2" />`
      );
    });

    // Configured Pins - Dezenter, dezentes Pad + dezentes Label (keine riesigen Neonkreise)
    partState.pins.forEach((p) => {
      const px = p.x * 20;
      const py = p.y * 20;
      svgHtml.push(
        `<g class="part-grid-pin-circle" data-gx="${p.x}" data-gy="${p.y}">` +
        `<circle cx="${px}" cy="${py}" r="4.2" fill="#0f172a" stroke="#38bdf8" stroke-width="1.6" />` +
        `<circle cx="${px}" cy="${py}" r="1.5" fill="#38bdf8" />` +
        `</g>`
      );

      const anchor = p.x <= partState.width / 2 ? "end" : "start";
      const tx = p.x <= partState.width / 2 ? px - 7 : px + 7;
      const ty = py + 3.2;
      svgHtml.push(
        `<text x="${tx}" y="${ty}" text-anchor="${anchor}" font-family="ui-monospace, monospace" ` +
        `font-size="9" font-weight="600" fill="#94a3b8" stroke="#0f172a" stroke-width="2.2" paint-order="stroke" pointer-events="none">${escapeHtml(p.name)}</text>`
      );
    });

    // Hover Indicator (single reticle, only visible on hover)
    svgHtml.push(
      `<g id="grid-hover-reticle" style="display: none; pointer-events: none;">` +
      `<circle cx="0" cy="0" r="4.5" fill="none" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="2,2" />` +
      `</g>`
    );

    partGridSvg.innerHTML = svgHtml.join("\n");

    // Force Firefox to evaluate SVG image loader
    const svgImg = partGridSvg.querySelector("image");
    if (svgImg && imgSrc) {
      svgImg.setAttribute("href", imgSrc);
      svgImg.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", imgSrc);
    }

    // Attach Transform Handle & Move Listeners
    partGridSvg.querySelectorAll(".transform-handle").forEach((handleEl) => {
      handleEl.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        isTransforming = true;
        transformMode = handleEl.getAttribute("data-handle");
        startPointerX = e.clientX;
        startPointerY = e.clientY;
        initialImgX = partState.imgX;
        initialImgY = partState.imgY;
        initialImgW = partState.imgW;
        initialImgH = partState.imgH;
        renderPartCanvas();
      });
    });

    const dragSurface = partGridSvg.querySelector(".img-drag-surface");
    if (dragSurface) {
      dragSurface.addEventListener("mousedown", (e) => {
        e.stopPropagation();
        isPotentialMove = true;
        isTransforming = false;
        transformMode = "move";
        startPointerX = e.clientX;
        startPointerY = e.clientY;
        initialImgX = partState.imgX;
        initialImgY = partState.imgY;
        initialImgW = partState.imgW;
        initialImgH = partState.imgH;
      });
    }

    // Click on existing pins to remove/edit
    partGridSvg.querySelectorAll(".part-grid-pin-circle").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (isTransforming) return;
        e.stopPropagation();
        const gx = parseInt(el.getAttribute("data-gx"), 10);
        const gy = parseInt(el.getAttribute("data-gy"), 10);
        togglePin(gx, gy);
      });
    });

    // Grid Hover & Click to Place Pin
    partGridSvg.onmousemove = (e) => {
      if (isTransforming) return;
      const rect = partGridSvg.getBoundingClientRect();
      const ptX = (e.clientX - rect.left) / partCanvasZoom - pad;
      const ptY = (e.clientY - rect.top) / partCanvasZoom - pad;
      const gx = Math.round(ptX / 20);
      const gy = Math.round(ptY / 20);

      const reticle = partGridSvg.querySelector("#grid-hover-reticle");
      if (gx >= 0 && gx <= partState.width && gy >= 0 && gy <= partState.height) {
        canvasCursorPos.textContent = `Col (x): ${gx}, Row (y): ${gy}`;
        if (reticle) {
          reticle.style.display = "block";
          reticle.setAttribute("transform", `translate(${gx * 20}, ${gy * 20})`);
        }
      } else {
        canvasCursorPos.textContent = `x: -, y: -`;
        if (reticle) reticle.style.display = "none";
      }
    };

    partGridSvg.onmouseleave = () => {
      const reticle = partGridSvg.querySelector("#grid-hover-reticle");
      if (reticle) reticle.style.display = "none";
      canvasCursorPos.textContent = `x: -, y: -`;
    };

    partGridSvg.onclick = (e) => {
      if (isTransforming) return;
      if (e.target.closest(".transform-handle") || e.target.closest(".part-grid-pin-circle")) return;

      const rect = partGridSvg.getBoundingClientRect();
      const ptX = (e.clientX - rect.left) / partCanvasZoom - pad;
      const ptY = (e.clientY - rect.top) / partCanvasZoom - pad;
      const gx = Math.round(ptX / 20);
      const gy = Math.round(ptY / 20);

      if (gx >= 0 && gx <= partState.width && gy >= 0 && gy <= partState.height) {
        const dist = Math.hypot(ptX - gx * 20, ptY - gy * 20);
        if (dist <= 14) {
          togglePin(gx, gy);
        }
      }
    };
  }

  function togglePin(gx, gy) {
    const existingIdx = partState.pins.findIndex((p) => p.x === gx && p.y === gy);
    if (existingIdx >= 0) {
      partState.pins.splice(existingIdx, 1);
    } else {
      // Suggest automatic pin name
      let autoName = "";
      if (gx === 0 && gy === 0) autoName = "VIN";
      else if (gx === 0 && gy === partState.height) autoName = "GND";
      else autoName = `P${partState.pins.length + 1}`;

      partState.pins.push({ name: autoName, x: gx, y: gy });
    }
    renderPinsList();
    renderPartCanvas();
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // Init
  updateLineNumbers();
  initDocuments();
})();

