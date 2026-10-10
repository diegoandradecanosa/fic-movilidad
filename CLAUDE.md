# fic-movilidad — Aplicación de soporte al coordinador de movilidad (FIC, UDC)

Aplicación web para que el coordinador de movilidad de la Facultade de Informática (FIC) de la
Universidade da Coruña siga cada movilidad saliente (Erasmus+, Bilateral SM, SICUE) desde la
adjudicación del destino hasta la entrada de las notas en el expediente: acuerdo académico (LA),
sus modificaciones y el acta de reconocimiento ("After the Mobility", ATM).

Documento de requisitos completo (fuente de verdad funcional):
https://claude.ai/code/artifact/ae2b61ec-0700-4c90-9985-7a0eabd10cb0
(copia en el repo: `docs/requisitos.md`, exportada el 10/10/2026 y anonimizada).

## Stack y comandos

- Python 3.10+ (probado en 3.10 y 3.13), Django 5.1, SQLite en desarrollo, PostgreSQL en producción (nube externa, datos en la UE).
- Tests: `pytest` (pytest-django). Ejecutar siempre antes de dar algo por terminado.
- Arranque local: `python manage.py migrate && python manage.py runserver`.
- Importar adjudicaciones: `python manage.py importar_adjudicaciones <excel> --listado provisional|definitiva`.
- Importar destinos: `python manage.py importar_destinos data/destinos_FIC_2026-27.csv`.

## Estructura

- `config/` — settings y urls del proyecto.
- `movilidad/` — app principal: modelos, admin, importadores (`movilidad/importers/`), comandos.
- `data/` — catálogos públicos (materias de la FIC y destinos MoveON 2026/27). NO contiene datos de alumnos.
- `tests/` — tests con datos sintéticos generados en el propio test.
- `docs/` — requisitos (`requisitos.md`). Los documentos con datos de alumnos se quedan fuera del repo
  (OneDrive del coordinador / carpeta `privado/`, ignorada por git).

## Recursos locales (solo en el Mac del coordinador, no en el repo)

- Skill `acta-reconocimiento` y scripts en `ToRs26/_recursos_acta` (lib_extract, lib_grades, lib_docx)
  y el JSON de equivalencias de notas (Resol. 18/09/2017): reutilizar en el bloque 4.
- Excel de adjudicaciones de la ORI (DECANATO/Erasmus/<curso>/): para probar los importadores en local;
  nunca copiarlos a `tests/` ni hacer commit.

## Reglas de trabajo (obligatorias)

1. **Nunca subir datos reales de alumnos al repositorio** (nombres, DNI, notas, LA, ToR, correos).
   Los tests generan sus Excel/PDF sintéticos. Los catálogos de `data/` son públicos.
2. **DNI**: nunca se guarda en claro. Se guarda un HMAC (`dni_hash`, para emparejar importaciones)
   y una máscara (`dni_mascara`, p. ej. `34*****7R`) para mostrar.
3. **Nada firmado se sobrescribe**: LA y actas se versionan; una corrección crea una versión nueva.
4. **La IA propone, el coordinador decide**: todo lo extraído por IA (LA, ToR, emparejamientos,
   conversión de notas) queda como borrador hasta su aceptación explícita.
5. Cada cambio de estado de una movilidad se registra en `EventoMovilidad` (quién, cuándo, de qué a qué).
6. Interfaz en castellano/gallego; códigos y nombres internos en castellano sin tildes.
7. Trabajar por bloques pequeños con tests; abrir PR por bloque.

## Dominio (resumen)

Fases del proceso: 1) Adjudicación → 2) LA "Before" → 3) Modificaciones "During" (y ampliación,
reducción, interrupción) → 4) ToR, acta ATM y envío a Administración.

Estados de la Movilidad (orden): ADJUDICADA → ACEPTADA (o RENUNCIADA) → LA_PREPARACION →
LA_FIRMADO → EN_MOVILIDAD ⇄ MODIFICACION_ABIERTA → FINALIZADA → TOR_RECIBIDO → ACTA_BORRADOR →
ACTA_FIRMADA → ENVIADA_ADMINISTRACION → CERRADA. Desde CERRADA se puede abrir una corrección
(vuelve a ACTA_BORRADOR).

Formatos del acuerdo académico: OLA en EWP (Erasmus+ con destino en EWP), PDF/Word de la ORI
(Erasmus+ fuera de EWP y bilaterales) o acuerdo SICUE. Si el Before no se aprobó en EWP, todos los
During van en PDF (correo ORI 02/06/2026).

### Excel de adjudicaciones de la ORI

Cada fila es una solicitud (estudiante × destino × orden de preferencia). Columnas por nombre (el
orden y las columnas extra varían entre cursos): `NOME` ("Apellidos, Nombre"), `DNI`, `Titulación`,
`Programa`, `Orde de preferencia`, `Ano acad.`, `Inicio (semestre)` ("1º semester 2025/26"),
`Duración (semestres previstos)`, `País`, `Institución`, `Observacións ORI`,
`Certificación  de idiomas` (doble espacio), `Estado de solicitude - interno`, `Nota media`
(número o texto con coma), `Plaza ofertada`, `Prazo da Convocatoria` ("Convocatoria1"),
`Outros méritos (max. 5%)`, `Motivos rexeitamento`, `Condicións para adxudicación`.
Estados vistos: Adxudicatario/a, Rexeitado/a, Suplente N, "Suplente nº N", "lista de agarda",
"Non se oferta praza" (mayúsculas/minúsculas variables).

### Reglas de negocio (validar y avisar, nunca bloquear el criterio del coordinador)

| ID | Regla | Fuente |
|---|---|---|
| RN-01 | Mínimo ECTS UDC: 18 semestral, 36 anual (excepción si solo queda TFG) | Bases 2026/27 art. 17.3 |
| RN-02 | Máximo: el de destino (30/60); si lo supera, 39/78 (45/90 simultáneas) | Bases art. 17.3 |
| RN-03 | Diferencia UDC–destino ≤ 2 ECTS semestral, ≤ 4 anual | Bases 17.4 |
| RN-04 | Horas distintas por crédito: ECTS = horas / 8,5 | Bases 17.4 |
| RN-05 | 1 modificación por semestre, 1 mes desde llegada / inicio 2º semestre | Reglamento 3/2025 art. 28 |
| RN-06 | Firma de destino del LA anterior al certificado de llegada | Bases 26.c |
| RN-07 | Materia evaluada en la UDC en el 1er semestre no entra en el LA del 2º | Bases 17.2 |
| RN-12 | Varias materias de destino → una UDC: media ponderada por ECTS | Reglamento 25.2 |
| RN-13 | Conversión de notas con la tabla oficial (Resol. 18/09/2017) | skill acta-reconocimiento |
| RN-15 | Acta a la ORI ≤ 5 semanas desde la recepción del ToR | correo ORI 13/02/2026 |

(Lista completa RN-01…RN-18 en el documento de requisitos.)

## Decisiones tomadas

- Despliegue en nube externa; datos en la UE; contrato de encargado del tratamiento; DPD consultado
  antes de producción. Inicio de sesión con cuenta UDC (Microsoft Entra ID) — pendiente de implementar.
- Roles: coordinador (todo), tutor académico (futuro, alumnos asignados), Administración (lectura de
  todo + anotaciones/peticiones al alumno o al coordinador, sin editar LA ni actas).
- SICUE comparte el flujo de Erasmus+; solo cambia el formato del acuerdo.
- Integraciones previstas: Microsoft 365 (Outlook/Teams), OneDrive, exportaciones y avisos de
  MoveON, API de Claude para extraer LA/ToR. La API de MoveON requiere activación por la ORI.
