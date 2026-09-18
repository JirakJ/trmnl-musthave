# Vendored assets

- `inter-latin.woff2`, `inter-latin-ext.woff2` – Inter variable font (weights 300–700), latin and latin-ext
  subsets, from Google Fonts (`family=Inter:wght@300..700`, v20). SIL Open Font License 1.1.
  Latin-ext carries the Czech diacritics. The render shell embeds them with `@font-face` so a screen never
  depends on fonts.googleapis.com.

The TRMNL framework CSS/JS (`plugins.css`, `plugins.js`, ~18 MB unpacked) is not vendored: the server caches
it in `state/cache/` (see `musthave/framework.py`) and renders from the local copy.
