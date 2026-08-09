// ============================================================
// Widget de subida de PDFs para MaxConsulta (autocontenido)
// Se inyecta solo, no depende del resto del HTML.
// ============================================================
(function () {
  const API = "https://api.maxconsulta.com";
  const COLECCIONES = ["fiscal", "loboral", "boe", "seguridad_social", "BOCM", "Estatuto_trabajadores", "Convenios_Madrid"];
  const PIN_KEY = "maxconsulta_upload_pin";

  // --- estilos mínimos (scoped con prefijo mxup-) ---
  const css = `
    .mxup-fab{position:fixed;right:20px;bottom:20px;z-index:9999;background:#1b4fff;color:#fff;border:none;border-radius:999px;padding:12px 18px;font-weight:700;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);font-family:inherit}
    .mxup-fab:hover{background:#1740d6}
    .mxup-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:10000;display:none;align-items:center;justify-content:center}
    .mxup-overlay.open{display:flex}
    .mxup-card{background:#fff;border-radius:16px;padding:24px;width:min(440px,92vw);box-shadow:0 20px 60px rgba(0,0,0,.3);font-family:inherit;color:#111}
    .mxup-card h3{margin:0 0 4px;font-size:18px}
    .mxup-card p.sub{margin:0 0 16px;color:#666;font-size:13px}
    .mxup-row{margin-bottom:14px}
    .mxup-row label{display:block;font-size:13px;font-weight:600;margin-bottom:6px;color:#333}
    .mxup-row input,.mxup-row select{width:100%;padding:10px;border:1px solid #d5d5dd;border-radius:10px;font-size:14px;box-sizing:border-box;font-family:inherit}
    .mxup-actions{display:flex;gap:10px;margin-top:6px}
    .mxup-btn{flex:1;padding:11px;border:none;border-radius:10px;font-weight:700;cursor:pointer;font-size:14px;font-family:inherit}
    .mxup-btn.primary{background:#1b4fff;color:#fff}
    .mxup-btn.primary:disabled{background:#9db2ff;cursor:not-allowed}
    .mxup-btn.ghost{background:#eee;color:#333}
    .mxup-msg{margin-top:14px;font-size:13px;padding:10px;border-radius:8px;display:none}
    .mxup-msg.ok{display:block;background:#e6f7ed;color:#0a7d33}
    .mxup-msg.err{display:block;background:#fdeaea;color:#c0392b}
    .mxup-msg.info{display:block;background:#eef2ff;color:#1b4fff}
    .mxup-newcol{display:none;margin-top:8px}
    .mxup-newcol.show{display:block}
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // --- FAB (botón flotante) ---
  const fab = document.createElement("button");
  fab.className = "mxup-fab";
  fab.textContent = "⬆ Subir PDF";
  document.body.appendChild(fab);

  // --- overlay + card ---
  const overlay = document.createElement("div");
  overlay.className = "mxup-overlay";
  const opciones = COLECCIONES.map(c => `<option value="${c}">${c}</option>`).join("");
  overlay.innerHTML = `
    <div class="mxup-card">
      <h3>Subir documento PDF</h3>
      <p class="sub">Se indexará automáticamente en la colección elegida.</p>
      <div class="mxup-row">
        <label>Archivo PDF</label>
        <input type="file" id="mxup-file" accept="application/pdf,.pdf" />
      </div>
      <div class="mxup-row">
        <label>Colección</label>
        <select id="mxup-col">
          ${opciones}
          <option value="__nueva__">➕ Nueva colección…</option>
        </select>
        <div class="mxup-newcol" id="mxup-newcol-wrap">
          <input type="text" id="mxup-newcol" placeholder="nombre_nueva_coleccion" />
        </div>
      </div>
      <div class="mxup-row">
        <label>PIN de subida</label>
        <input type="password" id="mxup-pin" placeholder="PIN" />
      </div>
      <div class="mxup-actions">
        <button class="mxup-btn ghost" id="mxup-cancel">Cancelar</button>
        <button class="mxup-btn primary" id="mxup-send">Subir e indexar</button>
      </div>
      <div class="mxup-msg" id="mxup-msg"></div>
    </div>
  `;
  document.body.appendChild(overlay);

  // --- refs ---
  const $ = id => document.getElementById(id);
  const fileEl = $("mxup-file"), colEl = $("mxup-col"), pinEl = $("mxup-pin");
  const newcolWrap = $("mxup-newcol-wrap"), newcolEl = $("mxup-newcol");
  const sendBtn = $("mxup-send"), cancelBtn = $("mxup-cancel"), msg = $("mxup-msg");

  // precargar PIN guardado
  const savedPin = localStorage.getItem(PIN_KEY);
  if (savedPin) pinEl.value = savedPin;

  function showMsg(text, type) {
    msg.textContent = text;
    msg.className = "mxup-msg " + type;
  }
  function openModal() { overlay.classList.add("open"); msg.className = "mxup-msg"; }
  function closeModal() { overlay.classList.remove("open"); }

  fab.addEventListener("click", openModal);
  cancelBtn.addEventListener("click", closeModal);
  overlay.addEventListener("click", e => { if (e.target === overlay) closeModal(); });

  colEl.addEventListener("change", () => {
    newcolWrap.classList.toggle("show", colEl.value === "__nueva__");
  });

  sendBtn.addEventListener("click", async () => {
    const file = fileEl.files[0];
    let col = colEl.value;
    if (col === "__nueva__") col = (newcolEl.value || "").trim();
    const pin = pinEl.value.trim();

    if (!file) { showMsg("Selecciona un archivo PDF.", "err"); return; }
    if (!col) { showMsg("Indica una colección.", "err"); return; }
    if (!pin) { showMsg("Introduce el PIN.", "err"); return; }

    const fd = new FormData();
    fd.append("file", file);
    fd.append("coleccion", col);

    sendBtn.disabled = true;
    showMsg("Procesando… (esto puede tardar unos segundos)", "info");

    try {
      const resp = await fetch(`${API}/upload`, {
        method: "POST",
        headers: { "X-Upload-Pin": pin },
        body: fd,
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        localStorage.setItem(PIN_KEY, pin);
        showMsg(`✓ ${data.mensaje} — "${data.archivo}" en ${data.coleccion} (${data.chunks} fragmentos).`, "ok");
        fileEl.value = "";
      } else {
        const detail = data.detail || `Error ${resp.status}`;
        showMsg(`✗ ${detail}`, "err");
      }
    } catch (e) {
      showMsg("✗ Error de red: " + e.message, "err");
    } finally {
      sendBtn.disabled = false;
    }
  });
})();
