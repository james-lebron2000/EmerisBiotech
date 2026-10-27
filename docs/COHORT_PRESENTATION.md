# Company, product and treatment-response presentation

The public cohort page is now a three-part presentation with approximately equal
section heights on desktop:

1. EmerisBiotech: human response research direction, clinical question definition,
   traceable evidence and independent validation.
2. Human Response Atlas: the patient-state / intervention / response-over-time
   framework, intended research use and currently available preview functions.
3. Treatment response simulation: the existing 120-patient paired scenario atlas.

The Chinese edition is `cohort-lab/zh.html`; `cohort-lab/index.html` remains English.
Both use the same CSS, animation and immutable canonical result. Chinese prose,
controls, regimen labels and patient/method dialogs are translated in `i18n.js`;
source URLs, JSON keys and reproducible exported results remain unchanged.

Company copy was checked against the current local `human_response_program/PROGRESS.md`
and `Emeris_投资人BP_后续页面内容稿.md` in the BP workspace on 2026-10-28. The former
is the more recent status record. Clinical observations, controlled interventions
and human response modeling are presented as the research direction. No company-owned
cohort, completed intervention integration, validated drug-response model, customer
benefit or R&D decision benefit is claimed. Historical internal MRI work is not
represented as causal drug efficacy evidence.

The demonstration keeps its source-backed regimen catalogue and uncalibrated,
fictional kinetics. Models, fixture version, CSV, clinical-outcome nulls and offline
verification are unchanged. See `REGIMEN_COHORT.md` for their scope.

Playback starts on the first visible entry into the simulation section, so the
animation does not finish while a visitor is reading the introduction. Leaving the
section or hiding the tab pauses playback. Reduced-motion preferences start paused.
The full-screen control expands the simulation section itself. On desktop, the
120-patient matrix remains visible together when that section is in view; mobile
keeps a readable scrolling grid. Header navigation and calls to action connect the
three parts without introducing another application or account flow.
