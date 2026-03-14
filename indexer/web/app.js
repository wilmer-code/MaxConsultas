// app.js (FIX DEFINITIVO: evita scripts duplicados + bloquea listeners viejos que borran .active)
(() => {
  // ✅ Guard: si por cache/duplicado se carga 2 veces, no re-registra eventos
  if (window.__MAXCONSULTA_APP_INIT__) return;
  window.__MAXCONSULTA_APP_INIT__ = true;

  document.addEventListener("DOMContentLoaded", () => {
    const API = "https://api.maxconsulta.com";

    const els = {
      apiLabel: document.getElementById("apiLabel"),
      q: document.getElementById("q"),
      out: document.getElementById("out"),

      sourcesList: document.getElementById("sourcesList"),
      sourcesWrap: document.getElementById("sourcesWrap"),

      btnAsk: document.getElementById("btnAsk"),
      btnClear: document.getElementById("btnClear"),
      btnCopy: document.getElementById("btnCopy"),
      btnToggleSources: document.getElementById("btnToggleSources"),

      mode: document.getElementById("mode"),
      tone: document.getElementById("tone"),
      topk: document.getElementById("topk"),

      btnSettings: document.getElementById("btnSettings"),
      btnCloseSettings: document.getElementById("btnCloseSettings"),
      settingsModal: document.getElementById("settingsModal"),
    };

    if (els.apiLabel) els.apiLabel.textContent = API;

    // Estado
    let selectedCollection = ""; // "" = todas

    // Helpers: siempre leen DOM actual
    const getAreaBtns = () => Array.from(document.querySelectorAll(".seg-btn[data-collection]"));
    const getModeBtns = () => Array.from(document.querySelectorAll(".seg-mode"));

    /* =========================
       SYNC MODO (tabs <-> select)
    ========================= */
    function syncModeFromSelect() {
      const v = els.mode?.value || "paso_a_paso";
      getModeBtns().forEach(b => b.classList.toggle("active", (b.dataset.mode || "") === v));
    }

    function syncModeToSelect(v) {
      if (els.mode) els.mode.value = v;
      getModeBtns().forEach(b => b.classList.toggle("active", (b.dataset.mode || "") === v));
    }

    /* =========================
       SYNC ÁREA
    ========================= */
    function syncAreaToState(v) {
      selectedCollection = v || "";
      getAreaBtns().forEach(b => b.classList.toggle("active", (b.dataset.collection || "") === selectedCollection));
    }

    /* =========================
       INIT
    ========================= */
    (function initState() {
      const areaBtns = getAreaBtns();
      const activeArea = areaBtns.find(b => b.classList.contains("active"));
      selectedCollection = activeArea ? (activeArea.dataset.collection || "") : "";
      syncModeFromSelect();
    })();

    /* =========================================================
       ✅ CLICK CAPTURE + STOP:
       Si hay un script viejo que borra ".active" globalmente,
       lo cortamos aquí antes de que se ejecute.
    ========================================================= */
    document.addEventListener(
      "click",
      (ev) => {
        const btn = ev.target.closest("button");
        if (!btn) return;

        const isArea = btn.matches(".seg-btn[data-collection]");
        const isMode = btn.classList.contains("seg-mode");

        // Solo “secuestramos” clicks de estas tabs
        if (!isArea && !isMode) return;

        // ✅ Impide que otros listeners (viejos) actúen
        ev.preventDefault();
        ev.stopImmediatePropagation();

        if (isArea) {
          syncAreaToState(btn.dataset.collection || "");
          return;
        }

        if (isMode) {
          syncModeToSelect(btn.dataset.mode || "paso_a_paso");
          return;
        }
      },
      true // ✅ CAPTURE
    );

    // Si cambias el select (aunque esté oculto), sincroniza tabs
    els.mode?.addEventListener("change", syncModeFromSelect);

    /* =========================
       Ajustes modal
    ========================= */
    els.btnSettings?.addEventListener("click", () => els.settingsModal?.showModal());
    els.btnCloseSettings?.addEventListener("click", () => els.settingsModal?.close());

    /* =========================
       Limpiar
    ========================= */
    els.btnClear?.addEventListener("click", () => {
      if (els.q) els.q.value = "";
      if (els.out) els.out.textContent = "—";

      if (els.sourcesList) els.sourcesList.innerHTML = "";
      if (els.sourcesWrap) els.sourcesWrap.hidden = true;

      if (els.btnToggleSources) {
        els.btnToggleSources.setAttribute("aria-expanded", "false");
        els.btnToggleSources.textContent = "Ver fuentes";
      }
    });

    /* =========================
       Toggle fuentes
    ========================= */
    els.btnToggleSources?.addEventListener("click", () => {
      if (!els.sourcesWrap) return;

      const isOpen = !els.sourcesWrap.hidden;
      els.sourcesWrap.hidden = isOpen;

      els.btnToggleSources?.setAttribute("aria-expanded", String(!isOpen));

      if (!isOpen) {
        els.btnToggleSources.textContent = "Ocultar fuentes";
      } else {
        const n = els.sourcesList?.children?.length || 0;
        els.btnToggleSources.textContent = n ? `Ver fuentes (${n})` : "Ver fuentes";
      }
    });

    /* =========================
       Copiar respuesta
    ========================= */
    els.btnCopy?.addEventListener("click", async () => {
      const txt = els.out?.textContent || "";
      if (!txt || txt === "—") return;

      try {
        await navigator.clipboard.writeText(txt);
        els.btnCopy.textContent = "Copiado ✓";
        setTimeout(() => (els.btnCopy.textContent = "Copiar respuesta"), 1200);
      } catch {
        alert("No se pudo copiar automáticamente. Selecciona el texto y copia manualmente.");
      }
    });

    /* =========================
       Helpers: escape + render fuentes
    ========================= */
    function escapeHtml(str) {
      return String(str ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function renderSources(sources) {
      if (!els.sourcesList) return;

      const arr = Array.isArray(sources) ? sources : [];
      if (!arr.length) {
        els.sourcesList.innerHTML = `<div class="muted">No hay fuentes devueltas por la API.</div>`;
        return;
      }

      els.sourcesList.innerHTML = arr.map((s, i) => {
        const title = s.title || s.document || s.doc || s.file || `Fuente ${i + 1}`;

        const metaParts = [];
        if (s.collection) metaParts.push(`Área: ${s.collection}`);
        if (s.section) metaParts.push(`Sección: ${s.section}`);
        if (typeof s.score !== "undefined") metaParts.push(`Score: ${Number(s.score).toFixed(3)}`);

        const meta = metaParts.join(" · ");
        const snippetRaw = s.snippet || s.content || s.text || "";
        const snippet = snippetRaw ? snippetRaw : JSON.stringify(s, null, 2);

        const safe = escapeHtml(snippet);
        const short = safe.slice(0, 800);
        const needsDots = safe.length > 800;

        return `
          <div class="source-card">
            <div class="source-head">
              <div>
                <div class="source-title">${escapeHtml(title)}</div>
                ${meta ? `<div class="source-meta">${escapeHtml(meta)}</div>` : ``}
              </div>
              <span class="source-chip">#${i + 1}</span>
            </div>

            <div class="source-snippet">${short}${needsDots ? "…" : ""}</div>

            <div class="source-actions">
              <button class="ghost" type="button" data-copy-snippet="${i}">Copiar fragmento</button>
            </div>
          </div>
        `;
      }).join("");

      els.sourcesList.querySelectorAll("[data-copy-snippet]").forEach(btn => {
        btn.addEventListener("click", async () => {
          const idx = parseInt(btn.dataset.copySnippet, 10);
          const s = arr[idx];
          const snippet = s?.snippet || s?.content || s?.text || "";
          if (!snippet) return;

          try {
            await navigator.clipboard.writeText(snippet);
            btn.textContent = "Copiado ✓";
            setTimeout(() => (btn.textContent = "Copiar fragmento"), 1200);
          } catch {
            alert("No se pudo copiar automáticamente.");
          }
        });
      });
    }

    /* =========================
       Instrucciones
    ========================= */
    function enrichQuestion(question) {
      const mode = els.mode?.value || "paso_a_paso";
      const tone = els.tone?.value || "neutro";

      const modeText = {
        respuesta: "Responde de forma directa y clara.",
        resumen: "Responde en formato resumen con puntos clave.",
        completa: "Responde de forma completa y detallada.",
        paso_a_paso: "Responde paso a paso con numeración y checklist final.",
      }[mode] || "Responde de forma clara.";

      const toneText = tone === "formal"
        ? "Mantén tono formal, profesional y propio de gestoría."
        : "Mantén tono neutro, profesional y cercano.";

      return `${question}\n\nInstrucciones: ${modeText} ${toneText}`;
    }

    /* =========================
       ask()
    ========================= */
    async function ask() {
      const qRaw = els.q?.value?.trim() || "";
      if (!qRaw) return alert("Escribe una pregunta");

      const question = enrichQuestion(qRaw);
      const collection = selectedCollection ? selectedCollection : null;
      const top_k = parseInt(els.topk?.value || "6", 10);

      if (els.out) els.out.textContent = "Pensando…";

      if (els.sourcesList) els.sourcesList.innerHTML = "";
      if (els.sourcesWrap) els.sourcesWrap.hidden = true;

      if (els.btnToggleSources) {
        els.btnToggleSources.textContent = "Ver fuentes";
        els.btnToggleSources.setAttribute("aria-expanded", "false");
      }

      try {
        const res = await fetch(`${API}/ask`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, collection, top_k }),
        });

        const rawText = await res.text();
        let data;
        try { data = JSON.parse(rawText); } catch { data = { raw: rawText }; }

        if (!res.ok) {
          if (els.out) els.out.textContent = `Error API: ${res.status}`;
          renderSources([{ title: "Respuesta API (error)", snippet: JSON.stringify(data, null, 2) }]);
          if (els.sourcesWrap) els.sourcesWrap.hidden = false;
          if (els.btnToggleSources) {
            els.btnToggleSources.textContent = "Ocultar fuentes";
            els.btnToggleSources.setAttribute("aria-expanded", "true");
          }
          return;
        }

        if (els.out) els.out.textContent = data.answer || "Sin respuesta";

        const sourcesArr = Array.isArray(data.sources) ? data.sources : [];
        renderSources(sourcesArr);

        const n = sourcesArr.length;
        if (els.btnToggleSources) els.btnToggleSources.textContent = n ? `Ver fuentes (${n})` : "Ver fuentes";

      } catch (e) {
        if (els.out) els.out.textContent = "Error de red / CORS";
        renderSources([{ title: "Error", snippet: String(e) }]);
        if (els.sourcesWrap) els.sourcesWrap.hidden = false;
        if (els.btnToggleSources) {
          els.btnToggleSources.textContent = "Ocultar fuentes";
          els.btnToggleSources.setAttribute("aria-expanded", "true");
        }
      }
    }

    els.btnAsk?.addEventListener("click", ask);
    els.q?.addEventListener("keydown", (ev) => {
      if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") ask();
    });
  });
})();