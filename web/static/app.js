// Minimalist Schematic.txt Web Client

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
  const btnTheme = document.getElementById("btn-theme");
  const exampleSelect = document.getElementById("example-select");
  const canvasWrapper = document.getElementById("canvas-wrapper");
  const dragDivider = document.getElementById("drag-divider");
  const editorPane = document.querySelector(".editor-pane");
  const previewPane = document.querySelector(".preview-pane");

  let darkTheme = true;
  let compileTimeout = null;
  let currentSvgRaw = "";

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

    try {
      const resp = await fetch("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: codeInput.value, dark_theme: darkTheme })
      });

      const data = await resp.json();

      if (data.status === "ok") {
        currentSvgRaw = data.svg;
        svgViewport.innerHTML = data.svg;
        errorBox.classList.add("hidden");
        setStatus("ok", "Compiled successfully");

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
      showError({ message: "Network / Server error: " + err.message });
      setStatus("error", "Server connection error");
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

  codeInput.addEventListener("input", scheduleCompile);

  // Tab key & shortcuts
  codeInput.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const start = codeInput.selectionStart;
      const end = codeInput.selectionEnd;
      codeInput.value = codeInput.value.substring(0, start) + "  " + codeInput.value.substring(end);
      codeInput.selectionStart = codeInput.selectionEnd = start + 2;
      scheduleCompile();
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

  // 5. Theme Toggle
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

  // 7. Load Examples
  async function loadExamples() {
    try {
      const resp = await fetch("/api/examples");
      const data = await resp.json();
      const examples = data.examples || {};
      exampleSelect.innerHTML = '<option value="" disabled selected>Load example...</option>';

      const keys = Object.keys(examples);
      keys.forEach((filename) => {
        const opt = document.createElement("option");
        opt.value = filename;
        opt.textContent = filename.replace(/\.txt$/, "").replace(/_/g, " ");
        exampleSelect.appendChild(opt);
      });

      // Default load first example if editor is empty
      if (keys.length > 0 && !codeInput.value.trim()) {
        const preferred = keys.find(k => k.includes("sensors_bus")) || keys[0];
        codeInput.value = examples[preferred];
        exampleSelect.value = preferred;
        updateLineNumbers();
        compileSchematic();
      }

      exampleSelect.addEventListener("change", () => {
        const selected = exampleSelect.value;
        if (examples[selected]) {
          codeInput.value = examples[selected];
          updateLineNumbers();
          baseVb = null;
          currentVb = null;
          compileSchematic();
        }
      });
    } catch (err) {
      console.warn("Could not load examples:", err);
    }
  }

  // Init
  updateLineNumbers();
  loadExamples();
})();
