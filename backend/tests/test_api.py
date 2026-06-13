from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from andes_api.app import create_app
from andes_core.config import AndesSettings
from andes_core.schemas import AnalysisKind, JobState

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORIGINAL_SRC = Path("/Users/charlie/Acdemica/ylab/ANDES/src")


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
    assert payload["cache"]["seed_strategy"] == "cache_key"
    assert isinstance(payload["cache"]["seed"], int)


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

    job_response = client.get(f"/jobs/{second_id}")
    cancel_response = client.post(f"/jobs/{second_id}/cancel")

    assert first.status_code == 202
    assert second.status_code == 202
    assert job_response.status_code == 200
    assert job_response.json()["queue"]["position"] == 2
    assert cancel_response.status_code == 200
    assert cancel_response.json()["job"]["state"] == JobState.CANCELLED.value


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


def test_cross_owner_cancel_requires_admin_token(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"admin_token": "secret"})
    app = create_app(settings)
    client = TestClient(app)
    job = app.state.store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"genes": ["A"]},
        owner_key="ip:other-client",
    )

    blocked = client.post(f"/jobs/{job.id}/cancel")
    allowed = client.post(
        f"/jobs/{job.id}/cancel",
        headers={"x-andes-admin-token": "secret"},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["job"]["state"] == JobState.CANCELLED.value


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
    )
    store.write_result(job.id, {"ok": True})
    downloads = store.run_dir(job.id) / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "results.csv").write_text("term,z_score\nTERM_A,1.0\n", encoding="utf-8")
    store.mark_succeeded(job.id)

    json_response = client.get(f"/jobs/{job.id}/download/results.json")
    csv_response = client.get(f"/jobs/{job.id}/download/results.csv")

    assert json_response.status_code == 200
    assert json_response.json() == {"ok": True}
    assert csv_response.status_code == 200
    assert "TERM_A" in csv_response.text


def test_download_artifacts_are_backfilled_from_result_json(tmp_path):
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    store = app.state.store
    job = store.create_job(
        AnalysisKind.SET_SIMILARITY,
        {"query_gene_set_path": "query.gmt", "min_gene_set_size": 1, "max_gene_set_size": 3},
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

    results_response = client.get(f"/jobs/{job.id}/download/results.csv")
    pair_response = client.get(f"/jobs/{job.id}/download/pair-table.csv")
    matrix_response = client.get(f"/jobs/{job.id}/download/matrix.csv")

    assert results_response.status_code == 200
    assert "QUERY_A vs TARGET_A" in results_response.text
    assert pair_response.status_code == 200
    assert "QUERY_A,query,2,TARGET_A,target,2" in pair_response.text
    assert matrix_response.status_code == 200
    assert "query_term,TARGET_A" in matrix_response.text
