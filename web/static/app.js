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

  // Pan & Zoom state
  let scale = 1.0;
  let panX = 0;
  let panY = 0;
  let isPanning = false;
  let startX = 0;
  let startY = 0;

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
        applyTransform();
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

  // 3. Pan & Zoom Engine
  function applyTransform() {
    svgViewport.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
  }

  function resetZoom() {
    scale = 1.0;
    panX = 0;
    panY = 0;
    applyTransform();
  }

  btnResetZoom.addEventListener("click", resetZoom);

  canvasWrapper.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = canvasWrapper.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
    const newScale = Math.min(Math.max(scale * zoomFactor, 0.2), 5.0);

    panX = mouseX - (mouseX - panX) * (newScale / scale);
    panY = mouseY - (mouseY - panY) * (newScale / scale);
    scale = newScale;
    applyTransform();
  }, { passive: false });

  canvasWrapper.addEventListener("mousedown", (e) => {
    if (e.target.closest(".error-box")) return;
    isPanning = true;
    startX = e.clientX - panX;
    startY = e.clientY - panY;
  });

  window.addEventListener("mousemove", (e) => {
    if (!isPanning) return;
    panX = e.clientX - startX;
    panY = e.clientY - startY;
    applyTransform();
  });

  window.addEventListener("mouseup", () => {
    isPanning = false;
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
          resetZoom();
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
