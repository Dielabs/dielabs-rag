# Dielabs RAG — comandi del progetto. Uso: make <target> SOFTWARE=vllm VERSION=0.30.0
PY := .venv/bin/python
SOFTWARE ?= vllm

.PHONY: venv versions build extract corpus qdrant

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
