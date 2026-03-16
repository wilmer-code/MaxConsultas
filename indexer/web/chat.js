(() => {
  const API = "https://api.maxconsulta.com";

  const els = {
    apiLabel: document.getElementById("apiLabel"),
    chatLog: document.getElementById("chatLog"),
    msg: document.getElementById("msg"),
    btnSend: document.getElementById("btnSend"),
    btnNewChat: document.getElementById("btnNewChat"),
    tone: document.getElementById("tone"),
    internetFallback: document.getElementById("internetFallback"),
    explicitUrl: document.getElementById("explicitUrl"),
    templateKind: document.getElementById("templateKind"),
    templateSelect: document.getElementById("templateSelect"),
    btnGenerate: document.getElementById("btnGenerate"),
    genResult: document.getElementById("genResult"),
  };

  if (els.apiLabel) els.apiLabel.textContent = API;

  let selectedCollection = "";

  function esc(t) {
    return String(t || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
  const history = [];
  const templates = { docx: [], xlsx: [] };
  let lastChatPayload = null;
  let lastDraftText = "";

  document.querySelectorAll(".seg-btn[data-collection]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn[data-collection]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      selectedCollection = btn.dataset.collection || "";
    });
  });

  function render() {
    const lines = history.map((h) => {
      const who = h.role === "user" ? "Tú" : "Asistente";
      const meta = h.meta ? `\n${h.meta}` : "";
      return `【${who}】\n${h.content}${meta}\n`;
    });
    els.chatLog.textContent = lines.join("\n");
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  async function loadTemplates() {
    try {
      const [docxRes, xlsxRes] = await Promise.all([
        fetch(`${API}/documents/templates`),
        fetch(`${API}/spreadsheets/templates`),
      ]);

      templates.docx = docxRes.ok ? await docxRes.json() : [];
      templates.xlsx = xlsxRes.ok ? await xlsxRes.json() : [];
      fillTemplateSelect();
    } catch {
      templates.docx = [];
      templates.xlsx = [];
      fillTemplateSelect();
    }
  }

  function fillTemplateSelect() {
    const kind = els.templateKind?.value || "none";
    els.templateSelect.innerHTML = "";

    const optNone = document.createElement("option");
    optNone.value = "";
    optNone.textContent = "Selecciona plantilla";
    els.templateSelect.appendChild(optNone);

    if (kind === "docx") {
      templates.docx.forEach((t) => {
        const o = document.createElement("option");
        o.value = t.id;
        o.textContent = t.title;
        els.templateSelect.appendChild(o);
      });
    } else if (kind === "xlsx") {
      templates.xlsx.forEach((t) => {
        const o = document.createElement("option");
        o.value = t.id;
        o.textContent = t.title;
        els.templateSelect.appendChild(o);
      });
    }
  }

  function buildPayload(userText) {
    const tone = els.tone?.value || "neutro";
    const explicitUrl = (els.explicitUrl?.value || "").trim();

    return {
      message: userText,
      question: userText,
      url: explicitUrl || null,
      history: history.slice(-10).map((h) => ({ role: h.role, content: h.content })),
      collection: selectedCollection || null,
      tone,
      top_k: 6,
      internet: true,
    };
  }

  async function send() {
    const text = (els.msg?.value || "").trim();
    if (!text) return;

    const wordIntent = /(pas(a|ame)lo.*(word|docx)|dame(lo)?\s+en\s+(word|docx)|en\s+word\s+para\s+descargar)/i.test(text);
    if (wordIntent) {
      history.push({ role: "user", content: text });
      els.msg.value = "";
      render();

      if (!lastDraftText) {
        history.push({ role: "assistant", content: "No tengo un borrador previo. Primero pide una respuesta y luego dime 'pásamelo a Word'." });
        render();
        return;
      }

      try {
        const res = await fetch(`${API}/documents/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            template_id: "doc_generico",
            data: { title: "MaxConsulta - Respuesta", body: lastDraftText },
          }),
        });
        const out = await res.json();
        if (!res.ok) {
          history.push({ role: "assistant", content: `No pude generar Word: ${res.status}` });
          render();
          return;
        }
        const dl = /^https?:\/\//.test(out.download_url) ? out.download_url : `${API}${out.download_url}`;
        history.push({ role: "assistant", content: `Documento Word generado. Enlace: ${dl}` });
        render();
        return;
      } catch (e) {
        history.push({ role: "assistant", content: `No pude generar Word: ${String(e)}` });
        render();
        return;
      }
    }

    history.push({ role: "user", content: text });
    els.msg.value = "";
    render();

    history.push({ role: "assistant", content: "Pensando…" });
    render();

    const payload = buildPayload(text);

    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const raw = await res.text();
      let data;
      try {
        data = JSON.parse(raw);
      } catch {
        data = { raw };
      }

      history.pop();

      if (!res.ok) {
        history.push({ role: "assistant", content: `Error API: ${res.status}\n${JSON.stringify(data, null, 2)}` });
        render();
        return;
      }

      const meta = [
        `rag_sufficient=${data.rag_sufficient}`,
        `internet_used=${data.internet_used}`,
        `internet_reason=${data.internet_reason}`,
        `url_used=${Array.isArray(data.url_used) ? data.url_used.join(',') : (data.url_used || '-')}`,
      ].join(" | ");

      let content = (data.answer || "Sin respuesta").trim();
      const webSources = data.internet_sources || [];
      let sourcesHtml = "";
      if (webSources.length) {
        const items = webSources.slice(0, 3).map((s) => {
          const t = esc(s.title || s.url);
          const u = esc(s.url || "");
          const sn = esc((s.snippet || "").slice(0, 200));
          return `<li style="margin-bottom:8px;"><a href="${u}" target="_blank" rel="noopener">${t}</a><div class="muted">${sn}</div></li>`;
        }).join("");
        sourcesHtml = `<div style="margin-top:10px;"><strong>Fuentes oficiales consultadas</strong><ol style="margin:6px 0 0 18px;">${items}</ol></div>`;
      }

      if (data.needs_data && Array.isArray(data.fields_required) && data.fields_required.length) {
        const miss = data.fields_required.map((f) => `<li>${esc(f)}</li>`).join("");
        sourcesHtml += `<div style="margin-top:10px;"><strong>Faltan datos:</strong><ul style="margin:6px 0 0 18px;">${miss}</ul></div>`;
      }

      if (data.download_url) {
        const dl = /^https?:\/\//.test(data.download_url) ? data.download_url : `${API}${data.download_url}`;
        sourcesHtml += `<div style="margin-top:10px;"><a class="primary" href="${esc(dl)}" target="_blank" rel="noopener">Descargar Word (.docx)</a></div>`;
      }

      history.push({ role: "assistant", content, meta, sourcesHtml });
      render();

      lastChatPayload = data;
      lastDraftText = (data.draft_text || data.answer || "").trim();
    } catch (e) {
      history.pop();
      history.push({ role: "assistant", content: `Error de red / CORS: ${String(e)}` });
      render();
    }
  }

  async function generate() {
    if (!lastChatPayload) {
      els.genResult.textContent = "Primero haz una consulta en el chat.";
      return;
    }

    const kind = els.templateKind?.value || "none";
    const templateId = els.templateSelect?.value || "";

    if (kind === "none" || !templateId) {
      els.genResult.textContent = "Selecciona tipo y plantilla.";
      return;
    }

    const data = { ...(lastChatPayload.extracted_fields || {}) };
    delete data._template_id;

    try {
      let endpoint;
      let body;
      if (kind === "docx") {
        endpoint = `${API}/documents/generate`;
        body = { template_id: templateId, data, draft_text: lastChatPayload.draft_text || null };
      } else {
        endpoint = `${API}/spreadsheets/generate`;
        body = { template_id: templateId, data, draft_text: lastChatPayload.draft_text || null };
      }

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const txt = await res.text();
      const out = JSON.parse(txt);

      if (!res.ok) {
        els.genResult.textContent = `Error: ${res.status} ${txt}`;
        return;
      }

      const url = `${API}${out.download_url}`;
      els.genResult.innerHTML = `<a href="${url}" target="_blank" rel="noopener">Descargar archivo generado</a>`;
    } catch (e) {
      els.genResult.textContent = `Error generando: ${String(e)}`;
    }
  }

  els.btnSend?.addEventListener("click", send);
  els.msg?.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") send();
  });

  els.btnNewChat?.addEventListener("click", () => {
    history.length = 0;
    lastChatPayload = null;
    els.chatLog.textContent = "";
    els.msg.value = "";
    els.genResult.textContent = "";
  });

  els.templateKind?.addEventListener("change", fillTemplateSelect);
  els.btnGenerate?.addEventListener("click", generate);

  loadTemplates();
})();
