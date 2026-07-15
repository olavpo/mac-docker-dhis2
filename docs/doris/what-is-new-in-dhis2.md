# What is New in DHIS2

Monday 15 June, 08:30 · Sophus Lie Auditorium
Presenters: Markus Bekken and Marta Vila, with colleagues from the HISP Centre core, product, and Climate & Health teams
[Recording](https://youtu.be/V9QJrJazmaM) · [Slides](https://drive.google.com/drive/folders/1PSye-1zUa7vWQ5DDwqnupVCtc_nojSBW) · [Dryfta](https://dac2026.dryfta.com/program-schedule/program/6/what-is-new-in-dhis2-eng-fr)

## Summary

The opening plenary covered three things: the new DHIS2 shared service fee, the app competition finalists, and the v43 / Android Capture 3.4 feature release. The Climate & Health segment that closed the session is not in the transcript.

The shared service fee is HISP Centre's answer to donors (starting with the 2023 Lusaka Agenda and reinforced by 2024–25 funding cuts) asking it to recover core costs elsewhere. The fee funds only the shared core work the University of Oslo does (security patches, releases, documentation, global training), not implementation, servers, or technical assistance, which stay in a competitive provider ecosystem to avoid vendor lock-in. It is voluntary, not a license or tax, and priced by World Bank income classification: USD 5,000 per year per production instance for low-income public sector, rising steeply for high-income and private-sector use. Paying organizations become "contributing partners" with more input into strategic priorities. HISP Centre framed this alongside its digital public goods partnerships (MOSIP, OpenFN, OpenCRVS), a broader technology partner program (including Esri), and a push to position HISP groups as a multi-technology digitalization network rather than a DHIS2-only provider. It hopes the fee also drives rationalization of the many redundant DHIS2 instances.

The app competition had four finalists: an AI metadata automation Chrome extension (HISP MENA) that builds programs from plain-English descriptions; Audit Vision (SoDigital / HISP Mozambique) for real-time metadata change tracking, alerts, and rollback; Magic Glasses 2 (John Painter), an R/Shiny data-quality and outlier workflow on aggregate data; and Org Unit Sync (HISP Sri Lanka) for comparing and synchronizing org-unit metadata across instances.

Markus and Marta then demoed v43. The performance work is the headline: single events and tracker events were split into separate tables, unlocking optimizations, and there is now an automated performance testing rig. Field-filtering fixes gave large throughput gains on tracker endpoints. Other changes: the completed metadata management app (replacing Maintenance), a new agentic-coding developer track (official AI skills, crawlable docs, bundled app-platform source), optional Apache Doris analytics backend, custom string overrides via the datastore, custom instance theming, and a graceful session-expiry warning. On collect and analyze: configurable tracked-entity search, non-analyzable (sensitive) attributes kept out of analytics, JavaScript support restored in the new data entry app, richer working-list filters, program-rule action priority, per-instance period selection, dashboard slideshow autoplay, maps improvements, a bulk data entry plugin, and a new individual data visualizer app that will consolidate line lists, pivot tables, and charts for tracker data.

## Key points

- Shared service fee: voluntary, USD 5,000/year per production instance for low-income public sector, scaling up by income group; funds core only, not implementation.
- Contributing partners get a say in strategic direction; goal is sustainable core financing without vendor lock-in.
- v43 performance gains come from splitting event/tracker tables plus field-filtering fixes; an automated performance test rig now guards against regressions.
- New metadata management app has functional parity and is bundled in v43, ready to migrate to.
- New individual data visualizer app will replace line listing and event reports, then absorb charts.

## Resources

- [App Competition finalist videos (folder)](https://drive.google.com/drive/folders/1yDzuY-OHNQuNfhXOW5f1gRsWyOtxMM9t)
- [What´s New v43 & 3.4?](https://docs.google.com/presentation/d/1X_10LETAuaHAMoxK2OQLIWlv0nrnUNNXGnow5e5oaR8/edit)
