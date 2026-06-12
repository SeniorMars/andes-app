# Manual ANDES v2 Upload Fixtures

These files use Entrez IDs that are present in the default ANDES embedding gene
list at `/Users/charlie/Acdemica/ylab/ANDES/data/embedding/consensus_node.txt`.

Use them for quick browser tests with the normal backend configuration:

- `query_genes.txt`: set-similarity gene-list upload.
- `ranked_genes.txt`: GSEA ranked-list upload.
- `query_small.gmt`: small query GMT for collection-vs-collection set similarity.
- `target_small.gmt`: small target GMT for collection-vs-collection set similarity.
- `test_go.obo` plus `test_go_annotations.tsv`: GO/OBO upload pair.

For these small files, keep the UI defaults at min size `10` and max size `300`.
