remote_host := "horchata"
remote_dir := env_var_or_default("ANDES_REMOTE_DIR", "/home/cjh16/andes-app")
local_dir := justfile_directory()
user := env_var_or_default("ANDES_REMOTE_USER", "cjh16")
remote_fish := env_var_or_default("ANDES_FISH", "/home/cjh16/.local/bin/fish")
excludes := "--exclude-from=" + local_dir + "/.rsyncignore"

backend := local_dir + "/backend"
web := local_dir + "/web"
python := "uv run python"
npm := "env -u NPM_CONFIG_TMP -u npm_config_tmp npm"
npx := "env -u NPM_CONFIG_TMP -u npm_config_tmp npx"

# List available commands
default:
    just --list

# Copy the root env template if .env does not exist.
env:
    [ -f .env ] || cp .env.example .env

# Install backend and frontend dependencies.
setup: setup-backend setup-web install-firefox

# Install backend dependencies.
setup-backend:
    cd {{backend}} && uv sync

# Install frontend dependencies.
setup-web:
    cd {{web}} && {{npm}} install

# Install only the Playwright Firefox runtime.
install-firefox:
    cd {{web}} && {{npx}} playwright install firefox

# Run all backend and frontend checks.
check: test-backend lint-backend typecheck-backend typecheck-web lint-web build-web test-e2e

# Run backend tests.
test-backend:
    cd {{backend}} && {{python}} -m pytest

# Run backend lint.
lint-backend:
    cd {{backend}} && {{python}} -m ruff check .

# Run backend type checks.
typecheck-backend:
    cd {{backend}} && {{python}} -m mypy src

# Run web type checks.
typecheck-web:
    cd {{web}} && {{npm}} run typecheck

# Run web lint.
lint-web:
    cd {{web}} && {{npm}} run lint

# Build the web app.
build-web:
    cd {{web}} && {{npm}} run build

# Run Playwright admin prompt coverage in Firefox.
test-e2e:
    cd {{web}} && {{npm}} run test:e2e

# Validate configured backend data paths.
validate-data:
    cd {{backend}} && uv run andes validate-data

# Start the API locally.
api:
    cd {{backend}} && uv run andes-api

# Start the worker locally.
worker:
    cd {{backend}} && uv run andes-worker

# Start the Next dev server locally.
web:
    cd {{web}} && {{npm}} run dev

# Run backend cleanup locally.
cleanup dry_run="--dry-run":
    cd {{backend}} && uv run andes cleanup {{dry_run}}

# Open a shell in the remote project directory.
ssh:
    ssh -t {{remote_host}} 'cd {{remote_dir}} && exec $$SHELL'

# Create the remote project directory on horchata.
remote-mkdir:
    ssh {{remote_host}} 'mkdir -p {{remote_dir}}'

# Dry-run push so you can inspect what would change on horchata.
push-dry: remote-mkdir
    rsync -avzn {{local_dir}}/ {{remote_host}}:{{remote_dir}}/ {{excludes}}

# Push local code/config templates to horchata.
push: remote-mkdir
    rsync -avz {{local_dir}}/ {{remote_host}}:{{remote_dir}}/ {{excludes}}

# Push with deletion on horchata. Use push-dry first.
push-delete: remote-mkdir
    rsync -avz --delete {{local_dir}}/ {{remote_host}}:{{remote_dir}}/ {{excludes}}

# Dry-run pull so you can inspect what would change locally.
pull-dry:
    rsync -avzn {{remote_host}}:{{remote_dir}}/ {{local_dir}}/ {{excludes}}

# Pull code/config templates from horchata.
pull:
    rsync -avz {{remote_host}}:{{remote_dir}}/ {{local_dir}}/ {{excludes}}

# Install dependencies on horchata.
remote-setup: push
    ssh {{remote_host}} '{{remote_fish}} -l -c "cd {{remote_dir}} && just setup"'

# Copy .env.example to .env on horchata if needed.
remote-env: push
    ssh {{remote_host}} '{{remote_fish}} -l -c "cd {{remote_dir}} && just env"'

# Run all checks on horchata.
remote-check:
    ssh {{remote_host}} '{{remote_fish}} -l -c "cd {{remote_dir}} && just check"'

# Run backend tests on horchata.
remote-test:
    ssh {{remote_host}} '{{remote_fish}} -l -c "cd {{remote_dir}} && just test-backend"'

# Build the web app on horchata.
remote-build-web:
    ssh {{remote_host}} '{{remote_fish}} -l -c "cd {{remote_dir}} && just build-web"'

# Run cleanup on horchata.
remote-cleanup dry_run="--dry-run":
    ssh {{remote_host}} '{{remote_fish}} -l -c "cd {{remote_dir}} && just cleanup {{dry_run}}"'

# Push, install dependencies, and run checks on horchata.
deploy-check: remote-setup remote-check
