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

Acta de reconocimiento (bloque 4): los recursos de la skill `acta-reconocimiento` no están en el repo.
`RECURSOS_ACTA_DIR` apunta a `ToRs26/_recursos_acta` (plantilla docx, cabecera, logo y JSON de
equivalencias); `ACTA_FIRMANTE` es el nombre que firma; `CORREO_ORI` recibe copia del «ATM de …».
Importar la tabla oficial de equivalencias: `python manage.py importar_equivalencias "$RECURSOS_ACTA_DIR/equivalencias_calificaciones_espana.json"`.
El PDF del acta se genera solo si LibreOffice (`soffice`) está instalado. Extracción con IA de las
notas del ToR y de las filas del LA (acción «Proponer las filas desde el PDF con IA» en una versión
en borrador): `pip install -e ".[ia]"` y `ANTHROPIC_API_KEY` (envía el PDF a Anthropic: pendiente
del DPD). Lo extraído queda en borrador, con las dudas en las notas de la versión y en cada fila.

## Roles

Se crean al migrar dos grupos: **Coordinación** (todo) y **Administración** (lectura de todos los
expedientes, LA, ToR y actas, y anotaciones/peticiones; no edita LA ni actas). Asigna cada usuario a
su grupo en el admin (Usuarios). Se entra en `/cuentas/login/` con usuario y contraseña de Django o,
si está configurada, con la cuenta UDC. El admin (`/admin/`) solo lo usa Coordinación.

## Cuenta UDC (Microsoft Entra ID)

Desactivada mientras no estén las tres variables `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` y
`ENTRA_CLIENT_SECRET`. Hay que pedir al Servizo de Informática el registro de una aplicación en
Entra ID con:

- cuentas **solo de este directorio** (inquilino de la UDC);
- plataforma **Web**, URI de redirección `https://<servidor>/oidc/callback/` (y
  `http://localhost:8000/oidc/callback/` para probar en local);
- un **secreto de cliente**; permisos delegados `openid`, `email` y `profile` (sin Microsoft Graph).

Entra quien tenga en la aplicación un usuario activo con su correo `@udc.es`/`@udc.gal` (se da de
alta en el admin, con el correo, y se le asigna el grupo). Con `ENTRA_CREAR_USUARIOS=1` se crean
solos, sin permisos, hasta que se les asigne un grupo. `ENTRA_DOMINIOS` cambia los dominios
admitidos. Usuario y contraseña siguen funcionando (superusuario, desarrollo).

## Estado

- [x] Bloque 1: importar adjudicaciones de la ORI (provisional → definitiva), expedientes de
      movilidad con estados y traza de cambios, catálogo de destinos MoveON.
- [x] Bloque 2: versiones del acuerdo académico (Before / During), LA vigente y validaciones.
- [x] Bloque 3: plazos y alertas («Mis pendientes», resumen diario por correo, envío del LA
      modificado a Administración).
- [x] Bloque 4: ToR (registro, extracción con IA opcional, emparejamiento con el LA), conversión de
      notas, acta «After the Mobility» (docx, versiones y correcciones) y envío «ATM de …».
- [x] Bloque 5: panel de expedientes (`/`), ficha del expediente y acceso de Administración con
      anotaciones y peticiones al alumno o al coordinador.
- [x] RF-10: importar el PDF del LA (OLA de EWP, modelo de la ORI, SICUE) con IA como borrador.
- [x] RF-11: comparar la «Plantilla LA.xlsx» con el LA vigente (enlace «Comparar con la plantilla»
      en la movilidad; el Excel no se guarda).
- [x] Inicio de sesión con la cuenta UDC (Entra ID), a falta del registro de la aplicación.
