"""
Tests para app/scheduler.py - Scheduler de sincronización semanal.
"""

import pytest
from unittest.mock import patch, MagicMock

from app import scheduler


# ── start_scheduler ──────────────────────────────────────────────────

def test_start_scheduler_enabled(monkeypatch):
    """Con SYNC_ENABLED=true debe arrancar el scheduler."""
    monkeypatch.setenv("SYNC_ENABLED", "true")
    monkeypatch.setenv("SYNC_DAY", "mon")
    monkeypatch.setenv("SYNC_HOUR", "3")
    monkeypatch.setenv("SYNC_MINUTE", "30")

    with patch.object(scheduler.scheduler, "add_job") as mock_add, \
         patch.object(scheduler.scheduler, "start") as mock_start:
        scheduler.start_scheduler()

    mock_add.assert_called_once()
    mock_start.assert_called_once()

    # Verificar que se configura el día correcto
    call_kwargs = mock_add.call_args
    trigger = call_kwargs[0][1]  # Segundo argumento posicional = CronTrigger
    assert call_kwargs[1]["id"] == "weekly_sync"


def test_start_scheduler_disabled(monkeypatch):
    """Con SYNC_ENABLED=false no debe arrancar."""
    monkeypatch.setenv("SYNC_ENABLED", "false")

    with patch.object(scheduler.scheduler, "add_job") as mock_add, \
         patch.object(scheduler.scheduler, "start") as mock_start:
        scheduler.start_scheduler()

    mock_add.assert_not_called()
    mock_start.assert_not_called()


def test_start_scheduler_defaults(monkeypatch):
    """Sin variables de entorno, usa defaults (sun, 4:00)."""
    monkeypatch.delenv("SYNC_ENABLED", raising=False)
    monkeypatch.delenv("SYNC_DAY", raising=False)
    monkeypatch.delenv("SYNC_HOUR", raising=False)
    monkeypatch.delenv("SYNC_MINUTE", raising=False)

    with patch.object(scheduler.scheduler, "add_job") as mock_add, \
         patch.object(scheduler.scheduler, "start"):
        scheduler.start_scheduler()

    mock_add.assert_called_once()


# ── stop_scheduler ───────────────────────────────────────────────────

def test_stop_scheduler_running():
    """Si el scheduler está corriendo, debe pararlo."""
    with patch.object(type(scheduler.scheduler), "running", new_callable=lambda: property(lambda self: True)), \
         patch.object(scheduler.scheduler, "shutdown") as mock_shutdown:
        scheduler.stop_scheduler()
    mock_shutdown.assert_called_once_with(wait=False)


def test_stop_scheduler_not_running():
    """Si el scheduler no está corriendo, no hace nada."""
    with patch.object(type(scheduler.scheduler), "running", new_callable=lambda: property(lambda self: False)), \
         patch.object(scheduler.scheduler, "shutdown") as mock_shutdown:
        scheduler.stop_scheduler()
    mock_shutdown.assert_not_called()


# ── _run_weekly_sync ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_weekly_sync_success():
    """Sync semanal exitoso debe ejecutar todas las fases."""
    with patch("app.scheduler.tussam_service") as mock_service:
        mock_service.sync_paradas_from_api = MagicMock(return_value=_async_return(967))
        mock_service.sync_lineas_from_api = MagicMock(return_value=_async_return(43))
        mock_service.sync_paradas_lineas_from_api = MagicMock(return_value=_async_return(1756))
        mock_service.sync_direcciones_all = MagicMock(
            return_value=_async_return({"total": 10, "ok": 9, "errors": 1})
        )

        await scheduler._run_weekly_sync()

    mock_service.sync_paradas_from_api.assert_called_once()
    mock_service.sync_lineas_from_api.assert_called_once()
    mock_service.sync_paradas_lineas_from_api.assert_called_once()
    mock_service.sync_direcciones_all.assert_called_once()


@pytest.mark.asyncio
async def test_run_weekly_sync_phase1_error():
    """Si fase 1 falla, no debe ejecutar fase 2 (geocodificación)."""
    with patch("app.scheduler.tussam_service") as mock_service:
        mock_service.sync_paradas_from_api = MagicMock(side_effect=Exception("API error"))
        mock_service.sync_direcciones_all = MagicMock(return_value=_async_return({}))

        await scheduler._run_weekly_sync()

    mock_service.sync_direcciones_all.assert_not_called()


@pytest.mark.asyncio
async def test_run_weekly_sync_phase2_error():
    """Si fase 2 falla, no debe propagar la excepción."""
    with patch("app.scheduler.tussam_service") as mock_service:
        mock_service.sync_paradas_from_api = MagicMock(return_value=_async_return(967))
        mock_service.sync_lineas_from_api = MagicMock(return_value=_async_return(43))
        mock_service.sync_paradas_lineas_from_api = MagicMock(return_value=_async_return(1756))
        mock_service.sync_direcciones_all = MagicMock(side_effect=Exception("Nominatim down"))

        # No debe lanzar excepción
        await scheduler._run_weekly_sync()


# ── Helper ───────────────────────────────────────────────────────────

class _async_return:
    """Wrapper para que MagicMock sea awaitable."""
    def __init__(self, value):
        self.value = value

    def __await__(self):
        yield
        return self.value
