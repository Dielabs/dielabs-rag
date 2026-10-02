// Dielabs RAG, web GUI. Nessuna libreria: la pagina legge gli eventi NDJSON di /api/ask
// (sources, thinking, token, done, error) e ridisegna la risposta mentre arriva.

const $ = (id) => document.getElementById(id);
const selSoftware = $("software"), selVersione = $("versione");
const modulo = $("modulo"), campo = $("domanda"), invia = $("invia");
const conversazione = $("conversazione"), scorrimento = $("scorrimento");
let kbs = [];
let occupato = false;

// ---------- scelta della documentazione ----------
async function caricaKb() {
  kbs = await fetch("/api/kbs").then((r) => r.json());
  selSoftware.innerHTML = kbs.map((k) => `<option value="${k.name}">${esc(k.display_name)}</option>`).join("");
  const salvato = JSON.parse(sessionStorage.getItem("kb") || "null");
  if (salvato && kbs.some((k) => k.name === salvato.software)) selSoftware.value = salvato.software;
  aggiornaVersioni(salvato && salvato.version);
}
function kbCorrente() { return kbs.find((k) => k.name === selSoftware.value); }
function aggiornaVersioni(preferita) {
  const k = kbCorrente();
  selVersione.innerHTML = k.versions.map((v, i) =>
    `<option value="${v}">${v}${i === 0 ? " (ultima)" : ""}</option>`).join("");
  if (preferita && k.versions.includes(preferita)) selVersione.value = preferita;
  ricordaKb();
}
function ricordaKb() {
  try { sessionStorage.setItem("kb", JSON.stringify({ software: selSoftware.value, version: selVersione.value })); } catch (e) {}
  const inv = $("kb-invito");
  if (inv) inv.textContent = `${kbCorrente().display_name} ${selVersione.value}`;
}
selSoftware.addEventListener("change", () => aggiornaVersioni());
selVersione.addEventListener("change", ricordaKb);

// ---------- markdown essenziale (titoli, elenchi annidati, tabelle, codice, citazioni) ----------
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function inline(t) {
  const codici = [];
  t = t.replace(/`([^`\n]+)`/g, (_, c) => { codici.push(c); return `\u0000${codici.length - 1}\u0000`; });
  t = esc(t);
  t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  t = t.replace(/\[(\d{1,2})\](?!\()/g, '<a class="cit" href="#" data-n="$1">$1</a>');
  t = t.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[\s(])\*([^*\s][^*\n]*?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  t = t.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codici[+i])}</code>`);
  return t;
}
function riga_tabella(l) {
  return l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}
function blocchi(testo) {
  const righe = testo.split("\n");
  let html = "", para = [], pila = [];  // pila di elenchi aperti: {tipo, rientro}
  const chiudiPara = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  const chiudiElenchi = (fino = -1) => {
    while (pila.length && pila[pila.length - 1].rientro > fino) html += `</li></${pila.pop().tipo}>`;
  };
  for (let i = 0; i < righe.length; i++) {
    const l = righe[i];
    let m;
    if (!l.trim()) { chiudiPara(); continue; }
    if ((m = l.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/))) {
      chiudiPara();
      const rientro = m[1].replace(/\t/g, "    ").length, tipo = /\d/.test(m[2]) ? "ol" : "ul";
      chiudiElenchi(rientro);
      const cima = pila[pila.length - 1];
      if (cima && cima.rientro === rientro && cima.tipo === tipo) html += "</li><li>";
      else {
        if (cima && cima.rientro === rientro) { html += `</li></${pila.pop().tipo}>`; }
        html += `<${tipo}><li>`; pila.push({ tipo, rientro });
      }
      html += inline(m[3]);
      continue;
    }
    if (pila.length && /^\s+\S/.test(l) && !para.length) { html += " " + inline(l.trim()); continue; }
    chiudiElenchi();
    if ((m = l.match(/^(#{1,6})\s+(.*)$/))) { chiudiPara(); const h = m[1].length <= 2 ? 3 : 4; html += `<h${h}>${inline(m[2])}</h${h}>`; continue; }
    if (/^\s*([-*_])\1{2,}\s*$/.test(l)) { chiudiPara(); html += "<hr>"; continue; }
    if (/^\s*>/.test(l)) { chiudiPara(); html += `<blockquote>${inline(l.replace(/^\s*>\s?/, ""))}</blockquote>`; continue; }
    if (/^\s*\|/.test(l) && i + 1 < righe.length && /^\s*\|?\s*:?-{2,}/.test(righe[i + 1])) {
      chiudiPara();
      const testa = riga_tabella(l); i += 1;
      let corpo = "";
      while (i + 1 < righe.length && /^\s*\|/.test(righe[i + 1])) {
        i += 1; corpo += "<tr>" + riga_tabella(righe[i]).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
      }
      html += `<div class="tabella"><table><thead><tr>${testa.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${corpo}</tbody></table></div>`;
      continue;
    }
    para.push(l.trim());
  }
  chiudiPara(); chiudiElenchi();
  return html;
}
function markdown(src) {
  // i blocchi di codice prima di tutto; un blocco non ancora chiuso (streaming) vale fino in fondo
  const parti = src.split(/^```/m);
  let html = "";
  parti.forEach((p, i) => {
    if (i % 2 === 0) { html += blocchi(p); return; }
    const a_capo = p.indexOf("\n");
    const codice = a_capo === -1 ? "" : p.slice(a_capo + 1).replace(/\n$/, "");
    html += `<pre><code>${esc(codice)}</code></pre>`;
  });
  return html;
}

// ---------- un giro di domanda e risposta ----------
function nuovoGiro(q, kb) {
  const vuoto = $("vuoto");
  if (vuoto) { vuoto.classList.add("esce"); setTimeout(() => vuoto.remove(), 350); }
  const giro = document.createElement("article");
  giro.className = "giro";
  giro.innerHTML = `<p class="domanda">${esc(q)}<span class="kb">${esc(kb)}</span></p>
    <p class="stato">Cerco nella documentazione</p>
    <div class="risposta"></div>`;
  conversazione.appendChild(giro);
  requestAnimationFrame(() => scorrimento.scrollTo({ top: giro.offsetTop - 48, behavior: "smooth" }));
  return giro;
}

function disegnaFonti(giro, sezioni) {
  const host = (u) => { try { const x = new URL(u); return x.host + x.pathname; } catch (e) { return u; } };
  const box = document.createElement("section");
  box.className = "fonti";
  box.innerHTML = `<h2>Fonti</h2><ol>${sezioni.map((s) => `
    <li class="fonte" data-n="${s.n}">
      <span class="num">${s.n}</span>
      <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.page)}</a>
      <span class="sezione">${esc(s.section)}${s.truncated ? ", tagliata" : ""}, ${esc(host(s.url))}</span>
    </li>`).join("")}</ol>`;
  return box;
}

async function chiedi(q) {
  const kb = kbCorrente();
  const software = kb.name, version = selVersione.value;
  const giro = nuovoGiro(q, `${kb.display_name} ${version}`);
  const stato = giro.querySelector(".stato"), risposta = giro.querySelector(".risposta");
  let testo = "", sezioni = [], fonti = null, inAttesa = false;

  const ridisegna = () => { inAttesa = false; risposta.innerHTML = markdown(testo); };

  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q, software, version }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.error || `Il server ha risposto ${r.status}.`);
    }
    const lettore = r.body.getReader(), dec = new TextDecoder();
    let resto = "";
    for (;;) {
      const { value, done } = await lettore.read();
      if (done) break;
      resto += dec.decode(value, { stream: true });
      let a;
      while ((a = resto.indexOf("\n")) >= 0) {
        const riga = resto.slice(0, a).trim(); resto = resto.slice(a + 1);
        if (!riga) continue;
        const ev = JSON.parse(riga);
        if (ev.type === "sources") {
          sezioni = ev.sections;
          stato.textContent = `Leggo ${sezioni.length} ${sezioni.length === 1 ? "sezione" : "sezioni"}`;
        } else if (ev.type === "thinking") {
          stato.textContent = "Il modello sta ragionando";
        } else if (ev.type === "token") {
          if (stato.isConnected) { stato.remove(); risposta.classList.add("scrive"); }
          testo += ev.text;
          if (!inAttesa) { inAttesa = true; requestAnimationFrame(ridisegna); }
        } else if (ev.type === "done") {
          stato.remove(); risposta.classList.remove("scrive");
          if (ev.empty) testo = "_Il modello non ha restituito testo. Riprova la domanda._";
          ridisegna();
          fonti = disegnaFonti(giro, sezioni);
          fonti.querySelectorAll(".fonte").forEach((li) => {
            if (!ev.cited.includes(+li.dataset.n)) li.classList.add("non-citata");
          });
          giro.appendChild(fonti);
          const costo = ev.cost_usd != null ? `${ev.cost_usd.toFixed(4).replace(".", ",")} $` : "costo n/d";
          giro.insertAdjacentHTML("beforeend",
            `<p class="meta">${esc(ev.provider || "provider n/d")}, ${esc(ev.quantization || "precisione n/d")}, ${costo}, ${String(ev.seconds).replace(".", ",")} s</p>`);
        } else if (ev.type === "error") {
          throw new Error(ev.message);
        }
      }
    }
  } catch (err) {
    if (stato.isConnected) stato.remove();
    risposta.classList.remove("scrive");
    giro.insertAdjacentHTML("beforeend", `<p class="errore">Risposta interrotta: ${esc(err.message)}. Il dettaglio è nel log del server.</p>`);
  }
}

// citazione nel testo -> illumina la fonte corrispondente nello stesso giro
conversazione.addEventListener("click", (e) => {
  const a = e.target.closest("a.cit");
  if (!a) return;
  e.preventDefault();
  const li = a.closest(".giro").querySelector(`.fonte[data-n="${a.dataset.n}"]`);
  if (!li) return;
  li.scrollIntoView({ block: "nearest", behavior: "smooth" });
  li.classList.add("illuminata");
  setTimeout(() => li.classList.remove("illuminata"), 1400);
});

// ---------- compositore ----------
function adatta() { campo.style.height = "auto"; campo.style.height = Math.min(campo.scrollHeight, 192) + "px"; }
campo.addEventListener("input", adatta);
campo.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); modulo.requestSubmit(); }
});
modulo.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = campo.value.trim();
  if (!q || occupato) return;
  occupato = true; invia.disabled = true;
  campo.value = ""; adatta();
  await chiedi(q);
  occupato = false; invia.disabled = false; campo.focus();
});

caricaKb().then(() => campo.focus()).catch(() => {
  conversazione.innerHTML = '<p class="errore">Non riesco a leggere l\'elenco della documentazione. Il server è acceso?</p>';
});
