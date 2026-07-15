# How to Set Up a Dedicated Analytics Database for DHIS2 using Apache Doris and ClickHouse

Thursday 18 June, 08:05 · Auditorium 4
Presenters: Lars Helge Øverland and Bob Jolliffe (DHIS2 core team), with Ola moderating.
[Recording](https://youtu.be/YeaV7FCTbes) · [Slides](https://drive.google.com/drive/folders/1tgTMlcJxtnJMRZrZ06YcL38vsm5EHFFV) · [Dryfta](https://dac2026.dryfta.com/program-schedule/program/52/how-to-set-up-a-dedicated-analytics-database-for-dhis2-using-apache-doris-and-clickhouse)

## Summary

After two decades of data accumulation, large DHIS2 databases hit query timeouts and analytics table generation runs that stretch to 12 hours or more. Because DHIS2 runs data entry, validation, and analytics on one Postgres instance, slow analytics degrade everything. Postgres tuning has reached its limit for the biggest countries, so the core team built a dedicated analytics database option, released incrementally since January 2024. Postgres stays for metadata, transactional data, and PostGIS geometry; an optional columnar database handles analytical tables and queries only. The two coexist, and the switch is transparent to end users.

The team chose Apache Doris and ClickHouse, both open source, widely adopted, on-premise capable, and horizontally scalable. A new SQL builder abstraction isolates database-specific code so other engines can be plugged in later. Data is pulled from Postgres into the analytics database over JDBC (Doris JDBC catalog, ClickHouse Postgres table engine). Setup is four dhis.conf properties: database type, connection URL, username, password. The databases are fast because they store data column-oriented, compress heavily, sort on insert, and parallelize queries; they use no indexes, which also speeds table generation. As of v42 aggregate data and events are supported; v43 adds enrollment and tracker analytics. Server-side event clustering (PostGIS) and data-quality outlier detection still run on Postgres.

Bob Jolliffe presented a Ghana Health Service test that replayed 30,000 real analytics queries from a day's access logs against Postgres alone and Postgres plus Doris. The speed gain was modest because Ghana had already tuned its dashboards to survive, keeping most queries fast; Doris removed the slow tail. The wider benefits mattered more: the transactional system stayed quiet under analytics load (no connection exhaustion, better user experience), full analytics dropped from 90 to 47 minutes, and moving the daily "shark tooth" table churn out of Postgres makes high availability and incremental backups practical. Both presenters called it stable and ready for production, with a low barrier to trialing since the analytics database holds only ephemeral data and can be removed without risk.

## Key points

- Optional dedicated analytics database (Doris or ClickHouse) alongside Postgres; transparent to users, configured with four dhis.conf lines.
- v43 supports aggregate, events, enrollment, and tracker; PostGIS event clustering and outlier detection remain on Postgres.
- Ghana test: modest speed gain on an already-tuned system, but analytics run time roughly halved and the transactional system stayed responsive under load.
- Removing analytics table churn from Postgres enables cleaner replication (HA) and incremental backups.
- Continuous/incremental analytics works for aggregate and events; enrollment incremental loading is not yet implemented.

## Resources

- [Analytics Database for DHIS2 DAC2026](https://docs.google.com/presentation/d/1XG6shGolahc_6VIrLzjHUFRmAp9M7g1p1ts7hdR4HUA/edit)
