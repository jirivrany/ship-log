.PHONY: test verify verify-down

test:
	python -m pytest -q

# Spins up a SEPARATE container (service "verify", not "app") against a
# throwaway copy of the live DB/uploads, so manual/exploratory poking
# (curl, browser) never touches real data or the real dev container.
# Runs on port 8001, independent of `docker compose up` on 8000.
# The copy and cleanup both run inside containers, since data/ is
# root-owned (written by the app container) and not writable from the host.
verify:
	docker run --rm \
		-v "$(PWD)/data:/src:ro" \
		-v "$(PWD)/data-verify:/dst" \
		alpine sh -c "rm -rf /dst/* && cp -r /src/. /dst/"
	docker compose -f docker-compose.verify.yml up -d
	@echo "Verify environment running: http://localhost:8001 (isolated copy in ./data-verify)"

verify-down:
	docker compose -f docker-compose.verify.yml down
	docker run --rm -v "$(PWD)/data-verify:/dst" alpine sh -c "rm -rf /dst/*"
