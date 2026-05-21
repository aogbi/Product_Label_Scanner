/**
 * Product Label Scanner — Client-side Application Logic
 *
 * Handles: file upload, API calls (scan/history/delete), UI state transitions.
 */

document.addEventListener("DOMContentLoaded", () => {
  // ---- DOM Elements ----
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  const previewContainer = document.getElementById("previewContainer");
  const previewImage = document.getElementById("previewImage");
  const scannerLine = document.getElementById("scannerLine");
  const scanOverlay = document.getElementById("scanOverlay");

  const idleState = document.getElementById("idleState");
  const dataForm = document.getElementById("dataForm");
  const lotInput = document.getElementById("lotInput");
  const expInput = document.getElementById("expInput");
  const dateStatus = document.getElementById("dateStatus");
  const confFill = document.getElementById("confFill");
  const confVal = document.getElementById("confVal");
  const rawTextBox = document.getElementById("rawTextBox");
  const rawToggle = document.getElementById("rawToggle");

  const btnReset = document.getElementById("btnReset");
  const btnSave = document.getElementById("btnSave");
  const toast = document.getElementById("toast");
  const historyGrid = document.getElementById("historyGrid");
  const historyCount = document.getElementById("historyCount");
  const historyEmpty = document.getElementById("historyEmpty");

  const cameraInput = document.getElementById("cameraInput");
  const btnBrowseProxy = document.getElementById("btnBrowseProxy");
  const btnCameraProxy = document.getElementById("btnCameraProxy");

  // ---- File & Camera Upload ----
  dropzone.addEventListener("click", () => fileInput.click());
  btnBrowseProxy.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
  btnCameraProxy.addEventListener("click", (e) => { e.stopPropagation(); cameraInput.click(); });

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
  });

  fileInput.addEventListener("change", function () {
    if (this.files.length) handleFile(this.files[0]);
  });

  // ---- State ----
  let currentFile = null;
  let currentLabelId = null;

  // ---- Init ----
  loadHistory();

  cameraInput.addEventListener("change", function () {
    if (this.files.length) handleFile(this.files[0]);
  });

  function handleFile(file) {
    if (!file.type.startsWith("image/")) {
      showToast("Invalid file type", "Please upload an image file.", true);
      return;
    }
    currentFile = file;

    // Show preview
    const reader = new FileReader();
    reader.onload = (e) => {
      previewImage.src = e.target.result;
      dropzone.style.display = "none";
      previewContainer.classList.add("active");
      uploadAndScan(file);
    };
    reader.readAsDataURL(file);
  }

  // ---- API: Scan ----
  async function uploadAndScan(file) {
    // Show scanning UI
    scanOverlay.classList.add("active");
    scannerLine.style.display = "block";
    idleState.style.display = "flex";
    dataForm.classList.remove("active");

    const formData = new FormData();
    formData.append("image", file);

    try {
      const res = await fetch("/api/scan/", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Server error ${res.status}`);
      }

      const data = await res.json();
      currentLabelId = data.id;
      displayResults(data);
    } catch (err) {
      console.error("Scan failed:", err);
      showToast("Scan Failed", err.message, true);
      scanOverlay.classList.remove("active");
      scannerLine.style.display = "none";
    }
  }

  function displayResults(data) {
    // Stop scanning animation
    scanOverlay.classList.remove("active");
    scannerLine.style.display = "none";

    // Hide idle, show form
    idleState.style.display = "none";
    dataForm.classList.add("active");

    // Populate fields
    lotInput.value = data.lot_number || "—";
    rawTextBox.textContent = data.raw_text || "No text extracted.";

    // Confidence bar
    const pct = Math.round((data.confidence || 0) * 100);
    confFill.style.width = pct + "%";
    confVal.textContent = pct + "%";

    // Typewriter effect for expiry date
    const expiry = data.expiry_date || "Not Found";
    expInput.value = "";
    let i = 0;
    const tw = setInterval(() => {
      expInput.value += expiry.charAt(i);
      i++;
      if (i >= expiry.length) {
        clearInterval(tw);
        updateDateStatus(expiry, data.expiry_date_parsed);
      }
    }, 45);

    // Reload history
    loadHistory();
  }

  function updateDateStatus(dateStr, parsedDate) {
    dateStatus.className = "date-status";
    if (!dateStr || dateStr === "Not Found") {
      dateStatus.textContent = "NOT FOUND";
      dateStatus.classList.add("check");
      return;
    }

    if (parsedDate) {
      const exp = new Date(parsedDate);
      const now = new Date();
      if (exp < now) {
        dateStatus.textContent = "⚠ EXPIRED";
        dateStatus.classList.add("expired");
        expInput.classList.add("expired");
        expInput.classList.remove("valid");
      } else {
        dateStatus.textContent = "✓ VALID";
        dateStatus.classList.add("ok");
        expInput.classList.add("valid");
        expInput.classList.remove("expired");
      }
    } else {
      dateStatus.textContent = "CHECK FORMAT";
      dateStatus.classList.add("check");
    }
  }

  // ---- Raw Text Toggle ----
  rawToggle.addEventListener("click", () => {
    rawTextBox.classList.toggle("active");
    rawToggle.textContent = rawTextBox.classList.contains("active")
      ? "▾ Hide raw OCR text"
      : "▸ Show raw OCR text";
  });

  // ---- Buttons ----
  btnReset.addEventListener("click", resetUI);

  btnSave.addEventListener("click", async () => {
    if (!currentLabelId) {
      showToast("No record", "No scanned record to save.", true);
      return;
    }

    const payload = {
      lot_number: lotInput.value || null,
      expiry_date: expInput.value || null,
    };

    try {
      const res = await fetch(`/api/labels/${currentLabelId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || JSON.stringify(err) || `Server ${res.status}`);
      }

      showToast("Data Confirmed", "Record saved in database.");
      loadHistory(); // <-- Add this to refresh the list!
      setTimeout(resetUI, 1500);
    } catch (err) {
      console.error("Save failed:", err);
      showToast("Save Failed", err.message, true);
    }
  });

  function resetUI() {
    fileInput.value = "";
    currentFile = null;
    currentLabelId = null;
    previewImage.src = "";
    previewContainer.classList.remove("active");
    dropzone.style.display = "";
    dataForm.classList.remove("active");
    idleState.style.display = "flex";
    expInput.value = "";
    expInput.className = "form-input mono";
    lotInput.value = "";
    rawTextBox.textContent = "";
    rawTextBox.classList.remove("active");
    rawToggle.textContent = "▸ Show raw OCR text";
    confFill.style.width = "0%";
    confVal.textContent = "0%";
  }

  // ---- API: History ----
  async function loadHistory() {
    try {
      const res = await fetch("/api/history/");
      if (!res.ok) return;
      const data = await res.json();
      renderHistory(data);
    } catch (err) {
      console.error("Failed to load history:", err);
    }
  }

  function renderHistory(items) {
    historyCount.textContent = items.length;
    if (!items.length) {
      historyEmpty.style.display = "block";
      historyGrid.innerHTML = "";
      return;
    }
    historyEmpty.style.display = "none";

    historyGrid.innerHTML = items
      .map(
        (item) => `
      <div class="history-card" data-id="${item.id}">
        <div class="history-thumb">
          <img src="${item.image}" alt="Label" loading="lazy" />
        </div>
        <div class="history-info">
          <div class="lot">${item.lot_number || "No LOT"}</div>
          <div class="exp">${item.expiry_date || "No date"}</div>
          <div class="date-label">${new Date(item.created_at).toLocaleString()}</div>
        </div>
        <button class="btn-delete" onclick="deleteLabel(${item.id})" title="Delete">✕</button>
      </div>
    `
      )
      .join("");
  }

  // ---- API: Delete ----
  window.deleteLabel = async function (id) {
    if (!confirm("Delete this scan record?")) return;
    try {
      const res = await fetch(`/api/labels/${id}/`, { method: "DELETE" });
      if (res.ok || res.status === 204) {
        showToast("Deleted", "Record removed.");
        loadHistory();
      }
    } catch (err) {
      console.error("Delete failed:", err);
    }
  };

  // ---- Toast ----
  function showToast(title, desc, isError = false) {
    const icon = toast.querySelector(".toast-icon");
    const tTitle = toast.querySelector(".t-title");
    const tDesc = toast.querySelector(".t-desc");

    icon.textContent = isError ? "!" : "✓";
    icon.style.background = isError ? "var(--danger)" : "var(--success)";
    tTitle.textContent = title;
    tDesc.textContent = desc;

    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 3500);
  }
});
