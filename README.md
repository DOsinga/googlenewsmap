# Google News Map

Live Google News headlines placed on a world map. A small named-entity model
finds locations in headlines and resolves them against the GeoNames gazetteer.

Run it locally with:

```bash
uvx douwe googlenewsmap
```

The local headline cache is stored in `news.db` and is not committed.
