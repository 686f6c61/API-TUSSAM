"""
TUSSAM API - Script de Geocodificación
=======================================

Geocodifica las paradas de TUSSAM que no tienen calle o número asignado.
Lee las coordenadas directamente de la tabla `paradas` y escribe
los resultados en la misma tabla.

Proveedores soportados:
- nominatim: OpenStreetMap (gratuito, 1 req/s)
- auto: prueba nominatim primero, fallback a heurísticas

Uso:
    python scripts/geocode_paradas.py
    python scripts/geocode_paradas.py --dry-run
    python scripts/geocode_paradas.py --proveedor nominatim
    python scripts/geocode_paradas.py --db /ruta/personalizada.db

Autor: 686f6c61 (https://github.com/686f6c61)
Versión: 1.0.0
Licencia: MIT
"""

import sqlite3
import json
import time
import sys
import os
import argparse

# Añadir el directorio raíz al path para importar httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "TUSSAM-API-Geocoder/1.0 (https://github.com/686f6c61/API-TUSSAM)"}


def get_paradas_sin_direccion(db_path: str) -> list:
    """Obtiene las paradas que no tienen calle asignada."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT codigo, nombre, latitud, longitud "
        "FROM paradas WHERE calle IS NULL OR calle = ''"
    )
    result = cursor.fetchall()
    conn.close()
    return result


def geocode_nominatim(lat: float, lon: float) -> dict:
    """
    Geocodifica coordenadas usando Nominatim (OpenStreetMap).

    Returns:
        dict con calle, numero, codigo_postal, municipio, provincia,
        comunidad_autonoma, direccion_completa.
    """
    try:
        params = {
            "lat": lat, "lon": lon,
            "format": "json", "addressdetails": 1, "zoom": 18,
        }
        r = httpx.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"    Nominatim HTTP {r.status_code}")
            return None

        data = r.json()
        addr = data.get("address", {})

        calle = addr.get("road") or addr.get("footway") or addr.get("path") or ""
        numero = addr.get("house_number", "")
        cp = addr.get("postcode", "")
        municipio = addr.get("city") or addr.get("town") or addr.get("municipality", "")
        provincia = addr.get("county") or addr.get("state_district", "Sevilla")
        comunidad = addr.get("state", "")
        completa = f"{calle} {numero}".strip() if calle else ""

        if not calle:
            return None

        return {
            "calle": calle, "numero": numero, "codigo_postal": cp,
            "municipio": municipio, "provincia": provincia,
            "comunidad_autonoma": comunidad, "direccion_completa": completa,
        }
    except Exception as e:
        print(f"    Error: {e}")
        return None


def update_parada(db_path: str, codigo: str, direccion: dict):
    """Actualiza los campos de dirección de una parada."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        UPDATE paradas
        SET calle=?, numero=?, codigo_postal=?,
            municipio=?, provincia=?, comunidad_autonoma=?,
            direccion_completa=?
        WHERE codigo=?
    """,
        (
            direccion["calle"], direccion["numero"], direccion["codigo_postal"],
            direccion["municipio"], direccion["provincia"],
            direccion["comunidad_autonoma"], direccion["direccion_completa"],
            codigo,
        ),
    )
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Geocodifica las paradas de TUSSAM sin dirección."
    )
    parser.add_argument(
        "--proveedor", choices=["auto", "nominatim"], default="auto",
        help="Proveedor de geocodificación (default: auto)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simular sin guardar cambios en la BD"
    )
    parser.add_argument(
        "--db", default="data/tussam.db",
        help="Ruta a la base de datos SQLite (default: data/tussam.db)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Error: base de datos no encontrada en {args.db}")
        sys.exit(1)

    paradas = get_paradas_sin_direccion(args.db)

    if not paradas:
        print("✓ Todas las paradas ya tienen dirección asignada.")
        return

    print(f"Encontradas {len(paradas)} paradas sin geocodificar.")
    print(f"Proveedor: {args.proveedor}")
    print("Iniciando geocodificación (1 req/s para respetar Nominatim)...\n")

    ok = 0
    errores = 0

    for i, (codigo, nombre, lat, lon) in enumerate(paradas, 1):
        print(f"[{i}/{len(paradas)}] {codigo}: {nombre} ({lat:.6f}, {lon:.6f})")

        direccion = geocode_nominatim(lat, lon)

        if direccion:
            print(f"  ✓ {direccion['direccion_completa']} (CP: {direccion['codigo_postal']})")
            if not args.dry_run:
                update_parada(args.db, codigo, direccion)
            ok += 1
        else:
            print(f"  ✗ Sin resultado")
            errores += 1

        # Rate limiting: 1 petición por segundo
        if i < len(paradas):
            time.sleep(1.1)

    print(f"\n{'='*50}")
    print(f"Resultado: {ok} ok, {errores} errores")
    if args.dry_run:
        print("(Dry-run: no se guardaron cambios)")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
