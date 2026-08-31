SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PROFILE ?= current-install
SERVER ?= root@192.168.104.201

.PHONY: help zip verify profile port server-preflight clean-remote

help:
	@printf '%s\n' \
	  'make zip [PROFILE=current-install]  build and package exclusively on $(SERVER)' \
	  'make verify                         verify fetched artifacts offline' \
	  'make profile PROFILE=name            capture a future installed build profile' \
	  'make port PROFILE=name               test the patch series against a profile' \
	  'make server-preflight                inspect remote capacity and tools' \
	  'make clean-remote                     bounded cleanup of this project only'

zip:
	@./scripts/remote-build.sh zip '$(PROFILE)' '$(SERVER)'

verify:
	@./scripts/verify-dist.sh '$(PROFILE)'

profile:
	@./scripts/capture-profile.sh '$(PROFILE)'

port:
	@./scripts/remote-build.sh port '$(PROFILE)' '$(SERVER)'

server-preflight:
	@./scripts/remote-build.sh preflight '$(PROFILE)' '$(SERVER)'

clean-remote:
	@./scripts/remote-build.sh clean '$(PROFILE)' '$(SERVER)'

