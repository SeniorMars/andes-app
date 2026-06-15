from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import andes_api.app as app_module
from andes_api.app import create_app
from andes_core.config import AndesSettings
from andes_core.schemas import AnalysisKind, JobState

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORIGINAL_SRC = Path(
    os.environ.get("ANDES_ORIGINAL_SRC", Path.home() / "Acdemica/ylab/ANDES/src")
)


def _settings(tmp_path: Path) -> AndesSettings:
    return AndesSettings(
        original_src=ORIGINAL_SRC,
        embedding_path=FIXTURES / "mini_embedding.csv",
        gene_list_path=FIXTURES / "mini_genes.txt",
        default_gene_set_path=FIXTURES / "mini_gene_sets.gmt",
        runs_dir=tmp_path / "runs",
        sqlite_path=tmp_path / "runs" / "jobs.sqlite3",
        cache_dir=tmp_path / "cache",
    )


def _settings_with_aliases(tmp_path: Path) -> AndesSettings:
    settings = _settings(tmp_path)
    return settings.model_copy(update={"alias_path": FIXTURES / "mini_aliases.tsv"})


def _loopback_client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def _preview_token_payload(token: str) -> dict[str, object]:
    version, encoded_payload, _signature = token.split(".", 2)
    assert version == "v2"
    payload = json.loads(app_module._base64url_decode(encoded_payload))
    assert isinstance(payload, dict)
    return payload


def _assert_set_similarity_submission_recomputes_preview(
    app,
    client: TestClient,
    *,
    data: dict[str, str],
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> None:
    recompute_calls = 0

    def recompute_preview(_request):
        nonlocal recompute_calls
        recompute_calls += 1
        return {"can_submit": True}

    app.state.engine.preview_set_similarity = recompute_preview
    response = client.post("/jobs/set-similarity", data=data, files=files)

    assert response.status_code == 202
    assert recompute_calls == 1


def test_health_endpoint():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_data_status_reports_cache_without_requiring_it_for_readiness():
    client = _loopback_client(create_app())
    response = client.get("/data/status")
    assert response.status_code == 200
    payload = response.json()
    assert "cache" in payload
    assert "bma" in payload["cache"]
    assert "es" in payload["cache"]
    assert "jobs" in payload
    assert "config" in payload
    assert payload["config"]["workers"] >= 1
    assert "cache_dir" not in payload["checks"]
    assert "root" not in payload["cache"]
    assert "path" not in payload["cache"]["bma"]
    assert "path" not in payload["cache"]["es"]
    assert "sqlite_path" not in payload["jobs"]
    assert "runs_dir" not in payload["jobs"]
    assert "alias_path" not in payload["config"]


def test_local_loopback_dev_origin_is_allowed():
    client = TestClient(create_app())
    for origin in ("http://127.250.116.207:3000", "http://0.0.0.0:3000"):
        response = client.options(
            "/jobs/set-similarity",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_set_similarity_rejects_unknown_genes_before_queueing(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.post("/jobs/set-similarity", data={"genes_text": "MISSING"})

    assert response.status_code == 400
    assert "none of the input genes" in response.json()["detail"]


def test_gsea_rejects_unknown_ranked_genes_before_queueing(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.post("/jobs/gsea", data={"ranked_text": "MISSING\t1.0"})

    assert response.status_code == 400
    assert "none of the ranked genes" in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "base_data"),
    [
        ("/preview/set-similarity", {"genes_text": "A\nB"}),
        ("/jobs/set-similarity", {"genes_text": "A\nB"}),
        ("/preview/gsea", {"ranked_text": "A\t1\nB\t0"}),
        ("/jobs/gsea", {"ranked_text": "A\t1\nB\t0"}),
    ],
)
@pytest.mark.parametrize(
    ("size_fields", "detail"),
    [
        (
            {"min_gene_set_size": "0", "max_gene_set_size": "3"},
            "min_gene_set_size must be >= 1",
        ),
        (
            {"min_gene_set_size": "1", "max_gene_set_size": "0"},
            "max_gene_set_size must be >= 1",
        ),
        (
            {"min_gene_set_size": "4", "max_gene_set_size": "3"},
            "max_gene_set_size must be >= min_gene_set_size",
        ),
    ],
)
def test_analysis_form_endpoints_reject_invalid_size_ranges(
    tmp_path,
    path,
    base_data,
    size_fields,
    detail,
):
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.post(path, data={**base_data, **size_fields})

    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_runtime_form_fields_are_ignored_by_api(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/jobs/set-similarity",
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "workers": "999",
            "null_iterations": "1",
        },
    )

    assert response.status_code == 202
    payload = app.state.store.read_input(response.json()["id"])
    assert payload["workers"] is None
    assert payload["null_iterations"] is None


def test_set_similarity_preview_reports_counts_and_cache_status(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB\nMISSING", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["estimated_pair_count"] == 3
    assert payload["genes"]["matched_count"] == 2
    assert payload["genes"]["unmatched_count"] == 1
    assert payload["target_collection"]["usable_term_count"] == 3
    assert payload["cache"]["status"] in {"build", "reuse", "extend_or_rebuild"}
    assert "path" not in payload["cache"]
    assert payload["cache"]["file"]
    assert payload["cache"]["seed_strategy"] == "cache_key"
    assert isinstance(payload["cache"]["seed"], int)
    assert payload["preview_digest"]
    token_payload = _preview_token_payload(payload["preview_digest"])
    assert isinstance(token_payload["payload_hash"], str)
    assert isinstance(token_payload["expires_at"], str)
    datetime.fromisoformat(token_payload["expires_at"])


def test_matching_preview_digest_skips_submission_preview(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    preview_digest = preview.json()["preview_digest"]

    def fail_if_recomputed(_request):
        raise AssertionError("submission recomputed preview")

    app.state.engine.preview_set_similarity = fail_if_recomputed
    response = client.post(
        "/jobs/set-similarity",
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "preview_digest": preview_digest,
        },
    )

    assert preview.status_code == 200
    assert response.status_code == 202


def test_configured_preview_digest_secret_survives_process_secret_change(
    tmp_path,
    monkeypatch,
):
    settings = _settings(tmp_path).model_copy(
        update={"preview_digest_secret": "stable-preview-secret"}
    )
    app = create_app(settings)
    client = TestClient(app)

    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    preview_digest = preview.json()["preview_digest"]

    def fail_if_recomputed(_request):
        raise AssertionError("submission recomputed preview")

    monkeypatch.setattr(app_module, "_PROCESS_LOCAL_PREVIEW_DIGEST_SECRET", b"changed")
    app.state.engine.preview_set_similarity = fail_if_recomputed
    response = client.post(
        "/jobs/set-similarity",
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "preview_digest": preview_digest,
        },
    )

    assert preview.status_code == 200
    assert response.status_code == 202


def test_changed_size_range_recomputes_submission_preview(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )

    assert preview.status_code == 200
    _assert_set_similarity_submission_recomputes_preview(
        app,
        client,
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "2",
            "preview_digest": preview.json()["preview_digest"],
        },
    )


def test_changed_uploaded_file_contents_recompute_submission_preview(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={
            "gene_set_file": ("sets.gmt", b"TERM_X\tcustom\tA\tB\n", "text/plain"),
        },
    )

    assert preview.status_code == 200
    _assert_set_similarity_submission_recomputes_preview(
        app,
        client,
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "preview_digest": preview.json()["preview_digest"],
        },
        files={
            "gene_set_file": ("sets.gmt", b"TERM_Y\tcustom\tA\tB\n", "text/plain"),
        },
    )


def test_changed_default_gene_set_file_recomputes_submission_preview(tmp_path):
    default_gene_set_path = tmp_path / "default.gmt"
    default_gene_set_path.write_text(
        (FIXTURES / "mini_gene_sets.gmt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    settings = _settings(tmp_path).model_copy(
        update={"default_gene_set_path": default_gene_set_path}
    )
    app = create_app(settings)
    client = TestClient(app)

    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    default_gene_set_path.write_text(
        default_gene_set_path.read_text(encoding="utf-8")
        + "TERM_NEW\tchanged\tA\tB\n",
        encoding="utf-8",
    )

    assert preview.status_code == 200
    _assert_set_similarity_submission_recomputes_preview(
        app,
        client,
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "preview_digest": preview.json()["preview_digest"],
        },
    )


def test_expired_preview_digest_recomputes_submission_preview(tmp_path, monkeypatch):
    settings = _settings(tmp_path).model_copy(
        update={
            "preview_digest_secret": "stable-preview-secret",
            "preview_digest_ttl_seconds": 1,
        }
    )
    app = create_app(settings)
    client = TestClient(app)

    monkeypatch.setattr(app_module.time, "time", lambda: 100.0)
    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    preview_digest = preview.json()["preview_digest"]

    recompute_calls = 0

    def recompute_preview(_request):
        nonlocal recompute_calls
        recompute_calls += 1
        return {"can_submit": True}

    app.state.engine.preview_set_similarity = recompute_preview
    monkeypatch.setattr(app_module.time, "time", lambda: 102.0)
    response = client.post(
        "/jobs/set-similarity",
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "preview_digest": preview_digest,
        },
    )

    assert preview.status_code == 200
    assert response.status_code == 202
    assert recompute_calls == 1


def test_bad_preview_digest_signature_recomputes_submission_preview(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    preview_digest = preview.json()["preview_digest"]
    replacement = "0" if preview_digest[-1] != "0" else "1"
    bad_digest = f"{preview_digest[:-1]}{replacement}"

    assert preview.status_code == 200
    _assert_set_similarity_submission_recomputes_preview(
        app,
        client,
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "preview_digest": bad_digest,
        },
    )
    _assert_set_similarity_submission_recomputes_preview(
        app,
        client,
        data={
            "genes_text": "A\nB",
            "min_gene_set_size": "1",
            "max_gene_set_size": "3",
            "preview_digest": "x" * 513,
        },
    )


def test_job_limit_rejects_oversized_submission(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"max_term_pairs": 2})
    client = TestClient(create_app(settings))

    response = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "job exceeds server limits"
    assert detail["preview"]["estimated_pair_count"] == 3


def test_blocked_preview_does_not_return_digest(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"max_term_pairs": 2})
    client = TestClient(create_app(settings))

    response = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )

    assert response.status_code == 200
    assert response.json()["can_submit"] is False
    assert "preview_digest" not in response.json()


def test_job_limit_allows_admin_override(tmp_path):
    settings = _settings(tmp_path).model_copy(
        update={"max_term_pairs": 2, "allow_large_jobs": True}
    )
    client = TestClient(create_app(settings))

    preview = client.post(
        "/preview/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    response = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )

    assert preview.status_code == 200
    assert preview.json()["over_limit"] is True
    assert preview.json()["can_submit"] is True
    assert response.status_code == 202


def test_queue_position_and_cancel_endpoint(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    first = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    second = client.post(
        "/jobs/gsea",
        data={"ranked_text": "A\t1\nB\t0", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    second_id = second.json()["id"]
    second_token = second.json()["access_token"]

    job_response = client.get(
        f"/jobs/{second_id}",
        headers={"x-andes-job-token": second_token},
    )
    cancel_response = client.post(
        f"/jobs/{second_id}/cancel",
        headers={"x-andes-job-token": second_token},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert job_response.status_code == 200
    assert job_response.json()["queue"]["position"] == 2
    assert cancel_response.status_code == 200
    assert cancel_response.json()["job"]["state"] == JobState.CANCELLED.value


def test_api_created_jobs_require_access_token_for_public_reads(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"admin_token": "secret"})
    app = create_app(settings)
    client = TestClient(app)

    created = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )
    payload = created.json()
    job_id = payload["id"]
    access_token = payload["access_token"]
    app.state.store.write_result(
        job_id,
        {
            "kind": "set_similarity",
            "results": [
                {
                    "term": "TERM_A",
                    "description": "alpha",
                    "size": 2,
                    "true_score": None,
                    "z_score": 1.0,
                    "p_value": 0.01,
                    "p_value_corrected": 0.02,
                    "log10_p_value_corrected": 1.7,
                    "significant": True,
                }
            ],
            "input_gene_count": 2,
            "valid_gene_count": 2,
            "invalid_genes": [],
            "warnings": [],
            "parameters": {"mode": "gene_list"},
        },
    )
    downloads = app.state.store.run_dir(job_id) / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "results.csv").write_text("term,z_score\nTERM_A,1.0\n", encoding="utf-8")

    blocked_job = client.get(f"/jobs/{job_id}")
    blocked_results = client.get(f"/jobs/{job_id}/results")
    blocked_download = client.get(f"/jobs/{job_id}/download/results.csv")
    allowed_job = client.get(f"/jobs/{job_id}", headers={"x-andes-job-token": access_token})
    allowed_results = client.get(f"/jobs/{job_id}/results?token={access_token}")
    allowed_download = client.get(f"/jobs/{job_id}/download/results.csv?token={access_token}")
    admin_allowed = client.get(f"/jobs/{job_id}", headers={"x-andes-admin-token": "secret"})
    oversized_token = client.get(f"/jobs/{job_id}", headers={"x-andes-job-token": "x" * 513})

    assert created.status_code == 202
    assert access_token
    assert "owner_key" not in payload
    assert blocked_job.status_code == 403
    assert blocked_results.status_code == 403
    assert blocked_download.status_code == 403
    assert allowed_job.status_code == 200
    assert allowed_job.headers["cache-control"] == "no-store"
    assert "owner_key" not in allowed_job.json()["job"]
    assert allowed_results.status_code == 200
    assert allowed_results.headers["cache-control"] == "no-store"
    assert allowed_download.status_code == 200
    assert allowed_download.headers["cache-control"] == "no-store"
    assert admin_allowed.status_code == 200
    assert admin_allowed.headers["cache-control"] == "no-store"
    assert oversized_token.status_code == 403


def test_jobs_without_tokens_are_admin_only(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"admin_token": "secret"})
    app = create_app(settings)
    client = TestClient(app)
    store = app.state.store
    job = store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"], "min_gene_set_size": 1, "max_gene_set_size": 3},
        owner_key="ip:127.0.0.1",
    )
    store.write_result(job.id, {"ok": True})

    public_job = client.get(f"/jobs/{job.id}")
    public_results = client.get(f"/jobs/{job.id}/results")
    public_cancel = client.post(f"/jobs/{job.id}/cancel")
    admin_job = client.get(f"/jobs/{job.id}", headers={"x-andes-admin-token": "secret"})

    assert public_job.status_code == 403
    assert public_results.status_code == 403
    assert public_cancel.status_code == 403
    assert public_job.json()["detail"] == "job token required"
    assert admin_job.status_code == 200


def test_public_results_are_sanitized(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"admin_token": "secret"})
    app = create_app(settings)
    client = TestClient(app)
    store = app.state.store
    job = store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"]},
        access_token="job-secret",
    )
    private_runs_path = str(settings.runs_dir.resolve() / job.id / "query.gmt")
    private_cache_path = str(settings.cache_dir.resolve() / "bma" / "cache.pkl")
    private_embedding_path = str(settings.embedding_path.resolve())
    store.mark_failed(job.id, f"failed while reading {private_runs_path}")
    raw_result = {
        "kind": "set_similarity",
        "results": [],
        "input_gene_count": 1,
        "valid_gene_count": 1,
        "invalid_genes": [],
        "warnings": [
            f"ignored cached artifact {private_cache_path}",
            f"source upload {private_runs_path} had no usable genes",
            f"embedding loaded from {private_embedding_path}",
        ],
        "parameters": {
            "mode": "gene_list",
            "gene_set_path": "/srv/andes/data/private-target.gmt",
            "query_gene_set_path": "/srv/andes/runs/job/query.gmt",
            "cache": {
                "kind": "bma",
                "status": "reuse",
                "hit": True,
                "path": "/srv/andes/cache/bma/private-cache.pkl",
                "file": "private-cache.pkl",
            },
        },
    }
    store.write_result(job.id, raw_result)

    results_response = client.get(
        f"/jobs/{job.id}/results",
        headers={"x-andes-job-token": "job-secret"},
    )
    download_response = client.get(f"/jobs/{job.id}/download/results.json?token=job-secret")

    assert results_response.status_code == 200
    assert download_response.status_code == 200
    job_payload = client.get(
        f"/jobs/{job.id}",
        headers={"x-andes-job-token": "job-secret"},
    ).json()["job"]
    assert private_runs_path not in job_payload["error"]
    assert "<server-path>" in job_payload["error"]
    for payload in (results_response.json(), download_response.json()):
        parameters = payload["parameters"]
        assert "gene_set_path" not in parameters
        assert "query_gene_set_path" not in parameters
        assert parameters["gene_set_file"] == "private-target.gmt"
        assert parameters["query_gene_set_file"] == "query.gmt"
        assert "path" not in parameters["cache"]
        assert parameters["cache"]["file"] == "private-cache.pkl"
        assert private_cache_path not in " ".join(payload["warnings"])
        assert private_runs_path not in " ".join(payload["warnings"])
        assert private_embedding_path not in " ".join(payload["warnings"])
        assert all("<server-path>" in warning for warning in payload["warnings"])
    assert store.read_result(job.id) == raw_result


def test_queue_owner_limit_rejects_too_many_active_jobs(tmp_path):
    settings = _settings(tmp_path).model_copy(
        update={"max_jobs_per_owner": 1, "trusted_user_header": "x-andes-user"}
    )
    app = create_app(settings)
    client = TestClient(app)
    headers = {"x-andes-user": "charlie"}

    first = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        headers=headers,
    )
    second = client.post(
        "/jobs/gsea",
        data={"ranked_text": "A\t1\nB\t0", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        headers=headers,
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert "too many queued/running jobs" in second.json()["detail"]


def test_queue_owner_limit_ignores_untrusted_user_header(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"max_jobs_per_owner": 1})
    app = create_app(settings)
    client = TestClient(app)

    first = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        headers={"x-andes-user": "one"},
    )
    second = client.post(
        "/jobs/gsea",
        data={"ranked_text": "A\t1\nB\t0", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        headers={"x-andes-user": "two"},
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert "too many queued/running jobs" in second.json()["detail"]


def test_global_queue_limit_rejects_when_queue_is_full(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"max_queued_jobs": 1})
    app = create_app(settings)
    client = TestClient(app)

    first = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        headers={"x-andes-user": "one"},
    )
    second = client.post(
        "/jobs/gsea",
        data={"ranked_text": "A\t1\nB\t0", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        headers={"x-andes-user": "two"},
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert "server queue is full" in second.json()["detail"]


def test_admin_token_protects_admin_endpoints(tmp_path):
    app = create_app(_settings(tmp_path).model_copy(update={"admin_token": "secret"}))
    client = TestClient(app)

    blocked = client.get("/admin/queue")
    allowed = client.get("/admin/queue", headers={"x-andes-admin-token": "secret"})
    status_allowed = client.get("/data/status", headers={"authorization": "Bearer secret"})

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert status_allowed.status_code == 200


def test_admin_without_token_rejects_forwarded_loopback_request(tmp_path):
    client = _loopback_client(create_app(_settings(tmp_path)))

    response = client.get("/admin/queue", headers={"x-forwarded-for": "203.0.113.10"})

    assert response.status_code == 403
    assert "admin token required" in response.json()["detail"]


def test_admin_without_token_rejects_non_loopback_host(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(
        app,
        base_url="http://andes.example",
        client=("127.0.0.1", 50000),
    )

    response = client.get("/data/status")

    assert response.status_code == 403
    assert "admin token required" in response.json()["detail"]


def test_cancel_requires_job_token_or_admin_token(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"admin_token": "secret"})
    app = create_app(settings)
    client = TestClient(app)
    job = app.state.store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"]},
        access_token="job-secret",
    )

    blocked = client.post(f"/jobs/{job.id}/cancel")
    allowed = client.post(
        f"/jobs/{job.id}/cancel",
        headers={"x-andes-admin-token": "secret"},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["job"]["state"] == JobState.CANCELLED.value


def test_rerun_requires_job_token_or_admin_token(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"admin_token": "secret"})
    app = create_app(settings)
    client = TestClient(app)
    job = app.state.store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"], "min_gene_set_size": 1, "max_gene_set_size": 3},
        access_token="job-secret",
    )

    response = client.post(f"/jobs/{job.id}/rerun")

    assert response.status_code == 403
    assert "job token required" in response.json()["detail"]


def test_admin_queue_and_recover_stale_endpoint(tmp_path):
    app = create_app(_settings(tmp_path))
    client = _loopback_client(app)
    store = app.state.store
    job = store.create_job(AnalysisKind.SET_SIMILARITY, {"genes": ["A"]}, owner_key="ip:test")
    assert store.claim_next() is not None
    with store.connect() as conn:
        conn.execute(
            "UPDATE jobs SET started_at = ? WHERE id = ?",
            ("1970-01-01T00:00:00+00:00", job.id),
        )

    queue_response = client.get("/admin/queue")
    recover_response = client.post("/admin/queue/recover-stale")

    assert queue_response.status_code == 200
    assert queue_response.json()["jobs"][0]["id"] == job.id
    assert recover_response.status_code == 200
    assert recover_response.json()["recovered_jobs"] == 1
    assert store.get_job(job.id).state == JobState.FAILED  # type: ignore[union-attr]


def test_alias_mapping_is_applied_before_queueing(tmp_path):
    app = create_app(_settings_with_aliases(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "ALPHA\nBETA", "min_gene_set_size": "1", "max_gene_set_size": "3"},
    )

    assert response.status_code == 202
    payload = app.state.store.read_input(response.json()["id"])
    assert payload["genes"] == ["A", "B"]
    assert payload["id_mapping"]["genes"]["mapped_count"] == 2


def test_set_similarity_accepts_valid_uploaded_gmt(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={"gene_set_file": ("sets.gmt", b"TERM_X\tcustom\tA\tB\n", "text/plain")},
    )

    assert response.status_code == 202
    payload = app.state.store.read_input(response.json()["id"])
    gene_set_path = Path(payload["gene_set_path"])
    assert gene_set_path.name == "target_gene_sets.gmt"
    assert gene_set_path.read_text(encoding="utf-8") == "TERM_X\tcustom\tA\tB\n"


def test_set_similarity_accepts_query_collection_upload(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/jobs/set-similarity",
        data={"min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={"query_gene_set_file": ("query.gmt", b"TERM_Q\tquery\tA\tB\n", "text/plain")},
    )

    assert response.status_code == 202
    payload = app.state.store.read_input(response.json()["id"])
    query_path = Path(payload["query_gene_set_path"])
    assert payload["genes"] is None
    assert query_path.name == "query_gene_sets.gmt"
    assert query_path.read_text(encoding="utf-8") == "TERM_Q\tquery\tA\tB\n"


def test_set_similarity_accepts_go_obo_annotation_upload(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/jobs/set-similarity",
        data={"min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={
            "query_obo_file": (
                "go.obo",
                (FIXTURES / "mini_go.obo").read_bytes(),
                "text/plain",
            ),
            "query_annotation_file": (
                "go.tsv",
                (FIXTURES / "mini_go_annotations.tsv").read_bytes(),
                "text/plain",
            ),
        },
    )

    assert response.status_code == 202
    payload = app.state.store.read_input(response.json()["id"])
    query_gmt = Path(payload["query_gene_set_path"]).read_text(encoding="utf-8")
    assert "GO:0000001\troot biological process\tA\tB" in query_gmt
    assert "GO:0000002\tchild biological process\tA\tB" in query_gmt
    assert "GO:0000003" not in query_gmt


def test_set_similarity_rejects_obo_without_annotations(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.post(
        "/jobs/set-similarity",
        data={"min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={
            "query_obo_file": (
                "go.obo",
                (FIXTURES / "mini_go.obo").read_bytes(),
                "text/plain",
            ),
        },
    )

    assert response.status_code == 400
    assert "require both an OBO file and an annotation file" in response.json()["detail"]


def test_set_similarity_rejects_invalid_uploaded_gmt(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={"gene_set_file": ("sets.gmt", b"not-gmt\n", "text/plain")},
    )

    assert response.status_code == 400
    assert "GMT line 1" in response.json()["detail"]


def test_download_result_json_and_csv_artifacts(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    store = app.state.store
    job = store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"], "min_gene_set_size": 1, "max_gene_set_size": 3},
        access_token="job-secret",
    )
    store.write_result(
        job.id,
        {
            "kind": "set_similarity",
            "results": [
                {
                    "term": "TERM_A",
                    "description": "alpha",
                    "size": 2,
                    "true_score": None,
                    "z_score": 1.0,
                    "p_value": 0.01,
                    "p_value_corrected": 0.02,
                    "log10_p_value_corrected": 1.7,
                    "significant": True,
                }
            ],
            "input_gene_count": 1,
            "valid_gene_count": 1,
            "invalid_genes": [],
            "warnings": [],
            "parameters": {"mode": "gene_list"},
        },
    )
    downloads = store.run_dir(job.id) / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "results.csv").write_text(
        "term,z_score\n=STALE,999\n",
        encoding="utf-8",
    )
    store.mark_succeeded(job.id)

    json_response = client.get(
        f"/jobs/{job.id}/download/results.json",
        headers={"x-andes-job-token": "job-secret"},
    )
    csv_response = client.get(
        f"/jobs/{job.id}/download/results.csv",
        headers={"Origin": "http://localhost:3000", "x-andes-job-token": "job-secret"},
    )

    assert json_response.status_code == 200
    assert json_response.headers["cache-control"] == "no-store"
    assert json_response.json()["results"][0]["term"] == "TERM_A"
    assert csv_response.status_code == 200
    assert csv_response.headers["cache-control"] == "no-store"
    assert "content-disposition" in csv_response.headers["access-control-expose-headers"].lower()
    assert "TERM_A" in csv_response.text
    assert "=STALE" not in csv_response.text


def test_download_artifacts_are_backfilled_from_result_json(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    store = app.state.store
    job = store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"query_gene_set_path": "query.gmt", "min_gene_set_size": 1, "max_gene_set_size": 3},
        access_token="job-secret",
    )
    store.write_result(
        job.id,
        {
            "kind": "set_similarity",
            "results": [
                {
                    "term": "QUERY_A vs TARGET_A",
                    "description": "target",
                    "size": 2,
                    "query_term": "QUERY_A",
                    "query_description": "query",
                    "query_size": 2,
                    "target_term": "TARGET_A",
                    "target_description": "target",
                    "target_size": 2,
                    "true_score": None,
                    "z_score": 2.5,
                    "p_value": 0.01,
                    "p_value_corrected": 0.02,
                    "log10_p_value_corrected": 1.7,
                    "significant": True,
                }
            ],
            "input_gene_count": 1,
            "valid_gene_count": 1,
            "invalid_genes": [],
            "warnings": [],
            "parameters": {"mode": "gene_set_collection"},
        },
    )
    store.mark_succeeded(job.id)

    headers = {"x-andes-job-token": "job-secret"}
    results_response = client.get(f"/jobs/{job.id}/download/results.csv", headers=headers)
    pair_response = client.get(f"/jobs/{job.id}/download/pair-table.csv", headers=headers)
    matrix_response = client.get(f"/jobs/{job.id}/download/matrix.csv", headers=headers)

    assert results_response.status_code == 200
    assert "QUERY_A vs TARGET_A" in results_response.text
    assert pair_response.status_code == 200
    assert "QUERY_A,query,2,TARGET_A,target,2" in pair_response.text
    assert matrix_response.status_code == 200
    assert "query_term,TARGET_A" in matrix_response.text


def test_generated_csv_downloads_escape_formula_prefixes(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    store = app.state.store
    job = store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"], "min_gene_set_size": 1, "max_gene_set_size": 3},
        access_token="job-secret",
    )
    private_runs_path = str(settings.runs_dir.resolve() / job.id / "sets.gmt")
    store.write_result(
        job.id,
        {
            "kind": "set_similarity",
            "results": [
                {
                    "term": "=TERM_A",
                    "description": f"+alpha from {private_runs_path}",
                    "size": 2,
                    "true_score": None,
                    "z_score": -2.5,
                    "p_value": 0.01,
                    "p_value_corrected": 0.02,
                    "log10_p_value_corrected": 1.7,
                    "significant": True,
                },
                {
                    "term": "-TERM_B",
                    "description": "  =spaced",
                    "size": 2,
                    "true_score": None,
                    "z_score": -1.25,
                    "p_value": 0.02,
                    "p_value_corrected": 0.03,
                    "log10_p_value_corrected": 1.5,
                    "significant": False,
                }
            ],
            "input_gene_count": 1,
            "valid_gene_count": 1,
            "invalid_genes": [],
            "warnings": [],
            "parameters": {"mode": "gene_list"},
        },
    )
    store.mark_succeeded(job.id)
    downloads = store.run_dir(job.id) / "downloads"
    downloads.mkdir(parents=True)
    with zipfile.ZipFile(downloads / "report.zip", "w") as archive:
        archive.writestr("unsafe.txt", private_runs_path)

    headers = {"x-andes-job-token": "job-secret"}
    results_response = client.get(f"/jobs/{job.id}/download/results.csv", headers=headers)
    zip_response = client.get(f"/jobs/{job.id}/download/report.zip", headers=headers)

    assert results_response.status_code == 200
    assert "'=TERM_A,'+alpha" in results_response.text
    assert "'-TERM_B,'  =spaced" in results_response.text
    assert ",,-2.5," in results_response.text
    assert ",,-1.25," in results_response.text
    assert private_runs_path not in results_response.text
    assert "<server-path>" in results_response.text
    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        results_csv = archive.read("results.csv").decode("utf-8")
        assert "'=TERM_A,'+alpha" in results_csv
        assert "'-TERM_B,'  =spaced" in results_csv
        assert ",,-2.5," in results_csv
        assert ",,-1.25," in results_csv
        assert private_runs_path not in results_csv
        assert "<server-path>" in results_csv


def test_mapping_report_and_report_zip_are_generated_from_results(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    store = app.state.store
    private_runs_path = str(settings.runs_dir.resolve() / "private" / "sets.gmt")
    private_cache_path = str(settings.cache_dir.resolve() / "bma" / "private.pkl")
    job = store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"], "min_gene_set_size": 1, "max_gene_set_size": 3},
        access_token="job-secret",
    )
    store.write_result(
        job.id,
        {
            "kind": "set_similarity",
            "results": [
                {
                    "term": "TERM_A",
                    "description": f"alpha from {private_runs_path}",
                    "size": 2,
                    "true_score": None,
                    "z_score": 2.5,
                    "p_value": 0.01,
                    "p_value_corrected": 0.02,
                    "log10_p_value_corrected": 1.7,
                    "significant": True,
                },
                {
                    "term": "TERM_B",
                    "description": "beta",
                    "size": 2,
                    "true_score": None,
                    "z_score": -1.0,
                    "p_value": 0.2,
                    "p_value_corrected": 0.25,
                    "log10_p_value_corrected": 0.6,
                    "significant": False,
                },
            ],
            "input_gene_count": 2,
            "valid_gene_count": 1,
            "invalid_genes": ["MISSING"],
            "warnings": [f"1 gene was not mapped while reading {private_runs_path}"],
            "parameters": {
                "mode": "gene_list",
                "gene_set_path": private_runs_path,
                "cache": {"path": private_cache_path, "file": "private.pkl"},
                "id_mapping": {
                    "genes": {
                        "records": [
                            {
                                "submitted": "ALPHA",
                                "mapped": "A",
                                "id_type": "alias",
                                "source": "alias_file",
                            },
                            {
                                "submitted": "UNKNOWN",
                                "mapped": None,
                                "id_type": "unknown",
                                "source": "unmapped",
                            },
                        ]
                    }
                },
            },
        },
    )
    store.mark_succeeded(job.id)
    downloads = store.run_dir(job.id) / "downloads"
    downloads.mkdir(parents=True)
    with zipfile.ZipFile(downloads / "report.zip", "w") as archive:
        archive.writestr("unsafe.txt", private_runs_path)

    headers = {"x-andes-job-token": "job-secret"}
    mapping_response = client.get(
        f"/jobs/{job.id}/download/mapping-report.csv",
        headers=headers,
    )
    zip_response = client.get(f"/jobs/{job.id}/download/report.zip", headers=headers)

    assert mapping_response.status_code == 200
    assert "collection,submitted_id,mapped_id,detected_type,source,status" in mapping_response.text
    assert "genes,ALPHA,A,alias,alias_file,mapped" in mapping_response.text
    assert "genes,UNKNOWN,,unknown,unmapped,unmapped" in mapping_response.text
    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        names = set(archive.namelist())
        assert "results.json" in names
        assert "results.csv" in names
        assert "mapping-report.csv" in names
        assert "parameters.json" in names
        assert "cache.json" in names
        assert "figures/z-scores.svg" in names
        assert "unsafe.txt" not in names
        results_json = archive.read("results.json").decode("utf-8")
        assert "sets.gmt" in results_json
        for name in names:
            if not name.endswith((".json", ".csv", ".txt", ".svg")):
                continue
            text = archive.read(name).decode("utf-8")
            assert private_runs_path not in text
            assert private_cache_path not in text


def test_rerun_clones_uploaded_gene_set_into_new_job(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    created = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={"gene_set_file": ("sets.gmt", b"TERM_X\tcustom\tA\tB\n", "text/plain")},
    )
    assert created.status_code == 202
    source_id = created.json()["id"]
    access_token = created.json()["access_token"]

    rerun = client.post(
        f"/jobs/{source_id}/rerun",
        headers={"x-andes-job-token": access_token},
    )

    assert rerun.status_code == 202
    rerun_payload = rerun.json()
    assert rerun_payload["id"] != source_id
    assert rerun_payload["access_token"]
    source_input = app.state.store.read_input(source_id)
    rerun_input = app.state.store.read_input(rerun_payload["id"])
    assert rerun_input["gene_set_path"] != source_input["gene_set_path"]
    assert Path(rerun_input["gene_set_path"]).read_text(encoding="utf-8") == (
        "TERM_X\tcustom\tA\tB\n"
    )
    assert Path(rerun_input["gene_set_path"]).is_relative_to(
        app.state.store.run_dir(rerun_payload["id"])
    )


def test_rerun_deleted_uploaded_gene_set_returns_clean_400(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    created = client.post(
        "/jobs/set-similarity",
        data={"genes_text": "A\nB", "min_gene_set_size": "1", "max_gene_set_size": "3"},
        files={"gene_set_file": ("sets.gmt", b"TERM_X\tcustom\tA\tB\n", "text/plain")},
    )
    assert created.status_code == 202
    source_id = created.json()["id"]
    access_token = created.json()["access_token"]
    source_input = app.state.store.read_input(source_id)
    Path(source_input["gene_set_path"]).unlink()

    rerun = client.post(
        f"/jobs/{source_id}/rerun",
        headers={"x-andes-job-token": access_token},
    )

    assert rerun.status_code == 400
    assert rerun.json()["detail"] == "source upload for gene_set_path is no longer available"
