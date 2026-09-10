# KPG-193 Data Provenance

This directory is intended to contain the KPG-193 case used by the AI-EMS Agent demo.

Expected local file:

`KPG193_ver2_0_powsybl_full.mat`

The MAT file itself is **not committed to this public repository**. The repository keeps only provenance and setup documentation. During development, the file is kept on the developer machine; for the final offline Linux workstation demo, the required case file is transferred separately and placed in this directory.

## Original dataset

The source system is **KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies**, developed by the AGM Center at KENTECH.

- Project: KPG (Korean Power Grid) Platform / KPG Test System
- Version used: KPG-193 v2.0
- Paper: Geonho Song and Jip Kim, "KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies," arXiv:2411.14756, 2024.
- Upstream repository: https://github.com/agm-center/kpg-testgrid
- Documentation: https://agm.kentech.ac.kr/docs/kpg-test-system/

Refer to the upstream project and paper for the authoritative description and licensing terms of KPG-193.

## PyPowSyBl integration case

`KPG193_ver2_0_powsybl_full.mat` keeps the KPG MATPOWER fields used by the shared case, including the two parallel DC-line records. The file is loaded through `src/ai_ems/utils/kpg_powsybl_adapter.py` rather than directly through PowSyBl's MATPOWER importer.

The adapter is required because the KPG case and the current PowSyBl MATPOWER importer represent the parallel HVDC lines differently:

- the KPG case can encode reverse DC flow with the sign of `PF`, while IIDM uses a non-negative target power plus a converter mode for direction;
- the two parallel DC lines share the same endpoint buses, while PowSyBl's MATPOWER importer derives converter/HVDC identifiers from the bus pair and therefore cannot distinguish both rows directly.

The adapter therefore:

1. reads the shared MAT file without modifying it;
2. creates a temporary MATPOWER case with `dcline`/`dclinecost` suppressed only for the AC-network import;
3. imports the AC network with the original base-voltage information preserved;
4. creates the two VSC-HVDC links explicitly with unique IDs through the public PyPowSyBl API.

The integrated model has:

- 193 buses
- 201 physical generators
- 193 loads
- 385 AC lines
- 2 HVDC lines represented as actual IIDM HVDC/VSC elements

Regression checks against the earlier dummy-generator model showed matching AC operating-point results to numerical tolerance, including voltage range, all 385 AC-line flows, and the 201 common generator outputs. The new model is therefore used as the default case for Security Analysis, Sensitivity Analysis, redispatch validation, Agent workflows, and the Physics API.
