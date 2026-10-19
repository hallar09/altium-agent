# SPDX-License-Identifier: Apache-2.0
"""Board analysis that needs no editor: SI/PI/EMC suites, DFM against
manufacturer profiles, stack-up synthesis and impedance (analyze_* tools).

The SPICE path forked from eda-agent lives in ``altium_agent.sim`` and is
exposed as ``analyze_sim_*``; the altium-mcp-pro engines join here in
phase (b).
"""
