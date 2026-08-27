# KPG-193 Data Provenance

This directory is intended to contain the PyPowSyBl-compatible KPG-193 case used by the AI-EMS Agent demo.

Expected local file:

`KPG193_ver2_0_pypowsybl.mat`

## Original dataset

The source system is **KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies**, developed by the AGM Center at KENTECH.

- Project: KPG (Korean Power Grid) Platform / KPG Test System
- Version used: KPG-193 v2.0
- License stated by the upstream repository: Open Database License (ODbL) 1.0
- Paper: Geonho Song and Jip Kim, "KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies," arXiv:2411.14756, 2024.
- Upstream repository: https://github.com/agm-center/kpg-testgrid
- Documentation: https://agm.kentech.ac.kr/docs/kpg-test-system/

## PyPowSyBl-compatible derived case

`KPG193_ver2_0_pypowsybl.mat` is not the untouched upstream MAT file. It is a derived MATPOWER case prepared for this project so that the KPG-193 network can be imported and analyzed with PyPowSyBl.

The current conversion follows the previously validated KPG practice code:

- base-voltage information is preserved when importing to PyPowSyBl;
- the original `dcline` representation is omitted from the derived MATPOWER case;
- fixed HVDC transfers are represented using terminal dummy generators, consistent with the earlier KPG/PYPOWER practice;
- the resulting case has been used successfully for AC load flow and PyPowSyBl Security Analysis.

This repository's code and any derived case do not replace the upstream KPG documentation or dataset. Refer to the upstream project and paper for the authoritative description of KPG-193.
