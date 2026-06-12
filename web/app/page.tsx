import Link from "next/link";

export default function HomePage() {
  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Local analysis console</p>
          <h2>Run ANDES jobs with the optimized cache and inspect results as they finish.</h2>
          <p>
            ANDES v2 keeps the Python compute path explicit: paste gene inputs, queue a job, and
            let the worker process use the configured embedding and gene-set files.
          </p>
        </div>
        <aside className="panel run-card">
          <div>
            <p className="eyebrow">Recommended start</p>
            <h3>Set similarity</h3>
            <p>
              Best first check for a local install because it validates input parsing, cache
              loading, job polling, and result rendering.
            </p>
          </div>
          <Link className="button primary" href="/set-similarity">
            Queue set similarity
          </Link>
        </aside>
      </section>

      <section className="grid" aria-label="Analysis tools">
        <article className="panel pad">
          <p className="eyebrow">BMA search</p>
          <h2>Set similarity</h2>
          <p>
            Compare a pasted gene set against the configured ontology collection using the ANDES
            best-match average cache.
          </p>
          <Link className="button secondary" href="/set-similarity">
            Open tool
          </Link>
        </article>
        <article className="panel pad">
          <p className="eyebrow">Ranked enrichment</p>
          <h2>GSEA</h2>
          <p>
            Score ranked genes against the same embedding-backed gene sets and review corrected
            enrichment terms.
          </p>
          <Link className="button secondary" href="/gsea">
            Open tool
          </Link>
        </article>
      </section>
    </>
  );
}
