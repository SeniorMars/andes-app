import Link from "next/link";

const methodSteps = [
  {
    title: "Represent genes in an embedding space",
    body: "ANDES works on existing gene embeddings, such as protein interaction embeddings, without needing to train a new gene-set embedding."
  },
  {
    title: "Find reciprocal best matches",
    body: "For each gene in one set, ANDES finds the most similar gene in the other set, then repeats the same operation in the reverse direction."
  },
  {
    title: "Average the best-match evidence",
    body: "The reciprocal best-match average keeps substructure visible when a gene set contains several pathways or biological processes."
  },
  {
    title: "Normalize by a size-aware null",
    body: "Monte Carlo nulls account for gene-set cardinality, so scores are comparable across small and large gene sets."
  }
];

const applications = [
  "Embedding-aware overrepresentation analysis",
  "Rank-based enrichment",
  "Gene-set similarity across annotation databases",
  "Cross-organism functional transfer",
  "Drug-disease mapping",
  "Phenotype prioritization"
];

export default function AboutPage() {
  return (
    <div className="about-page">
      <section className="about-hero">
        <div>
          <p className="eyebrow">About the method</p>
          <h2>ANDES compares gene sets without collapsing their biology into a centroid.</h2>
          <p>
            This app is based on the best-match framework from the paper Enhancing gene set
            analysis in embedding spaces: a novel best-match approach by Lechuan Li, Ruth Dannenfelser,
            Charlie Cruz, and Vicky Yao.
          </p>
          <div className="row">
            <Link className="button primary" href="/set-similarity">
              Run set similarity
            </Link>
            <Link className="button secondary" href="/gsea">
              Run GSEA
            </Link>
          </div>
        </div>
        <aside className="source-card">
          <p className="eyebrow">Paper</p>
          <h3>Best-match analysis in embedding spaces</h3>
          <p>
            ANDES was introduced as an Algorithm for Network Data Embedding and Similarity
            analysis, designed for set comparisons where elements form diverse communities in an
            embedding space.
          </p>
          <a href="https://doi.org/10.1101/2023.11.21.568145">bioRxiv preprint</a>
        </aside>
      </section>

      <section className="stat-strip" aria-label="ANDES highlights">
        <div>
          <strong>Best-match</strong>
          <span>reciprocal gene-to-set matching</span>
        </div>
        <div>
          <strong>Size-aware</strong>
          <span>Monte Carlo null distributions</span>
        </div>
        <div>
          <strong>2 modes</strong>
          <span>set similarity and ranked enrichment</span>
        </div>
        <div>
          <strong>8 workers</strong>
          <span>current local runtime target</span>
        </div>
      </section>

      <section className="article-grid">
        <article className="panel pad">
          <p className="eyebrow">Problem</p>
          <h2>Why not just average embeddings?</h2>
          <p>
            Gene embeddings can capture physical, structural, and functional relationships between
            genes. Standard set comparison approaches often average those embeddings, which can hide
            the internal diversity of a gene set.
          </p>
          <p>
            The paper highlights disease and pathway sets that contain several sub-processes. A
            single centroid can blur those subgroups, while best-match scoring can preserve the fact
            that different parts of one set may correspond to different parts of another.
          </p>
        </article>

        <article className="panel pad">
          <p className="eyebrow">Core idea</p>
          <h2>Best-match average</h2>
          <p>
            ANDES computes pairwise cosine similarities between genes in two sets. It then asks, for
            every gene in each set, which gene in the other set is the closest match. The final score
            is the reciprocal average of those best matches.
          </p>
          <p>
            A high score means genes from both sets can find close counterparts in the embedding
            space, even when the sets do not have direct gene overlap.
          </p>
        </article>
      </section>

      <section className="panel pad">
        <div className="section-head">
          <div>
            <p className="eyebrow">How it works</p>
            <h2>Method flow</h2>
          </div>
          <span className="subtle">Embedding-aware and cardinality-aware</span>
        </div>
        <ol className="method-steps">
          {methodSteps.map((step) => (
            <li key={step.title}>
              <strong>{step.title}</strong>
              <span>{step.body}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="evidence-band">
        <div className="evidence-copy">
          <p className="eyebrow">Evidence</p>
          <h2>What the paper showed</h2>
          <p>
            ANDES was tested on matched KEGG and GO terms, GEO2KEGG enrichment benchmarks,
            drug-disease relationships, and cross-organism transfer tasks. The consistent result:
            best-match scoring preserved useful substructure that mean-based baselines often lost.
          </p>
        </div>
        <div className="workflow-panel">
          <p className="eyebrow">This app</p>
          <h3>ANDES v2 exposes the core workflows</h3>
          <div className="chip-list" aria-label="ANDES v2 workflows">
            {applications.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </div>
      </section>

    </div>
  );
}
