import OpenAI from "openai";
import { createClient } from "@supabase/supabase-js";

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const sb = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);

const TOP_K = Number(process.env.DEFAULT_TOP_K || 8);
const MIN_SIM = Number(process.env.MIN_SIMILARITY || 0.80);
const ALLOWED = new Set((process.env.ALLOWED_USER_IDS || "").split(",").map(s => s.trim()).filter(Boolean));

async function telegramSend(chatId, text) {
  const url = `https://api.telegram.org/bot${process.env.BOT_TOKEN}/sendMessage`;
  await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text })
  });
}

async function embedQuery(q) {
  const resp = await openai.embeddings.create({
    model: process.env.EMBED_MODEL,
    input: q
  });
  return resp.data[0].embedding;
}

function buildMessages(question, matches) {
  const sources = matches.map((m, i) => {
    return `[#${i+1}] ${m.title} | ${m.source || "N/D"} | pág ${m.page_start ?? "?"}\n${m.content}`;
  }).join("\n\n");

  return [
    {
      role: "system",
      content:
`Eres un asistente experto en gestoría.
Responde SOLO con la información presente en FUENTES.
Si no hay información suficiente, di: "No lo encuentro en mi documentación actual."
Incluye siempre al final un bloque "Fuentes" con [#] y página.`
    },
    { role: "user", content: `Pregunta: ${question}\n\nFUENTES:\n${sources}` }
  ];
}

export default async function handler(req, res) {
  try {
    if (req.method !== "POST") return res.status(200).send("OK");

    const update = req.body;
    const msg = update.message;
    if (!msg?.text) return res.status(200).send("OK");

    const chatId = msg.chat.id;
    const userId = String(msg.from?.id || "");
    if (ALLOWED.size && !ALLOWED.has(userId)) return res.status(200).send("OK");

    const question = msg.text.trim();
    await telegramSend(chatId, "Buscando en la documentación…");

    const qEmb = await embedQuery(question);
    const { data, error } = await sb.rpc("match_chunks", {
      query_embedding: qEmb,
      match_count: TOP_K,
      filter_collection: null
    });
    if (error) throw error;

    const matches = (data || []).filter(m => (m.similarity ?? 0) >= MIN_SIM);
    if (!matches.length) {
      await telegramSend(chatId, "No lo encuentro en mi documentación actual.\n\nPrueba a subir un PDF/BOE relacionado o dime en qué área (colección) debería buscar.");
      return res.status(200).send("OK");
    }

    const completion = await openai.chat.completions.create({
      model: process.env.CHAT_MODEL,
      temperature: 0.1,
      messages: buildMessages(question, matches.slice(0, 8))
    });

    const answer = completion.choices[0].message.content || "";
    await telegramSend(chatId, answer.slice(0, 3800));

    return res.status(200).send("OK");
  } catch (e) {
    console.error(e);
    return res.status(200).send("OK");
  }
}