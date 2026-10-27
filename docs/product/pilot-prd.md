# PRD del piloto: la búsqueda de consistencia

- Estado: borrador para revisión; alcance propuesto, no compromiso de implementación.
- Fecha: 2026-10-27.
- Responsable de producto: Maximiliano Castrucci.
- Nombre interno: athlete-platform. La marca no bloquea el desarrollo.
- Antecedente: [visión de producto en athlete, PR #10](https://github.com/mcastrucci/athlete/pull/10). Este documento es la referencia de alcance del nuevo producto; los cambios posteriores se revisan aquí.

## 1. Propósito

Ayudar al atleta a sostener un proceso coherente de desarrollo individual. La consistencia conecta objetivos, bloques, semanas, sesiones y respuesta del atleta.

> Entrená con un plan de desarrollo: objetivos claros, volumen organizado y progresión que se revisa con tus resultados.

La unidad de producto es el bloque de desarrollo: prioridades, dosis, progresión, evaluaciones y criterios de revisión. Cada sesión ejecuta una intención dentro de ese bloque.

La hipótesis es que coordinar el entrenamiento completo y explicar los ajustes aporta valor a un atleta que ya recibe programación. No asumimos que estas capacidades sean exclusivas ni que una programación de box carezca de estructura.

## 2. Usuario y problema

Atleta avanzado o comprometido que ya entrena CrossFit y puede combinar clases del box, Oly, fuerza, skills y accesorios. Tiene objetivos concretos, pero sus fuentes de programación no necesariamente conocen el conjunto de su entrenamiento.

Necesita saber qué está desarrollando, si la distribución del trabajo acompaña sus prioridades y qué conviene mantener, agregar, reducir, sustituir o mover.

La experiencia real difiere del plan por asistencia, escalados, cargas utilizadas, sesiones adicionales y respuesta individual. El producto debe evaluar ese recorrido.

El comprador y usuario inicial es el atleta. La gestión de coaches, alumnos y boxes es una expansión posible. El caso personal de Max es una referencia inicial, no una plantilla universal ni la única evidencia de utilidad.

## 3. Hipótesis y resultado del piloto

Hipótesis principal: un atleta con programación existente entiende mejor su entrenamiento, aplica ajustes razonados y vuelve a revisar su evolución semanalmente.

El piloto debe completar este circuito:

1. Perfil y objetivos.
2. Carga y confirmación de la programación.
3. Evaluación contra el contexto del atleta.
4. Propuesta de ajustes y aceptación explícita.
5. Registro de ejecución y respuesta.
6. Revisión semanal que informa la siguiente semana.

La mejora de marcas deportivas es exploratoria. Un piloto pequeño y corto no demuestra eficacia fisiológica ni superioridad frente a otro método.

## 4. Alcance propuesto

| Área | Piloto | Después del piloto |
| --- | --- | --- |
| Usuario | Atleta adulto que ya entrena; español | Principiantes y gestión de boxes/coaches |
| Journey | CrossFit con Oly, fuerza, acondicionamiento y accesorios existentes | Hybrid/HYROX con evaluaciones y reglas propias |
| Planificación | Evaluar y ajustar una programación existente dentro de un bloque explícito | Generar programas completos desde cero |
| Skills | Registrar nivel, objetivo y práctica existente; detectar incompatibilidades | Biblioteca de evaluaciones y progresiones adaptativas de skills |
| Entrada | Texto, XLSX, PDF y fotos JPG/PNG; corrección manual | Más formatos e integraciones automáticas |
| Seguimiento | Sesiones, ejecución parcial, respuesta y revisión semanal | Wearables y adaptación continua |
| Experiencia | Flujo usable desde el móvil; formato de interfaz a definir | Apps nativas y funciones sociales |

El bloque inicial puede ser propuesto por el producto o provenir del coach. Si se desconoce la intención del programa externo, se solicita o se marca como desconocida.

Se propone trabajar con las últimas dos semanas, el plan de la próxima semana cuando esté disponible y un horizonte de bloque de cuatro semanas. Dos semanas permiten describir lo observado; no bastan para afirmar la calidad de toda una periodización.

No es necesario conocer las cuatro semanas futuras del box: se conserva la intención del bloque y se ajusta con la nueva información, sin inventar sesiones.

## 5. Recorrido y requisitos

### P1. Perfil, prioridades y restricciones

Capturar experiencia, objetivos ordenados por prioridad, métricas iniciales con fecha, disponibilidad real, equipamiento, compromisos fijos y flexibles, y restricciones reportadas.

Registrar los skills relevantes diferenciando ejecución aislada, consistencia, series e integración en metcon; no exigir una evaluación exhaustiva para empezar.

El atleta identifica qué programación quiere conservar y qué permite modificar. Un objetivo sin benchmark puede ser provisional y debe explicitar qué evaluación falta.

**Aceptación:** dos atletas con distinta disponibilidad y compromisos pueden usar el flujo sin heredar datos ni reglas personales entre sí; los campos faltantes permanecen visibles.

### P2. Carga, interpretación y confirmación

Aceptar texto pegado, XLSX, PDF con texto o escaneado y fotos legibles de una hoja o pizarra. Conservar la fuente y presentar una interpretación editable antes de evaluarla.

Extraer fechas, sesiones y bloques; ejercicios/variantes, series, repeticiones, carga, intensidad, descansos y formato del metcon cuando estén presentes. Distinguir unidades, porcentajes y referencias de carga.

No completar como hechos los campos ausentes. Ante texto ambiguo, imágenes ilegibles, ejercicios desconocidos o formatos no soportados, señalar el problema y permitir corrección manual o nueva carga. Mostrar las limitaciones de tamaño y páginas antes de subir; sus valores se definirán en el plan técnico.

No duplicar sesiones silenciosamente al volver a cargar un archivo. Cambiar un dato confirmado deja desactualizada la evaluación afectada hasta recalcularla.

**Aceptación:** la evaluación usa una versión confirmada por el atleta; cada dato relevante permite localizar su fuente o corrección manual. Hay ejemplos de aceptación para cada formato, incluida una foto ambigua y una recarga.

### P3. Evaluación del entrenamiento completo

Comparar lo observado con objetivos, disponibilidad, restricciones y contexto del bloque. Incluir:

- Distribución de trabajo por capacidades y patrones relevantes.
- Dosis por ejercicio y exposición repetida entre sesiones, incluyendo trabajo adicional.
- Proximidad de estímulos potencialmente incompatibles y compromisos fijos.
- Oportunidades de desarrollo, mantenimiento y evaluación.
- Datos faltantes y límites de la interpretación.

Separar volumen planificado y realizado. Conservar unidades originales: repeticiones, kilos, tiempo, distancia o calorías no son intercambiables. Si se usa una equivalencia, identificar modelo, supuestos y límites; no presentarla como medida directa de recuperación.

Evitar una nota global arbitraria de “bueno/malo”. Los hallazgos deben ser específicos, priorizados y verificables. No atribuir una intención de periodización que no figure en la fuente ni se haya confirmado.

**Aceptación:** el informe distingue datos, estimaciones e hipótesis; un dato desconocido no se convierte en cero ni en ausencia de problema.

### P4. Ajustes y explicación por ejercicio

Proponer una versión modificada comparable con el plan original. Cada cambio incluye dosis, ubicación, objetivo y efecto sobre el tiempo y la carga total. Respetar los compromisos declarados inmodificables; si no existe una propuesta compatible, explicar el conflicto y pedir una decisión.

Para cada ejercicio prescrito o evaluado debe poder consultarse un “por qué” breve, con detalle adicional:

- Aporte al objetivo: desarrollo, mantenimiento, transferencia o práctica técnica.
- Dosis y momento: intensidad, frecuencia, progresión y ubicación.
- Costo y compatibilidad con el resto del entrenamiento.
- Restricciones, información faltante y alternativas.
- Qué resultado justificaría progresar, mantener, reducir o reemplazar.

Las explicaciones deben corresponder con los datos, reglas y cálculos realmente aplicados. Distinguir evidencia disponible, criterio de coaching e hipótesis; no inventar referencias ni precisión.

El atleta acepta, modifica o rechaza la propuesta. Conservar el original, las alternativas y la versión aceptada. Ningún ajuste cambia silenciosamente el plan activo.

**Aceptación:** cada modificación es rastreable y puede rechazarse; las restricciones explícitas se cumplen o bloquean la propuesta. La explicación cambia si cambian el contexto o la dosis.

### P5. Ejecución y respuesta

Permitir registrar una sesión completa, parcial, modificada o no realizada, con cargas/repeticiones/resultados cuando se conozcan. Solicitar un check-in breve de esfuerzo, sueño, energía, molestias y notas, sin obligar a completar todos los campos.

Distinguir “sin registrar”, “no realizado” y “realizado con dosis desconocida”. Mostrar fecha y antigüedad de los datos usados para decidir; no transformar una respuesta de ayer en el estado de hoy.

**Aceptación:** una sesión parcial no se computa como completa; cero y desconocido generan resultados diferentes; las correcciones actualizan los informes afectados sin borrar la trazabilidad.

### P6. Revisión semanal y continuidad del bloque

Comparar intención, ejecución y respuesta; mostrar qué cambió, qué quedó pendiente y qué conviene revisar. Proponer la próxima semana con la nueva programación disponible.

Mantener prioridades, criterios de progresión y evaluación del bloque entre semanas. No rehacerlo por variaciones aisladas sin justificarlo ni añadir trabajo para compensar automáticamente todo lo no realizado.

**Aceptación:** la revisión puede explicar qué decisión se mantiene o cambia y qué observación la respalda. El atleta confirma la siguiente versión antes de activarla.

## 6. Límites de la recomendación y confianza

El producto informa y ayuda a planificar entrenamiento; no diagnostica lesiones ni prescribe rehabilitación. Respeta las restricciones reportadas y puede pedir revisión humana cuando faltan datos o una propuesta no resulta compatible.

Los umbrales personales del repositorio athlete no se vuelven límites universales. Las reglas deportivas y sus explicaciones requieren revisión de alguien con experiencia profesional pertinente antes del uso con participantes externos.

Las cargas estimadas no certifican que alguien esté recuperado. Una recomendación debe poder declarar “información insuficiente”.

El atleta conserva control sobre sus datos: acceso individual, exportación y eliminación desde el piloto, aunque la operación inicialmente sea asistida. Los archivos personales, fotos y registros no se guardan como fixtures ni se publican en GitHub. No se usan para entrenamiento de modelos sin un consentimiento separado.

## 7. Cómo evaluaremos el piloto

Propuesta para acordar: cinco atletas, incluyendo a Max y al menos tres personas con una combinación de entrenamiento diferente, durante cuatro semanas posteriores al onboarding. Seguimiento asistido y revisión experta de las propuestas; no prometer autonomía completa.

Registrar numeradores, denominadores y motivos de abandono. Separar ayuda manual del producto para no atribuirle capacidades que todavía hace una persona.

| Señal | Definición | Umbral propuesto |
| --- | --- | --- |
| Activación | Perfil + programación confirmada + primera evaluación consultada | Al menos 4 de 5 |
| Valor aplicado | Aplica al menos un ajuste y explica para qué le sirve | Al menos 3 de 5 |
| Continuidad | Completa al menos 3 revisiones semanales de 4 | Al menos 3 de 5 |
| Comprensión | Puede explicar el motivo de dos decisiones de su plan en una entrevista breve | Al menos 4 de 5 |
| Calidad | Revisión de todos los planes propuestos y muestra de explicaciones por el revisor experto | Sin conflictos críticos de restricciones pendientes al entregar un plan |

Medir también tiempo de onboarding, correcciones por formato, minutos de asistencia por atleta/semana, recomendaciones rechazadas y motivos. Son señales de costo operativo y fricción; no sustituirlas por cantidad de sesiones registradas.

Cumplir los umbrales justifica una siguiente iteración, no prueba ajuste producto-mercado. Si falla la utilidad, revisar propuesta y alcance; si falla la calidad de interpretación o prescripción, corregir antes de ampliar el grupo.

## 8. Fuera de alcance

- Programación desde cero para personas que no entrenan.
- Un optimizador universal que garantice el mejor plan o resultados.
- Gestión comercial de boxes, permisos de coach y programación grupal.
- Journey Hybrid/HYROX completo y progresiones automáticas de skills.
- Diagnóstico, rehabilitación, nutrición y recomendaciones de suplementos.
- Wearables, sincronización con otras apps, videoanálisis y redes sociales.
- Cobros, planes comerciales y apps nativas.

Estos puntos no se consideran implementados por reservarles espacio en el modelo futuro.

## 9. Decisiones pendientes antes del plan técnico

| ID | Decisión | Propuesta inicial |
| --- | --- | --- |
| D1 | Cohorte, duración y reclutamiento | Cinco atletas, cuatro semanas; validar acceso a participantes |
| D2 | Alcance de prescripción | Ajustar planes existentes; generación completa y progresiones nuevas de skills después |
| D3 | Operación y revisión deportiva | Piloto asistido; identificar revisor y disponibilidad antes de incorporar terceros |
| D4 | Interfaz y fricción tolerable | Uso móvil; comparar web propia y una experiencia asistida al planificar |
| D5 | Métricas y umbrales | Usar sección 7 como hipótesis; acordar antes de medir |
| D6 | Capacidades de importación | Cuatro entradas de P2; definir límites y corpus representativo |
| D7 | Datos y proveedores | Acordar almacenamiento, acceso, retención, eliminación y tratamiento por proveedores |

El nombre comercial, el modelo de monetización y la gestión de boxes no bloquean el piloto.

## 10. Seguimiento en el repositorio

Este PRD guarda el alcance y los criterios de aceptación. El futuro `docs/engineering/pilot-plan.md` tendrá etapas con checkboxes, validación y enlaces a PRs; todavía no se define arquitectura ni stack.

- [x] Registrar el primer borrador del PRD.
- [ ] Revisar alcance y resolver D1–D7 con decisiones fechadas.
- [ ] Preparar el plan técnico y decidir qué reutilizar del motor existente.
- [ ] Verificar los criterios P1–P6 y la revisión experta antes de incorporar terceros.
- [ ] Ejecutar el piloto y registrar resultados contra las hipótesis.

Los cambios de alcance se hacen por PR. Cada etapa técnica debe poder vincularse con un requisito de este documento y una comprobación observable. No se trasladan datos personales de athlete al producto; cualquier reutilización del motor se define en el plan técnico.
