from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compose_passes_preview_digest_settings():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'ANDES_PREVIEW_DIGEST_SECRET: "${ANDES_PREVIEW_DIGEST_SECRET:-}"' in compose
    assert (
        'ANDES_PREVIEW_DIGEST_TTL_SECONDS: "${ANDES_PREVIEW_DIGEST_TTL_SECONDS:-900}"'
        in compose
    )


def test_env_example_leaves_compose_original_root_blank():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "ANDES_ORIGINAL_ROOT=\n" in env_example
    assert "ANDES_ORIGINAL_ROOT=/path/to/ANDES" not in env_example
