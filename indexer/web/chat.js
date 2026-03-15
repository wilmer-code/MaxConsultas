(() => {
  const API = "https://api.maxconsulta.com";

  const els = {
    apiLabel: document.getElementById("apiLabel"),
    chatLog: document.getElementById("chatLog"),
    msg: document.getElementById("msg"),
    btnSend: document.getElementById("btnSend"),
    btnNewChat: document.getElementById("btnNewChat"),
    tone: document.getElementById("tone"),
  };

  if (els.apiLabel) els.apiLabel.textContent = API;

  let selectedCollection = ""; // "" = todas
  const history = []; // {role:"user"|"assistant", content:string}

  document.querySelectorAll(".seg-btn[data-collection]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn[data-collection]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      selectedCollection = btn.dataset.collection || "";
    });
  });

  function render() {
    const lines = history.map(h => {
      const who = h.role === "user" ? "Tú" : "Asistente";
      return `【${who}】\n${h.content}\n`;
    });
    els.chatLog.textContent = lines.join("\n");
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  function buildPrompt(userText) {
    const tone = els.tone?.value || "neutro";
    const toneText = tone === "formal"
      ? "Mantén tono formal, profesional y propio de gestoría."
      : "Mantén tono neutro, profesional y cercano.";

    const recent = history.slice(-10).map(h => `${h.role.toUpperCase()}: ${h.content}`).join("\n");
    return `CONVERSACIÓN (reciente):\n${recent}\n\nMENSAJE DEL USUARIO:\n${userText}\n\nInstrucciones: Responde como asistente de gestoría. ${toneText}`;
  }

  async function send() {
    const text = (els.msg?.value || "").trim();
    if (!text) return;

    history.push({ role: "user", content: text });
    els.msg.value = "";
    render();

    // placeholder
    history.push({ role: "assistant", content: "Pensando…" });
    render();

    const question = buildPrompt(text);
    const collection = selectedCollection ? selectedCollection : null;
    const top_k = 6;

    try {
      const res = await fetch(`${API}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, collection, top_k }),
      });

      const raw = await res.text();
      let data;
      try { data = JSON.parse(raw); } catch { data = { raw }; }

      history.pop();

      if (!res.ok) {
        history.push({ role: "assistant", content: `Error API: ${res.status}\n${JSON.stringify(data, null, 2)}` });
        render();
        return;
      }

      history.push({ role: "assistant", content: (data.answer || "Sin respuesta").trim() });
      render();

    } catch (e) {
      history.pop();
      history.push({ role: "assistant", content: `Error de red / CORS: ${String(e)}` });
      render();
    }
  }

  els.btnSend?.addEventListener("click", send);
  els.msg?.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") send();
  });

  els.btnNewChat?.addEventListener("click", () => {
    history.length = 0;
    els.chatLog.textContent = "";
    els.msg.value = "";
  });
})();
