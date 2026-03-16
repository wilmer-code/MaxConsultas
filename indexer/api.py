import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI
from openpyxl import Workbook
from pydantic import BaseModel
from supabase import create_client
from docx import Document

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"].strip()

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
TOP_K = int(os.getenv("TOP_K", "6"))

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# CORS (solo una vez)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "https://maxconsulta.com",
        "https://app.maxconsulta.com",
        "https://api.maxconsulta.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOCS_DIR = Path("/tmp/maxconsulta_docs")
SHEETS_DIR = Path("/tmp/maxconsulta_sheets")
DOCS_DIR.mkdir(parents=True, exist_ok=True)
SHEETS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWLIST = {
    "boe.es", "www.boe.es",
    "sepe.es", "www.sepe.es",
    "seg-social.es", "www.seg-social.es",
    "borm.es", "www.borm.es",
    "bocm.es", "www.bocm.es",
    "comunidad.madrid", "www.comunidad.madrid",
}

RAG_SIM_THRESHOLD = 0.72


class AskIn(BaseModel):
    question: str
    collection: str | None = None
    top_k: int | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    message: str | None = None
    question: str | None = None
    history: list[ChatMessage] = []
    collection: str | None = None
    area: str | None = None
    tone: str | None = "neutro"
    top_k: int | None = 6
    internet: bool = True
    url: str | None = None


class DocumentGenerateIn(BaseModel):
    template_id: str
    data: dict = {}
    draft_text: str | None = None


class SpreadsheetGenerateIn(BaseModel):
    template_id: str
    data: dict = {}
    draft_text: str | None = None


REQUIRED_BY_TEMPLATE = {
    "carta_despido_objetivo": [
        "empresa_nombre", "empresa_cif", "empresa_domicilio",
        "trabajador_nombre", "trabajador_dni", "fecha_efectos", "causa", "localidad_fecha_firma",
    ],
    "gastos_autonomo_basico": ["periodo"],
}


def _render_docx_carta_despido(data: dict, draft_text: str | None) -> Document:
    doc = Document()
    doc.add_heading("Carta de despido objetivo", level=1)
    doc.add_paragraph(f"Empresa: {data.get('empresa_nombre', '')}")
    doc.add_paragraph(f"CIF: {data.get('empresa_cif', '')}")
    doc.add_paragraph(f"Domicilio: {data.get('empresa_domicilio', '')}")
    doc.add_paragraph("")
    doc.add_paragraph(f"Trabajador/a: {data.get('trabajador_nombre', '')}")
    doc.add_paragraph(f"DNI/NIE: {data.get('trabajador_dni', '')}")
    doc.add_paragraph("")
    doc.add_paragraph(f"Fecha de efectos: {data.get('fecha_efectos', '')}")
    doc.add_paragraph("Motivo / causa:")
    doc.add_paragraph(data.get("causa", ""))
    doc.add_paragraph("")
    doc.add_paragraph(f"Localidad y fecha: {data.get('localidad_fecha_firma', '')}")
    doc.add_paragraph("Firma: ______________________________")
    if draft_text:
        doc.add_paragraph("")
        doc.add_heading("Borrador", level=2)
        doc.add_paragraph(draft_text)
    return doc


def _render_docx_reclamacion_previa_ss(data: dict, draft_text: str | None) -> Document:
    doc = Document()
    doc.add_heading("Reclamación previa Seguridad Social", level=1)
    doc.add_paragraph(f"Interesado/a: {data.get('interesado_nombre', '')}")
    doc.add_paragraph(f"DNI/NIE: {data.get('interesado_dni', '')}")
    doc.add_paragraph(f"Expediente: {data.get('expediente', '')}")
    doc.add_paragraph(f"Hechos: {data.get('hechos', '')}")
    doc.add_paragraph(f"Solicitud: {data.get('solicitud', '')}")
    if draft_text:
        doc.add_paragraph("")
        doc.add_heading("Borrador", level=2)
        doc.add_paragraph(draft_text)
    return doc


def _render_docx_solicitud_generica(data: dict, draft_text: str | None) -> Document:
    doc = Document()
    doc.add_heading("Solicitud genérica", level=1)
    doc.add_paragraph(f"Organismo destinatario: {data.get('organismo', '')}")
    doc.add_paragraph(f"Solicitante: {data.get('solicitante_nombre', '')}")
    doc.add_paragraph(f"DNI/NIE: {data.get('solicitante_dni', '')}")
    doc.add_paragraph(f"Asunto: {data.get('asunto', '')}")
    doc.add_paragraph(f"Exposición: {data.get('exposicion', '')}")
    doc.add_paragraph(f"Solicitud concreta: {data.get('peticion', '')}")
    if draft_text:
        doc.add_paragraph("")
        doc.add_heading("Borrador", level=2)
        doc.add_paragraph(draft_text)
    return doc


def _render_xlsx_gastos_autonomo(data: dict, rows: list[dict] | None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Gastos"
    ws.append(["Periodo", data.get("periodo", "")])
    ws.append([])
    ws.append(["Fecha", "Concepto", "Importe"])

    total = 0.0
    for r in rows or []:
        importe = float(r.get("importe", 0) or 0)
        total += importe
        ws.append([r.get("fecha", ""), r.get("concepto", ""), importe])

    ws.append([])
    ws.append(["TOTAL", "", total])
    return wb


def _render_xlsx_registro_horas(data: dict, rows: list[dict] | None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Registro horas"
    ws.append(["Empleado", data.get("empleado", "")])
    ws.append([])
    ws.append(["Fecha", "Entrada", "Salida", "Horas"])
    for r in rows or []:
        ws.append([r.get("fecha", ""), r.get("entrada", ""), r.get("salida", ""), r.get("horas", "")])
    return wb


DOCX_TEMPLATES = {
    "carta_despido_objetivo": {
        "id": "carta_despido_objetivo",
        "title": "Carta de despido objetivo (plantilla)",
        "description": "Carta laboral básica de despido objetivo.",
        "required_fields": REQUIRED_BY_TEMPLATE["carta_despido_objetivo"],
        "optional_fields": ["referencias_legales", "cargo_firmante"],
        "render": _render_docx_carta_despido,
    },
    "reclamacion_previa_ss": {
        "id": "reclamacion_previa_ss",
        "title": "Reclamación previa SS",
        "description": "Escrito base de reclamación previa en Seguridad Social.",
        "required_fields": ["interesado_nombre", "interesado_dni", "hechos", "solicitud"],
        "optional_fields": ["expediente"],
        "render": _render_docx_reclamacion_previa_ss,
    },
    "solicitud_generica": {
        "id": "solicitud_generica",
        "title": "Solicitud genérica",
        "description": "Plantilla universal para administración.",
        "required_fields": ["organismo", "solicitante_nombre", "solicitante_dni", "peticion"],
        "optional_fields": ["asunto", "exposicion"],
        "render": _render_docx_solicitud_generica,
    },
}

XLSX_TEMPLATES = {
    "gastos_autonomo_basico": {
        "id": "gastos_autonomo_basico",
        "title": "Gastos Autónomo Básico (plantilla)",
        "description": "Libro simple de gastos con total.",
        "required_fields": REQUIRED_BY_TEMPLATE["gastos_autonomo_basico"],
        "optional_fields": ["rows"],
        "render": _render_xlsx_gastos_autonomo,
    },
    "registro_horas": {
        "id": "registro_horas",
        "title": "Registro de horas",
        "description": "Timesheet básico por día.",
        "required_fields": ["empleado"],
        "optional_fields": ["rows"],
        "render": _render_xlsx_registro_horas,
    },
}


@app.get("/")
def root():
    return {"ok": True, "service": "api"}


@app.get("/health")
def health():
    return {"ok": True}


def embed_query(text: str):
    # embeddings API: input puede ser string o lista; aquí lo dejamos simple
    resp = client.embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding


def retrieve_chunks(query_embedding, match_count: int, collection_name: str | None):
    """
    IMPORTANTÍSIMO:
    Para evitar el error PGRST203 (overloads), llamamos SIEMPRE
    a match_chunks con la firma que incluye p_collection_name y p_storage_bucket.
    """
    payload = {
        "query_embedding": query_embedding,
        "match_count": match_count,
        "p_collection_name": collection_name,   # puede ser None
        "p_storage_bucket": None,               # forzamos firma única
    }

    res = sb.rpc("match_chunks", payload).execute()

    return res.data or []


@app.post("/ask")
def ask(inp: AskIn):
    q = (inp.question or "").strip()
    if not q:
        return {"answer": "Escribe una pregunta.", "sources": []}

    top_k = inp.top_k or TOP_K

    try:
        q_emb = embed_query(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando embedding: {e}")

    try:
        rows = retrieve_chunks(q_emb, top_k, inp.collection)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    context_blocks = []
    sources = []

    for r in rows:
        title = r.get("title") or "documento"
        p1 = r.get("page_start")
        p2 = r.get("page_end")
        content = (r.get("content") or "").strip()

        sources.append({
            "title": title,
            "page_start": p1,
            "page_end": p2,
            "similarity": float(r.get("similarity", 0) or 0),
        })

        cite = f"[{title}, pág. {p1}{'' if p1 == p2 else '-' + str(p2)}]"
        context_blocks.append(f"{cite}\n{content}")

    context = "\n\n---\n\n".join(context_blocks)

    system = (
        "Eres un asistente experto en normativa y gestión de gestoría (laboral, fiscal y seguridad social). "
        "Responde en español claro. "
        "Usa SOLO la información del CONTEXTO. "
        "Si falta información, dilo. "
        "Incluye citas al final de frases relevantes usando el formato [titulo, pág. x]."
    )

    user = f"PREGUNTA:\n{q}\n\nCONTEXTO:\n{context}"

    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        answer = resp.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en chat completion: {e}")

    return {"answer": answer, "sources": sources}


def _best_similarity(sources: list[dict]) -> float:
    vals = []
    for s in sources or []:
        sim = s.get("similarity")
        try:
            vals.append(float(sim))
        except (TypeError, ValueError):
            continue
    return max(vals) if vals else 0.0


def _extract_url(text: str) -> str | None:
    m = re.search(r"https?://[^\s)]+", text or "")
    return m.group(0) if m else None


def _is_allowed_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return host in ALLOWLIST


def _clean_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    return " ".join(soup.stripped_strings)


def _internet_fetch(url: str, question: str) -> tuple[str, list[dict]]:
    headers = {"User-Agent": "MaxConsultaBot/1.0"}
    r = requests.get(url, timeout=10, headers=headers)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else url)
    text = _clean_text_from_html(r.text)
    snippet = text[:200] + ("..." if len(text) > 200 else "")
    answer = (
        f"He consultado la fuente oficial permitida: {title}. "
        f"Resumen rápido relacionado con tu consulta '{question}':\n\n{snippet}"
    )
    return answer, [{"title": title, "url": url, "snippet": snippet}]


def _search_official_sources(query: str, area: str | None = None) -> tuple[str, list[dict], str, list[str], list[dict]]:
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return (
            "Activa SERPAPI_API_KEY en el servidor para buscar en fuentes oficiales.",
            [],
            "search_provider_missing",
            [],
            [],
        )

    merged_query = query if not area else f"{query} {area}"
    official_scope = (
        "(site:boe.es OR site:www.boe.es OR site:sepe.es OR site:www.sepe.es OR "
        "site:seg-social.es OR site:www.seg-social.es OR site:borm.es OR site:www.borm.es OR "
        "site:bocm.es OR site:www.bocm.es OR site:comunidad.madrid OR site:www.comunidad.madrid)"
    )
    q = f"{official_scope} {merged_query}"

    try:
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google", "q": q, "api_key": api_key, "num": 5},
            timeout=12,
            headers={"User-Agent": "MaxConsultaBot/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return ("Error al consultar proveedor de búsqueda oficial.", [], "search_provider_error", [], [])

    candidate_urls: list[str] = []
    for item in data.get("organic_results", [])[:5]:
        link = (item.get("link") or "").strip()
        if link and _is_allowed_url(link):
            candidate_urls.append(link)

    seen = set()
    urls = []
    for u in candidate_urls:
        if u not in seen:
            seen.add(u)
            urls.append(u)

    urls = urls[:3]
    if not urls:
        return (
            "No encontré resultados oficiales relevantes. Prueba afinando la consulta.",
            [],
            "no_official_results",
            [],
            [],
        )

    sources: list[dict] = []
    snippets: list[str] = []
    citations: list[dict] = []

    for i, u in enumerate(urls, start=1):
        try:
            headers = {"User-Agent": "MaxConsultaBot/1.0"}
            r = requests.get(u, timeout=10, headers=headers)
            r.raise_for_status()
            content_type = (r.headers.get("content-type") or "").lower()
            title = u
            snippet = "Fuente oficial consultada"

            if "pdf" in content_type or u.lower().endswith(".pdf"):
                snippet = "Documento PDF oficial (sin volcado de contenido)."
            else:
                soup = BeautifulSoup(r.text, "html.parser")
                title = (soup.title.string.strip() if soup.title and soup.title.string else u)
                text = _clean_text_from_html(r.text)[:12000]
                snippet = text[:200] + ("..." if len(text) > 200 else "")

            sources.append({"title": title, "url": u, "snippet": snippet})
            citations.append({"n": i, "url": u})
            snippets.append(f"[{i}] {title}: {snippet}")
        except Exception:
            continue

    if not sources:
        return (
            "No pude recuperar contenido útil de los resultados oficiales.",
            [],
            "no_official_results",
            urls,
            [],
        )

    urls_text = "\n".join([f"[{i}] {u}" for i, u in enumerate([s['url'] for s in sources], start=1)])
    answer = (
        "Resumen con fuentes oficiales consultadas:\n\n"
        + "\n".join(snippets)
        + "\n\nFuentes oficiales consultadas:\n"
        + urls_text
    )
    return (answer, sources, "searched_official_sources", [s["url"] for s in sources], citations)


def _detect_template(message: str) -> tuple[str | None, str | None]:
    t = (message or "").lower()
    if "despido" in t or "carta de despido" in t:
        return "docx", "carta_despido_objetivo"
    if "reclamación" in t or "reclamacion" in t or "seguridad social" in t or "inss" in t:
        return "docx", "reclamacion_previa_ss"
    if "solicitud" in t or "instancia" in t or "escrito" in t:
        return "docx", "solicitud_generica"
    if "excel" in t and ("gasto" in t or "autonom" in t):
        return "xlsx", "gastos_autonomo_basico"
    if "timesheet" in t or "registro" in t or "horas" in t or "jornada" in t:
        return "xlsx", "registro_horas"
    return None, None


def _extract_fields(history: list[ChatMessage], message: str) -> dict:
    text = "\n".join([m.content for m in history if m.role == "user"] + [message])
    out = {}
    patterns = {
        "empresa_nombre": r"empresa\s*[:\-]\s*([^\n,;]+)",
        "empresa_cif": r"cif\s*[:\-]\s*([A-Z0-9\-]+)",
        "empresa_domicilio": r"domicilio\s*[:\-]\s*([^\n;]+)",
        "trabajador_nombre": r"trabajador\s*[:\-]\s*([^\n,;]+)",
        "trabajador_dni": r"dni\s*[:\-]\s*([A-Z0-9\-]+)",
        "fecha_efectos": r"fecha de efectos\s*[:\-]\s*([^\n,;]+)",
        "causa": r"causa\s*[:\-]\s*([^\n]+)",
        "localidad_fecha_firma": r"localidad y fecha\s*[:\-]\s*([^\n,;]+)",
        "periodo": r"periodo\s*[:\-]\s*([^\n,;]+)",
        "empleado": r"empleado\s*[:\-]\s*([^\n,;]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            out[key] = m.group(1).strip()
    return out


@app.post("/chat")
def chat(inp: ChatIn):
    question = (inp.message or inp.question or "").strip()
    if not question:
        return {
            "answer": "Escribe una pregunta.",
            "sources": [],
            "rag_sufficient": False,
            "internet_used": False,
            "internet_reason": "internet_disabled",
            "internet_sources": [],
            "needs_data": False,
            "fields_required": [],
            "can_generate_doc": False,
            "doc_type": None,
            "extracted_fields": {},
            "draft_text": None,
            "url_used": None,
            "citations": [],
            "rag_override_reason": None,
        }

    history_lines = []
    for h in (inp.history or [])[-10:]:
        role = "Usuario" if h.role == "user" else "Asistente"
        history_lines.append(f"{role}: {h.content}")

    tone = inp.tone or "neutro"
    tone_instruction = "Tono formal y profesional." if tone == "formal" else "Tono neutro y profesional."

    composed = (
        f"{tone_instruction}\n"
        "No inventes datos de personas, empresa, fechas o causas.\n"
        f"HISTORIAL:\n{chr(10).join(history_lines) if history_lines else '(sin historial)'}\n\n"
        f"CONSULTA ACTUAL:\n{question}"
    )

    rag = ask(AskIn(question=composed, collection=inp.collection, top_k=inp.top_k or TOP_K))
    rag_answer = (rag.get("answer") or "").strip()
    sources = rag.get("sources") or []

    best_similarity = _best_similarity(sources)
    sources_count = len(sources)
    answer_len = len(rag_answer or "")
    rag_sufficient_base = (sources_count >= 2 and best_similarity >= 0.72) or (
        answer_len >= 400 and sources_count >= 1
    )

    ql = question.lower()
    needs_official_lookup = any(k in ql for k in [
        "texto exacto",
        "publicado hoy",
        "hoy en el boe",
        "boe de hoy",
        "enlace oficial",
        "dame enlace",
        "dame el enlace",
        "última publicación",
        "ultima publicacion",
        "vigente",
        "vigente hoy",
        "normativa vigente",
    ])

    provided_url_pre = (inp.url or _extract_url(question) or "").strip() or None
    if inp.internet and not provided_url_pre and needs_official_lookup and best_similarity < 0.72:
        rag_sufficient = False
        rag_override_reason = "needs_official_lookup_low_similarity"
    else:
        rag_sufficient = rag_sufficient_base
        rag_override_reason = None

    internet_used = False
    internet_reason = "internet_disabled"
    internet_sources: list[dict] = []
    citations: list[dict] = []
    url_used: list[str] | None = None
    answer = rag_answer

    if inp.internet:
        provided_url = provided_url_pre
        if provided_url and not _is_allowed_url(provided_url):
            internet_reason = "blocked_domain"
            answer = (answer + "\n\nBloqueado: solo fuentes oficiales permitidas.").strip()
        elif provided_url:
            try:
                web_answer, web_sources = _internet_fetch(provided_url, question)
                internet_used = True
                internet_reason = "rag_insufficient"
                internet_sources = web_sources
                citations = [{"n": 1, "url": provided_url}]
                url_used = [provided_url]
                answer = (answer + "\n\n" + web_answer + "\n\nFuentes oficiales consultadas:\n[1] " + provided_url).strip()
            except Exception:
                internet_reason = "no_official_results"
                answer = (answer + "\n\nNo pude recuperar contenido de la URL oficial indicada.").strip()
        elif rag_sufficient:
            internet_reason = "rag_sufficient"
        else:
            area = inp.area or inp.collection
            auto_answer, auto_sources, auto_reason, auto_urls, auto_citations = _search_official_sources(question, area)
            internet_reason = auto_reason
            if auto_sources:
                internet_used = True
                internet_sources = auto_sources
                url_used = auto_urls
                citations = auto_citations
                answer = (answer + "\n\n" + auto_answer).strip()
            else:
                internet_used = False
                answer = (answer + "\n\n" + auto_answer).strip()

    doc_type, template_id = _detect_template(question)
    extracted_fields = _extract_fields(inp.history or [], question)
    if template_id:
        extracted_fields["_template_id"] = template_id

    required = []
    if template_id in DOCX_TEMPLATES:
        required = DOCX_TEMPLATES[template_id]["required_fields"]
    elif template_id in XLSX_TEMPLATES:
        required = XLSX_TEMPLATES[template_id]["required_fields"]

    missing = [f for f in required if not str(extracted_fields.get(f, "")).strip()]
    needs_data = bool(template_id and missing)
    can_generate_doc = bool(template_id and not missing)

    if needs_data:
        answer = (
            "Resumen de lo entendido:\n"
            f"Quieres generar una plantilla ({template_id}).\n\n"
            "Datos necesarios:\n- " + "\n- ".join(missing) +
            "\n\nNo voy a inventar datos. Si lo prefieres, te preparo una plantilla en blanco con ____."
        )

    return {
        "answer": answer,
        "sources": sources,
        "rag_sufficient": rag_sufficient,
        "internet_used": internet_used,
        "internet_reason": internet_reason,
        "internet_sources": internet_sources,
        "citations": citations,
        "rag_override_reason": rag_override_reason,
        "needs_data": needs_data,
        "fields_required": missing,
        "can_generate_doc": can_generate_doc,
        "doc_type": doc_type,
        "extracted_fields": extracted_fields,
        "draft_text": answer if can_generate_doc else None,
        "url_used": url_used,
    }


@app.get("/documents/templates")
def list_document_templates():
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "description": t["description"],
            "required_fields": t["required_fields"],
            "optional_fields": t["optional_fields"],
        }
        for t in DOCX_TEMPLATES.values()
    ]


@app.post("/documents/generate")
def generate_document(inp: DocumentGenerateIn):
    tpl = DOCX_TEMPLATES.get(inp.template_id)
    if not tpl:
        raise HTTPException(status_code=400, detail="template_id de DOCX no soportado")

    missing = [f for f in tpl["required_fields"] if not str((inp.data or {}).get(f, "")).strip()]
    if missing:
        raise HTTPException(status_code=400, detail={"needs_data": True, "fields_required": missing})

    doc = tpl["render"](inp.data or {}, inp.draft_text)
    doc_id = str(uuid.uuid4())
    path = DOCS_DIR / f"{doc_id}.docx"
    doc.save(str(path))

    return {"doc_id": doc_id, "download_url": f"/documents/{doc_id}"}


@app.get("/documents/{doc_id}")
def download_document(doc_id: str):
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="doc_id inválido")

    path = DOCS_DIR / f"{doc_id}.docx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{doc_id}.docx",
    )


@app.get("/spreadsheets/templates")
def list_spreadsheet_templates():
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "description": t["description"],
            "required_fields": t["required_fields"],
            "optional_fields": t["optional_fields"],
        }
        for t in XLSX_TEMPLATES.values()
    ]


@app.post("/spreadsheets/generate")
def generate_spreadsheet(inp: SpreadsheetGenerateIn):
    tpl = XLSX_TEMPLATES.get(inp.template_id)
    if not tpl:
        raise HTTPException(status_code=400, detail="template_id de XLSX no soportado")

    missing = [f for f in tpl["required_fields"] if not str((inp.data or {}).get(f, "")).strip()]
    if missing:
        raise HTTPException(status_code=400, detail={"needs_data": True, "fields_required": missing})

    rows = inp.data.get("rows", []) if isinstance(inp.data, dict) else []
    wb = tpl["render"](inp.data or {}, rows)

    sheet_id = str(uuid.uuid4())
    path = SHEETS_DIR / f"{sheet_id}.xlsx"
    wb.save(str(path))

    return {"sheet_id": sheet_id, "download_url": f"/spreadsheets/{sheet_id}"}


@app.get("/spreadsheets/{sheet_id}")
def download_spreadsheet(sheet_id: str):
    try:
        uuid.UUID(sheet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="sheet_id inválido")

    path = SHEETS_DIR / f"{sheet_id}.xlsx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Spreadsheet no encontrado")

    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{sheet_id}.xlsx",
    )
