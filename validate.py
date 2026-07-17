#!/usr/bin/env python3
"""
Validador estático de la web de API TUSSAM.
============================================

Comprueba, sin dependencias externas, que las páginas y los artefactos de SEO
están bien formados y son coherentes antes de publicar la web en la rama
`landing` (GitHub Pages). No sustituye a una revisión visual, pero detecta
regresiones obvias: HTML mal cerrado, meta SEO ausentes, enlaces a ficheros
inexistentes o imagen social con dimensiones incorrectas.

Uso:
    python3 web/validate.py

Devuelve código de salida 0 si todo pasa, 1 si hay algún fallo.
"""

from __future__ import annotations

import html.parser
import json
import pathlib
import re
import struct
import sys
import xml.parsers.expat

WEB_DIR = pathlib.Path(__file__).resolve().parent

# Etiquetas HTML que no requieren cierre (void elements).
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# Cadenas de SEO que cada página HTML debe incluir para compartirse bien.
REQUIRED_META = [
    'rel="canonical"',
    'property="og:title"',
    'property="og:image"',
    'name="twitter:card"',
    'content="summary_large_image"',
]


class TagBalanceChecker(html.parser.HTMLParser):
    """Verifica que las etiquetas HTML no-void queden correctamente cerradas."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            # Cierre desordenado: desapilar hasta la etiqueta correspondiente.
            while self.stack and self.stack.pop() != tag:
                pass


def png_dimensions(path: pathlib.Path) -> tuple[int, int]:
    """Devuelve (ancho, alto) de un PNG leyendo su cabecera IHDR."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("no es un PNG válido")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def check_html(path: pathlib.Path, failures: list[str], require_seo: bool = True) -> None:
    """Valida balance de etiquetas, meta SEO y enlaces locales de una página.

    ``require_seo`` se desactiva para plantillas internas (como la tarjeta social)
    que no son páginas públicas y no necesitan meta de indexación.
    """
    text = path.read_text(encoding="utf-8")

    checker = TagBalanceChecker()
    checker.feed(text)
    if checker.stack:
        failures.append(f"{path.name}: etiquetas sin cerrar: {checker.stack[-5:]}")

    if require_seo:
        for needle in REQUIRED_META:
            if needle not in text:
                failures.append(f"{path.name}: falta la meta SEO {needle!r}")

    if "<title>" not in text:
        failures.append(f"{path.name}: falta <title>")

    # Comprobar que los recursos locales referenciados existen.
    for match in re.findall(r'(?:href|src)="([^"#?:]+)"', text):
        if match.startswith(("http", "//", "mailto:", "data:")):
            continue
        target = (WEB_DIR / match).resolve()
        if not target.exists():
            failures.append(f"{path.name}: enlace a fichero inexistente {match!r}")


def check_xml(path: pathlib.Path, failures: list[str]) -> None:
    """Valida que un XML esté bien formado.

    Usa el parser expat de la stdlib, que no resuelve entidades externas por
    defecto (evita XXE), y se desactiva explícitamente la expansión de entidades
    generales para no ser vulnerable a ataques de expansión (billion laughs).
    """
    parser = xml.parsers.expat.ParserCreate()
    # No expandir entidades: solo nos interesa la buena formación estructural.
    parser.DefaultHandler = lambda data: None
    try:
        parser.Parse(path.read_bytes(), True)
    except xml.parsers.expat.ExpatError as exc:
        failures.append(f"{path.name}: XML mal formado ({exc})")


def main() -> int:
    failures: list[str] = []

    # Páginas públicas: exigen meta SEO completa. La tarjeta social es una
    # plantilla interna de render y solo se valida su estructura HTML.
    for name in ("index.html", "docs.html", "paradas.html"):
        path = WEB_DIR / name
        if not path.exists():
            failures.append(f"falta la página {name}")
            continue
        check_html(path, failures, require_seo=True)

    social = WEB_DIR / "social-card.html"
    if social.exists():
        check_html(social, failures, require_seo=False)

    sitemap = WEB_DIR / "sitemap.xml"
    if sitemap.exists():
        check_xml(sitemap, failures)
    else:
        failures.append("falta sitemap.xml")

    for required in (
        "robots.txt", ".nojekyll", "favicon.svg", "styles.css", "app.js",
        "paradas.js", "CNAME",
    ):
        if not (WEB_DIR / required).exists():
            failures.append(f"falta {required}")

    # Datos del mapa: deben existir y ser JSON de listas no vacías.
    for datos in ("paradas.json", "lineas.json"):
        path = WEB_DIR / datos
        if not path.exists():
            failures.append(f"falta {datos}")
            continue
        try:
            contenido = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"{datos}: JSON inválido ({exc})")
            continue
        if not isinstance(contenido, list) or not contenido:
            failures.append(f"{datos}: debe ser una lista no vacía")

    og = WEB_DIR / "og-tussam.png"
    if not og.exists():
        failures.append("falta la imagen social og-tussam.png")
    else:
        width, height = png_dimensions(og)
        if (width, height) != (1200, 630):
            failures.append(f"og-tussam.png debe ser 1200x630, es {width}x{height}")

    if failures:
        print("VALIDACIÓN FALLIDA:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("Validación de la web correcta: HTML, SEO, sitemap e imagen social OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
