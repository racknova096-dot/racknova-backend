from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil
import uvicorn

from racknova_native_config import logs_dir
from racknova_native_entry import get_app


class RackNovaLocalService(win32serviceutil.ServiceFramework):
    _svc_name_ = "RackNovaLocal"
    _svc_display_name_ = "RackNova Local"
    _svc_description_ = (
        "Servidor Local-First de RackNova: API, POS, inventario, dashboard y sync."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server: uvicorn.Server | None = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.server is not None:
            self.server.should_exit = True
        win32event.SetEvent(self.stop_event)

    def _wait_for_postgres(self, timeout_seconds: int = 120) -> bool:
        pg_isready = (
            Path(sys.executable).resolve().parent
            / "PostgreSQL"
            / "bin"
            / "pg_isready.exe"
        )

        if not pg_isready.exists():
            raise RuntimeError(
                f"No se encontró pg_isready.exe: {pg_isready}"
            )

        deadline = time.monotonic() + timeout_seconds

        servicemanager.LogInfoMsg(
            "RackNova Local esperando PostgreSQL en 127.0.0.1:54329."
        )

        while time.monotonic() < deadline:
            if (
                win32event.WaitForSingleObject(self.stop_event, 0)
                == win32event.WAIT_OBJECT_0
            ):
                return False

            try:
                proc = subprocess.run(
                    [
                        str(pg_isready),
                        "-h",
                        "127.0.0.1",
                        "-p",
                        "54329",
                        "-t",
                        "2",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NO_WINDOW",
                        0,
                    ),
                )

                if proc.returncode == 0:
                    servicemanager.LogInfoMsg(
                        "PostgreSQL listo. Iniciando RackNova Local."
                    )
                    return True

            except (OSError, subprocess.SubprocessError):
                pass

            if (
                win32event.WaitForSingleObject(self.stop_event, 2000)
                == win32event.WAIT_OBJECT_0
            ):
                return False

        raise RuntimeError(
            "PostgreSQL no estuvo listo en 120 segundos."
        )

    def SvcDoRun(self):
        try:
            log_path = logs_dir() / "racknova-local-service.log"
            os.environ["RACKNOVA_NATIVE_SERVICE_LOG"] = str(log_path)

            servicemanager.LogInfoMsg("RackNova Local iniciando.")

            if not self._wait_for_postgres():
                servicemanager.LogInfoMsg(
                    "RackNova Local detenido durante espera de PostgreSQL."
                )
                return

            app = get_app()

            config = uvicorn.Config(
                app=app,
                host="0.0.0.0",
                port=8000,
                log_level="info",
                access_log=False,
                # El SCM de Windows no garantiza una consola/stdout válida.
                # Uvicorn intenta configurar formatters de consola por defecto;
                # en el servicio congelado eso puede abortar antes de abrir 8000.
                # Los eventos críticos del servicio se registran con servicemanager.
                log_config=None,
            )
            self.server = uvicorn.Server(config)
            self.server.run()

        except Exception as exc:
            servicemanager.LogErrorMsg(
                f"RackNova Local terminó con error: {exc}"
            )
            raise
        finally:
            servicemanager.LogInfoMsg("RackNova Local detenido.")

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Windows Service Control Manager inicia el EXE sin argumentos.
        # En ejecutables congelados debemos conectar explícitamente
        # el proceso con el dispatcher de servicios.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(RackNovaLocalService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # install / remove / start / stop / debug
        win32serviceutil.HandleCommandLine(RackNovaLocalService)
