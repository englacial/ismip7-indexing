# Viewer catalogs

`catalog.json` lists every verified group of both Icechunk stores (218
entries, tagged by region, source, experiment, product, resolution and kind);
`catalog-ais.json` and `catalog-gris.json` are the per-region subsets. They are
built from the index with

    ismip7-catalog --index index.parquet --region AIS \
        --store-url https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/englacial/ismip7/icechunk-ais \
        --out site/catalog-ais.json

(and the same for GrIS), then merged. The Pages workflow builds gridlook with
`catalog.json` as its default catalog.
