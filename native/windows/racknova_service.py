from __future__ import annotations

import os
import sys

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

    def SvcDoRun(self):
        log_path = logs_dir() / "racknova-local-service.log"
        os.environ["RACKNOVA_NATIVE_SERVICE_LOG"] = str(log_path)

        servicemanager.LogInfoMsg("RackNova Local iniciando.")

        app = get_app()
        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
            access_log=False,
        )
        self.server = uvicorn.Server(config)

        try:
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
