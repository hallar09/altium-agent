# SPDX-License-Identifier: Apache-2.0
"""A net called ``-LED_R`` must reach Altium as ``-LED_R``.

Two places could lose it: the command line, where argparse reads a value
starting with '-' as an option, and the wire, where list fields are joined
and split. Both are checked here; the wire through the simulator.
"""

from __future__ import annotations

import argparse

from altium_agent.core.cli_args import join_list_options, split_names


def _parser():
    p = argparse.ArgumentParser()
    p.add_argument("--nets", default="")
    p.add_argument("--dry", action="store_true")
    return p


def test_the_equals_form_parses_as_is():
    args = _parser().parse_args(["--nets=-LED_R,-LED_G", "--dry"])
    assert split_names(args.nets) == ["-LED_R", "-LED_G"] and args.dry


def test_the_spaced_form_is_joined_before_argparse_sees_it():
    argv = join_list_options(["--nets", "-LED_R,-LED_G", "--dry"], ["--nets"])
    args = _parser().parse_args(argv)
    assert split_names(args.nets) == ["-LED_R", "-LED_G"] and args.dry


def test_other_arguments_are_untouched():
    assert join_list_options(["--dry", "x"], ["--nets"]) == ["--dry", "x"]


def test_a_dash_net_survives_the_bridge(e2e_bridge):
    e2e_bridge.send_command("pcb.create_nets_from_list",
                            {"nets": "-LED_R|-LED_G|GND"}, timeout=5.0)
    nets = e2e_bridge.send_command("pcb.get_nets", timeout=5.0)["nets"]
    assert "-LED_R" in nets and "-LED_G" in nets
