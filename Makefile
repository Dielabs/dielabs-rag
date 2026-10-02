# Dielabs RAG — comandi del progetto. Uso: make <target> SOFTWARE=vllm VERSION=0.30.0
PY := .venv/bin/python
SOFTWARE ?= vllm

.PHONY: venv versions build extract corpus qdrant chunks load report search ask eval eval-answer web update update-plan

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

search:            ## ricerca su una KB, es. make search SOFTWARE=vllm VERSION=0.30.0 Q="how do I enable prefix caching"
	@test -n "$(Q)" || (echo "Serve Q=\"domanda\""; exit 1)
	$(PY) src/search.py $(SOFTWARE) $(VERSION) -- "$(Q)"

ask:               ## risposta con citazioni su una KB, es. make ask SOFTWARE=vllm VERSION=0.30.0 Q="how do I enable prefix caching"
	@test -n "$(Q)" || (echo "Serve Q=\"domanda\""; exit 1)
	$(PY) src/answer.py $(SOFTWARE) $(VERSION) -- "$(Q)"

eval:              ## valutazione della ricerca con eval/questions.yaml (gratis, tutto in locale)
	$(PY) src/evaluate.py

eval-answer:       ## valutazione di ricerca e risposta (chiama OpenRouter, costa circa 0,01 $)
	$(PY) src/evaluate.py --answer

web:               ## web GUI su http://dollaro:8095 (ADR-0009)
	$(PY) src/web.py --port 8095

update-plan:       ## cosa farebbe l'aggiornamento di un software (ADR-0010)
	$(PY) src/update.py $(SOFTWARE) --plan

update:            ## aggiorna le KB di un software: carica le versioni nuove, poi toglie le uscite (ADR-0010)
	$(PY) src/update.py $(SOFTWARE)
