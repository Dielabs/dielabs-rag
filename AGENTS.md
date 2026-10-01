# Dielabs RAG — repo di codice

Contesto, regole, decisioni (ADR) e stato del progetto **non stanno qui**: stanno nel vault Obsidian di Diego,
cartella `94_Dielabs_RAG_Project/`. Leggere prima `00_MOC — Dielabs RAG`, `06_Stato progetto`, `02_Regole`, `09_Scartate`.

Qui: codice, schede dei software (`sources/`), Makefile. I dati prodotti stanno in `data/` (fuori da git, ricostruibili).

- `make versions SOFTWARE=vllm` — versioni da tenere secondo la scheda
- `make corpus SOFTWARE=vllm VERSION=0.30.0` — build MkDocs in Docker + estrazione in `data/corpus/<software>/<versione>/pages.jsonl`

Prosa in italiano, codice e identificatori in inglese. Commit piccoli, messaggio in italiano.
