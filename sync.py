#!/usr/bin/env python3
"""Reconstruye el catalogo brandeado de Plyrap: hornea lo nuevo y reescribe el feed.

Corre solo, cada hora, por GitHub Actions. No depende de ninguna maquina ni del
token de la API de Tiendanube: los datos salen de las paginas publicas de la
tienda.

Que hace, en orden:
  1. Lee el sitemap y cada pagina de producto.
  2. Hornea el marco sobre las fotos que todavia no estan en img/.
  3. Escribe img/index.json y feed_brandeado.csv.

Es idempotente: una foto que ya se horneo no se vuelve a bajar ni a procesar,
porque el nombre del archivo es el hash de su URL original.
"""
import csv, hashlib, html, io, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageChops

TIENDA   = "https://tienda.plyrap.com.ar"
BASE_IMG = "https://scalelabs-ar.github.io/plyrap-catalogo-img/img/"
DIR      = os.path.dirname(os.path.abspath(__file__))
IMGS     = os.path.join(DIR, "img")
MARCO    = os.path.join(DIR, "marco.png")
UA       = {"User-Agent": "Mozilla/5.0"}

# Banda libre entre el header del marco y la pildora de abajo. Medida sobre
# marco.png. Si se rediseña el marco, hay que volver a medirla.
LIENZO, TOP, BOT = 1080, 262, 840

slug = lambda u: hashlib.sha1(u.encode()).hexdigest()[:16]


def bajar(url, binario=False):
    d = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=45).read()
    return d if binario else d.decode("utf-8", "replace")


# ---------------------------------------------------------------- horneado ---

def color_de_fondo(foto):
    """Promedio del borde. Evita el rectangulo blanco cuando el fondo no es
    blanco puro. Si el borde es oscuro cae a blanco, para que una foto sobre
    fondo negro no pinte el lienzo entero."""
    w, h = foto.size
    px = foto.load()
    b = max(2, min(w, h) // 40)
    s = []
    for x in range(0, w, 3):
        s += [px[x, y] for y in list(range(b)) + list(range(h - b, h))]
    for y in range(0, h, 3):
        s += [px[x, y] for x in list(range(b)) + list(range(w - b, w))]
    prom = tuple(round(sum(c[i] for c in s) / len(s)) for i in range(3))
    return prom if min(prom) >= 235 else (255, 255, 255)


def recortar_fondo(foto, fondo, margen=0.03):
    """Recorta el aire que trae la foto del proveedor.

    Varias vienen cuadradas con el producto en una franja: sin esto se escala
    el blanco y el producto queda chico. NO retoca el producto, solo normaliza
    cuanto aire lo rodea, igual para todos."""
    d = ImageChops.difference(foto, Image.new("RGB", foto.size, fondo)).convert("L")
    caja = d.point(lambda p: 255 if p > 12 else 0).getbbox()
    if not caja:
        return foto
    x0, y0, x1, y1 = caja
    if (x1 - x0) * (y1 - y0) < foto.width * foto.height * 0.02:
        return foto                      # deteccion dudosa: mejor no tocar
    m = round(max(x1 - x0, y1 - y0) * margen)
    return foto.crop((max(0, x0 - m), max(0, y0 - m),
                      min(foto.width, x1 + m), min(foto.height, y1 + m)))


def hornear(url, marco):
    destino = os.path.join(IMGS, slug(url) + ".jpg")
    if os.path.exists(destino):
        return "ya estaba"
    try:
        foto = Image.open(io.BytesIO(bajar(url, binario=True))).convert("RGB")
    except Exception as e:
        print("  ERROR bajando", type(e).__name__, url)
        return "error"

    fondo = color_de_fondo(foto)
    foto  = recortar_fondo(foto, fondo)

    # OJO: thumbnail() solo achica, y una foto ya recortada quedaria a tamaño
    # original, perdida en el lienzo. Escalar explicito en los dos sentidos.
    caja = BOT - TOP
    esc  = min(round(LIENZO * 0.92) / foto.width, caja / foto.height)
    foto = foto.resize((max(1, round(foto.width * esc)),
                        max(1, round(foto.height * esc))), Image.LANCZOS)

    lienzo = Image.new("RGB", (LIENZO, LIENZO), fondo)
    lienzo.paste(foto, ((LIENZO - foto.width) // 2, TOP + (caja - foto.height) // 2))
    lienzo = lienzo.convert("RGBA")
    lienzo.alpha_composite(marco)
    lienzo.convert("RGB").save(destino, quality=88, optimize=True)
    return "generada"


# -------------------------------------------------------------- la tienda ---

def limpiar(t, limite):
    t = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(t or "")).split())
    return t[: limite - 1] + "…" if len(t) > limite else t


def leer_producto(url):
    try:
        h = bajar(url)
    except Exception as e:
        print("  ERROR", type(e).__name__, url)
        return []

    def meta(prop):
        m = re.search(rf'<meta property="{prop}" content="([^"]*)"', h)
        return html.unescape(m.group(1)) if m else ""

    nombre = meta("og:title")
    desc   = meta("og:description") or nombre
    cat = re.findall(r'<a[^>]+href="' + TIENDA + r'/([a-z0-9\-]+)/"[^>]*itemprop="item"', h)

    # SOLO el primer data-variants: es el de #single-product. Los que siguen son
    # los productos relacionados, y tomarlos multiplica el feed por 6.
    m = re.search(r'data-variants="([^"]+)"', h)
    if not m:
        return []

    # El pixel manda este id en content_ids. Si el feed usa otro, Meta no puede
    # atribuir conversiones a productos y el catalogo queda ciego.
    esp = re.search(r"productVariantId\s*=\s*'(\d+)'", h)

    filas = []
    for v in json.loads(html.unescape(m.group(1))):
        if not v.get("is_visible", True):
            continue
        img = v.get("image_url") or ""
        if img.startswith("//"):
            img = "https:" + img
        if not img:
            continue
        gestiona   = v.get("stock") is not None
        disponible = (not gestiona) or (v.get("stock") or 0) > 0
        filas.append({
            "id": str(v["id"]),
            "title": limpiar(nombre, 200),
            "description": limpiar(desc, 5000),
            "availability": "in stock" if disponible and v.get("available", True)
                            else "out of stock",
            "condition": "new",
            "price": f'{v["price_number"]:.2f} ARS',
            "link": url,
            "image_link": "",                 # se completa despues del horneado
            "foto": img,
            "brand": "Plyrap",
            "product_type": cat[0].replace("-", " ") if cat else "",
            "quantity_to_sell_on_facebook": v.get("stock") if gestiona else "",
        })

    if esp and esp.group(1) not in [f["id"] for f in filas]:
        print(f"  OJO: el pixel manda {esp.group(1)} y el feed no lo tiene — {url}")
    return filas


def main():
    os.makedirs(IMGS, exist_ok=True)
    marco = Image.open(MARCO).convert("RGBA")
    assert marco.size == (LIENZO, LIENZO), f"El marco tiene que ser {LIENZO}x{LIENZO}"

    urls = sorted(set(re.findall(rf"{TIENDA}/productos/[a-z0-9\-]+/", bajar(f"{TIENDA}/sitemap.xml"))))
    print(f"productos en el sitemap: {len(urls)}")

    filas = [f for g in ThreadPoolExecutor(6).map(leer_producto, urls) for f in g]
    if not filas:
        # Sin esto, un error transitorio de la tienda vacia el feed y Meta lo
        # levanta vacio a la hora siguiente: la campaña se cae sin aviso.
        sys.exit("ERROR: la tienda no devolvio productos — no se toca el feed.")

    fotos = sorted({f["foto"] for f in filas})
    res = list(ThreadPoolExecutor(6).map(lambda u: hornear(u, marco), fotos))
    print(f"fotos: {len(fotos)} | generadas: {res.count('generada')} | "
          f"ya estaban: {res.count('ya estaba')} | errores: {res.count('error')}")

    disponibles = sorted(f[:-4] for f in os.listdir(IMGS) if f.endswith(".jpg"))
    json.dump(disponibles, open(os.path.join(IMGS, "index.json"), "w"))

    sin_marco = 0
    for f in filas:
        foto = f.pop("foto")
        if slug(foto) in disponibles:
            f["image_link"] = BASE_IMG + slug(foto) + ".jpg"
        else:
            f["image_link"] = foto      # mejor sin marco que roto
            sin_marco += 1

    cols = ["id", "title", "description", "availability", "condition", "price",
            "link", "image_link", "brand", "product_type",
            "quantity_to_sell_on_facebook"]
    with open(os.path.join(DIR, "feed_brandeado.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(filas)

    agotados = sum(1 for f in filas if f["availability"] == "out of stock")
    print(f"-> feed_brandeado.csv: {len(filas)} variantes "
          f"({agotados} sin stock, {sin_marco} sin marco)")


if __name__ == "__main__":
    main()
