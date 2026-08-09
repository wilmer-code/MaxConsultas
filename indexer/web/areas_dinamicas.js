// ============================================================
// Pestañas de Área dinámicas para MaxConsulta
// Lee /colecciones y reconstruye los botones .seg-btn del área.
// Autocontenido: no modifica app.js. Respeta data-collection y .active.
// ============================================================
(function () {
  const API = "https://api.maxconsulta.com";

  // Formatea nombre técnico -> etiqueta visible bonita
  function pretty(name) {
    const especiales = {
      "loboral": "Laboral",
      "boe": "BOE",
      "bocm": "BOCM",
      "seguridad_social": "Seg. Social",
      "estatuto_trabajadores": "Estatuto",
      "convenios_madrid": "Convenios Madrid",
      "fiscal": "Fiscal",
    };
    const key = name.toLowerCase();
    if (especiales[key]) return especiales[key];
    // genérico: reemplaza _ por espacio y capitaliza cada palabra
    return name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  async function cargarAreas() {
    const cont = document.querySelector(".segmented-area");
    if (!cont) return; // si no existe el contenedor, no hacemos nada

    let cols = [];
    try {
      const r = await fetch(`${API}/colecciones`);
      const data = await r.json();
      cols = (data && data.colecciones) ? data.colecciones : [];
    } catch (e) {
      // si falla, dejamos las pestañas que ya están en el HTML
      console.warn("No se pudieron cargar colecciones dinámicas:", e);
      return;
    }

    // recordar cuál estaba activa (por si ya había selección)
    const activaPrev = cont.querySelector(".seg-btn.active");
    const collActiva = activaPrev ? (activaPrev.getAttribute("data-collection") || "") : "";

    // reconstruir: "Todas" fijo + una por colección real
    let html = '<button class="seg-btn" data-collection="" type="button">Todas</button>';
    for (const c of cols) {
      html += `<button class="seg-btn" data-collection="${c.name}" type="button">${pretty(c.name)}</button>`;
    }
    cont.innerHTML = html;

    // restaurar activa (o "Todas" por defecto)
    let restaurada = false;
    cont.querySelectorAll(".seg-btn").forEach(btn => {
      if ((btn.getAttribute("data-collection") || "") === collActiva) {
        btn.classList.add("active");
        restaurada = true;
      }
    });
    if (!restaurada) {
      const primera = cont.querySelector('.seg-btn[data-collection=""]');
      if (primera) primera.classList.add("active");
    }

    // re-enganchar el click (mismo comportamiento que el HTML original:
    // marcar active exclusivo). app.js lee data-collection de la activa.
    cont.querySelectorAll(".seg-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        cont.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", cargarAreas);
  } else {
    cargarAreas();
  }
})();
