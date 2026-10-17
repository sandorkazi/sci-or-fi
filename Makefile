FRANCHISE ?= star-trek
DATE      ?= $(shell date +%F)
RUBRIC    ?= rubric-v2

.PHONY: help build ingest signals score render validate clean tree

help:
	@echo "make build                  ingest -> signals -> score -> render -> validate"
	@echo "make FRANCHISE=star-trek score"
	@echo "make RUBRIC=rubric-v1 score   compare rubric versions against the same measurements"

build: ingest signals score render validate

ingest:
	python3 -m sciorfi ingest --franchise $(FRANCHISE)

signals:
	python3 -m sciorfi signals --franchise $(FRANCHISE)

score:
	python3 -m sciorfi score --franchise $(FRANCHISE) --rubric $(RUBRIC)

render:
	python3 -m sciorfi render --franchise $(FRANCHISE) --rubric $(RUBRIC)

validate:
	python3 -m sciorfi validate --franchise $(FRANCHISE) --rubric $(RUBRIC)

tree:
	@find data sciorfi rubric -type f | sort

clean:
	rm -rf cache data/derived
