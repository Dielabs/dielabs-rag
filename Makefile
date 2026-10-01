# Dielabs RAG — comandi del progetto. Uso: make <target> SOFTWARE=vllm VERSION=0.30.0
PY := .venv/bin/python
SOFTWARE ?= vllm

.PHONY: venv versions build extract corpus qdrant chunks load report

venv:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

versions:          ## versioni da tenere secondo la scheda
	$(PY) src/sources.py $(SOFTWARE)

build:             ## build MkDocs in Docker di una versione
	@test -n "$(VERSION)" || (echo "Serve VERSION=..."; exit 1)
	$(PY) src/build.py $(SOFTWARE) $(VERSION)

extract:           ## estrazione delle pagine dal sito costruito
	@test -n "$(VERSION)" || (echo "Serve VERSION=..."; exit 1)
	$(PY) src/extract.py $(SOFTWARE) $(VERSION)

corpus: build extract   ## build + estrazione di una versione

qdrant:            ## avvia Qdrant in Docker (ADR-0006)
	docker compose up -d qdrant

chunks:            ## taglio in pezzi di una versione (ADR-0007)
	@test -n "$(VERSION)" || (echo "Serve VERSION=..."; exit 1)
	$(PY) src/chunk.py $(SOFTWARE) $(VERSION)

load:              ## carica i pezzi di una versione in Qdrant (ADR-0007)
	@test -n "$(VERSION)" || (echo "Serve VERSION=..."; exit 1)
	$(PY) src/load.py $(SOFTWARE) $(VERSION)

report:            ## report delle differenze tra due versioni (ADR-0008), es. A=0.29.0 B=0.30.0
	@test -n "$(A)" -a -n "$(B)" || (echo "Servono A=... e B=..."; exit 1)
	$(PY) src/report.py $(SOFTWARE) $(A) $(B)
