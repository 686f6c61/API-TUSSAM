"""
TUSSAM API - Scheduler de Sincronización Semanal
=================================================

Ejecuta automáticamente la sincronización de datos de TUSSAM
usando APScheduler con AsyncIOScheduler.

Configuración por variables de entorno:
- SYNC_DAY: Día de la semana (mon, tue, wed, thu, fri, sat, sun). Default: sun
- SYNC_HOUR: Hora UTC (0-23). Default: 4
- SYNC_MINUTE: Minuto (0-59). Default: 0
- SYNC_ENABLED: Activar/desactivar scheduler (true/false). Default: true
"""

import os
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app import database
from app.services.tussam import tussam_service

logger = logging.getLogger("tussam.scheduler")

scheduler = AsyncIOScheduler()


async def _run_weekly_sync():
    """
    Job semanal: sincroniza paradas, líneas, relaciones y direcciones.

    Cada fase es independiente: un fallo en líneas no impide intentar las
    relaciones. Al final se emite un resumen honesto —``error`` si alguna fase
    falló— para que la monitorización no interprete un cierre en INFO como éxito.
    """
    # Comparte el lock de sincronización con los endpoints /sync/*: si hay un
    # sync manual en curso, no arrancamos el semanal para no solapar escrituras
    # sobre el mismo catálogo.
    sync_lock = tussam_service.get_sync_lock()
    if sync_lock.locked():
        logger.warning(
            "Sync semanal omitido: ya hay una sincronización manual en curso"
        )
        return

    async with sync_lock:
        await _run_weekly_sync_locked()


async def _run_weekly_sync_locked():
    """Cuerpo del sync semanal, ejecutado ya con el lock de sincronización."""
    logger.info("Iniciando sincronización semanal programada...")
    fallos: list[str] = []

    # Fase 1: Sync de datos estructurales (cada paso independiente)
    try:
        count_paradas = await tussam_service.sync_paradas_from_api()
        logger.info("Sync paradas OK: %d", count_paradas)
    except Exception:
        logger.exception("Error syncing paradas — abortando sync semanal")
        return  # Sin paradas no tiene sentido continuar con el resto.

    try:
        count_lineas = await tussam_service.sync_lineas_from_api()
        logger.info("Sync líneas OK: %d", count_lineas)
    except Exception:
        logger.exception("Error syncing líneas — continuando con relaciones")
        fallos.append("líneas")

    try:
        count_relaciones = await tussam_service.sync_paradas_lineas_from_api()
        logger.info("Sync relaciones OK: %d", count_relaciones)
    except Exception:
        logger.exception("Error syncing relaciones parada-línea")
        fallos.append("relaciones")

    # Fase 2: Geocodificación de direcciones (más lento, ~4 min)
    try:
        result = await tussam_service.sync_direcciones_all()
        logger.info(
            "Geocodificación OK: %d total, %d ok, %d errores",
            result["total"], result["ok"], result["errors"],
        )
    except Exception:
        logger.exception("Error en geocodificación")
        fallos.append("geocodificación")

    # Limpieza de cache expirada para acotar el crecimiento del fichero SQLite.
    try:
        purgadas = await database.purge_tiempos_cache()
        logger.info("Cache de tiempos purgada: %d filas expiradas", purgadas)
    except Exception:
        logger.exception("Error purgando cache de tiempos")
        fallos.append("purga de cache")

    if fallos:
        logger.error(
            "Sincronización semanal completada CON FALLOS en: %s", ", ".join(fallos)
        )
    else:
        logger.info("Sincronización semanal completada sin incidencias.")


def start_scheduler():
    """Configura y arranca el scheduler según variables de entorno."""
    enabled = os.getenv("SYNC_ENABLED", "true").lower() == "true"
    if not enabled:
        logger.info("Scheduler desactivado (SYNC_ENABLED=false)")
        return

    day = os.getenv("SYNC_DAY", "sun")
    try:
        # Mediodía UTC por defecto: la API de TUSSAM solo lista las líneas activas
        # en el instante de la consulta, así que hay que sincronizar en horario de
        # servicio para capturar la red completa (ver guardián de completitud).
        hour = int(os.getenv("SYNC_HOUR", "11"))
        minute = int(os.getenv("SYNC_MINUTE", "0"))
    except ValueError as e:
        logger.error("SYNC_HOUR/SYNC_MINUTE inválidos: %s. Scheduler desactivado.", e)
        return

    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        logger.error("SYNC_HOUR=%d o SYNC_MINUTE=%d fuera de rango. Scheduler desactivado.", hour, minute)
        return

    scheduler.add_job(
        _run_weekly_sync,
        CronTrigger(day_of_week=day, hour=hour, minute=minute),
        id="weekly_sync",
        name="Sincronización semanal TUSSAM",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler activo: sync cada %s a las %02d:%02d UTC",
        day, hour, minute,
    )


def stop_scheduler():
    """Para el scheduler si está corriendo."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido.")
