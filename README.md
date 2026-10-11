# fic-movilidad

Aplicación de soporte al coordinador de movilidad de la Facultade de Informática (UDC): seguimiento
de cada movilidad saliente desde la adjudicación hasta el acta de reconocimiento.

Ver [CLAUDE.md](CLAUDE.md) para el dominio, las reglas y las decisiones, y
[docs/requisitos.md](docs/requisitos.md) para el proceso y los requisitos completos.

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python manage.py migrate
python manage.py createsuperuser
python manage.py importar_destinos data/destinos_FIC_2026-27.csv
python manage.py importar_asignaturas data/catalogo_asignaturas_FIC_2026-27.csv
python manage.py importar_adjudicaciones "ruta/al/Excel de la ORI.xlsx" --listado provisional
python manage.py runserver   # http://127.0.0.1:8000/admin/
pytest
```

Correo (variables de entorno; en desarrollo los correos se imprimen en la consola):
`CORREO_COORDINADOR` (remitente, copia y resumen diario), `CORREO_ADMINISTRACION`, `EMAIL_HOST`,
`EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`; `ENVIO_AUTOMATICO_LA=0` desactiva el envío
automático del LA modificado. Resumen diario de plazos (cron): `python manage.py avisar_plazos`.

## Estado

- [x] Bloque 1: importar adjudicaciones de la ORI (provisional → definitiva), expedientes de
      movilidad con estados y traza de cambios, catálogo de destinos MoveON.
- [x] Bloque 2: versiones del acuerdo académico (Before / During), LA vigente y validaciones.
- [x] Bloque 3: plazos y alertas («Mis pendientes», resumen diario por correo, envío del LA
      modificado a Administración).
- [ ] Bloque 4: ToR, conversión de notas y acta ATM.
