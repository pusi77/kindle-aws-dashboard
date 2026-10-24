# Deploy-specific values (ALARM_EMAIL, AWS_PROFILE) come from .env, see .env.example.
-include .env
export

TOKEN_PARAMETER ?= /kindle-dashboard/token
REGION := eu-west-1
REQS := lambdas/renderer/requirements.txt
UV_EXPORT := uv export -q --no-dev --no-emit-project --no-header --format requirements-txt

.PHONY: install preview test lint lock synth diff deploy token render

install:
	uv sync
	npm ci

# Render docs/preview/*.png from fixtures/*.json. No AWS calls.
preview:
	uv run python scripts/preview.py

test:
	uv run pytest
	npm test

lint:
	uv run ruff check .
	uv run ruff format --check .
	npm run typecheck
	$(UV_EXPORT) | diff -q - $(REQS) >/dev/null || (echo "$(REQS) is stale: make lock" && exit 1)

# Pinned, hashed renderer deps for Lambda bundling, from uv.lock.
lock:
	uv lock
	$(UV_EXPORT) -o $(REQS)

synth:
	npx cdk synth --quiet

diff:
	npx cdk diff -c alarmEmail=$(ALARM_EMAIL) -c tokenParameter=$(TOKEN_PARAMETER)

deploy:
	npx cdk deploy -c alarmEmail=$(ALARM_EMAIL) -c tokenParameter=$(TOKEN_PARAMETER)

# Create or rotate the Kindle's token. The value goes through stdin, never the command line.
# Read it back for the Kindle's env.sh with:
#   aws ssm get-parameter --name $(TOKEN_PARAMETER) --with-decryption --query Parameter.Value --output text
token:
	openssl rand -hex 32 | tr -d '\n' | aws ssm put-parameter --region $(REGION) \
		--name $(TOKEN_PARAMETER) --type SecureString --overwrite --value file:///dev/stdin >/dev/null
	@echo "Token stored in $(TOKEN_PARAMETER). Update DASH_TOKEN on the Kindle."

# Render once now instead of waiting for the next scheduled slot (e.g. right after the first deploy).
render:
	aws lambda invoke --region $(REGION) --invocation-type Event --function-name \
		"$$(aws cloudformation describe-stacks --region $(REGION) --stack-name KindleFinopsDashboard \
		--query "Stacks[0].Outputs[?OutputKey=='RendererName'].OutputValue" --output text)" /dev/null
