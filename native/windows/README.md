# RackNova Native Windows — F1

F1 crea la base real del instalador nativo de Windows.

El workflow genera:

`RackNova_Setup_Native_F1.exe`

Incluye backend FastAPI empaquetado, Dashboard Local, PostgreSQL 16.15,
servicio Windows, DPAPI, firewall LAN, health check y diagnóstico.

## No requiere en el PC cliente

- Docker Desktop
- WSL2
- virtualización BIOS
- Python instalado
- Node instalado

## Importante

F1 es una foundation build interna. No distribuir a clientes finales todavía.

F2 añadirá:
- código de activación;
- credencial única por nodo;
- bootstrap Cloud -> Local con identidad de nodo;
- eliminación de la dependencia comercial del secret global B3;
- UX final de activación.
