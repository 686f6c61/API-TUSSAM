"""
TUSSAM API - Escáner de Cambios en Paradas
==========================================

Compara las paradas en la base de datos local con las que
devuelve ahora mismo la API de TUSSAM. Detecta:

- Paradas nuevas (en la API pero no en la DB)
- Paradas eliminadas (en la DB pero no en la API)
- Paradas modificadas (mismo código, distinto nombre o coordenadas)

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 1.0.0
Licencia: MIT
"""

import sqlite3
import httpx
import sys
import os
from datetime import datetime
from typing import Dict, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = "data/tussam.db"
BASE_URL = "https://reddelineas.tussam.es"


def format_datetime() -> str:
    dt = datetime.now()
    return dt.strftime("%d-%m-%YT%H:%M:%S").replace(":", "%3A")


def get_local_paradas(db_path: str) -> Dict[str, dict]:
    """Obtiene todas las paradas de la DB local."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT codigo, nombre, latitud, longitud, calle, numero FROM paradas")
    rows = cursor.fetchall()
    conn.close()

    return {
        row[0]: {
            "codigo": row[0],
            "nombre": row[1],
            "latitud": round(row[2], 6),
            "longitud": round(row[3], 6),
            "calle": row[4] or "",
            "numero": row[5] or "",
        }
        for row in rows
    }


def get_remote_paradas() -> Dict[str, dict]:
    """
    Obtiene todas las paradas desde la API de TUSSAM.
    Itera por todas las líneas y sus nodos.
    """
    client = httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={
            "User-Agent": "TUSSAM-Scanner/1.0",
            "Accept": "application/json",
        },
    )

    fh = format_datetime()
    todas: Dict[str, dict] = {}

    print("Paso 1: Obteniendo líneas...")
    url_lineas = f"{BASE_URL}/API/infotus-ui/lineas/{fh}"
    r = client.get(url_lineas)
    r.raise_for_status()
    lineas = r.json().get("result", {}).get("lineasDisponibles", [])
    print(f"  → {len(lineas)} líneas encontradas")

    print("Paso 2: Obteniendo nodos de cada línea...")
    for i, linea in enumerate(lineas):
        linea_num = linea.get("linea", 0)
        if not linea_num:
            continue

        for sentido in [1, 2]:
            try:
                url = f"{BASE_URL}/API/infotus-ui/nodosLinea/{linea_num}/{sentido}/{fh}"
                resp = client.get(url)
                if resp.status_code != 200:
                    continue
                nodos = resp.json().get("result", [])

                for nodo in nodos:
                    codigo = str(nodo.get("codigo", ""))
                    if not codigo or codigo in todas:
                        continue

                    pos = nodo.get("posicion", {})
                    lat = pos.get("latitudE6", 0) / 1_000_000
                    lon = pos.get("longitudE6", 0) / 1_000_000

                    if lat and lon:
                        nombre = nodo.get("descripcion", {}).get("texto", "")
                        todas[codigo] = {
                            "codigo": codigo,
                            "nombre": nombre,
                            "latitud": round(lat, 6),
                            "longitud": round(lon, 6),
                        }
            except Exception as e:
                print(f"  ⚠ Error línea {linea_num} sentido {sentido}: {e}")

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(lineas)} líneas procesadas, {len(todas)} paradas únicas")

    client.close()
    return todas


def compare(local: Dict, remote: Dict):
    """Compara las paradas locales con las remotas."""
    local_codes: Set[str] = set(local.keys())
    remote_codes: Set[str] = set(remote.keys())

    new_codes = remote_codes - local_codes
    removed_codes = local_codes - remote_codes
    common_codes = local_codes & remote_codes

    # Detectar modificadas (mismo código, diferentes datos)
    modified = []
    for code in sorted(common_codes):
        local_parada = local[code]
        remote_parada = remote[code]
        changes = []
        if local_parada["nombre"] != remote_parada["nombre"]:
            changes.append(
                f"nombre: '{local_parada['nombre']}' → '{remote_parada['nombre']}'"
            )
        if abs(local_parada["latitud"] - remote_parada["latitud"]) > 0.0001:
            changes.append(
                f"latitud: {local_parada['latitud']} → {remote_parada['latitud']}"
            )
        if abs(local_parada["longitud"] - remote_parada["longitud"]) > 0.0001:
            changes.append(
                f"longitud: {local_parada['longitud']} → {remote_parada['longitud']}"
            )
        if changes:
            modified.append((code, changes))

    # Imprimir informe
    print("\n" + "=" * 60)
    print("INFORME DE CAMBIOS")
    print("=" * 60)

    print("\n📊 Resumen:")
    print(f"   Paradas en DB local:  {len(local)}")
    print(f"   Paradas en API remota: {len(remote)}")
    print(f"   Diferencia:           {len(remote) - len(local):+d}")

    if new_codes:
        print(f"\n🆕 NUEVAS ({len(new_codes)}):")
        for code in sorted(new_codes):
            r = remote[code]
            print(f"   {code}: {r['nombre']}")
            print(f"        📍 {r['latitud']}, {r['longitud']}")

    if removed_codes:
        print(f"\n🗑️ ELIMINADAS ({len(removed_codes)}):")
        for code in sorted(removed_codes):
            local_parada = local[code]
            print(
                f"   {code}: {local_parada['nombre']} "
                f"({local_parada['calle']} {local_parada['numero']})"
            )

    if modified:
        print(f"\n✏️ MODIFICADAS ({len(modified)}):")
        for code, changes in modified:
            local_parada = local[code]
            print(f"   {code}: {local_parada['nombre']}")
            for c in changes:
                print(f"        ↳ {c}")

    if not new_codes and not removed_codes and not modified:
        print(f"\n✅ Sin cambios: las {len(common_codes)} paradas son idénticas.")

    print(f"\n{'=' * 60}")
    print("FIN DEL INFORME")
    print("=" * 60)

    return {
        "new": sorted(new_codes),
        "removed": sorted(removed_codes),
        "modified": modified,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Escanea cambios en paradas de TUSSAM")
    parser.add_argument("--db", default=DB_PATH, help="Ruta a la DB SQLite")
    parser.add_argument("--only-local", action="store_true", help="Solo mostrar paradas locales (sin consultar API)")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Error: DB no encontrada en {args.db}")
        sys.exit(1)

    local = get_local_paradas(args.db)

    if args.only_local:
        print(f"Paradas en DB local: {len(local)}")
        return

    print("Obteniendo paradas de la API de TUSSAM...\n")
    remote = get_remote_paradas()
    compare(local, remote)


if __name__ == "__main__":
    main()
