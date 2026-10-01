# Dielabs RAG — repo di codice

Contesto, regole, decisioni (ADR) e stato del progetto **non stanno qui**: stanno nel vault Obsidian di Diego,
cartella `94_Dielabs_RAG_Project/`. Leggere prima `00_MOC — Dielabs RAG`, `06_Stato progetto`, `02_Regole`, `09_Scartate`.

Qui: codice, schede dei software (`sources/`), Makefile. I dati prodotti stanno in `data/` (fuori da git, ricostruibili).

- `make versions SOFTWARE=vllm` — versioni da tenere secondo la scheda
- `make corpus SOFTWARE=vllm VERSION=0.30.0` — build MkDocs in Docker + estrazione in `data/corpus/<software>/<versione>/pages.jsonl`
- `make chunks` / `make load` (stesse variabili) — taglio in pezzi e caricamento in Qdrant (`make qdrant` per accenderlo)
- `make report SOFTWARE=vllm A=0.29.0 B=0.30.0` — report delle differenze tra due versioni in `data/reports/<software>/<A>_<B>.md`, senza LLM
- `make search SOFTWARE=vllm VERSION=0.30.0 Q="domanda"` — ricerca su una KB (ibrida + deduplica + reranker), log in `data/logs/search/`

Prosa in italiano, codice e identificatori in inglese. Commit piccoli, messaggio in italiano.
