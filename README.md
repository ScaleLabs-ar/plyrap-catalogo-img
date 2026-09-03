# plyrap-catalogo-img

Imágenes del catálogo de Plyrap con el marco de marca horneado, para los anuncios
de catálogo de Meta (Feed, Instagram, Historias y Reels).

- `img/<sha1>.jpg` — una por foto del catálogo. El nombre es `sha1(url_original)[:16]`.
- `img/index.json` — lista de hashes disponibles.
- `feed_suplementario.csv` — `id,image_link`. Se conecta en Commerce Manager como
  feed suplementario y solo pisa la imagen; precio y stock los sigue mandando la app
  de Tiendanube.

Se regenera con `brandear.py` de la skill `meta-catalogo-brandeado`.
Fuente y proceso: `Plyrap/meta-overlay/` en el drive de Scale Labs.
