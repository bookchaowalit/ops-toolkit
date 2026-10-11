# solo-ops — thin Makefile; the canonical commands are Task tasks (see `task --list`).
#
# Usage:
#   make setup   — copy infra/.env.example to infra/.env when missing
#   make test    — offline Python test scripts (task test)
#   make health  — operational health through the owner CLI (needs `task install`, solo-mcp sibling)
#   make lint    — hardcoded secret / stale path scan of infra/scripts
#   make clean   — remove generated state

.PHONY: setup check-env health lint lint\:secrets lint\:paths test clean

PYTHON  := python3
SCRIPTS := infra/scripts

setup: check-env
	@echo "✅ solo-ops setup complete"

check-env:
	@if [ ! -f infra/.env ]; then \
		echo "⚠️  infra/.env not found — copying from infra/.env.example"; \
		cp infra/.env.example infra/.env; \
		echo "⚠️  Edit infra/.env with your actual API tokens before running scripts"; \
	fi

health:
	@task status

lint: lint\:secrets lint\:paths

lint\:secrets:
	@echo "🔍 Scanning for hardcoded secrets in Python scripts..."
	@grep -rn 'GOCSPX-\|fc-[a-f0-9]\{32\}\|ghp_\|gho_\|sk_live\|sk_test\|xox[bpas]-\|CLIENT_ID = "4452' $(SCRIPTS)/ -r --include='*.py' --exclude=security_audit.py --exclude=scan_secrets.py && echo 'FAIL: secrets found' || echo '✅ No hardcoded secrets found'

lint\:paths:
	@echo "🔍 Scanning for hardcoded absolute paths..."
	@grep -rn '/home/bookchaowalit/' $(SCRIPTS)/ -r --include='*.py' --include='*.sh' && echo 'FAIL: stale paths' || echo '✅ No stale absolute paths found'

test:
	@task test

clean:
	rm -f data/backups/*.db.gz
	rm -rf .npm-cache/ infra/test-results
	@echo "✅ Cleaned generated/state files"
